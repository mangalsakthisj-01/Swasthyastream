"""Explainable civic complaint triage with an optional Gemini vision/text review.

AI outputs are advisory only. Critical-safety phrase detection runs first and
cannot be downgraded by the optional model. Reports are never rejected solely
because model confidence is low or evidence is missing.
"""
from __future__ import annotations
import base64
import json
import os
import re
from typing import Any

import requests

CRITICAL_TERMS = (
    "road cave-in", "road collapsed", "building collapse", "building collapsed",
    "live wire", "electric shock", "electrocution", "fire accident", "major flood",
    "flash flood", "person unconscious", "not breathing", "severe bleeding",
    "gas leak", "chemical leak", "bridge collapse", "open manhole with person",
    "மின்சாரம் தாக்க", "கட்டிடம் இடிந்த", "சாலை இடிந்த", "மூச்சு விடவில்லை",
)
HIGH_TERMS = (
    "overflowing sewage", "water contamination", "dengue cluster", "mosquito breeding",
    "large pothole", "fallen electric pole", "contaminated water", "sewage overflow",
)
CATEGORY_TERMS = {
    "Water & Drainage": ["water", "drain", "leak", "sewage", "flood", "waterlogging", "pipeline", "overflow", "தண்ணீர்", "கழிவுநீர்"],
    "Road & Transport": ["road", "pothole", "traffic", "footpath", "bridge", "cave-in", "street", "சாலை", "குழி"],
    "Waste & Sanitation": ["garbage", "waste", "dump", "sanitation", "rubbish", "litter", "smell", "குப்பை"],
    "Public Health": ["dengue", "mosquito", "fever", "outbreak", "health", "contamination", "infection", "காய்ச்சல்"],
    "Electricity & Lighting": ["electricity", "streetlight", "light", "wire", "pole", "power", "shock", "மின்சாரம்"],
    "Other Civic Issue": [],
}


def _has(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _gemini_review(category: str, description: str, severity: str, image: bytes | None, mime: str | None, api_key: str) -> dict[str, Any] | None:
    if not api_key:
        return None
    prompt = (
        "Assess a civic-service report for category relevance and urgency, not legal truth. "
        "Do not identify people, diagnose illnesses, or decide the citizen is lying. "
        "Return JSON with category_match (true/false/uncertain), confidence (integer 0-100), "
        "suggested_severity (Low/Medium/High/Critical), reason (short), needs_human_review (boolean). "
        "Treat uncertainty as human review. Never downgrade an obvious emergency.\n"
        f"Category: {category}\nDescription: {description[:2000]}\nSeverity: {severity}"
    )
    parts: list[dict[str, Any]] = [{"text": prompt}]
    if image and mime and mime.startswith("image/") and len(image) <= 4 * 1024 * 1024:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode("ascii")}})
    payload = {"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    try:
        response = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            params={"key": api_key}, json=payload, timeout=15,
        )
        response.raise_for_status()
        raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
        result = json.loads(raw)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def assess_complaint(category: str, description: str, severity: str = "Medium", image: bytes | None = None,
                     image_mime: str | None = None, allow_external_ai: bool = False,
                     api_key: str | None = None) -> dict[str, Any]:
    """Return a triage result; never rejects a complaint automatically."""
    text = f"{category}. {description}".lower()
    critical = severity.lower() == "critical" or _has(text, CRITICAL_TERMS)
    high = severity.lower() == "high" or _has(text, HIGH_TERMS)
    score = 100 if critical else 82 if high else 28 if severity.lower() == "low" else 52
    risk = "Critical" if critical else "High" if high else "Low" if severity.lower() == "low" else "Medium"

    model_result = None
    if allow_external_ai and api_key:
        model_result = _gemini_review(category, description, severity, image, image_mime, api_key)
    if model_result:
        try:
            match_val = str(model_result.get("category_match", "uncertain")).lower()
            confidence = max(0, min(100, int(model_result.get("confidence", 50))))
            suggested = str(model_result.get("suggested_severity", severity)).title()
            if suggested not in {"Low", "Medium", "High", "Critical"}:
                suggested = "Medium"
            if critical:
                suggested, score, risk = "Critical", 100, "Critical"
            else:
                score = max(score, {"Low": 25, "Medium": 50, "High": 80, "Critical": 95}[suggested])
                risk = suggested
            validation = "Likely category match" if match_val == "true" else "Mismatch — human review" if match_val == "false" else "Needs human review"
            return {
                "critical_override": critical, "priority_score": score, "risk_level": risk,
                "severity": suggested, "ai_validation": validation, "ai_confidence": confidence,
                "ai_reason": str(model_result.get("reason", "AI-assisted review"))[:700],
                "triage_provider": "Gemini", "review_required": bool(model_result.get("needs_human_review", True)) or match_val != "true",
            }
        except Exception:
            pass

    category_terms = CATEGORY_TERMS.get(category, [])
    match = _has(text, category_terms) if category_terms else len(description.strip()) > 12
    if critical:
        validation = "Urgent — bypass standard queue"
        reason = "A configured safety trigger was detected. Route urgently and request responsible human verification."
    elif image:
        validation = "Evidence attached — human review"
        reason = "Rule-based text triage completed. The uploaded media has not been verified by an AI model; a human should review it."
    elif match:
        validation = "Text appears category-consistent"
        reason = "The description contains terms related to the selected category. This is a weak signal, not proof of validity."
    else:
        validation = "Needs human review"
        reason = "The report has insufficient category-specific signals. It is retained for human review, not rejected."
    return {
        "critical_override": critical, "priority_score": score, "risk_level": risk,
        "severity": risk if critical or high else severity.title(), "ai_validation": validation,
        "ai_confidence": 62 if match else 42, "ai_reason": reason,
        "triage_provider": "Rules", "review_required": True,
    }
