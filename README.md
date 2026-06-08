# RentRank NYC

RentRank NYC is a spreadsheet-first apartment-search assistant for NYC rentals. It pulls StreetEasy-style listings from RapidAPI, applies hard quantitative filters, scores qualitative fit with heuristics and Gemini, estimates commute times to/from work via Google Maps, and syncs data to a central Google Sheet review workflow.

The project is intentionally manual-run: it helps build a ranked candidate list of apartments that you (and your roommates) can vote on to tour. It does not contact brokers, submit applications, or make final housing decisions.

## What It Does

- Searches NYC rental listings through a RapidAPI StreetEasy-compatible adapter.
- Filters hard requirements such as bedrooms, bathrooms, rent, laundry, and commute.
- Scores candidates out of `100` using weighted categories such as commute, centrality, bedroom fit, hosting space, light/modernity, laundry, and budget.
- Uses Gemini multimodal analysis for candidate listings when image URLs are available.
- Uses Google Maps Distance Matrix for two commute windows:
  - apartment to destination at 9:00am on the next weekday
  - destination back to apartment at 6:00pm on the next weekday
- Caches listings and model scores to avoid repeating paid work.
- Writes a Google Sheet workflow with `Candidates`, `Tours`, `Rejected`, `Tour Checklist`, `Preference Profile`, and `Application Docs` tabs.
- Preserves active candidates across reruns unless they are rejected or promoted.

## Requirements

- Python `3.11+`
- A RapidAPI key for the NYC real-estate / StreetEasy-style API
- A Google Gemini API key
- A Google Maps API key for live commute checks, or `demo` for no-bill commute demo mode
- Google Sheets access via either local OAuth or a service account if you want sheet writes

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env` with your own keys. Never commit `.env`.

Run the setup wizard to create private local config files:

```bash
rentrank-nyc init
```

The wizard writes:

- `secrets/config/preferences.json`: private renter details, move-in date, budget, commute destination, neighborhoods, and scoring preferences.
- `secrets/config/workspace.json`: private Google Sheet ID, Drive folder target, and sheet title.

Both files are ignored by git. Public templates live in `config/preferences.example.json` and `config/workspace.example.json`.

## Environment Variables

Required for live listing search:

- `RAPIDAPI_KEY`: your RapidAPI key.
- `RAPIDAPI_REALTY_HOST`: default `st-easy-api.p.rapidapi.com`.
- `RAPIDAPI_REALTY_BASE_URL`: default `https://st-easy-api.p.rapidapi.com`.
- `RAPIDAPI_REALTY_SEARCH_PATH`: default `/search/rent`.
- `RAPIDAPI_REALTY_DETAIL_PATH`: default `/rental_detailsbyid`.
- `RAPIDAPI_MAX_REQUESTS`: safety cap for a run. Start low while testing.

Required for Gemini scoring:

- `GEMINI_API_KEY`: Google Gemini / Generative Language API key.
- `GEMINI_MODEL`: default `gemini-3.5-flash`.

Required for live commute checks:

- `GOOGLE_MAPS_API_KEY`: Google Maps key with Distance Matrix API access.
- Use `GOOGLE_MAPS_API_KEY=demo` to skip billable Maps calls while testing.

Required for Google Sheets writes, choose one auth style:

- OAuth local testing:
  - `GOOGLE_OAUTH_CLIENT_SECRET`
  - `GOOGLE_OAUTH_TOKEN`
  - `GOOGLE_OAUTH_PORT`
- Service account:
  - `GOOGLE_APPLICATION_CREDENTIALS`

Optional:

- `GOOGLE_SHEETS_SPREADSHEET_ID`: write to an existing sheet.
- `GOOGLE_DRIVE_FOLDER_ID`: create a sheet inside a Drive folder.
- `RENTRANK_WORKSPACE_PATH`: private workspace config path. Defaults to `secrets/config/workspace.json` when that file exists, otherwise `config/workspace.example.json`.
- `NYC_OPEN_DATA_APP_TOKEN`: NYC Open Data app token for HPD lookups.
- `RENTRANK_PROFILE_PATH`: private preference profile path. Defaults to `secrets/config/preferences.json` when that file exists, otherwise `config/preferences.example.json`.
- `APARTMENT_RENTER_NAMES`: comma-separated renter names for private local runs.
- `APARTMENT_RENTER_EMAILS`: comma-separated renter emails for private local runs.
- `APARTMENT_MOVE_IN`: private move-in text override.
- `APARTMENT_COMMUTE_DESTINATION`: private commute destination override.
- `APARTMENT_OUTREACH_APPLICANT_DETAILS`: private outreach details block. Use `\n` for line breaks.
- `APARTMENT_CREDIT_SCORE_NOTES`: private application-doc notes for credit score proof.

The public templates stay generic. Your ignored `secrets/config/*.json` files or private `.env` can restore personal details locally without changing tracked source files.

## Google Setup

Enable these Google APIs in the project tied to your keys:

- Generative Language API / Gemini API
- Distance Matrix API
- Google Sheets API
- Google Drive API

For local OAuth Sheets access:

1. Create an OAuth desktop/web client in Google Cloud.
2. Download the client secret JSON outside version control.
3. Set `GOOGLE_OAUTH_CLIENT_SECRET=/absolute/path/to/client_secret_...json`.
4. Set `GOOGLE_OAUTH_TOKEN=secrets/google-oauth-token.json`.
5. Set `GOOGLE_OAUTH_PORT=8080`.
6. Add `http://localhost:8080/` as an authorized redirect URI.

For service-account Sheets access:

1. Create a service account.
2. Download its JSON key outside version control.
3. Set `GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/google-service-account.json`.
4. Share the target sheet or Drive folder with the service account email.

## Health Check

Before running the full pipeline, verify Google auth:

```bash
rentrank-nyc --check-google-apis
```

The output includes safe key fingerprints, Gemini status, and Maps status without printing secrets.

## Dry Runs

Run against local seed listings with no external writes:

```bash
rentrank-nyc \
  --seed-listings examples/seed_listings.json \
  --dry-run \
  --output out/dry-run.json
```

Run a live API dry-run without writing to Sheets:

```bash
rentrank-nyc \
  --dry-run \
  --use-gemini \
  --limit 25 \
  --rapidapi-max-requests 20 \
  --listing-cache .cache/apartment_search/live_test_listings.json
```

## Production Run

Remove `--dry-run` when you are ready to sync the Google Sheet:

```bash
rentrank-nyc \
  --use-gemini \
  --limit 50 \
  --rapidapi-max-requests 20 \
  --listing-cache .cache/apartment_search/live_production_listings.json
```

The request cap is enforced. If the RapidAPI cap is reached, the run stops gracefully and returns partial results instead of crashing.

## Sheet Workflow

Each run reads the existing workbook first:

- Existing active candidates are preserved and reranked with new candidates.
- Any candidate with a `No` vote is moved to `Rejected`.
- Candidates with both reviewers voting `Yes` move to `Tours`.
- High-scoring candidates can auto-promote to `Tours`.
- `Application Sent` and active tour rows remain in `Tours`.
- Rejected tour rows move to `Rejected`.

## Request Budgeting

Estimate request volume before a larger run:

```bash
rentrank-nyc \
  --estimate-requests 100 \
  --cache-hit-rate 0.6 \
  --detail-requests-needed \
  --use-gemini \
  --maps-requests
```

RapidAPI requests, Gemini calls, Google Maps calls, and HPD calls are estimated separately. Google Maps estimates count two calls per unseen listing because the pipeline checks both commute directions.

## Preferences

The easiest private local setup is the wizard:

```bash
rentrank-nyc init
```

You can also generate only the preference file:

```bash
rentrank-nyc --init-profile secrets/config/preferences.json
```

Then edit `secrets/config/preferences.json` with your renter names, move-in date, commute destination, budget, neighborhoods, and scoring preferences. To use a different private profile:

```bash
RENTRANK_PROFILE_PATH=/absolute/path/to/preferences.json rentrank-nyc --dry-run
```

When no private profile exists, RentRank NYC falls back to the sanitized example profile.

## Workspace Config

`secrets/config/workspace.json` replaces the old `Shared/link.txt` flow. It can contain:

- `google_sheets_spreadsheet_id`: write to an existing Sheet.
- `google_drive_folder_id`: create new Sheets inside a Drive folder.
- `google_drive_folder_link`: paste a Drive folder URL instead of an ID.
- `google_sheets_title`: title used when RentRank NYC creates a new Sheet.

Environment variables like `GOOGLE_SHEETS_SPREADSHEET_ID` still work and override the workspace file.

## Tests

```bash
pytest
```

## Safety Notes

Do not commit:

- `.env`
- OAuth tokens
- Google client secret JSON files
- service account JSON files
- `.cache/`
- local output files
- private apartment preference documents
