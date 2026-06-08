from __future__ import annotations

import os

from apartment_search.models import PreferenceProfile


TOUR_CHECKLIST = [
    ("Must Verify", "Is this the exact listed unit?", "Confirm photos, floor, unit number, move-in date, and listed rent."),
    ("Must Verify", "Does laundry meet the dealbreaker?", "Verify in-unit or in-building laundry, hours, cost, and machine count."),
    ("Must Verify", "Are there any money surprises?", "Confirm gross vs net rent, broker fee, application fee, deposits, and move-in fees."),
    ("Room Feel", "Do both bedrooms feel fair?", "Check bed plus desk fit, closet/storage, privacy, windows, and noise."),
    ("Room Feel", "Can we host comfortably?", "Look for real seating space, TV wall, flow, and whether the common area feels natural."),
    ("Room Feel", "Does the apartment feel bright and modern?", "Judge actual light, finish quality, kitchen/bath condition, and wide-angle photo mismatch."),
    ("Building", "Would we feel good living here?", "Check entry security, package handling, stairs/elevator, cleanliness, trash, pests, and super responsiveness."),
    ("Location", "Does the block work day and night?", "Assess subway walk, street feel, late-night comfort, nearby food/cafes, and friend accessibility."),
    ("Final Gut Check", "Would we apply quickly if approved?", "Write the strongest pro, biggest concern, and what must be clarified before applying."),
]


BASE_APPLICATION_DOCUMENTS = [
    ("Photo ID", "Needed", "No", "Government-issued ID."),
    ("Employment or offer letter", "Needed", "Yes", "Especially important because start dates are pending."),
    ("Recent pay stubs", "If available", "No", "May be unavailable before job start."),
    ("Recent bank statements", "Needed", "No", "Usually last two or three months."),
    ("Tax return or W-2", "Needed", "No", "Prior-year proof if requested."),
    ("Credit score proof", "Needed", "Yes", "Add each applicant's current credit score or credit report when ready."),
    ("Reference letter", "Optional", "Yes", "Helpful but not always required."),
    ("Guarantor documents", "Backup", "No", "Only if landlord requires despite income profile."),
]


def tour_checklist_rows() -> list[dict[str, str]]:
    return [
        {"section": section, "prompt": prompt, "what_to_notice": what_to_notice}
        for section, prompt, what_to_notice in TOUR_CHECKLIST
    ]


def application_doc_rows(profile: PreferenceProfile) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for person in profile.renter_names:
        for document, status, adaptable, notes in BASE_APPLICATION_DOCUMENTS:
            if document == "Credit score proof":
                notes = os.getenv("APARTMENT_CREDIT_SCORE_NOTES", notes)
            rows.append(
                {
                    "person": person,
                    "document": document,
                    "status": status,
                    "adaptable": adaptable,
                    "notes": notes,
                }
            )
    return rows
