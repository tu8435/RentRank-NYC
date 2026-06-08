from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apartment_search.preferences import EXAMPLE_PROFILE_PATH
from apartment_search.workspace import EXAMPLE_WORKSPACE_PATH


InputFn = Callable[[str], str]
PrintFn = Callable[[str], None]
MAX_RENTERS = 8

QUALITATIVE_PRESETS = {
    "balanced": {},
    "bright-modern": {
        "bright_modern": 32,
        "bedroom_fit": 18,
        "hosting_space": 16,
        "commute_transit": 14,
        "centrality": 10,
    },
    "commute-first": {
        "commute_transit": 28,
        "centrality": 15,
        "bright_modern": 18,
        "bedroom_fit": 16,
        "hosting_space": 12,
    },
    "budget-first": {
        "budget": 18,
        "commute_transit": 18,
        "bright_modern": 18,
        "bedroom_fit": 16,
        "hosting_space": 12,
    },
    "hosting-wfh": {
        "hosting_space": 24,
        "bedroom_fit": 22,
        "bright_modern": 18,
        "commute_transit": 14,
        "centrality": 10,
    },
    "amenities-first": {
        "laundry": 14,
        "kitchen": 10,
        "bathrooms": 8,
        "bright_modern": 22,
        "commute_transit": 14,
    },
    "space-first": {
        "bedroom_fit": 26,
        "hosting_space": 22,
        "bright_modern": 18,
        "commute_transit": 12,
    },
}

BOROUGH_OPTIONS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]

HARD_REJECT_OPTIONS = [
    "obviously_dark",
    "tiny_or_unfair_bedrooms",
    "no_real_common_space",
    "nearby_laundromat_only",
    "sketchy_description",
    "basement_or_cellar_unit",
    "no_bedroom_windows",
    "unsafe_or_poorly_maintained_building",
    "extreme_commute",
    "unworkable_walkup",
]

BOOST_OPTIONS = [
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
    "excellent_natural_light",
    "renovated_kitchen",
    "large_equal_bedrooms",
    "dedicated_desk_space",
    "great_subway_access",
    "quiet_block",
    "strong_storage",
    "pet_friendly",
]

PENALTY_OPTIONS = [
    "stock_or_rendered_photos",
    "missing_bedroom_photos",
    "missing_living_room_photos",
    "too_far_east_west_from_train",
    "four_plus_floor_walkup",
    "pest_history",
    "hpd_violations",
    "insecure_entry",
    "vague_description",
    "low_natural_light",
    "awkward_layout",
    "dated_finishes",
    "small_common_area",
    "no_dishwasher",
    "long_subway_walk",
    "high_broker_fee",
    "poor_listing_quality",
]


def run_init_wizard(
    profile_path: str | Path = "secrets/config/preferences.json",
    workspace_path: str | Path = "secrets/config/workspace.json",
    *,
    force: bool = False,
    input_fn: InputFn = input,
    print_fn: PrintFn = print,
) -> dict[str, str]:
    print_fn("RentRank NYC setup wizard")
    print_fn("Press Enter to accept a default. Private files are written under secrets/config by default.")

    profile = _load_json(EXAMPLE_PROFILE_PATH)
    workspace = _load_json(EXAMPLE_WORKSPACE_PATH)

    _prompt_profile(profile, input_fn, print_fn)
    _prompt_workspace(workspace, input_fn, print_fn)

    profile_output = Path(profile_path)
    workspace_output = Path(workspace_path)
    _write_json(profile_output, profile, force, input_fn, print_fn)
    _write_json(workspace_output, workspace, force, input_fn, print_fn)

    print_fn(f"Wrote private preferences to {profile_output}")
    print_fn(f"Wrote private workspace config to {workspace_output}")
    return {"profile_path": str(profile_output), "workspace_path": str(workspace_output)}


def _prompt_profile(profile: dict[str, Any], input_fn: InputFn, print_fn: PrintFn) -> None:
    print_fn("\nProfile")
    budget = profile["budget"]
    renter_count = _ask_int_range(
        "Number of renters",
        int(budget.get("people") or len(profile["renter_names"]) or 1),
        1,
        MAX_RENTERS,
        input_fn,
    )
    budget["people"] = renter_count
    profile["renter_names"] = _ask_people("Renter name", renter_count, profile["renter_names"], "Renter", input_fn)
    profile["renter_emails"] = _ask_people("Renter email", renter_count, profile["renter_emails"], "", input_fn)
    profile["move_in"] = _ask("Move-in date or range", profile["move_in"], input_fn)
    profile["lease_months"] = _ask_int("Lease length in months", profile["lease_months"], input_fn)

    print_fn("\nBudget")
    budget["target_share_min"] = _ask_int("Target minimum share per person", budget["target_share_min"], input_fn)
    budget["target_share_max"] = _ask_int("Target maximum share per person", budget["target_share_max"], input_fn)
    budget["stretch_share_max"] = _ask_int("Stretch maximum share per person", budget["stretch_share_max"], input_fn)
    budget["outreach_max_rent"] = _ask_int("Max total rent to mention in outreach", budget["outreach_max_rent"], input_fn)

    print_fn("\nHard filters")
    profile["min_bedrooms"] = _ask_int("Minimum bedrooms", profile["min_bedrooms"], input_fn)
    profile["min_bathrooms"] = _ask_float("Minimum bathrooms", profile["min_bathrooms"], input_fn)
    profile["preferred_bathrooms"] = _ask_float("Preferred bathrooms", profile["preferred_bathrooms"], input_fn)
    profile["acceptable_laundry"] = _ask_list(
        "Acceptable laundry statuses (in_unit, in_building, nearby_laundromat, none, unknown)",
        profile["acceptable_laundry"],
        input_fn,
    )

    print_fn("\nCommute")
    commute = profile["commute"]
    commute["destination_address"] = _ask("Commute destination address", commute["destination_address"], input_fn)
    commute["target_minutes"] = _ask_int("Ideal commute minutes", commute["target_minutes"], input_fn)
    commute["max_minutes"] = _ask_int("Maximum acceptable commute minutes", commute["max_minutes"], input_fn)
    commute["max_subway_walk_minutes"] = _ask_int(
        "Maximum walk to subway minutes", commute["max_subway_walk_minutes"], input_fn
    )
    commute["prefer_few_transfers"] = _ask_bool("Prefer fewer transfers", commute["prefer_few_transfers"], input_fn)

    print_fn("\nBoroughs")
    profile["preferred_boroughs"] = _ask_numbered_multi(
        "Preferred boroughs",
        BOROUGH_OPTIONS,
        profile.get("preferred_boroughs", ["Manhattan"]),
        input_fn,
        print_fn,
    )
    profile["acceptable_boroughs"] = _ask_numbered_multi(
        "Acceptable boroughs",
        BOROUGH_OPTIONS,
        profile.get("acceptable_boroughs", ["Brooklyn"]),
        input_fn,
        print_fn,
    )
    profile["preferred_locations"] = []
    profile["acceptable_locations"] = []

    print_fn("\nQualitative scoring")
    preset = _ask_numbered_choice(
        "Qualitative preset",
        list(QUALITATIVE_PRESETS),
        "balanced",
        input_fn,
        print_fn,
    )
    profile["qualitative"]["weights"].update(QUALITATIVE_PRESETS[preset])
    profile["qualitative"]["hard_rejects"] = _ask_numbered_multi(
        "Hard qualitative rejects", HARD_REJECT_OPTIONS, profile["qualitative"]["hard_rejects"], input_fn, print_fn
    )
    profile["qualitative"]["boosts"] = _ask_numbered_multi(
        "Qualitative boosts", BOOST_OPTIONS, profile["qualitative"]["boosts"], input_fn, print_fn
    )
    profile["qualitative"]["penalties"] = _ask_numbered_multi(
        "Qualitative penalties", PENALTY_OPTIONS, profile["qualitative"]["penalties"], input_fn, print_fn
    )


def _prompt_workspace(workspace: dict[str, Any], input_fn: InputFn, print_fn: PrintFn) -> None:
    print_fn("\nGoogle Sheet / Drive")
    workspace["google_sheets_spreadsheet_id"] = _ask(
        "Existing Google Sheet ID (blank to create one)", workspace["google_sheets_spreadsheet_id"], input_fn
    )
    workspace["google_drive_folder_id"] = _ask(
        "Google Drive folder ID (blank if not using a folder)", workspace["google_drive_folder_id"], input_fn
    )
    workspace["google_drive_folder_link"] = _ask(
        "Google Drive folder link (optional alternative to folder ID)",
        workspace["google_drive_folder_link"],
        input_fn,
    )
    workspace["google_sheets_title"] = _ask("Google Sheet title", workspace["google_sheets_title"], input_fn)


def _ask(prompt: str, default: Any, input_fn: InputFn) -> str:
    default_text = "" if default is None else str(default)
    response = input_fn(f"{prompt} [{default_text}]: ").strip()
    return response or default_text


def _ask_list(prompt: str, default: list[Any], input_fn: InputFn) -> list[str]:
    response = _ask(prompt, ", ".join(str(item) for item in default), input_fn)
    return [item.strip() for item in response.split(",") if item.strip()]


def _ask_numbered_choice(
    prompt: str,
    options: list[str],
    default: str,
    input_fn: InputFn,
    print_fn: PrintFn,
) -> str:
    _print_numbered_options(prompt, options, print_fn)
    default_index = _indexes_for_values(options, [default])[0]
    while True:
        response = _ask(f"{prompt} number", default_index, input_fn)
        try:
            index = int(response)
        except ValueError:
            print_fn("Please enter one number from the list.")
            continue
        if 1 <= index <= len(options):
            return options[index - 1]
        print_fn(f"Please enter a number between 1 and {len(options)}.")


def _ask_numbered_multi(
    prompt: str,
    options: list[str],
    defaults: list[str],
    input_fn: InputFn,
    print_fn: PrintFn,
) -> list[str]:
    _print_numbered_options(prompt, options, print_fn)
    default_indexes = _indexes_for_values(options, defaults)
    default_text = ", ".join(str(index) for index in default_indexes)
    while True:
        response = _ask(f"{prompt} numbers, comma-separated", default_text, input_fn)
        try:
            indexes = [int(part.strip()) for part in response.split(",") if part.strip()]
        except ValueError:
            print_fn("Please enter comma-separated numbers from the list.")
            continue
        if all(1 <= index <= len(options) for index in indexes):
            return [options[index - 1] for index in dict.fromkeys(indexes)]
        print_fn(f"Please enter numbers between 1 and {len(options)}.")


def _print_numbered_options(prompt: str, options: list[str], print_fn: PrintFn) -> None:
    print_fn(f"{prompt}:")
    for index, option in enumerate(options, start=1):
        print_fn(f"  {index}. {option}")


def _indexes_for_values(options: list[str], values: list[str]) -> list[int]:
    indexes = [options.index(value) + 1 for value in values if value in options]
    return indexes or [1]


def _ask_people(prompt: str, count: int, defaults: list[Any], fallback_prefix: str, input_fn: InputFn) -> list[str]:
    values: list[str] = []
    for index in range(count):
        fallback = f"{fallback_prefix} {index + 1}".strip()
        default = str(defaults[index]).strip() if index < len(defaults) else fallback
        values.append(_ask(f"{prompt} {index + 1}", default, input_fn))
    return values


def _ask_int(prompt: str, default: int, input_fn: InputFn) -> int:
    while True:
        response = _ask(prompt, default, input_fn)
        try:
            return int(response)
        except ValueError:
            print("Please enter a whole number.")


def _ask_int_range(prompt: str, default: int, minimum: int, maximum: int, input_fn: InputFn) -> int:
    while True:
        value = _ask_int(prompt, default, input_fn)
        if minimum <= value <= maximum:
            return value
        print(f"Please enter a number between {minimum} and {maximum}.")


def _ask_float(prompt: str, default: float, input_fn: InputFn) -> float:
    while True:
        response = _ask(prompt, default, input_fn)
        try:
            return float(response)
        except ValueError:
            print("Please enter a number.")


def _ask_bool(prompt: str, default: bool, input_fn: InputFn) -> bool:
    default_text = "yes" if default else "no"
    while True:
        response = _ask(f"{prompt} (yes/no)", default_text, input_fn).lower()
        if response in {"y", "yes", "true", "1"}:
            return True
        if response in {"n", "no", "false", "0"}:
            return False
        print("Please enter yes or no.")


def _ask_choice(prompt: str, choices: list[str], default: str, input_fn: InputFn) -> str:
    while True:
        response = _ask(f"{prompt} ({', '.join(choices)})", default, input_fn)
        if response in choices:
            return response
        print(f"Please choose one of: {', '.join(choices)}")


def _write_json(path: Path, data: dict[str, Any], force: bool, input_fn: InputFn, print_fn: PrintFn) -> None:
    if path.exists() and not force:
        overwrite = _ask_bool(f"{path} exists. Overwrite it", False, input_fn)
        if not overwrite:
            print_fn(f"Skipped {path}")
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return data
