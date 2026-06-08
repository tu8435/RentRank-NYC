from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from sys import stderr
from typing import Any

from apartment_search.models import PreferenceProfile, RankedListing, ReviewStatus


CANDIDATE_HEADERS = [
    "Lifecycle",
    "Rank",
    "Total Score",
    "Model Confidence",
    "StreetEasy URL",
    "Address",
    "Unit",
    "Neighborhood",
    "Borough",
    "Rent",
    "Rent / Person",
    "Bedrooms",
    "Bathrooms",
    "Laundry",
    "Commute To Work Minutes",
    "Commute Home Minutes",
    "Subway Walk Minutes",
    "Subway Transfers",
    "Move-In",
    "Listed At",
    "Days On Market",
    "No Fee",
    "Fee Notes",
    "Light / Modernity",
    "Bedroom Fit",
    "Hosting Space",
    "Commute / Transit",
    "Centrality",
    "Kitchen",
    "Outdoor Space",
    "Description Quality",
    "HPD Risk",
    "HPD Open Violations",
    "Red Flags",
    "Follow-Up Questions",
    "Model Rationale",
    "Reviewer 1 Vote",
    "Reviewer 2 Vote",
    "Reviewer 1 Notes",
    "Reviewer 2 Notes",
    "Open Houses",
    "Contact",
    "Last Contacted",
    "Outreach Draft",
    "Tour Checklist Status",
    "Source ID",
]

TOUR_HEADERS = [
    "Tour Status",
    "Promotion Reason",
    "Rank",
    "Total Score",
    "Model Confidence",
    "StreetEasy URL",
    "Address",
    "Unit",
    "Neighborhood",
    "Borough",
    "Rent",
    "Rent / Person",
    "Bedrooms",
    "Bathrooms",
    "Laundry",
    "Commute To Work Minutes",
    "Commute Home Minutes",
    "Red Flags",
    "Model Rationale",
    "Reviewer 1 Vote",
    "Reviewer 2 Vote",
    "Reviewer 1 Notes",
    "Reviewer 2 Notes",
    "Tour Date",
    "Tour Time",
    "Tour Notes",
    "Decision Notes",
    "Open Houses",
    "Contact",
    "Last Contacted",
    "Outreach Draft",
    "Source ID",
]

TOUR_CHECKLIST_HEADERS = [
    "Section",
    "Prompt",
    "What To Notice",
    "Quick Rating",
    "Notes",
]

PREFERENCE_HEADERS = ["Category", "Key", "Value", "Notes"]
APPLICATION_DOC_HEADERS = ["Person", "Document", "Status", "Adaptable?", "Notes"]
REJECTED_HEADERS = [
    "Rejected At",
    "Rejected Stage",
    "StreetEasy URL",
    "Address",
    "Reason",
    "Score",
    "Reviewer 1 Notes",
    "Reviewer 2 Notes",
    "Tour Notes",
    "Source ID",
]

ACTIVE_TABS = ["Candidates", "Tours", "Tour Checklist", "Preference Profile", "Application Docs", "Rejected"]
AUTO_PROMOTE_SCORE = float(os.getenv("AUTO_PROMOTE_SCORE", "85"))


@dataclass(slots=True)
class GoogleSheetsConfig:
    spreadsheet_id: str | None = None
    folder_id: str | None = None
    spreadsheet_title: str = "RentRank NYC Candidates"
    credentials_path: str | None = None
    oauth_client_secret_path: str | None = None
    oauth_token_path: str = "secrets/google-oauth-token.json"


class GoogleSheetsWriter:
    def __init__(self, config: GoogleSheetsConfig) -> None:
        self.config = config

    @classmethod
    def from_env(
        cls,
        folder_link: str | None = None,
        spreadsheet_id: str | None = None,
        folder_id: str | None = None,
        spreadsheet_title: str | None = None,
    ) -> "GoogleSheetsWriter":
        resolved_folder_id = (
            parse_drive_folder_id(folder_id or "")
            or parse_drive_folder_id(os.getenv("GOOGLE_DRIVE_FOLDER_ID") or "")
            or parse_drive_folder_id(folder_link or "")
        )
        resolved_spreadsheet_id = spreadsheet_id or parse_spreadsheet_id(os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID") or "")
        return cls(
            GoogleSheetsConfig(
                spreadsheet_id=resolved_spreadsheet_id,
                folder_id=resolved_folder_id,
                spreadsheet_title=spreadsheet_title or os.getenv("GOOGLE_SHEETS_TITLE", "RentRank NYC Candidates"),
                credentials_path=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
                oauth_client_secret_path=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
                oauth_token_path=os.getenv("GOOGLE_OAUTH_TOKEN", "secrets/google-oauth-token.json"),
            )
        )

    def write(
        self,
        ranked_listings: list[RankedListing],
        profile: PreferenceProfile,
        tour_checklist: list[dict[str, str]],
        application_docs: list[dict[str, str]],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        workbook = build_workbook_values(ranked_listings, profile, tour_checklist, application_docs)
        if dry_run:
            return {"dry_run": True, "workbook": workbook}

        sheets_service, drive_service = _build_google_services(
            credentials_path=self.config.credentials_path,
            oauth_client_secret_path=self.config.oauth_client_secret_path,
            oauth_token_path=self.config.oauth_token_path,
        )
        spreadsheet_id = self.config.spreadsheet_id or self._create_spreadsheet(sheets_service, drive_service)
        self._ensure_tabs(sheets_service, spreadsheet_id, ACTIVE_TABS)
        _stage("Reading existing sheet state")
        existing = _read_existing_workbook(sheets_service, spreadsheet_id)
        _stage("Syncing Candidates, Tours, and Rejected")
        workbook = build_workbook_values(ranked_listings, profile, tour_checklist, application_docs, existing)
        for tab_name, rows in workbook.items():
            _stage(f"Writing tab: {tab_name}")
            sheets_service.spreadsheets().values().clear(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab_name}'!A1:ZZ",
                body={},
            ).execute()
            sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab_name}'!A1",
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            ).execute()
        _apply_sheet_formatting(sheets_service, spreadsheet_id)
        return {
            "dry_run": False,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
            "tabs": list(workbook),
        }

    def _create_spreadsheet(self, sheets_service: Any, drive_service: Any) -> str:
        spreadsheet = (
            sheets_service.spreadsheets()
            .create(body={"properties": {"title": self.config.spreadsheet_title}})
            .execute()
        )
        spreadsheet_id = spreadsheet["spreadsheetId"]
        if self.config.folder_id:
            drive_service.files().update(
                fileId=spreadsheet_id,
                addParents=self.config.folder_id,
                fields="id, parents",
            ).execute()
        return spreadsheet_id

    @staticmethod
    def _ensure_tabs(sheets_service: Any, spreadsheet_id: str, desired_tabs: list[str]) -> None:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing = {sheet["properties"]["title"] for sheet in spreadsheet.get("sheets", [])}
        requests = [
            {"addSheet": {"properties": {"title": tab_name}}}
            for tab_name in desired_tabs
            if tab_name not in existing
        ]
        if requests:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            ).execute()


def build_workbook_values(
    ranked_listings: list[RankedListing],
    profile: PreferenceProfile,
    tour_checklist: list[dict[str, str]],
    application_docs: list[dict[str, str]],
    existing_workbook: dict[str, list[list[Any]]] | None = None,
) -> dict[str, list[list[Any]]]:
    existing_workbook = existing_workbook or {}
    workflow = build_workflow_rows(ranked_listings, profile, existing_workbook)
    return {
        "Candidates": [CANDIDATE_HEADERS, *workflow["Candidates"]],
        "Tours": [TOUR_HEADERS, *workflow["Tours"]],
        "Tour Checklist": [TOUR_CHECKLIST_HEADERS, *_tour_rows(tour_checklist, existing_workbook.get("Tour Checklist", []))],
        "Preference Profile": [PREFERENCE_HEADERS, *_preference_rows(profile)],
        "Application Docs": [APPLICATION_DOC_HEADERS, *_application_doc_rows(application_docs)],
        "Rejected": [REJECTED_HEADERS, *workflow["Rejected"]],
    }


def build_workflow_rows(
    ranked_listings: list[RankedListing],
    profile: PreferenceProfile,
    existing_workbook: dict[str, list[list[Any]]] | None = None,
) -> dict[str, list[list[Any]]]:
    existing_workbook = existing_workbook or {}
    existing_candidates = _rows_by_key(_dict_rows(existing_workbook.get("Candidates", [])))
    existing_tours = _rows_by_key(_dict_rows(existing_workbook.get("Tours", [])))
    existing_rejected = _rows_by_key(_dict_rows(existing_workbook.get("Rejected", [])))

    candidates: dict[str, dict[str, Any]] = {}
    tours: dict[str, dict[str, Any]] = {}
    rejected: dict[str, dict[str, Any]] = dict(existing_rejected)

    for key, existing in existing_tours.items():
        if _is_tour_rejected(existing):
            rejected[key] = _rejected_from_existing(existing, "Tour", existing.get("Decision Notes") or "Rejected after tour")
        else:
            tours[key] = existing

    processed_keys: set[str] = set()
    for index, ranked in enumerate(sorted(ranked_listings, key=lambda item: item.score.total, reverse=True), start=1):
        machine = _machine_row_dict(ranked, profile, index)
        key = _row_key(machine)
        if not key or key in rejected:
            continue
        processed_keys.add(key)

        existing_candidate = existing_candidates.get(key, {})
        existing_tour = tours.get(key, {})
        merged = _merge_manual_fields(machine, existing_candidate, candidate=True)
        merged = _merge_manual_fields(merged, existing_tour, tour=True)

        rejection_reason = _candidate_rejection_reason(merged)
        if rejection_reason:
            rejected[key] = _rejected_from_existing(merged, "Candidate", rejection_reason)
        elif existing_tour:
            tours[key] = _tour_row_from_machine(merged, existing_tour, existing_tour.get("Promotion Reason") or "Existing Tour")
        elif _should_promote(merged):
            reason = "Auto Promoted" if _score_value(merged) >= AUTO_PROMOTE_SCORE else "Promoted By Votes"
            tours[key] = _tour_row_from_machine(merged, {}, reason)
        else:
            merged["Lifecycle"] = "Needs Review"
            candidates[key] = merged

    for key, existing in existing_candidates.items():
        if key in processed_keys or key in rejected or key in tours:
            continue
        rejection_reason = _candidate_rejection_reason(existing)
        if rejection_reason:
            rejected[key] = _rejected_from_existing(existing, "Candidate", rejection_reason)
        elif _should_promote(existing):
            reason = "Auto Promoted" if _score_value(existing) >= AUTO_PROMOTE_SCORE else "Promoted By Votes"
            tours[key] = _tour_row_from_machine(existing, {}, reason)
        else:
            existing["Lifecycle"] = existing.get("Lifecycle") or "Needs Review"
            candidates[key] = existing

    return {
        "Candidates": _candidate_output_rows(list(candidates.values())),
        "Tours": [_dict_to_row(row, TOUR_HEADERS) for row in sorted(tours.values(), key=_sort_by_rank)],
        "Rejected": [_dict_to_row(row, REJECTED_HEADERS) for row in sorted(rejected.values(), key=_sort_by_rejected)],
    }


def parse_drive_folder_id(link: str) -> str | None:
    if link and "/" not in link:
        return link
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", link)
    return match.group(1) if match else None


def parse_spreadsheet_id(value: str) -> str | None:
    if not value:
        return None
    if "/" not in value:
        return value
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", value)
    return match.group(1) if match else None


def _read_existing_workbook(sheets_service: Any, spreadsheet_id: str) -> dict[str, list[list[Any]]]:
    ranges = [f"'{tab}'!A1:ZZ" for tab in ["Candidates", "Tours", "Rejected", "Tour Checklist"]]
    result = (
        sheets_service.spreadsheets()
        .values()
        .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges, valueRenderOption="UNFORMATTED_VALUE")
        .execute()
    )
    workbook: dict[str, list[list[Any]]] = {}
    for tab, value_range in zip(["Candidates", "Tours", "Rejected", "Tour Checklist"], result.get("valueRanges", [])):
        workbook[tab] = value_range.get("values", [])
    return workbook


def _apply_sheet_formatting(sheets_service: Any, spreadsheet_id: str) -> None:
    spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_ids = {sheet["properties"]["title"]: sheet["properties"]["sheetId"] for sheet in spreadsheet.get("sheets", [])}
    requests: list[dict[str, Any]] = []
    header_map = {
        "Candidates": CANDIDATE_HEADERS,
        "Tours": TOUR_HEADERS,
        "Tour Checklist": TOUR_CHECKLIST_HEADERS,
        "Preference Profile": PREFERENCE_HEADERS,
        "Application Docs": APPLICATION_DOC_HEADERS,
        "Rejected": REJECTED_HEADERS,
    }
    for tab_name, headers in header_map.items():
        sheet_id = sheet_ids.get(tab_name)
        if sheet_id is None:
            continue
        requests.extend(
            [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {"frozenRowCount": 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "wrapStrategy": "WRAP",
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,wrapStrategy)",
                    }
                },
                {
                    "repeatCell": {
                        "range": {"sheetId": sheet_id},
                        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat.wrapStrategy",
                    }
                },
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": 0,
                            "endIndex": len(headers),
                        }
                    }
                },
            ]
        )
    _add_dropdown(requests, sheet_ids, "Candidates", CANDIDATE_HEADERS, "Reviewer 1 Vote", ["", "Yes", "Maybe", "No"])
    _add_dropdown(requests, sheet_ids, "Candidates", CANDIDATE_HEADERS, "Reviewer 2 Vote", ["", "Yes", "Maybe", "No"])
    _add_dropdown(requests, sheet_ids, "Candidates", CANDIDATE_HEADERS, "Lifecycle", ["Needs Review", "Rejected"])
    _add_dropdown(requests, sheet_ids, "Tours", TOUR_HEADERS, "Tour Status", ["Tour Target", "Tour Requested", "Tour Scheduled", "Toured", "Application Sent", "Rejected"])
    _add_dropdown(requests, sheet_ids, "Tours", TOUR_HEADERS, "Reviewer 1 Vote", ["", "Yes", "Maybe", "No"])
    _add_dropdown(requests, sheet_ids, "Tours", TOUR_HEADERS, "Reviewer 2 Vote", ["", "Yes", "Maybe", "No"])
    _add_dropdown(requests, sheet_ids, "Tour Checklist", TOUR_CHECKLIST_HEADERS, "Quick Rating", ["", "Strong", "Okay", "Concern", "Dealbreaker"])
    if requests:
        sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def _add_dropdown(
    requests: list[dict[str, Any]],
    sheet_ids: dict[str, int],
    tab_name: str,
    headers: list[str],
    column_name: str,
    values: list[str],
) -> None:
    sheet_id = sheet_ids.get(tab_name)
    if sheet_id is None or column_name not in headers:
        return
    column_index = headers.index(column_name)
    requests.append(
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 1000,
                    "startColumnIndex": column_index,
                    "endColumnIndex": column_index + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": value} for value in values],
                    },
                    "strict": False,
                    "showCustomUi": True,
                },
            }
        }
    )


def _dict_rows(rows: list[list[Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    headers = [str(header) for header in rows[0]]
    dict_rows: list[dict[str, Any]] = []
    for row in rows[1:]:
        values = list(row) + [""] * max(0, len(headers) - len(row))
        item = dict(zip(headers, values, strict=False))
        if item.get("Reviewer 1 Review") and not item.get("Reviewer 1 Vote"):
            item["Reviewer 1 Vote"] = item["Reviewer 1 Review"]
        if item.get("Reviewer 2 Review") and not item.get("Reviewer 2 Vote"):
            item["Reviewer 2 Vote"] = item["Reviewer 2 Review"]
        dict_rows.append(item)
    return dict_rows


def _rows_by_key(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _row_key(row)
        if key:
            indexed[key] = row
    return indexed


def _row_key(row: dict[str, Any]) -> str:
    source_id = str(row.get("Source ID") or "").strip()
    url = str(row.get("StreetEasy URL") or "").strip()
    return source_id or url


def _machine_row_dict(ranked: RankedListing, profile: PreferenceProfile, rank: int) -> dict[str, Any]:
    listing = ranked.listing
    scores = ranked.score.categories
    return {
        "Lifecycle": "Needs Review",
        "Rank": rank,
        "Total Score": round(ranked.score.total, 2),
        "Model Confidence": round(ranked.score.confidence, 2),
        "StreetEasy URL": listing.url,
        "Address": listing.address,
        "Unit": listing.unit,
        "Neighborhood": listing.neighborhood,
        "Borough": listing.borough,
        "Rent": listing.rent,
        "Rent / Person": profile.budget.rent_per_person(listing.rent),
        "Bedrooms": listing.bedrooms,
        "Bathrooms": listing.bathrooms,
        "Laundry": ranked.filter_result.laundry_status.value,
        "Commute To Work Minutes": listing.commute_to_work_minutes or listing.commute_minutes,
        "Commute Home Minutes": listing.commute_home_minutes,
        "Subway Walk Minutes": listing.subway_walk_minutes,
        "Subway Transfers": listing.subway_transfers,
        "Move-In": profile.move_in,
        "Listed At": listing.listed_at,
        "Days On Market": listing.days_on_market,
        "No Fee": listing.no_fee,
        "Fee Notes": _fee_notes(listing),
        "Light / Modernity": round(scores.bright_modern, 2),
        "Bedroom Fit": round(scores.bedroom_fit, 2),
        "Hosting Space": round(scores.hosting_space, 2),
        "Commute / Transit": round(scores.commute_transit, 2),
        "Centrality": round(scores.centrality, 2),
        "Kitchen": round(scores.kitchen, 2),
        "Outdoor Space": round(scores.outdoor_space, 2),
        "Description Quality": round(scores.description_quality, 2),
        "HPD Risk": _hpd_risk_label(listing),
        "HPD Open Violations": _hpd_violation_count(listing),
        "Red Flags": "; ".join(ranked.score.red_flags),
        "Follow-Up Questions": "; ".join(ranked.score.follow_up_questions),
        "Model Rationale": ranked.score.rationale,
        "Reviewer 1 Vote": "",
        "Reviewer 2 Vote": "",
        "Reviewer 1 Notes": "",
        "Reviewer 2 Notes": "",
        "Open Houses": "; ".join(listing.open_house_dates),
        "Contact": _contact_summary(listing.agents),
        "Last Contacted": "",
        "Outreach Draft": ranked.outreach_draft,
        "Tour Checklist Status": "",
        "Source ID": listing.source_id,
    }


def _merge_manual_fields(
    base: dict[str, Any],
    existing: dict[str, Any],
    candidate: bool = False,
    tour: bool = False,
) -> dict[str, Any]:
    merged = dict(base)
    manual_fields = [
        "Reviewer 1 Vote",
        "Reviewer 2 Vote",
        "Reviewer 1 Notes",
        "Reviewer 2 Notes",
        "Last Contacted",
        "Tour Checklist Status",
    ]
    if candidate:
        manual_fields.extend(["Lifecycle", "Review Status"])
    if tour:
        manual_fields.extend(["Tour Status", "Tour Date", "Tour Time", "Tour Notes", "Decision Notes", "Promotion Reason"])
    for field in manual_fields:
        if existing.get(field) not in (None, ""):
            merged[field] = existing[field]
    return merged


def _candidate_rejection_reason(row: dict[str, Any]) -> str | None:
    lifecycle = _norm(row.get("Lifecycle") or row.get("Review Status"))
    if lifecycle == "rejected":
        return "Manually rejected"
    if _norm(row.get("Reviewer 1 Vote")) == "no":
        return "Reviewer 1 voted No"
    if _norm(row.get("Reviewer 2 Vote")) == "no":
        return "Reviewer 2 voted No"
    return None


def _should_promote(row: dict[str, Any]) -> bool:
    if _score_value(row) >= AUTO_PROMOTE_SCORE:
        return True
    return _norm(row.get("Reviewer 1 Vote")) == "yes" and _norm(row.get("Reviewer 2 Vote")) == "yes"


def _tour_row_from_machine(machine: dict[str, Any], existing: dict[str, Any], reason: str) -> dict[str, Any]:
    tour = {
        "Tour Status": existing.get("Tour Status") or "Tour Target",
        "Promotion Reason": existing.get("Promotion Reason") or reason,
        "Rank": machine.get("Rank"),
        "Total Score": machine.get("Total Score"),
        "Model Confidence": machine.get("Model Confidence"),
        "StreetEasy URL": machine.get("StreetEasy URL"),
        "Address": machine.get("Address"),
        "Unit": machine.get("Unit"),
        "Neighborhood": machine.get("Neighborhood"),
        "Borough": machine.get("Borough"),
        "Rent": machine.get("Rent"),
        "Rent / Person": machine.get("Rent / Person"),
        "Bedrooms": machine.get("Bedrooms"),
        "Bathrooms": machine.get("Bathrooms"),
        "Laundry": machine.get("Laundry"),
        "Commute To Work Minutes": machine.get("Commute To Work Minutes"),
        "Commute Home Minutes": machine.get("Commute Home Minutes"),
        "Red Flags": machine.get("Red Flags"),
        "Model Rationale": machine.get("Model Rationale"),
        "Reviewer 1 Vote": machine.get("Reviewer 1 Vote"),
        "Reviewer 2 Vote": machine.get("Reviewer 2 Vote"),
        "Reviewer 1 Notes": machine.get("Reviewer 1 Notes"),
        "Reviewer 2 Notes": machine.get("Reviewer 2 Notes"),
        "Tour Date": existing.get("Tour Date", ""),
        "Tour Time": existing.get("Tour Time", ""),
        "Tour Notes": existing.get("Tour Notes", ""),
        "Decision Notes": existing.get("Decision Notes", ""),
        "Open Houses": machine.get("Open Houses"),
        "Contact": machine.get("Contact"),
        "Last Contacted": machine.get("Last Contacted"),
        "Outreach Draft": machine.get("Outreach Draft"),
        "Source ID": machine.get("Source ID"),
    }
    return _merge_manual_fields(tour, existing, tour=True)


def _is_tour_rejected(row: dict[str, Any]) -> bool:
    return _norm(row.get("Tour Status")) == "rejected"


def _rejected_from_existing(row: dict[str, Any], stage: str, reason: str) -> dict[str, Any]:
    return {
        "Rejected At": row.get("Rejected At") or date.today().isoformat(),
        "Rejected Stage": row.get("Rejected Stage") or stage,
        "StreetEasy URL": row.get("StreetEasy URL"),
        "Address": row.get("Address"),
        "Reason": row.get("Reason") or reason,
        "Score": row.get("Score") or row.get("Total Score"),
        "Reviewer 1 Notes": row.get("Reviewer 1 Notes", ""),
        "Reviewer 2 Notes": row.get("Reviewer 2 Notes", ""),
        "Tour Notes": row.get("Tour Notes", ""),
        "Source ID": row.get("Source ID"),
    }


def _dict_to_row(row: dict[str, Any], headers: list[str]) -> list[Any]:
    return [row.get(header, "") for header in headers]


def _candidate_output_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    output_rows: list[list[Any]] = []
    sorted_rows = sorted(rows, key=_sort_by_score_desc)
    for rank, row in enumerate(sorted_rows, start=1):
        row["Rank"] = rank
        output_rows.append(_dict_to_row(row, CANDIDATE_HEADERS))
    return output_rows


def _sort_by_score_desc(row: dict[str, Any]) -> tuple[float, str]:
    return -_score_value(row), str(row.get("Address") or row.get("StreetEasy URL") or "")


def _sort_by_rank(row: dict[str, Any]) -> tuple[float, str]:
    rank = row.get("Rank")
    try:
        rank_value = float(rank)
    except (TypeError, ValueError):
        rank_value = 999999
    return rank_value, str(row.get("Address") or row.get("StreetEasy URL") or "")


def _sort_by_rejected(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("Rejected At") or ""), str(row.get("Address") or row.get("StreetEasy URL") or "")


def _score_value(row: dict[str, Any]) -> float:
    try:
        return float(row.get("Total Score") or 0)
    except (TypeError, ValueError):
        return 0


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _candidate_rows(ranked_listings: list[RankedListing], profile: PreferenceProfile) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for index, ranked in enumerate(sorted(ranked_listings, key=lambda item: item.score.total, reverse=True), start=1):
        listing = ranked.listing
        scores = ranked.score.categories
        contact = _contact_summary(listing.agents)
        rows.append(
            [
                ReviewStatus.NEW.value,
                index,
                round(ranked.score.total, 2),
                round(ranked.score.confidence, 2),
                listing.url,
                listing.address,
                listing.unit,
                listing.neighborhood,
                listing.borough,
                listing.rent,
                profile.budget.rent_per_person(listing.rent),
                listing.bedrooms,
                listing.bathrooms,
                ranked.filter_result.laundry_status.value,
                listing.commute_to_work_minutes or listing.commute_minutes,
                listing.commute_home_minutes,
                listing.subway_walk_minutes,
                listing.subway_transfers,
                profile.move_in,
                listing.listed_at,
                listing.days_on_market,
                listing.no_fee,
                _fee_notes(listing),
                round(scores.bright_modern, 2),
                round(scores.bedroom_fit, 2),
                round(scores.hosting_space, 2),
                round(scores.commute_transit, 2),
                round(scores.centrality, 2),
                round(scores.kitchen, 2),
                round(scores.outdoor_space, 2),
                round(scores.description_quality, 2),
                _hpd_risk_label(listing),
                _hpd_violation_count(listing),
                "; ".join(ranked.score.red_flags),
                "; ".join(ranked.score.follow_up_questions),
                ranked.score.rationale,
                "",
                "",
                "",
                "",
                "; ".join(listing.open_house_dates),
                contact,
                "",
                ranked.outreach_draft,
                "",
                listing.source_id,
            ]
        )
    return rows


def _tour_rows(items: list[dict[str, str]], existing_rows: list[list[Any]] | None = None) -> list[list[str]]:
    existing_by_prompt = {
        f"{row.get('Section', '')}|{row.get('Prompt', '')}": row for row in _dict_rows(existing_rows or [])
    }
    rows: list[list[str]] = []
    for item in items:
        key = f"{item.get('section', '')}|{item.get('prompt', '')}"
        existing = existing_by_prompt.get(key, {})
        rows.append(
            [
                item.get("section", ""),
                item.get("prompt", ""),
                item.get("what_to_notice", ""),
                existing.get("Quick Rating", ""),
                existing.get("Notes", ""),
            ]
        )
    return rows


def _preference_rows(profile: PreferenceProfile) -> list[list[Any]]:
    rows: list[list[Any]] = [
        ["Budget", "target_share_min", profile.budget.target_share_min, "Per person"],
        ["Budget", "target_share_max", profile.budget.target_share_max, "Per person"],
        ["Budget", "stretch_share_max", profile.budget.stretch_share_max, "Per person"],
        ["Budget", "target_total_max", profile.budget.target_total_max, "Computed"],
        ["Budget", "stretch_total_max", profile.budget.stretch_total_max, "Computed"],
        ["Commute", "destination", profile.commute.destination_address, ""],
        ["Commute", "max_minutes", profile.commute.max_minutes, ""],
        ["Commute", "max_subway_walk_minutes", profile.commute.max_subway_walk_minutes, ""],
        ["Hard Filter", "min_bedrooms", profile.min_bedrooms, ""],
        ["Hard Filter", "min_bathrooms", profile.min_bathrooms, ""],
        ["Hard Filter", "acceptable_laundry", ", ".join(sorted(profile.acceptable_laundry)), ""],
    ]
    for key, value in profile.qualitative.weights.items():
        rows.append(["Weight", key, value, "Higher means more important"])
    for item in profile.qualitative.hard_rejects:
        rows.append(["Hard Reject", item, True, ""])
    for item in profile.qualitative.boosts:
        rows.append(["Boost", item, True, ""])
    for item in profile.qualitative.penalties:
        rows.append(["Penalty", item, True, ""])
    return rows


def _application_doc_rows(items: list[dict[str, str]]) -> list[list[str]]:
    return [
        [
            item.get("person", ""),
            item.get("document", ""),
            item.get("status", "Needed"),
            item.get("adaptable", ""),
            item.get("notes", ""),
        ]
        for item in items
    ]


def _contact_summary(agents: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for agent in agents:
        name = agent.get("name") or agent.get("agent_name")
        phone = agent.get("phone")
        brokerage = agent.get("brokerage") or agent.get("company")
        parts.append(", ".join(str(value) for value in [name, phone, brokerage] if value))
    return " | ".join(parts)


def _fee_notes(listing: Any) -> str:
    if listing.no_fee is True:
        return "No-fee listed"
    if listing.no_fee is False:
        return "Fee status may apply; verify who hired broker"
    return "Fee status unknown; verify net/gross rent and broker fee"


def _hpd_risk_label(listing: Any) -> str:
    risk = listing.raw.get("hpd_risk", {}) if isinstance(listing.raw, dict) else {}
    return risk.get("risk_label", "not_checked")


def _hpd_violation_count(listing: Any) -> int | str:
    risk = listing.raw.get("hpd_risk", {}) if isinstance(listing.raw, dict) else {}
    return risk.get("open_violation_count", "")


def _build_google_services(
    credentials_path: str | None,
    oauth_client_secret_path: str | None = None,
    oauth_token_path: str = "secrets/google-oauth-token.json",
) -> tuple[Any, Any]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    credentials = None
    service_account_path = _usable_path(credentials_path)
    oauth_secret_path = _usable_path(oauth_client_secret_path) or _discover_oauth_client_secret()
    if service_account_path:
        credentials = service_account.Credentials.from_service_account_file(service_account_path, scopes=scopes)
    elif oauth_secret_path:
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = os.path.expanduser(oauth_token_path)
        if os.path.exists(token_path):
            credentials = Credentials.from_authorized_user_file(token_path, scopes=scopes)
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(oauth_secret_path, scopes)
                oauth_port = int(os.getenv("GOOGLE_OAUTH_PORT", "8080"))
                credentials = flow.run_local_server(port=oauth_port)
            os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as token_file:
                token_file.write(credentials.to_json())
    else:
        raise RuntimeError(
            "Set GOOGLE_APPLICATION_CREDENTIALS for a service account or "
            "GOOGLE_OAUTH_CLIENT_SECRET for local OAuth Sheets access."
        )
    return build("sheets", "v4", credentials=credentials), build("drive", "v3", credentials=credentials)


def _stage(message: str) -> None:
    print(f"[rentrank-nyc] {message}", file=stderr)


def _usable_path(path: str | None) -> str | None:
    if not path:
        return None
    expanded = os.path.expanduser(path.strip())
    if not expanded or expanded.startswith("/absolute/path/to/"):
        return None
    return expanded if os.path.exists(expanded) else None


def _discover_oauth_client_secret() -> str | None:
    for candidate in sorted(os.listdir(".")):
        if candidate.startswith("client_secret") and candidate.endswith(".json") and os.path.exists(candidate):
            return os.path.abspath(candidate)
    return None
