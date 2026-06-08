from __future__ import annotations

import os

from apartment_search.models import Listing, PreferenceProfile


OUTREACH_TEMPLATE = """Hi {landlord},

I'm {sender_name}. My roommate and I are on the market for apartments with a {move_in} move-in date.

{applicant_details}

Our maximum budget is ${max_budget:,} per month.

Is the rent on your listing for the property at {location} the net or gross rent?

Best regards,

{sender_name}
"""


def build_outreach_draft(listing: Listing, profile: PreferenceProfile, sender_name: str | None = None) -> str:
    landlord = _agent_name(listing) or "Landlord"
    location = listing.address or listing.display_name
    return OUTREACH_TEMPLATE.format(
        landlord=landlord,
        sender_name=sender_name or profile.renter_names[0],
        move_in=profile.move_in,
        applicant_details=_applicant_details(),
        max_budget=profile.budget.outreach_max_rent,
        location=location,
    )


def _agent_name(listing: Listing) -> str | None:
    if not listing.agents:
        return None
    first = listing.agents[0]
    name = first.get("name") or first.get("agent_name")
    return str(name) if name else None


def _applicant_details() -> str:
    details = (os.getenv("APARTMENT_OUTREACH_APPLICANT_DETAILS") or "").strip()
    if details:
        return details.replace("\\n", "\n")
    return "We can provide income, employment, credit, and application documentation upon request."
