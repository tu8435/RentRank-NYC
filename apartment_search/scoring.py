from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

import certifi

from apartment_search.models import CategoryScores, FilterResult, Listing, ListingScore, PreferenceProfile


class ScoreCache:
    def __init__(self, path: str | Path = ".cache/apartment_search/model_scores.json") -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, Any]] | None = None

    def get(self, key: str) -> dict[str, Any] | None:
        return self._load().get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        data = self._load()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._data is None:
            if self.path.exists():
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                self._data = {}
        return self._data


class ListingScorer:
    def __init__(
        self,
        profile: PreferenceProfile,
        use_gemini: bool = False,
        cache: ScoreCache | None = None,
        gemini_client: "GoogleGeminiScoringClient | None" = None,
    ) -> None:
        self.profile = profile
        self.use_gemini = use_gemini
        self.cache = cache or ScoreCache()
        self.gemini_client = gemini_client or GoogleGeminiScoringClient.from_env()

    def score(self, listing: Listing, filter_result: FilterResult) -> ListingScore:
        heuristic = heuristic_score_listing(listing, filter_result, self.profile)
        if not self.use_gemini or not self.gemini_client.enabled:
            return heuristic

        cache_key = listing_cache_key(listing, model_name=self.gemini_client.model)
        cached = self.cache.get(cache_key)
        if cached:
            cached_score = score_from_dict(cached)
            if cached_score.model_used == "google_gemini":
                return cached_score

        model_score = self.gemini_client.score_listing(listing, self.profile, heuristic)
        if model_score.model_used == "google_gemini":
            self.cache.set(cache_key, score_to_dict(model_score))
        return model_score


class GoogleGeminiScoringClient:
    def __init__(self, api_key: str | None, model: str = "gemini-3.5-flash") -> None:
        self.api_key = api_key
        self.model = model

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    @classmethod
    def from_env(cls) -> "GoogleGeminiScoringClient":
        return cls(
            api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        )

    def score_listing(self, listing: Listing, profile: PreferenceProfile, heuristic: ListingScore) -> ListingScore:
        api_key = (self.api_key or "").strip()
        if _looks_like_invalid_api_key(api_key):
            heuristic.red_flags.append(
                "Google Gemini scoring skipped: GEMINI_API_KEY appears malformed. "
                "Use a Google AI Studio or Google Cloud API key, not an OAuth client name/token."
            )
            heuristic.confidence = min(heuristic.confidence, 0.55)
            return heuristic

        prompt = _gemini_prompt(listing, profile, heuristic)
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for image_url in listing.image_urls[:6]:
            inline_data = _download_image_part(image_url)
            if inline_data:
                parts.append({"inline_data": inline_data})

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }
        query = urllib.parse.urlencode({"key": api_key})
        request = urllib.request.Request(
            url=f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?{query}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60, context=_ssl_context()) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            heuristic.red_flags.append(f"Google Gemini scoring failed: {_format_url_error(exc)}")
            heuristic.confidence = min(heuristic.confidence, 0.55)
            return heuristic

        text = _extract_google_gemini_text(payload)
        parsed = _parse_json_object(text)
        if not parsed:
            heuristic.red_flags.append("Google Gemini scoring returned unparseable output.")
            return heuristic
        return _model_score_from_payload(parsed, heuristic, profile.qualitative.weights)

    def check_connection(self) -> dict[str, Any]:
        api_key = (self.api_key or "").strip()
        if _looks_like_invalid_api_key(api_key):
            return {
                "configured": bool(api_key),
                "ok": False,
                "model": self.model,
                "error": "GEMINI_API_KEY is missing or malformed.",
            }
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "Return only this JSON object: {\"ok\": true}"}],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        }
        query = urllib.parse.urlencode({"key": api_key})
        request = urllib.request.Request(
            url=f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?{query}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {
                "configured": True,
                "ok": False,
                "model": self.model,
                "error": _format_url_error(exc),
            }
        return {
            "configured": True,
            "ok": bool(_extract_google_gemini_text(payload)),
            "model": self.model,
        }


def _looks_like_invalid_api_key(api_key: str) -> bool:
    if not api_key:
        return True
    if any(character.isspace() for character in api_key):
        return True
    if api_key.lower().startswith(("oauth", "client", "nyc apartment search", "-")):
        return True
    return False


def _format_url_error(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        body = error.read().decode("utf-8", errors="replace")
        if body:
            return f"HTTP {error.code}: {body}"
        return f"HTTP {error.code}: {error.reason}"
    return str(error)


def heuristic_score_listing(
    listing: Listing,
    filter_result: FilterResult,
    profile: PreferenceProfile,
) -> ListingScore:
    text = _text(listing)
    scores = CategoryScores(
        budget=_budget_score(listing, profile),
        commute_transit=_commute_score(listing, profile),
        centrality=_centrality_score(listing, profile),
        laundry=_laundry_score(filter_result),
        bright_modern=_keyword_score(
            text,
            positives=["bright", "sunlight", "natural light", "south-facing", "renovated", "modern", "oversized windows"],
            negatives=["dark", "garden level", "basement"],
            default=5,
        ),
        bedroom_fit=_keyword_score(
            text,
            positives=["queen", "king", "equal bedrooms", "split bedrooms", "closet", "home office", "desk"],
            negatives=["railroad", "junior", "flex", "convertible", "windowless"],
            default=5,
        ),
        hosting_space=_keyword_score(
            text,
            positives=["large living", "spacious living", "open living", "dining", "entertaining"],
            negatives=["no living room", "compact living", "railroad"],
            default=5,
        ),
        kitchen=_keyword_score(
            text,
            positives=["dishwasher", "counter space", "stainless", "renovated kitchen", "open kitchen"],
            negatives=["kitchenette", "dated kitchen"],
            default=5,
        ),
        bathrooms=8 if listing.bathrooms and listing.bathrooms >= 2 else 6 if listing.bathrooms and listing.bathrooms >= 1.5 else 5,
        outdoor_space=_keyword_score(
            text,
            positives=["private terrace", "balcony", "roof deck", "shared garden", "courtyard"],
            negatives=[],
            default=3,
        ),
        diligence=_diligence_score(listing, text),
        description_quality=_description_quality_score(listing),
    )
    red_flags = _red_flags(listing, text, filter_result)
    follow_ups = _follow_up_questions(listing, filter_result)
    total = weighted_total(scores, profile.qualitative.weights)
    confidence = _confidence(listing, filter_result)
    rationale = _rationale(listing, scores, red_flags, follow_ups)
    return ListingScore(
        total=total,
        categories=scores,
        confidence=confidence,
        rationale=rationale,
        red_flags=red_flags,
        follow_up_questions=follow_ups,
        model_used="heuristic",
    )


def weighted_total(scores: CategoryScores, weights: dict[str, float]) -> float:
    score_map = scores.as_dict()
    weight_sum = 0.0
    weighted = 0.0
    for key, weight in weights.items():
        if key not in score_map:
            continue
        weighted += score_map[key] * weight
        weight_sum += weight
    if not weight_sum:
        return 0
    return round((weighted / weight_sum) * 10, 2)


def listing_cache_key(listing: Listing, model_name: str | None = None) -> str:
    payload = {
        "model_name": model_name,
        "source_id": listing.source_id,
        "url": listing.url,
        "description": listing.description,
        "images": listing.image_urls,
        "rent": listing.rent,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def score_to_dict(score: ListingScore) -> dict[str, Any]:
    return {
        "total": score.total,
        "categories": asdict(score.categories),
        "confidence": score.confidence,
        "rationale": score.rationale,
        "red_flags": score.red_flags,
        "follow_up_questions": score.follow_up_questions,
        "model_used": score.model_used,
    }


def score_from_dict(data: dict[str, Any]) -> ListingScore:
    return ListingScore(
        total=float(data["total"]),
        categories=CategoryScores(**data["categories"]),
        confidence=float(data["confidence"]),
        rationale=str(data["rationale"]),
        red_flags=list(data.get("red_flags", [])),
        follow_up_questions=list(data.get("follow_up_questions", [])),
        model_used=str(data.get("model_used", "gemini_cached")),
    )


def _budget_score(listing: Listing, profile: PreferenceProfile) -> float:
    if listing.rent is None:
        return 4
    if listing.rent <= profile.budget.target_total_max:
        return 10
    if listing.rent <= profile.budget.stretch_total_max:
        overage = listing.rent - profile.budget.target_total_max
        stretch_range = profile.budget.stretch_total_max - profile.budget.target_total_max
        return max(5, 9 - (overage / max(stretch_range, 1)) * 4)
    return 0


def _commute_score(listing: Listing, profile: PreferenceProfile) -> float:
    minutes = listing.commute_to_work_minutes or listing.commute_minutes
    score = 5.0
    if minutes is not None:
        if minutes < 20:
            score = 10
        elif minutes < 25:
            score = 7.5
        elif minutes < 30:
            score = 4.5
        else:
            score = 2
    if listing.subway_walk_minutes is not None and listing.subway_walk_minutes > profile.commute.max_subway_walk_minutes:
        score -= 1.5
    if listing.subway_transfers is not None:
        if listing.subway_transfers == 0:
            score += 0.75
        elif listing.subway_transfers == 1:
            score -= 0.25
        elif listing.subway_transfers == 2:
            score -= 1.5
        else:
            score -= 2.5
    return max(0, min(10, score))


def _centrality_score(listing: Listing, profile: PreferenceProfile) -> float:
    location = " ".join([listing.neighborhood or "", listing.borough or ""]).lower()
    if any(area.lower() in location for area in profile.preferred_locations):
        return 10
    if any(borough.lower() in location for borough in profile.preferred_boroughs):
        return 9
    if "manhattan" in location:
        return 9
    if any(area.lower() in location for area in profile.acceptable_locations):
        return 7
    if any(borough.lower() in location for borough in profile.acceptable_boroughs):
        return 7
    if "brooklyn" in location:
        return 6
    return 5


def _laundry_score(filter_result: FilterResult) -> float:
    if filter_result.laundry_status.value == "in_unit":
        return 10
    if filter_result.laundry_status.value == "in_building":
        return 7
    return 0


def _keyword_score(text: str, positives: list[str], negatives: list[str], default: float) -> float:
    score = default
    for term in positives:
        if term in text:
            score += 1.2
    for term in negatives:
        if term in text:
            score -= 2
    return max(0, min(10, score))


def _diligence_score(listing: Listing, text: str) -> float:
    score = 7
    for term in ["pest", "bedbug", "violation", "complaint", "as-is"]:
        if term in text:
            score -= 2
    for term in ["secure entry", "package room", "live-in super", "virtual doorman", "doorman"]:
        if term in text:
            score += 0.8
    hpd_risk = listing.raw.get("hpd_risk", {}) if isinstance(listing.raw, dict) else {}
    risk_label = hpd_risk.get("risk_label")
    if risk_label == "high":
        score -= 4
    elif risk_label == "medium":
        score -= 2
    elif risk_label == "low":
        score -= 1
    return max(0, min(10, score))


def _description_quality_score(listing: Listing) -> float:
    description = listing.description or ""
    if len(description) < 80:
        return 3
    score = 6
    for term in ["actual unit", "dimensions", "floor plan", "move-in", "laundry", "dishwasher", "subway"]:
        if term in description.lower():
            score += 0.7
    return max(0, min(10, score))


def _red_flags(listing: Listing, text: str, filter_result: FilterResult) -> list[str]:
    flags = [*filter_result.reasons]
    if "stock photo" in text or "representative photo" in text or "similar unit" in text:
        flags.append("Photos may not be of the actual unit.")
    if "net effective" in text:
        flags.append("Net-effective rent needs verification.")
    if len(listing.image_urls) < 4:
        flags.append("Photo set is incomplete.")
    if any(term in text for term in ["dark", "windowless", "basement"]):
        flags.append("Possible natural-light issue.")
    if any(term in text for term in ["railroad", "flex", "convertible"]):
        flags.append("Possible bedroom fairness/privacy issue.")
    hpd_risk = listing.raw.get("hpd_risk", {}) if isinstance(listing.raw, dict) else {}
    if hpd_risk.get("risk_label") in {"medium", "high"}:
        count = hpd_risk.get("open_violation_count")
        flags.append(f"HPD open violation risk is {hpd_risk.get('risk_label')} ({count} sampled records).")
    return flags


def _follow_up_questions(listing: Listing, filter_result: FilterResult) -> list[str]:
    questions = [*filter_result.warnings]
    if len(listing.image_urls) < 4:
        questions.append("Ask for additional photos of bedrooms and living/common area.")
    if listing.no_fee is None:
        questions.append("Ask whether rent is net or gross and whether any broker fee applies.")
    if listing.commute_minutes is None:
        questions.append("Estimate commute to the configured destination before touring.")
    return questions


def _confidence(listing: Listing, filter_result: FilterResult) -> float:
    confidence = 0.65
    if listing.description and len(listing.description) > 300:
        confidence += 0.1
    if len(listing.image_urls) >= 6:
        confidence += 0.15
    if not filter_result.warnings:
        confidence += 0.05
    if filter_result.reasons:
        confidence -= 0.2
    return max(0.1, min(0.95, confidence))


def _rationale(listing: Listing, scores: CategoryScores, red_flags: list[str], follow_ups: list[str]) -> str:
    strengths = []
    score_map = scores.as_dict()
    for key, value in sorted(score_map.items(), key=lambda item: item[1], reverse=True)[:3]:
        strengths.append(f"{key.replace('_', ' ')} {value:.1f}/10")
    pieces = [f"{listing.display_name}: strongest categories are {', '.join(strengths)}."]
    if red_flags:
        pieces.append(f"Red flags: {'; '.join(red_flags[:3])}.")
    if follow_ups:
        pieces.append(f"Follow up: {'; '.join(follow_ups[:2])}.")
    return " ".join(pieces)


def _text(listing: Listing) -> str:
    return " ".join(
        [
            listing.description or "",
            " ".join(listing.amenities),
            listing.neighborhood or "",
            listing.borough or "",
        ]
    ).lower()


def _gemini_prompt(listing: Listing, profile: PreferenceProfile, heuristic: ListingScore) -> str:
    return f"""
Score this NYC rental listing for two recent grads who want a July move-in, 12-month lease, 2BR+,
laundry in-unit or in-building, a short commute to {profile.commute.destination_address}, and a
bright/modern apartment with fair WFH-capable bedrooms plus a real hosting/common area.

Return only JSON with this shape:
{{
  "categories": {{
    "bright_modern": 0-10,
    "bedroom_fit": 0-10,
    "hosting_space": 0-10,
    "commute_transit": 0-10,
    "centrality": 0-10,
    "laundry": 0-10,
    "budget": 0-10,
    "kitchen": 0-10,
    "bathrooms": 0-10,
    "outdoor_space": 0-10,
    "diligence": 0-10,
    "description_quality": 0-10
  }},
  "confidence": 0-1,
  "rationale": "short score breakdown and explanation",
  "red_flags": ["..."],
  "follow_up_questions": ["..."]
}}

Important focus areas: {", ".join(profile.qualitative.model_focus)}.
Current heuristic score: {heuristic.total}/100, rationale: {heuristic.rationale}
Listing data:
{json.dumps(_listing_payload(listing), indent=2)}
""".strip()


def _listing_payload(listing: Listing) -> dict[str, Any]:
    return {
        "url": listing.url,
        "address": listing.address,
        "unit": listing.unit,
        "neighborhood": listing.neighborhood,
        "borough": listing.borough,
        "rent": listing.rent,
        "bedrooms": listing.bedrooms,
        "bathrooms": listing.bathrooms,
        "square_feet": listing.square_feet,
        "description": listing.description,
        "amenities": listing.amenities,
        "image_count": len(listing.image_urls),
        "commute_minutes": listing.commute_minutes,
        "subway_walk_minutes": listing.subway_walk_minutes,
        "subway_transfers": listing.subway_transfers,
        "no_fee": listing.no_fee,
    }


def _download_image_part(url: str) -> dict[str, str] | None:
    if not url.startswith("http"):
        return None
    try:
        with urllib.request.urlopen(url, timeout=20, context=_ssl_context()) as response:
            mime_type = response.headers.get_content_type()
            if not mime_type.startswith("image/"):
                return None
            data = response.read(4_000_000)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    return {"mime_type": mime_type, "data": base64.b64encode(data).decode("ascii")}


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _extract_google_gemini_text(payload: dict[str, Any]) -> str:
    parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "\n".join(part.get("text", "") for part in parts if isinstance(part, dict))


def _parse_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _model_score_from_payload(
    payload: dict[str, Any],
    fallback: ListingScore,
    weights: dict[str, float],
) -> ListingScore:
    category_payload = payload.get("categories", {})
    categories = CategoryScores(
        **{
            key: float(category_payload.get(key, fallback.categories.as_dict()[key]))
            for key in fallback.categories.as_dict()
        }
    )
    total = weighted_total(categories, weights)
    return ListingScore(
        total=total,
        categories=categories,
        confidence=float(payload.get("confidence", fallback.confidence)),
        rationale=str(payload.get("rationale", fallback.rationale)),
        red_flags=list(payload.get("red_flags", fallback.red_flags)),
        follow_up_questions=list(payload.get("follow_up_questions", fallback.follow_up_questions)),
        model_used="google_gemini",
    )
