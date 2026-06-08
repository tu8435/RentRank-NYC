from __future__ import annotations

import os
from typing import Any

import requests

from apartment_search.models import LaundryStatus, Listing, PreferenceProfile
from apartment_search.providers.base import ListingProvider


class RapidApiRequestBudgetExceeded(RuntimeError):
    """Raised when a run reaches its configured RapidAPI request cap."""


class RapidApiRealtyProvider(ListingProvider):
    """StreetEasy-style provider backed by RapidAPI/RealtyAPI endpoints.

    The endpoint names are configurable because third-party NYC/StreetEasy APIs
    vary by marketplace. Defaults are aimed at RapidAPI's `nyc-real-estate-api`
    host, while keeping the older RealtyAPI-style paths configurable.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        host: str | None = None,
        search_path: str | None = None,
        detail_path: str | None = None,
        per_page: int | None = None,
        max_requests: int | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = api_key or os.getenv("RAPIDAPI_KEY")
        self.base_url = (base_url or os.getenv("RAPIDAPI_REALTY_BASE_URL") or "").rstrip("/")
        self.host = host or os.getenv("RAPIDAPI_REALTY_HOST", "nyc-real-estate-api.p.rapidapi.com")
        self.search_path = search_path or os.getenv("RAPIDAPI_REALTY_SEARCH_PATH", "/search/rent")
        self.detail_path = detail_path or os.getenv("RAPIDAPI_REALTY_DETAIL_PATH") or "/rental_detailsbyid"
        self.per_page = per_page or int(os.getenv("RAPIDAPI_PER_PAGE", "100"))
        self.batch_locations = os.getenv("RAPIDAPI_BATCH_LOCATIONS", "true").lower() != "false"
        self.max_requests = max_requests if max_requests is not None else _optional_int(os.getenv("RAPIDAPI_MAX_REQUESTS", "5"))
        self.timeout_seconds = timeout_seconds
        self.search_requests = 0
        self.detail_requests = 0

        if not self.base_url:
            self.base_url = f"https://{self.host}"

    def search(self, profile: PreferenceProfile) -> list[Listing]:
        if not self.api_key:
            raise RuntimeError("RAPIDAPI_KEY is required to search listings.")

        listings: list[Listing] = []
        locations = [
            *profile.preferred_locations,
            *profile.acceptable_locations,
            *profile.preferred_boroughs,
            *profile.acceptable_boroughs,
        ]
        search_locations = [",".join(locations)] if self.batch_locations else locations
        for location in search_locations:
            params = {
                "location": location,
                "priceRange": f"-{profile.budget.stretch_total_max}",
                "beds": _beds_filter(profile.min_bedrooms),
                "baths": _baths_filter(profile.min_bathrooms),
                "amenities": "washer_dryer,laundry",
                "page": 1,
            }
            payload = self._get(self.search_path, params=params)
            self.search_requests += 1
            for item in _extract_listing_items(payload):
                normalized = self._normalize_listing(item)
                if not normalized.neighborhood and not self.batch_locations:
                    normalized.neighborhood = location
                if normalized.laundry_status == LaundryStatus.UNKNOWN:
                    normalized.laundry_status = LaundryStatus.IN_BUILDING
                    normalized.raw["laundry_status"] = LaundryStatus.IN_BUILDING.value
                    normalized.raw["laundry_status_source"] = "search_filter:amenities=washer_dryer,laundry"
                listings.append(normalized)
        return _dedupe_listings(listings)

    def fetch_details(self, listing: Listing) -> Listing:
        if not self.api_key:
            raise RuntimeError("RAPIDAPI_KEY is required to fetch listing details.")
        if not self.detail_path or listing_has_detail_fields(listing):
            return listing

        params = {
            "buildingid": listing.source_id,
            "id": listing.source_id,
            "url_path": _url_path(listing.url),
        }
        payload = self._get(self.detail_path, params={key: value for key, value in params.items() if value})
        self.detail_requests += 1
        details = self._normalize_listing(payload)
        return _merge_listing(listing, details)

    def stats(self) -> dict[str, int]:
        return {
            "rapidapi_search_requests": self.search_requests,
            "rapidapi_detail_requests": self.detail_requests,
            "rapidapi_total_requests": self.search_requests + self.detail_requests,
            "rapidapi_max_requests": self.max_requests or 0,
        }

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_request_budget()
        headers = {"X-RapidAPI-Key": self.api_key}
        if self.host:
            headers["X-RapidAPI-Host"] = self.host

        response = requests.get(
            f"{self.base_url}/{path.lstrip('/')}",
            headers=headers,
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Expected object response from listing provider, received {type(data).__name__}.")
        return data

    def _ensure_request_budget(self) -> None:
        if self.max_requests is None:
            return
        current = self.search_requests + self.detail_requests
        if current + 1 > self.max_requests:
            raise RapidApiRequestBudgetExceeded(
                f"RapidAPI request cap reached: {current}/{self.max_requests}. "
                "Raise RAPIDAPI_MAX_REQUESTS only after confirming the plan budget."
            )

    def _normalize_listing(self, data: dict[str, Any]) -> Listing:
        node = data.get("node") if isinstance(data.get("node"), dict) else data
        url = _first_text(
            node,
            [
                "streeteasy_url",
                "street_easy_url",
                "streeteasyLink",
                "streetEasyLink",
                "listing_url",
                "sourceUrl",
                "source_url",
                "canonical_url",
                "web_url",
                "external_url",
                "link",
                "url",
            ],
        )
        url_path = _first_text(node, ["streeteasy_path", "url_path", "urlPath", "path"])
        if not url and url_path:
            url = f"https://streeteasy.com{url_path if url_path.startswith('/') else '/' + url_path}"

        amenities = _as_text_list(_first_value(node, ["amenities", "features", "matchedAmenities"]))
        laundry_status = _derive_laundry_status(node, amenities)
        neighborhood = _first_text(node, ["neighborhood", "area", "areaName", "name"])
        borough = _first_text(node, ["borough"]) or _infer_borough(neighborhood)
        listing = Listing(
            source="rapidapi_realty",
            source_id=str(
                _first_value(node, ["id", "buildingid", "building_id", "listing_id", "property_id", "zpid"])
                or url
                or ""
            ),
            url=url or "",
            address=_first_text(node, ["address", "street", "formatted_address"]),
            unit=_first_text(node, ["unit", "apartment", "unit_number"]),
            neighborhood=neighborhood,
            borough=borough,
            rent=_to_int(_first_value(node, ["price", "rent", "monthly_rent"])),
            bedrooms=_to_float(_first_value(node, ["bedrooms", "beds", "bedroom_count", "bedroomCount"])),
            bathrooms=_bathroom_count(node),
            square_feet=_to_int(_first_value(node, ["squareFeet", "square_feet", "sqft", "livingAreaSize"])),
            description=_first_text(node, ["description", "body", "listing_description"]),
            amenities=amenities,
            laundry_status=laundry_status,
            image_urls=_extract_images(node),
            listed_at=_first_text(node, ["listedAt", "listed_at", "created_at"]),
            days_on_market=_to_int(_first_value(node, ["daysOnMarket", "days_on_market"])),
            no_fee=_to_bool(_first_value(node, ["noFee", "no_fee"])),
            agents=_as_dict_list(_first_value(node, ["agents", "agent", "broker"])),
            open_house_dates=_as_text_list(_first_value(node, ["openHouseDates", "open_houses", "open_house_dates"])),
            latitude=_to_float(_first_value(node, ["latitude", "lat"])),
            longitude=_to_float(_first_value(node, ["longitude", "lng", "lon"])),
            raw=data,
        )
        listing.raw["laundry_status"] = laundry_status.value
        listing.raw["laundry_status_source"] = (
            listing.raw.get("laundry_status_source")
            or ("structured_api_fields" if laundry_status != LaundryStatus.UNKNOWN else "not_provided")
        )
        return listing


def _extract_listing_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [
        payload.get("listings"),
        payload.get("results"),
        payload.get("search_results", {}).get("listings")
        if isinstance(payload.get("search_results"), dict)
        else None,
        payload.get("data", {}).get("listings") if isinstance(payload.get("data"), dict) else None,
        payload.get("data") if isinstance(payload.get("data"), list) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    if payload.get("id") or payload.get("url"):
        return [payload]
    return []


def _dedupe_listings(listings: list[Listing]) -> list[Listing]:
    seen: set[str] = set()
    deduped: list[Listing] = []
    for listing in listings:
        key = listing.url or listing.source_id
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(listing)
    return deduped


def listing_has_detail_fields(listing: Listing) -> bool:
    return bool(listing.description and listing.image_urls and listing.amenities)


def _merge_listing(base: Listing, details: Listing) -> Listing:
    for field_name in base.__dataclass_fields__:
        current = getattr(base, field_name)
        incoming = getattr(details, field_name)
        if field_name == "raw":
            current.update({"details": details.raw})
            continue
        if field_name == "laundry_status":
            current_status = _coerce_laundry_status(current)
            incoming_status = _coerce_laundry_status(incoming)
            if incoming_status == LaundryStatus.IN_UNIT or current_status == LaundryStatus.UNKNOWN:
                setattr(base, field_name, incoming_status)
            continue
        if current in (None, "", [], {}):
            setattr(base, field_name, incoming)
        elif isinstance(current, list) and incoming:
            merged = [*current]
            for item in incoming:
                if item not in merged:
                    merged.append(item)
            setattr(base, field_name, merged)
    return base


def _first_value(data: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _optional_int(value: str | None) -> int | None:
    if value in (None, "", "none", "None", "0"):
        return None
    return int(value)


def _beds_filter(min_bedrooms: float) -> str:
    if min_bedrooms >= 4:
        return "4_plus"
    if min_bedrooms >= 3:
        return "3_plus"
    if min_bedrooms >= 2:
        return "2_plus"
    if min_bedrooms >= 1:
        return "1_plus"
    return "any"


def _derive_laundry_status(data: dict[str, Any], amenities: list[str]) -> LaundryStatus:
    explicit = _first_value(
        data,
        [
            "laundry_status",
            "laundryStatus",
            "laundryType",
            "laundry_type",
        ],
    )
    explicit_status = _laundry_status_from_token(explicit)
    if explicit_status != LaundryStatus.UNKNOWN:
        return explicit_status

    boolean_fields = {
        LaundryStatus.IN_UNIT: [
            "hasWasherDryer",
            "washerDryer",
            "washer_dryer",
            "inUnitLaundry",
            "in_unit_laundry",
        ],
        LaundryStatus.IN_BUILDING: [
            "hasLaundry",
            "buildingLaundry",
            "inBuildingLaundry",
            "laundry",
        ],
    }
    for status, field_names in boolean_fields.items():
        for field_name in field_names:
            value = data.get(field_name)
            if value is True or str(value).strip().lower() in {"true", "yes", "1"}:
                return status

    for amenity in amenities:
        status = _laundry_status_from_token(amenity)
        if status != LaundryStatus.UNKNOWN:
            return status
    return LaundryStatus.UNKNOWN


def _laundry_status_from_token(value: Any) -> LaundryStatus:
    token = _normalize_token(value)
    if not token:
        return LaundryStatus.UNKNOWN
    if token in {"washer_dryer", "washerdryer", "in_unit_laundry", "in_unit_washer_dryer", "washer_dryer_in_unit"}:
        return LaundryStatus.IN_UNIT
    if token in {"laundry", "laundry_room", "in_building_laundry", "laundry_in_building", "building_laundry", "on_site_laundry"}:
        return LaundryStatus.IN_BUILDING
    if token in {"nearby_laundromat", "laundromat_nearby", "laundry_nearby"}:
        return LaundryStatus.NEARBY
    if token in {"no_laundry", "laundry_not_available", "none"}:
        return LaundryStatus.NONE
    return LaundryStatus.UNKNOWN


def _coerce_laundry_status(value: Any) -> LaundryStatus:
    if isinstance(value, LaundryStatus):
        return value
    try:
        return LaundryStatus(str(value))
    except ValueError:
        return LaundryStatus.UNKNOWN


def _normalize_token(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip().lower().replace("-", "_").replace("/", "_").replace(" ", "_")


def _baths_filter(min_bathrooms: float) -> str:
    if min_bathrooms >= 3:
        return "3_plus"
    if min_bathrooms >= 2:
        return "2_plus"
    if min_bathrooms >= 1.5:
        return "1point5_plus"
    if min_bathrooms >= 1:
        return "1_plus"
    return "any"


def _first_text(data: dict[str, Any], keys: list[str]) -> str | None:
    value = _first_value(data, keys)
    if value is None:
        return None
    return str(value).strip() or None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace("$", "").replace(",", "")))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1", "no fee", "nofee"}


def _bathroom_count(data: dict[str, Any]) -> float | None:
    direct = _to_float(_first_value(data, ["bathrooms", "baths", "bathroom_count"]))
    if direct is not None:
        return direct
    full = _to_float(_first_value(data, ["full_bathrooms", "fullBathrooms", "fullBathroomCount"])) or 0
    half = _to_float(_first_value(data, ["half_bathrooms", "halfBathrooms", "half_baths", "halfBathroomCount"])) or 0
    return full + (half * 0.5) if full or half else None


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            if item in (None, ""):
                continue
            if isinstance(item, dict):
                text = _first_text(item, ["name", "label", "displayName", "description", "value"])
                if text:
                    values.append(text)
            else:
                values.append(str(item))
        return values
    return [str(value)]


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _extract_images(data: dict[str, Any]) -> list[str]:
    image_values = [
        data.get("images"),
        data.get("photos"),
        data.get("image_urls"),
        data.get("photo_url"),
        data.get("lead_photo"),
        data.get("imageUrl"),
        data.get("leadMedia"),
    ]
    urls: list[str] = []
    for value in image_values:
        for item in _as_image_urls(value):
            if item not in urls:
                urls.append(item)
    return urls


def _as_image_urls(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.startswith("http") else []
    if isinstance(value, dict):
        url = _first_text(value, ["url", "imageUrl", "image_url", "photo_url", "src", "href"])
        urls = [url] if url and url.startswith("http") else []
        key = _first_text(value, ["key", "photoKey", "photo_key"])
        if key:
            urls.append(_street_easy_photo_url(key))
        for nested_key in ["photo", "lead_photo"]:
            nested = value.get(nested_key)
            if isinstance(nested, dict):
                urls.extend(_as_image_urls(nested))
        return urls
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_as_image_urls(item))
        return urls
    return []


def _street_easy_photo_url(key: str) -> str:
    return f"https://photos.zillowstatic.com/fp/{key}-p_e.webp"


def _infer_borough(neighborhood: str | None) -> str | None:
    normalized = (neighborhood or "").strip().lower()
    if not normalized:
        return None
    if normalized in MANHATTAN_NEIGHBORHOODS:
        return "Manhattan"
    if normalized in BROOKLYN_NEIGHBORHOODS:
        return "Brooklyn"
    return None


MANHATTAN_NEIGHBORHOODS = {
    "soho",
    "noho",
    "lower east side",
    "east village",
    "west village",
    "greenwich village",
    "tribeca",
    "nolita",
    "chinatown",
    "two bridges",
    "chelsea",
    "flatiron",
    "gramercy park",
    "kips bay",
    "financial district",
    "civic center",
    "little italy",
}


BROOKLYN_NEIGHBORHOODS = {
    "williamsburg",
    "greenpoint",
    "bushwick",
    "downtown brooklyn",
    "fort greene",
    "clinton hill",
    "boerum hill",
    "cobble hill",
    "bedford-stuyvesant",
    "bed-stuy",
    "crown heights",
    "park slope",
    "prospect heights",
    "gowanus",
}


def _url_path(url: str | None) -> str | None:
    if not url or "streeteasy.com" not in url:
        return None
    return "/" + url.split("streeteasy.com/", 1)[1].lstrip("/")
