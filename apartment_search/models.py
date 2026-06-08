from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LaundryStatus(StrEnum):
    IN_UNIT = "in_unit"
    IN_BUILDING = "in_building"
    NEARBY = "nearby_laundromat"
    NONE = "none"
    UNKNOWN = "unknown"


class ReviewStatus(StrEnum):
    NEW = "New"
    INTERESTED = "Interested"
    MAYBE = "Maybe"
    TOUR_REQUESTED = "Tour Requested"
    REJECTED = "Rejected"
    APPLIED = "Applied"


@dataclass(slots=True)
class BudgetProfile:
    target_share_min: int
    target_share_max: int
    stretch_share_max: int
    people: int = 2
    outreach_max_rent: int = 5000

    @property
    def target_total_max(self) -> int:
        return self.target_share_max * self.people

    @property
    def stretch_total_max(self) -> int:
        return self.stretch_share_max * self.people

    def rent_per_person(self, rent: int | float | None) -> float | None:
        if rent is None:
            return None
        return round(float(rent) / self.people, 2)


@dataclass(slots=True)
class CommutePreference:
    destination_address: str
    target_minutes: int
    max_minutes: int
    max_subway_walk_minutes: int
    prefer_few_transfers: bool = True


@dataclass(slots=True)
class QualitativeProfile:
    weights: dict[str, float]
    hard_rejects: list[str]
    boosts: list[str]
    penalties: list[str]
    model_focus: list[str]


@dataclass(slots=True)
class PreferenceProfile:
    renter_names: list[str]
    renter_emails: list[str]
    move_in: str
    lease_months: int
    budget: BudgetProfile
    commute: CommutePreference
    preferred_boroughs: list[str]
    acceptable_boroughs: list[str]
    preferred_locations: list[str]
    acceptable_locations: list[str]
    min_bedrooms: int
    min_bathrooms: float
    preferred_bathrooms: float
    acceptable_laundry: set[LaundryStatus]
    qualitative: QualitativeProfile


@dataclass(slots=True)
class Listing:
    source: str
    source_id: str
    url: str
    address: str | None = None
    unit: str | None = None
    neighborhood: str | None = None
    borough: str | None = None
    rent: int | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    square_feet: int | None = None
    description: str | None = None
    amenities: list[str] = field(default_factory=list)
    laundry_status: LaundryStatus = LaundryStatus.UNKNOWN
    image_urls: list[str] = field(default_factory=list)
    listed_at: str | None = None
    days_on_market: int | None = None
    no_fee: bool | None = None
    agents: list[dict[str, Any]] = field(default_factory=list)
    open_house_dates: list[str] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    commute_minutes: int | None = None
    commute_to_work_minutes: int | None = None
    commute_home_minutes: int | None = None
    subway_walk_minutes: int | None = None
    subway_transfers: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        parts = [self.address, self.unit, self.neighborhood]
        return " ".join(str(part) for part in parts if part) or self.url or self.source_id


@dataclass(slots=True)
class FilterResult:
    passes: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    laundry_status: LaundryStatus = LaundryStatus.UNKNOWN


@dataclass(slots=True)
class CategoryScores:
    budget: float = 0
    commute_transit: float = 0
    centrality: float = 0
    laundry: float = 0
    bright_modern: float = 0
    bedroom_fit: float = 0
    hosting_space: float = 0
    kitchen: float = 0
    bathrooms: float = 0
    outdoor_space: float = 0
    diligence: float = 0
    description_quality: float = 0

    def as_dict(self) -> dict[str, float]:
        return {
            "budget": self.budget,
            "commute_transit": self.commute_transit,
            "centrality": self.centrality,
            "laundry": self.laundry,
            "bright_modern": self.bright_modern,
            "bedroom_fit": self.bedroom_fit,
            "hosting_space": self.hosting_space,
            "kitchen": self.kitchen,
            "bathrooms": self.bathrooms,
            "outdoor_space": self.outdoor_space,
            "diligence": self.diligence,
            "description_quality": self.description_quality,
        }


@dataclass(slots=True)
class ListingScore:
    total: float
    categories: CategoryScores
    confidence: float
    rationale: str
    red_flags: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    model_used: str = "heuristic"


@dataclass(slots=True)
class RankedListing:
    listing: Listing
    filter_result: FilterResult
    score: ListingScore
    outreach_draft: str
