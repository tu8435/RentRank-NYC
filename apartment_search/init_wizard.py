from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apartment_search.preferences import EXAMPLE_PROFILE_PATH
from apartment_search.workspace import EXAMPLE_WORKSPACE_PATH


InputFn = Callable[[str], str]
PrintFn = Callable[[str], None]

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
}


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
    profile["renter_names"] = _ask_list("Renter names", profile["renter_names"], input_fn)
    profile["renter_emails"] = _ask_list("Renter emails", profile["renter_emails"], input_fn)
    profile["move_in"] = _ask("Move-in date or range", profile["move_in"], input_fn)
    profile["lease_months"] = _ask_int("Lease length in months", profile["lease_months"], input_fn)

    print_fn("\nBudget")
    budget = profile["budget"]
    budget["people"] = _ask_int("Number of renters", budget["people"], input_fn)
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

    print_fn("\nNeighborhoods")
    profile["preferred_locations"] = _ask_list("Preferred neighborhoods", profile["preferred_locations"], input_fn)
    profile["acceptable_locations"] = _ask_list("Acceptable neighborhoods", profile["acceptable_locations"], input_fn)

    print_fn("\nQualitative scoring")
    preset = _ask_choice(
        "Qualitative preset",
        ["balanced", "bright-modern", "commute-first", "budget-first"],
        "balanced",
        input_fn,
    )
    profile["qualitative"]["weights"].update(QUALITATIVE_PRESETS[preset])
    profile["qualitative"]["hard_rejects"] = _ask_list(
        "Hard qualitative rejects", profile["qualitative"]["hard_rejects"], input_fn
    )
    profile["qualitative"]["boosts"] = _ask_list("Qualitative boosts", profile["qualitative"]["boosts"], input_fn)
    profile["qualitative"]["penalties"] = _ask_list(
        "Qualitative penalties", profile["qualitative"]["penalties"], input_fn
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


def _ask_int(prompt: str, default: int, input_fn: InputFn) -> int:
    while True:
        response = _ask(prompt, default, input_fn)
        try:
            return int(response)
        except ValueError:
            print("Please enter a whole number.")


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
