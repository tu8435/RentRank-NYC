from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from apartment_search.models import (
    BudgetProfile,
    CommutePreference,
    LaundryStatus,
    PreferenceProfile,
    QualitativeProfile,
)


DEFAULT_PROFILE_DATA: dict[str, Any] = {
    "renter_names": ["Renter One", "Renter Two"],
    "renter_emails": ["renter.one@example.com", "renter.two@example.com"],
    "move_in": "July 1 or first week of July",
    "lease_months": 12,
    "budget": {
        "target_share_min": 2000,
        "target_share_max": 2500,
        "stretch_share_max": 2700,
        "people": 2,
        "outreach_max_rent": 5000,
    },
    "commute": {
        "destination_address": "Workplace Address, New York, NY",
        "target_minutes": 20,
        "max_minutes": 30,
        "max_subway_walk_minutes": 7,
        "prefer_few_transfers": True,
    },
    "preferred_locations": [
        "SoHo",
        "NoHo",
        "Lower East Side",
        "East Village",
        "West Village",
        "Greenwich Village",
        "Tribeca",
        "Nolita",
        "Chinatown",
    ],
    "acceptable_locations": [
        "Williamsburg",
        "Greenpoint",
        "Bushwick",
        "Downtown Brooklyn",
        "Fort Greene",
        "Clinton Hill",
        "Boerum Hill",
        "Cobble Hill",
    ],
    "min_bedrooms": 2,
    "min_bathrooms": 1,
    "preferred_bathrooms": 1.5,
    "acceptable_laundry": ["in_unit", "in_building"],
    "qualitative": {
        "weights": {
            "bright_modern": 25,
            "bedroom_fit": 18,
            "hosting_space": 15,
            "commute_transit": 15,
            "centrality": 10,
            "laundry": 7,
            "budget": 5,
            "diligence": 5,
            "kitchen": 4,
            "bathrooms": 3,
            "outdoor_space": 3,
            "description_quality": 5,
        },
        "hard_rejects": [
            "obviously_dark",
            "tiny_or_unfair_bedrooms",
            "no_real_common_space",
            "nearby_laundromat_only",
            "sketchy_description",
        ],
        "boosts": [
            "in_unit_laundry",
            "two_bathrooms",
            "dishwasher",
            "counter_space",
            "private_outdoor_space",
            "shared_roof_or_yard",
            "elevator",
            "doorman_or_package_security",
            "downtown_manhattan_core",
            "central_for_manhattan_and_brooklyn_friends",
        ],
        "penalties": [
            "stock_or_rendered_photos",
            "missing_bedroom_photos",
            "missing_living_room_photos",
            "too_far_east_west_from_train",
            "four_plus_floor_walkup",
            "pest_history",
            "hpd_violations",
            "insecure_entry",
            "vague_description",
        ],
        "model_focus": [
            "window size and likely natural light",
            "modernity and condition of finishes",
            "room scale without over-trusting wide-angle photos",
            "whether both bedrooms can fit a bed and desk",
            "whether the living room supports hosting",
            "visual red flags like grime, damage, odd layouts, or poor maintenance",
            "photo completeness and whether photos appear to show the actual unit",
        ],
    },
}


def default_profile() -> PreferenceProfile:
    return profile_from_dict(_with_env_overrides(DEFAULT_PROFILE_DATA))


def load_profile(path: str | Path | None = None) -> PreferenceProfile:
    if path is None:
        return default_profile()
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    merged = _deep_merge(DEFAULT_PROFILE_DATA, data)
    merged = _with_env_overrides(merged)
    return profile_from_dict(merged)


def write_default_profile(path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(DEFAULT_PROFILE_DATA, indent=2), encoding="utf-8")


def profile_from_dict(data: dict[str, Any]) -> PreferenceProfile:
    budget = BudgetProfile(**data["budget"])
    commute = CommutePreference(**data["commute"])
    qualitative_data = data["qualitative"]
    qualitative = QualitativeProfile(
        weights={key: float(value) for key, value in qualitative_data["weights"].items()},
        hard_rejects=list(qualitative_data["hard_rejects"]),
        boosts=list(qualitative_data["boosts"]),
        penalties=list(qualitative_data["penalties"]),
        model_focus=list(qualitative_data["model_focus"]),
    )
    return PreferenceProfile(
        renter_names=list(data["renter_names"]),
        renter_emails=list(data["renter_emails"]),
        move_in=str(data["move_in"]),
        lease_months=int(data["lease_months"]),
        budget=budget,
        commute=commute,
        preferred_locations=list(data["preferred_locations"]),
        acceptable_locations=list(data["acceptable_locations"]),
        min_bedrooms=int(data["min_bedrooms"]),
        min_bathrooms=float(data["min_bathrooms"]),
        preferred_bathrooms=float(data["preferred_bathrooms"]),
        acceptable_laundry={LaundryStatus(value) for value in data["acceptable_laundry"]},
        qualitative=qualitative,
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _with_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge({}, data)

    if renter_names := _split_env_list("APARTMENT_RENTER_NAMES"):
        merged["renter_names"] = renter_names
    if renter_emails := _split_env_list("APARTMENT_RENTER_EMAILS"):
        merged["renter_emails"] = renter_emails
    if move_in := _env_value("APARTMENT_MOVE_IN"):
        merged["move_in"] = move_in
    if commute_destination := _env_value("APARTMENT_COMMUTE_DESTINATION"):
        merged["commute"]["destination_address"] = commute_destination

    return merged


def _split_env_list(name: str) -> list[str]:
    raw = _env_value(name)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _env_value(name: str) -> str:
    return (os.getenv(name) or "").strip()
