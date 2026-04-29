from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from app.settings import AzureOpenAISettings


class TranscriptAIResult(TypedDict):
    courses: list[dict[str, Any]]
    gpa: dict[str, Any]
    confidence: float | None
    notes: list[str]


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _extract_scale(scale_detected: Any) -> float | None:
    if isinstance(scale_detected, (int, float)):
        value = float(scale_detected)
        return value if value > 0 else None
    if isinstance(scale_detected, str):
        raw = scale_detected.strip()
        if not raw:
            return None
        cleaned = raw.replace("point", ".").replace(" ", "")
        for token in ("/", "outof"):
            if token in cleaned:
                cleaned = cleaned.split(token)[-1]
        cleaned = cleaned.replace("scale", "")
        try:
            value = float(cleaned)
            return value if value > 0 else None
        except ValueError:
            return None
    return None


def _grade_to_points(grade: Any) -> float | None:
    if not isinstance(grade, str):
        return None
    normalized = grade.strip().upper().replace(" ", "")
    if not normalized:
        return None
    if normalized in {"P", "PASS", "CR", "S", "SAT"}:
        return None

    letter_map = {
        "A+": 4.0,
        "A": 4.0,
        "A-": 3.7,
        "B+": 3.3,
        "B": 3.0,
        "B-": 2.7,
        "C+": 2.3,
        "C": 2.0,
        "C-": 1.7,
        "D+": 1.3,
        "D": 1.0,
        "D-": 0.7,
        "F": 0.0,
    }
    if normalized in letter_map:
        return letter_map[normalized]

    if re.fullmatch(r"\d{1,3}(\.\d+)?", normalized):
        value = float(normalized)
        if value <= 4.0:
            return max(0.0, min(4.0, value))
        if value <= 100.0:
            if value >= 90:
                return 4.0
            if value >= 80:
                return 3.0
            if value >= 70:
                return 2.0
            if value >= 60:
                return 1.0
            return 0.0
    return None


def _deterministic_unweighted_from_courses(payload: dict[str, Any]) -> float | None:
    courses = payload.get("courses")
    if not isinstance(courses, list):
        return None

    total_points = 0.0
    total_credits = 0.0
    for course in courses:
        if not isinstance(course, dict):
            continue
        grade_points = _to_float(course.get("grade_points"))
        if grade_points is None:
            grade_points = _grade_to_points(course.get("grade"))
        if grade_points is None:
            continue

        credit = _to_float(course.get("credit"))
        if credit is None:
            units = _to_float(course.get("units"))
            if units is not None:
                credit = units * 0.5
        if credit is None or credit <= 0:
            continue

        # Unweighted GPA caps grade points at 4.0.
        total_points += max(0.0, min(4.0, grade_points)) * credit
        total_credits += credit

    if total_credits <= 0:
        return None
    return round(total_points / total_credits, 3)


def _ensure_unweighted_gpa(payload: dict[str, Any]) -> dict[str, Any]:
    gpa = payload.get("gpa")
    if not isinstance(gpa, dict):
        gpa = {}

    deterministic = _deterministic_unweighted_from_courses(payload)
    if deterministic is not None:
        gpa["unweighted_4_scale"] = deterministic
        gpa["method"] = "deterministic_from_courses"
        payload["gpa"] = gpa
        notes = payload.get("notes")
        if not isinstance(notes, list):
            notes = []
        notes.append("Unweighted GPA computed deterministically from course grades and credits.")
        payload["notes"] = notes
        return payload

    unweighted = _to_float(gpa.get("unweighted_4_scale"))
    if unweighted is not None:
        gpa["unweighted_4_scale"] = round(max(0.0, min(4.0, unweighted)), 3)
        payload["gpa"] = gpa
        return payload

    weighted = _to_float(gpa.get("reported_weighted"))
    if weighted is None:
        payload["gpa"] = gpa
        return payload

    scale = _extract_scale(gpa.get("scale_detected"))
    if scale is None:
        scale = 5.0 if weighted > 4.0 else 4.0

    unweighted_calc = max(0.0, min(4.0, (weighted / scale) * 4.0))
    gpa["unweighted_4_scale"] = round(unweighted_calc, 3)
    gpa["method"] = "calculated_from_weighted"
    if not gpa.get("scale_detected"):
        gpa["scale_detected"] = str(scale)

    notes = payload.get("notes")
    if not isinstance(notes, list):
        notes = []
    notes.append(f"Unweighted GPA calculated from weighted GPA using scale {scale}.")
    payload["notes"] = notes
    payload["gpa"] = gpa
    return payload


def _system_prompt() -> str:
    return (
        "You are a high school transcript evaluation assistant. "
        "Return only JSON. No markdown. "
        "Count coursework by category and calculate unweighted GPA conversion."
    )


def _user_prompt(extracted_text: str) -> str:
    return (
        "Analyze the transcript text and output JSON with this exact shape:\n"
        "{\n"
        '  "courses": [\n'
        "    {\n"
        '      "course_title": string,\n'
        '      "subject": "mathematics" | "natural_sciences" | "social_sciences" | "foreign_language" | "other",\n'
        '      "units": number_or_null,\n'
        '      "credit": number_or_null,\n'
        '      "grade": string_or_null,\n'
        '      "grade_points": number_or_null\n'
        "    }\n"
        "  ],\n"
        '  "gpa": {\n'
        '    "reported_weighted": number_or_null,\n'
        '    "unweighted_4_scale": number_or_null,\n'
        '    "method": string,\n'
        '    "scale_detected": string_or_null\n'
        "  },\n"
        '  "notes": [string]\n'
        "}\n\n"
        "Conventions:\n"
        "- A unit equals 0.5 credits. If credits are present, convert with: units = credits / 0.5.\n"
        "- For each course, include grade and credit when available; keep grade as a normalized value such as A, A-, B+, etc.\n"
        "- If unweighted GPA is present, use/display only unweighted_4_scale.\n"
        "- If only weighted GPA is present, calculate unweighted_4_scale from the transcript data and explain method in notes.\n"
        "- If data is missing, use null and explain briefly in notes.\n\n"
        f"Transcript text:\n{extracted_text}"
    )


def analyze_transcript_with_azure_openai(
    extracted_text: str,
    settings: AzureOpenAISettings,
) -> TranscriptAIResult:
    if not settings.enabled:
        raise ValueError("Azure OpenAI is disabled.")

    missing = settings.missing_required
    if missing:
        raise ValueError(f"Missing Azure OpenAI settings: {', '.join(missing)}")

    try:
        from openai import AzureOpenAI
    except Exception as exc:
        raise ValueError("OpenAI SDK is not installed. Run pip install -r backend/requirements.txt.") from exc

    client = AzureOpenAI(
        api_key=settings.api_key,
        api_version=settings.api_version,
        azure_endpoint=settings.endpoint,
    )

    completion = client.chat.completions.create(
        model=settings.deployment,
        temperature=settings.temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(extracted_text)},
        ],
    )

    content = completion.choices[0].message.content or "{}"
    payload = _ensure_unweighted_gpa(json.loads(content))
    return TranscriptAIResult(
        courses=payload.get("courses", []),
        gpa=payload.get("gpa", {}),
        confidence=payload.get("confidence"),
        notes=payload.get("notes", []),
    )
