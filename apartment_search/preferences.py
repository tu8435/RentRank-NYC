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


PRIVATE_PROFILE_PATH = Path("secrets/config/preferences.json")
PRIVATE_PROFILE_DIR = Path("secrets/config/profiles")
EXAMPLE_PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "preferences.example.json"


def default_profile() -> PreferenceProfile:
    return load_profile()


def load_profile(path: str | Path | None = None) -> PreferenceProfile:
    base = _load_profile_data(EXAMPLE_PROFILE_PATH)
    if path is None:
        path = _default_profile_path()
    data = _load_profile_data(path)
    merged = data if _same_path(path, EXAMPLE_PROFILE_PATH) else _deep_merge(base, data)
    merged = _with_env_overrides(merged)
    return profile_from_dict(merged)


def write_default_profile(path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_load_profile_data(EXAMPLE_PROFILE_PATH), indent=2) + "\n", encoding="utf-8")


def profile_path_for_name(name: str) -> Path:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Profile name cannot be blank.")
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in normalized):
        raise ValueError("Profile names may only contain letters, numbers, dots, underscores, and hyphens.")
    return PRIVATE_PROFILE_DIR / f"{normalized}.json"


def _default_profile_path() -> Path:
    configured = _env_value("RENTRANK_PROFILE_PATH") or _env_value("APARTMENT_PROFILE_PATH")
    if configured:
        return Path(configured)
    if PRIVATE_PROFILE_PATH.exists():
        return PRIVATE_PROFILE_PATH
    return EXAMPLE_PROFILE_PATH


def _load_profile_data(path: str | Path) -> dict[str, Any]:
    profile_path = Path(path)
    with profile_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Profile at {profile_path} must be a JSON object.")
    return data


def _same_path(left: str | Path, right: str | Path) -> bool:
    return Path(left).resolve() == Path(right).resolve()


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
        preferred_boroughs=list(data.get("preferred_boroughs", [])),
        acceptable_boroughs=list(data.get("acceptable_boroughs", [])),
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
