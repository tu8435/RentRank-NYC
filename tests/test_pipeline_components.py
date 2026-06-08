import ssl
import urllib.error

import pytest

from apartment_search.cli import _safe_env_fingerprint
from apartment_search.commute import CommuteEstimator, next_weekday_timestamp
from apartment_search.diligence import application_doc_rows, tour_checklist_rows
from apartment_search.filtering import classify_laundry, filter_listing
from apartment_search.hpd import parse_nyc_address
from apartment_search.outreach import build_outreach_draft
from apartment_search.pipeline import ApartmentSearchPipeline
from apartment_search.providers.base import ListingProvider
from apartment_search.providers.rapidapi_realty import RapidApiRealtyProvider, RapidApiRequestBudgetExceeded
from apartment_search.request_budget import estimate_requests
from apartment_search.models import CategoryScores, FilterResult, LaundryStatus, Listing, ListingScore, RankedListing
from apartment_search.preferences import default_profile
from apartment_search.scoring import GoogleGeminiScoringClient, ListingScorer, _format_url_error, _ssl_context, heuristic_score_listing
from apartment_search.sheets import CANDIDATE_HEADERS, REJECTED_HEADERS, TOUR_HEADERS, build_workbook_values, build_workflow_rows
from apartment_search.sheets import parse_drive_folder_id, parse_spreadsheet_id
from apartment_search.sheets import _usable_path


def test_default_profile_uses_private_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APARTMENT_RENTER_NAMES", "Private One, Private Two")
    monkeypatch.setenv("APARTMENT_RENTER_EMAILS", "one@example.com, two@example.com")
    monkeypatch.setenv("APARTMENT_MOVE_IN", "August 1")
    monkeypatch.setenv("APARTMENT_COMMUTE_DESTINATION", "Private Office, New York, NY")

    profile = default_profile()

    assert profile.renter_names == ["Private One", "Private Two"]
    assert profile.renter_emails == ["one@example.com", "two@example.com"]
    assert profile.move_in == "August 1"
    assert profile.commute.destination_address == "Private Office, New York, NY"


def test_outreach_uses_private_applicant_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APARTMENT_RENTER_NAMES", "Private One, Private Two")
    monkeypatch.setenv("APARTMENT_OUTREACH_APPLICANT_DETAILS", "Line one\\nLine two")
    profile = default_profile()
    listing = Listing(source="test", source_id="1", url="https://streeteasy.com/example", address="123 Main Street")

    draft = build_outreach_draft(listing, profile)

    assert "Line one\nLine two" in draft
    assert "Private One" in draft


def test_application_docs_use_private_credit_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APARTMENT_CREDIT_SCORE_NOTES", "Private credit details live here.")
    profile = default_profile()

    rows = application_doc_rows(profile)

    credit_rows = [row for row in rows if row["document"] == "Credit score proof"]
    assert credit_rows
    assert all(row["notes"] == "Private credit details live here." for row in credit_rows)


def test_laundry_dealbreaker_rejects_laundromat_only() -> None:
    profile = default_profile()
    listing = Listing(
        source="test",
        source_id="1",
        url="https://streeteasy.com/example",
        rent=4200,
        bedrooms=2,
        bathrooms=1,
        description="Two bedroom with nearby laundromat.",
        amenities=["Nearby laundromat"],
    )

    result = filter_listing(listing, profile)

    assert not result.passes
    assert "Nearby laundromat" in " ".join(result.reasons)


def test_laundry_classifier_accepts_api_amenity_tokens() -> None:
    listing = Listing(
        source="test",
        source_id="laundry-token",
        url="https://streeteasy.com/example-laundry",
        amenities=["washer_dryer", "laundry"],
    )

    assert classify_laundry(listing) == LaundryStatus.IN_UNIT


def test_laundry_classifier_prefers_structured_status_over_text() -> None:
    listing = Listing(
        source="test",
        source_id="structured-laundry",
        url="https://streeteasy.com/example-structured-laundry",
        description="Marketing copy says laundry is nearby.",
        laundry_status=LaundryStatus.IN_BUILDING,
    )

    assert classify_laundry(listing) == LaundryStatus.IN_BUILDING


def test_laundry_classifier_does_not_accept_description_only_signal() -> None:
    listing = Listing(
        source="test",
        source_id="description-only-laundry",
        url="https://streeteasy.com/example-description-laundry",
        description="Beautiful two-bedroom with in-unit washer/dryer.",
    )

    assert classify_laundry(listing) == LaundryStatus.UNKNOWN


def test_strong_listing_scores_above_average() -> None:
    profile = default_profile()
    listing = Listing(
        source="test",
        source_id="2",
        url="https://streeteasy.com/example-2",
        address="100 Sample Street",
        neighborhood="SoHo",
        borough="Manhattan",
        rent=5000,
        bedrooms=2,
        bathrooms=1.5,
        description=(
            "Bright renovated two bedroom with oversized windows, dishwasher, counter space, "
            "in-unit washer/dryer, large living room for entertaining, and split bedrooms with desks."
        ),
        amenities=["In-unit washer/dryer", "Dishwasher"],
        image_urls=["https://example.com/1.jpg"] * 6,
        commute_minutes=12,
        subway_walk_minutes=4,
        subway_transfers=0,
        no_fee=True,
    )

    filter_result = filter_listing(listing, profile)
    score = heuristic_score_listing(listing, filter_result, profile)

    assert filter_result.passes
    assert score.total > 70
    assert score.categories.bright_modern >= 7


def test_workbook_contains_planned_tabs() -> None:
    profile = default_profile()
    workbook = build_workbook_values([], profile, tour_checklist_rows(), application_doc_rows(profile))

    assert set(workbook) == {"Candidates", "Tours", "Tour Checklist", "Preference Profile", "Application Docs", "Rejected"}
    assert workbook["Candidates"][0][0] == "Lifecycle"
    assert workbook["Tours"][0][0] == "Tour Status"
    assert len(workbook["Tour Checklist"]) > 5
    assert len(workbook["Application Docs"]) > 10


def test_parse_drive_folder_id() -> None:
    folder_id = parse_drive_folder_id("https://drive.google.com/drive/folders/1dWO56miPVrknCEQ6k-QwN8C9hN2loYDX")

    assert folder_id == "1dWO56miPVrknCEQ6k-QwN8C9hN2loYDX"


def test_parse_spreadsheet_id_accepts_full_url() -> None:
    spreadsheet_id = parse_spreadsheet_id(
        "https://docs.google.com/spreadsheets/d/1JgZ601oY2AFgOwVFQipUOEedNtmmTiGiMJGFy1k-nqg/edit"
    )

    assert spreadsheet_id == "1JgZ601oY2AFgOwVFQipUOEedNtmmTiGiMJGFy1k-nqg"


def test_placeholder_google_credentials_path_is_ignored() -> None:
    assert _usable_path("/absolute/path/to/google-service-account.json") is None


def test_parse_nyc_address_normalizes_street_abbreviations() -> None:
    assert parse_nyc_address("123 Main St") == ("123", "MAIN STREET")


def test_request_estimate_accounts_for_seen_listing_cache() -> None:
    estimate = estimate_requests(
        listings_considered=100,
        seen_cache_hit_rate=0.6,
        detail_requests_needed=True,
        use_gemini=True,
    )

    assert estimate.rapidapi_total_requests == 41
    assert estimate.expected_seen_cache_hits == 60
    assert estimate.gemini_model_requests == 40


def test_rapidapi_normalizer_accepts_direct_streeteasy_link() -> None:
    provider = RapidApiRealtyProvider(api_key="test", base_url="https://example.com")

    listing = provider._normalize_listing(
        {
            "id": "abc",
            "streeteasy_url": "https://streeteasy.com/building/example/2a",
            "price": "$5,000",
            "beds": 2,
            "baths": 1.5,
        }
    )

    assert listing.url == "https://streeteasy.com/building/example/2a"
    assert listing.rent == 5000
    assert listing.bedrooms == 2


def test_rapidapi_normalizer_accepts_st_easy_node_fields() -> None:
    provider = RapidApiRealtyProvider(api_key="test", base_url="https://example.com")

    listing = provider._normalize_listing(
        {
            "node": {
                "id": "4835703",
                "urlPath": "/rental/4835703",
                "street": "65 Dupont Street",
                "unit": "203",
                "areaName": "Greenpoint",
                "price": 5000,
                "bedroomCount": 2,
                "fullBathroomCount": 1,
                "halfBathroomCount": 1,
                "noFee": True,
                "amenities": [{"name": "In-unit washer/dryer"}],
                "photos": [{"url": "https://example.com/photo.jpg"}],
            }
        }
    )

    assert listing.url == "https://streeteasy.com/rental/4835703"
    assert listing.address == "65 Dupont Street"
    assert listing.neighborhood == "Greenpoint"
    assert listing.bedrooms == 2
    assert listing.bathrooms == 1.5
    assert listing.no_fee is True
    assert listing.amenities == ["In-unit washer/dryer"]
    assert listing.laundry_status == LaundryStatus.IN_UNIT
    assert listing.image_urls == ["https://example.com/photo.jpg"]


def test_rapidapi_normalizer_converts_photo_keys_to_image_urls() -> None:
    provider = RapidApiRealtyProvider(api_key="test", base_url="https://example.com")

    listing = provider._normalize_listing(
        {
            "node": {
                "id": "5064087",
                "urlPath": "/building/68-covert-street-brooklyn/2a",
                "street": "68 Covert Street",
                "areaName": "Bushwick",
                "price": 3500,
                "bedroomCount": 3,
                "fullBathroomCount": 1,
                "photos": [{"key": "1670f53a4aa12cee15a6882cf6517128"}],
                "leadMedia": {"photo": {"key": "0e2f365fa87cba20cdeaf9c52cf721c9"}},
            }
        }
    )

    assert listing.borough == "Brooklyn"
    assert "https://photos.zillowstatic.com/fp/1670f53a4aa12cee15a6882cf6517128-p_e.webp" in listing.image_urls
    assert "https://photos.zillowstatic.com/fp/0e2f365fa87cba20cdeaf9c52cf721c9-p_e.webp" in listing.image_urls


def test_manhattan_locations_get_stronger_centrality_than_brooklyn() -> None:
    profile = default_profile()
    manhattan = Listing(
        source="test",
        source_id="manhattan",
        url="https://streeteasy.com/manhattan",
        neighborhood="Kips Bay",
        borough="Manhattan",
        rent=5000,
        bedrooms=2,
        bathrooms=1,
        laundry_status=LaundryStatus.IN_UNIT,
    )
    brooklyn = Listing(
        source="test",
        source_id="brooklyn",
        url="https://streeteasy.com/brooklyn",
        neighborhood="Crown Heights",
        borough="Brooklyn",
        rent=5000,
        bedrooms=2,
        bathrooms=1,
        laundry_status=LaundryStatus.IN_UNIT,
    )

    manhattan_score = heuristic_score_listing(manhattan, filter_listing(manhattan, profile), profile)
    brooklyn_score = heuristic_score_listing(brooklyn, filter_listing(brooklyn, profile), profile)

    assert manhattan_score.categories.centrality == 9
    assert brooklyn_score.categories.centrality == 6
    assert manhattan_score.total > brooklyn_score.total


def test_google_maps_demo_mode_is_not_enabled() -> None:
    estimator = CommuteEstimator(api_key="demo")
    listing = Listing(source="test", source_id="1", url="https://streeteasy.com/example")

    enriched = estimator.enrich(listing, default_profile())

    assert not estimator.enabled
    assert enriched.raw["commute_mode"] == "google_maps_demo_no_external_request"
    assert "to_work" in enriched.raw["commute_estimate_settings"]


def test_google_gemini_defaults_to_gemini_35_flash() -> None:
    client = GoogleGeminiScoringClient(api_key="test")

    assert client.model == "gemini-3.5-flash"


def test_gemini_uses_certifi_ssl_context() -> None:
    assert isinstance(_ssl_context(), ssl.SSLContext)


def test_google_http_errors_include_response_body() -> None:
    error = urllib.error.HTTPError(
        url="https://example.com",
        code=403,
        msg="Forbidden",
        hdrs={},
        fp=FakeErrorBody(b'{"error":{"status":"PERMISSION_DENIED","message":"API disabled"}}'),
    )

    assert "PERMISSION_DENIED" in _format_url_error(error)
    assert "API disabled" in _format_url_error(error)


def test_google_checks_reject_malformed_gemini_key() -> None:
    result = GoogleGeminiScoringClient(api_key="bad key with spaces").check_connection()

    assert result["ok"] is False
    assert "malformed" in str(result["error"])


def test_safe_env_fingerprint_does_not_expose_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SECRET_KEY", "secret-value")

    result = _safe_env_fingerprint("TEST_SECRET_KEY")

    assert result["configured"] is True
    assert result["length"] == len("secret-value")
    assert result["fingerprint"] != "secret-value"


def test_scorer_ignores_cached_failed_gemini_fallback() -> None:
    profile = default_profile()
    listing = _listing("cached-failed-gemini", rent=5000)
    filter_result = FilterResult(passes=True, laundry_status=LaundryStatus.IN_UNIT)
    cache = FakeScoreCache(
        cached={
            "total": 1,
            "categories": CategoryScores().as_dict(),
            "confidence": 0.1,
            "rationale": "old failed fallback",
            "red_flags": ["Google Gemini scoring failed: old SSL error"],
            "follow_up_questions": [],
            "model_used": "heuristic",
        }
    )
    client = FakeGeminiClient(model_score=heuristic_score_listing(listing, filter_result, profile))

    score = ListingScorer(profile, use_gemini=True, cache=cache, gemini_client=client).score(listing, filter_result)

    assert client.calls == 1
    assert score.rationale != "old failed fallback"
    assert cache.set_calls == 0


def test_scorer_caches_successful_gemini_scores_only() -> None:
    profile = default_profile()
    listing = _listing("successful-gemini", rent=5000)
    filter_result = FilterResult(passes=True, laundry_status=LaundryStatus.IN_UNIT)
    successful_score = heuristic_score_listing(listing, filter_result, profile)
    successful_score.model_used = "google_gemini"
    cache = FakeScoreCache()
    client = FakeGeminiClient(model_score=successful_score)

    ListingScorer(profile, use_gemini=True, cache=cache, gemini_client=client).score(listing, filter_result)

    assert cache.set_calls == 1


def test_rapidapi_request_cap_blocks_extra_calls() -> None:
    provider = RapidApiRealtyProvider(api_key="test", base_url="https://example.com", max_requests=0)

    with pytest.raises(RuntimeError, match="RapidAPI request cap reached"):
        provider._get("/search/rent", {})


def test_maps_request_estimate_counts_two_commute_directions() -> None:
    estimate = estimate_requests(listings_considered=100, use_google_maps=True)

    assert estimate.google_maps_requests == 200


def test_next_weekday_timestamp_returns_future_time() -> None:
    assert next_weekday_timestamp(hour=9) > 0


def test_high_score_listing_auto_promotes_to_tours() -> None:
    profile = default_profile()
    workflow = build_workflow_rows([_ranked_listing(total=90)], profile)

    assert workflow["Candidates"] == []
    assert workflow["Tours"][0][TOUR_HEADERS.index("Promotion Reason")] == "Auto Promoted"


def test_two_yes_votes_promote_and_preserve_notes() -> None:
    profile = default_profile()
    existing_candidate = _row_from_headers(
        CANDIDATE_HEADERS,
        {
            "Lifecycle": "Needs Review",
            "StreetEasy URL": "https://streeteasy.com/test",
            "Source ID": "listing-1",
            "Reviewer 1 Vote": "Yes",
            "Reviewer 2 Vote": "Yes",
            "Reviewer 1 Notes": "Great light",
            "Reviewer 2 Notes": "Works for me",
        },
    )
    workflow = build_workflow_rows(
        [_ranked_listing(total=70)],
        profile,
        {"Candidates": [CANDIDATE_HEADERS, existing_candidate]},
    )

    assert workflow["Candidates"] == []
    tour = workflow["Tours"][0]
    assert tour[TOUR_HEADERS.index("Promotion Reason")] == "Promoted By Votes"
    assert tour[TOUR_HEADERS.index("Reviewer 1 Notes")] == "Great light"
    assert tour[TOUR_HEADERS.index("Reviewer 2 Notes")] == "Works for me"


def test_rejected_tour_moves_to_rejected_archive() -> None:
    profile = default_profile()
    existing_tour = _row_from_headers(
        TOUR_HEADERS,
        {
            "Tour Status": "Rejected",
            "StreetEasy URL": "https://streeteasy.com/test",
            "Address": "100 Sample Street",
            "Source ID": "listing-1",
            "Decision Notes": "Too dark in person",
        },
    )
    workflow = build_workflow_rows([], profile, {"Tours": [TOUR_HEADERS, existing_tour]})

    assert workflow["Tours"] == []
    assert workflow["Rejected"][0][3] == "100 Sample Street"
    assert workflow["Rejected"][0][4] == "Too dark in person"


def test_existing_active_candidates_are_preserved_and_reranked() -> None:
    profile = default_profile()
    existing_candidate = _row_from_headers(
        CANDIDATE_HEADERS,
        {
            "Lifecycle": "Needs Review",
            "Rank": 99,
            "Total Score": 80,
            "StreetEasy URL": "https://streeteasy.com/existing",
            "Address": "Existing Candidate",
            "Source ID": "existing",
            "Reviewer 1 Vote": "Maybe",
            "Reviewer 1 Notes": "Still worth reviewing",
        },
    )

    workflow = build_workflow_rows(
        [_ranked_listing_for("new", total=70)],
        profile,
        {"Candidates": [CANDIDATE_HEADERS, existing_candidate]},
    )

    assert len(workflow["Candidates"]) == 2
    assert workflow["Candidates"][0][CANDIDATE_HEADERS.index("Source ID")] == "existing"
    assert workflow["Candidates"][0][CANDIDATE_HEADERS.index("Rank")] == 1
    assert workflow["Candidates"][1][CANDIDATE_HEADERS.index("Source ID")] == "new"
    assert workflow["Candidates"][1][CANDIDATE_HEADERS.index("Rank")] == 2


def test_existing_candidate_with_any_no_vote_is_rejected() -> None:
    profile = default_profile()
    existing_candidate = _row_from_headers(
        CANDIDATE_HEADERS,
        {
            "Lifecycle": "Needs Review",
            "Total Score": 80,
            "StreetEasy URL": "https://streeteasy.com/no-vote",
            "Address": "No Vote Candidate",
            "Source ID": "no-vote",
            "Reviewer 2 Vote": "No",
        },
    )

    workflow = build_workflow_rows([], profile, {"Candidates": [CANDIDATE_HEADERS, existing_candidate]})

    assert workflow["Candidates"] == []
    assert workflow["Rejected"][0][REJECTED_HEADERS.index("Source ID")] == "no-vote"
    assert workflow["Rejected"][0][REJECTED_HEADERS.index("Reason")] == "Reviewer 2 voted No"


def test_commute_score_uses_tiers_and_transfer_penalties() -> None:
    profile = default_profile()

    ideal = _listing("ideal-commute", rent=5000)
    ideal.commute_to_work_minutes = 18
    ideal.subway_transfers = 0
    decent = _listing("decent-commute", rent=5000)
    decent.commute_to_work_minutes = 22
    decent.subway_transfers = 1
    weak = _listing("weak-commute", rent=5000)
    weak.commute_to_work_minutes = 27
    weak.subway_transfers = 2
    bad = _listing("bad-commute", rent=5000)
    bad.commute_to_work_minutes = 31
    bad.subway_transfers = 3

    filter_result = FilterResult(passes=True, laundry_status=LaundryStatus.IN_UNIT)

    assert heuristic_score_listing(ideal, filter_result, profile).categories.commute_transit == 10
    assert heuristic_score_listing(decent, filter_result, profile).categories.commute_transit == 7.25
    assert heuristic_score_listing(weak, filter_result, profile).categories.commute_transit == 3.0
    assert heuristic_score_listing(bad, filter_result, profile).categories.commute_transit == 0


def test_pipeline_returns_partial_results_when_request_cap_is_reached() -> None:
    profile = default_profile()
    provider = BudgetLimitedProvider(
        [
            _listing("passes-before-cap", rent=5000),
            _listing("would-exceed-cap", rent=5000),
        ],
        fail_on_detail_index=2,
    )
    pipeline = ApartmentSearchPipeline(
        provider=provider,
        profile=profile,
        sheets_writer=FakeSheetsWriter(),
        scorer=ListingScorer(profile),
        listing_cache=FakeListingCache(),
    )

    result = pipeline.run(dry_run=True, limit=2)

    assert result["candidate_count"] == 1
    assert result["stopped_early"] is True
    assert "RapidAPI request cap reached" in result["warnings"][0]
    assert result["request_stats"]["detail_requests"] == 2


def test_pipeline_prefilters_obvious_rejects_before_fetching_details() -> None:
    profile = default_profile()
    provider = BudgetLimitedProvider(
        [
            _listing("too-expensive", rent=10000),
            _listing("candidate", rent=5000),
        ]
    )
    pipeline = ApartmentSearchPipeline(
        provider=provider,
        profile=profile,
        sheets_writer=FakeSheetsWriter(),
        scorer=ListingScorer(profile),
        listing_cache=FakeListingCache(),
    )

    result = pipeline.run(dry_run=True, limit=2)

    assert result["candidate_count"] == 1
    assert provider.detail_requests == 1


def _ranked_listing(total: float) -> RankedListing:
    return _ranked_listing_for("listing-1", total)


def _ranked_listing_for(source_id: str, total: float) -> RankedListing:
    listing = Listing(
        source="test",
        source_id=source_id,
        url=f"https://streeteasy.com/{source_id}",
        address="100 Sample Street",
        neighborhood="SoHo",
        borough="Manhattan",
        rent=5000,
        bedrooms=2,
        bathrooms=1.5,
        description="Bright apartment with in-unit laundry.",
        amenities=["In-unit washer/dryer"],
    )
    return RankedListing(
        listing=listing,
        filter_result=FilterResult(passes=True, laundry_status=LaundryStatus.IN_UNIT),
        score=ListingScore(
            total=total,
            categories=CategoryScores(bright_modern=9, bedroom_fit=8, hosting_space=8),
            confidence=0.9,
            rationale="Strong test listing",
        ),
        outreach_draft="Hello",
    )


def _row_from_headers(headers: list[str], values: dict[str, object]) -> list[object]:
    return [values.get(header, "") for header in headers]


def _listing(source_id: str, rent: int) -> Listing:
    return Listing(
        source="test",
        source_id=source_id,
        url=f"https://streeteasy.com/{source_id}",
        address="100 Sample Street",
        neighborhood="SoHo",
        rent=rent,
        bedrooms=2,
        bathrooms=1.5,
        laundry_status=LaundryStatus.IN_UNIT,
        amenities=["washer_dryer"],
        image_urls=["https://example.com/1.jpg"] * 6,
        commute_minutes=12,
    )


class BudgetLimitedProvider(ListingProvider):
    def __init__(self, listings: list[Listing], fail_on_detail_index: int | None = None) -> None:
        self.listings = listings
        self.fail_on_detail_index = fail_on_detail_index
        self.detail_requests = 0

    def search(self, profile) -> list[Listing]:
        return self.listings

    def fetch_details(self, listing: Listing) -> Listing:
        self.detail_requests += 1
        if self.fail_on_detail_index and self.detail_requests >= self.fail_on_detail_index:
            raise RapidApiRequestBudgetExceeded("RapidAPI request cap reached: test")
        listing.description = "Bright two bedroom with a real living room, dishwasher, and modern finishes."
        return listing

    def stats(self) -> dict[str, int]:
        return {"detail_requests": self.detail_requests}


class FakeSheetsWriter:
    def write(self, ranked_listings, profile, tour_checklist, application_docs, dry_run=False):
        return {"dry_run": dry_run, "candidate_rows": len(ranked_listings)}


class FakeListingCache:
    def get(self, listing: Listing) -> None:
        return None

    def set(self, listing: Listing) -> None:
        return None

    def stats(self) -> dict[str, int]:
        return {"cached_listing_hits": 0, "cached_listing_misses": 0, "cached_listing_count": 0}


class FakeScoreCache:
    def __init__(self, cached: dict | None = None) -> None:
        self.cached = cached
        self.set_calls = 0

    def get(self, key: str) -> dict | None:
        return self.cached

    def set(self, key: str, value: dict) -> None:
        self.set_calls += 1
        self.cached = value


class FakeGeminiClient:
    enabled = True
    model = "fake-gemini"

    def __init__(self, model_score: ListingScore) -> None:
        self.model_score = model_score
        self.calls = 0

    def score_listing(self, listing: Listing, profile, heuristic: ListingScore) -> ListingScore:
        self.calls += 1
        return self.model_score


class FakeErrorBody:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        return None
