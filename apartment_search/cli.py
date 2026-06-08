from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from apartment_search.commute import CommuteEstimator
from apartment_search.init_wizard import run_init_wizard
from apartment_search.pipeline import build_pipeline
from apartment_search.preferences import write_default_profile
from apartment_search.request_budget import estimate_requests
from apartment_search.scoring import GoogleGeminiScoringClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NYC apartment search automation pipeline.")
    parser.add_argument("command", nargs="?", choices=["init"], help="Run an interactive setup command.")
    parser.add_argument("--profile", help="Path to a JSON preference profile file.")
    parser.add_argument("--workspace", help="Path to a JSON workspace config file.")
    parser.add_argument("--env-file", default=".env", help="Path to a dotenv-style environment file.")
    parser.add_argument("--seed-listings", help="Path to local listing JSON for dry runs or calibration.")
    parser.add_argument("--folder-link", help="Legacy path to a Google Drive folder link file.")
    parser.add_argument("--dry-run", action="store_true", help="Build output without writing to Google Sheets.")
    parser.add_argument("--use-gemini", action="store_true", help="Use Gemini scoring through Google's Gemini API when GEMINI_API_KEY is set.")
    parser.add_argument("--hpd-lookup", action="store_true", help="Check NYC Open Data for open HPD violations.")
    parser.add_argument("--limit", type=int, help="Maximum number of listings to process.")
    parser.add_argument("--listing-cache", default=".cache/apartment_search/listings.json", help="Path to the persistent seen-listing cache.")
    parser.add_argument("--rapidapi-max-requests", type=int, help="Maximum RapidAPI requests allowed for this run.")
    parser.add_argument("--estimate-requests", type=int, help="Estimate request usage for this many listings and exit.")
    parser.add_argument("--cache-hit-rate", type=float, default=0.0, help="Expected seen-listing cache hit rate for request estimates, from 0 to 1.")
    parser.add_argument("--detail-requests-needed", action="store_true", help="Estimate as if each unseen listing needs a RapidAPI detail request.")
    parser.add_argument("--maps-requests", action="store_true", help="Include Google Maps requests in the estimate.")
    parser.add_argument("--check-google-apis", action="store_true", help="Run sanitized Gemini and Maps credential checks, then exit.")
    parser.add_argument("--init-profile", help="Write the default preference profile JSON to this path and exit.")
    parser.add_argument("--profile-output", default="secrets/config/preferences.json", help="Path written by `init` for private preferences.")
    parser.add_argument("--workspace-output", default="secrets/config/workspace.json", help="Path written by `init` for private workspace config.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files when running `init`.")
    parser.add_argument("--output", help="Optional path for dry-run JSON output.")
    args = parser.parse_args()
    load_env_file(args.env_file)

    if args.command == "init":
        run_init_wizard(
            profile_path=args.profile_output,
            workspace_path=args.workspace_output,
            force=args.force,
        )
        return

    if args.init_profile:
        write_default_profile(args.init_profile)
        print(f"Wrote default preference profile to {args.init_profile}")
        return

    if args.check_google_apis:
        print(json.dumps(check_google_apis(), indent=2))
        return

    if args.estimate_requests:
        estimate = estimate_requests(
            listings_considered=args.estimate_requests,
            seen_cache_hit_rate=args.cache_hit_rate,
            detail_requests_needed=args.detail_requests_needed,
            use_gemini=args.use_gemini,
            use_google_maps=args.maps_requests,
            use_hpd=args.hpd_lookup,
        )
        print(json.dumps(estimate.as_dict(), indent=2))
        return

    pipeline = build_pipeline(
        profile_path=args.profile,
        workspace_path=args.workspace,
        folder_link_path=args.folder_link,
        seed_listings_path=args.seed_listings,
        use_gemini=args.use_gemini,
        enable_hpd_lookup=args.hpd_lookup,
        listing_cache_path=args.listing_cache,
        rapidapi_max_requests=args.rapidapi_max_requests,
    )
    result = pipeline.run(dry_run=args.dry_run, limit=args.limit)
    payload = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(payload, encoding="utf-8")
    print(payload)


def check_google_apis() -> dict[str, object]:
    return {
        "env": {
            "GEMINI_API_KEY": _safe_env_fingerprint("GEMINI_API_KEY"),
            "GOOGLE_MAPS_API_KEY": _safe_env_fingerprint("GOOGLE_MAPS_API_KEY"),
            "GEMINI_MODEL": os.getenv("GEMINI_MODEL", ""),
        },
        "gemini": GoogleGeminiScoringClient.from_env().check_connection(),
        "maps": CommuteEstimator().check_connection(),
    }


def _safe_env_fingerprint(name: str) -> dict[str, object]:
    value = (os.getenv(name) or "").strip()
    if not value:
        return {"configured": False, "length": 0, "fingerprint": None}
    return {
        "configured": True,
        "length": len(value),
        "fingerprint": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12],
    }

def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_inline_comment(value).strip().strip('"').strip("'")
        os.environ[key] = value


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, character in enumerate(value):
        if character in {"'", '"'}:
            quote = None if quote == character else character
        if character == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


if __name__ == "__main__":
    main()
