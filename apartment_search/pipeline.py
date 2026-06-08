from __future__ import annotations

import json
from pathlib import Path
from sys import stderr
from typing import Any, Iterable

from apartment_search.cache import ListingCache
from apartment_search.commute import CommuteEstimator
from apartment_search.diligence import application_doc_rows, tour_checklist_rows
from apartment_search.filtering import filter_listing
from apartment_search.hpd import HpdViolationClient
from apartment_search.models import LaundryStatus, Listing, PreferenceProfile, RankedListing
from apartment_search.outreach import build_outreach_draft
from apartment_search.preferences import load_profile
from apartment_search.providers import ListingProvider, RapidApiRealtyProvider, RapidApiRequestBudgetExceeded
from apartment_search.scoring import ListingScorer
from apartment_search.sheets import GoogleSheetsWriter
from apartment_search.workspace import load_workspace_config


class ApartmentSearchPipeline:
    def __init__(
        self,
        provider: ListingProvider,
        profile: PreferenceProfile,
        sheets_writer: GoogleSheetsWriter,
        scorer: ListingScorer,
        commute_estimator: CommuteEstimator | None = None,
        hpd_client: HpdViolationClient | None = None,
        listing_cache: ListingCache | None = None,
    ) -> None:
        self.provider = provider
        self.profile = profile
        self.sheets_writer = sheets_writer
        self.scorer = scorer
        self.commute_estimator = commute_estimator or CommuteEstimator()
        self.hpd_client = hpd_client or HpdViolationClient()
        self.listing_cache = listing_cache or ListingCache()

    def run(self, dry_run: bool = False, limit: int | None = None) -> dict[str, Any]:
        warnings: list[str] = []
        stopped_early = False
        _stage("Fetching listings from provider")
        try:
            raw_listings = self.provider.search(self.profile)
        except RapidApiRequestBudgetExceeded as error:
            raw_listings = []
            stopped_early = True
            warnings.append(str(error))
            _stage(f"Stopping early: {error}")
        ranked: list[RankedListing] = []
        processed_count = 0

        listings_to_process = raw_listings[:limit]
        for listing in _progress(listings_to_process, "Filtering and scoring listings"):
            processed_count += 1
            cached = self.listing_cache.get(listing)
            if cached:
                enriched = cached
            else:
                if _can_pre_filter(listing):
                    _stage(f"Pre-filtering hard requirements for {listing.display_name}")
                    preliminary_filter = filter_listing(listing, self.profile)
                    if not preliminary_filter.passes:
                        continue
                _stage(f"Enriching listing {listing.display_name}")
                try:
                    enriched = self.provider.fetch_details(listing)
                except RapidApiRequestBudgetExceeded as error:
                    stopped_early = True
                    warnings.append(str(error))
                    _stage(f"Stopping early: {error}")
                    break
                enriched = self.commute_estimator.enrich(enriched, self.profile)
                enriched.raw["hpd_risk"] = self.hpd_client.summarize_listing(enriched).as_dict()
                self.listing_cache.set(enriched)
            _stage(f"Filtering hard requirements for {enriched.display_name}")
            filter_result = filter_listing(enriched, self.profile)
            if not filter_result.passes:
                continue
            _stage(f"Scoring qualitative criteria for {enriched.display_name}")
            score = self.scorer.score(enriched, filter_result)
            outreach = build_outreach_draft(enriched, self.profile)
            ranked.append(RankedListing(enriched, filter_result, score, outreach))

        ranked.sort(key=lambda item: item.score.total, reverse=True)
        _stage("Syncing Google Sheet workflow")
        sheet_result = self.sheets_writer.write(
            ranked,
            self.profile,
            tour_checklist=tour_checklist_rows(),
            application_docs=application_doc_rows(self.profile),
            dry_run=dry_run,
        )
        return {
            "candidate_count": len(ranked),
            "top_candidates": [
                {
                    "rank": index,
                    "score": ranked_listing.score.total,
                    "url": ranked_listing.listing.url,
                    "address": ranked_listing.listing.address,
                    "rationale": ranked_listing.score.rationale,
                }
                for index, ranked_listing in enumerate(ranked[:10], start=1)
            ],
            "sheets": sheet_result,
            "warnings": warnings,
            "stopped_early": stopped_early,
            "processed_listing_count": processed_count,
            "requested_listing_limit": limit,
            "request_stats": {
                **self.provider.stats(),
                **self.listing_cache.stats(),
            },
        }


class SeedListingProvider(ListingProvider):
    """Local JSON provider for dry runs and calibrating the scoring profile."""

    def __init__(self, listings: list[Listing]) -> None:
        self.listings = listings

    def search(self, profile: PreferenceProfile) -> list[Listing]:
        return self.listings

    def fetch_details(self, listing: Listing) -> Listing:
        return listing


def build_pipeline(
    profile_path: str | Path | None = None,
    workspace_path: str | Path | None = None,
    folder_link_path: str | Path | None = None,
    seed_listings_path: str | Path | None = None,
    use_gemini: bool = False,
    enable_hpd_lookup: bool = False,
    listing_cache_path: str | Path = ".cache/apartment_search/listings.json",
    rapidapi_max_requests: int | None = None,
) -> ApartmentSearchPipeline:
    profile = load_profile(profile_path)
    provider: ListingProvider
    if seed_listings_path:
        provider = SeedListingProvider(load_seed_listings(seed_listings_path))
    else:
        provider = RapidApiRealtyProvider(max_requests=rapidapi_max_requests)

    workspace = load_workspace_config(workspace_path)
    folder_link = workspace.google_drive_folder_link or None
    if folder_link_path and Path(folder_link_path).exists():
        folder_link = Path(folder_link_path).read_text(encoding="utf-8").strip()

    sheets_writer = GoogleSheetsWriter.from_env(
        folder_link=folder_link,
        spreadsheet_id=workspace.google_sheets_spreadsheet_id,
        folder_id=workspace.google_drive_folder_id,
        spreadsheet_title=workspace.google_sheets_title,
    )
    scorer = ListingScorer(profile, use_gemini=use_gemini)
    hpd_client = HpdViolationClient(enabled=enable_hpd_lookup)
    listing_cache = ListingCache(listing_cache_path)
    return ApartmentSearchPipeline(
        provider,
        profile,
        sheets_writer,
        scorer,
        hpd_client=hpd_client,
        listing_cache=listing_cache,
    )


def _stage(message: str) -> None:
    print(f"[rentrank-nyc] {message}", file=stderr)


def _progress(items: list[Listing], description: str) -> Iterable[Listing]:
    try:
        from tqdm import tqdm
    except ImportError:
        return items
    return tqdm(items, desc=description, unit="listing", file=stderr)


def _can_pre_filter(listing: Listing) -> bool:
    return (
        listing.rent is not None
        and listing.bedrooms is not None
        and listing.bathrooms is not None
        and listing.laundry_status != LaundryStatus.UNKNOWN
    )


def load_seed_listings(path: str | Path) -> list[Listing]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        items = data.get("listings", [])
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("Seed listings JSON must be a list or an object with a listings list.")
    return [_listing_from_seed(item) for item in items if isinstance(item, dict)]


def _listing_from_seed(item: dict[str, Any]) -> Listing:
    return Listing(
        source=str(item.get("source", "seed")),
        source_id=str(item.get("source_id") or item.get("id") or item.get("url") or ""),
        url=str(item.get("url", "")),
        address=item.get("address"),
        unit=item.get("unit"),
        neighborhood=item.get("neighborhood"),
        borough=item.get("borough"),
        rent=item.get("rent"),
        bedrooms=item.get("bedrooms"),
        bathrooms=item.get("bathrooms"),
        square_feet=item.get("square_feet"),
        description=item.get("description"),
        amenities=list(item.get("amenities", [])),
        laundry_status=_laundry_status(item.get("laundry_status")),
        image_urls=list(item.get("image_urls", item.get("images", []))),
        listed_at=item.get("listed_at"),
        days_on_market=item.get("days_on_market"),
        no_fee=item.get("no_fee"),
        agents=list(item.get("agents", [])),
        open_house_dates=list(item.get("open_house_dates", [])),
        latitude=item.get("latitude"),
        longitude=item.get("longitude"),
        commute_minutes=item.get("commute_minutes"),
        commute_to_work_minutes=item.get("commute_to_work_minutes"),
        commute_home_minutes=item.get("commute_home_minutes"),
        subway_walk_minutes=item.get("subway_walk_minutes"),
        subway_transfers=item.get("subway_transfers"),
        raw=item,
    )


def _laundry_status(value: Any) -> LaundryStatus:
    if isinstance(value, LaundryStatus):
        return value
    if value in (None, ""):
        return LaundryStatus.UNKNOWN
    try:
        return LaundryStatus(str(value))
    except ValueError:
        return LaundryStatus.UNKNOWN
