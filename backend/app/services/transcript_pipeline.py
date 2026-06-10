from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException

from app.services.document_intelligence import extract_text_with_azure_document_intelligence
from app.services.transcript_ai import analyze_transcript_with_azure_openai
from app.settings import load_azure_document_intelligence_settings, load_azure_openai_settings

logger = logging.getLogger(__name__)

CATEGORIES = {
    "english",
    "mathematics",
    "natural_sciences",
    "social_sciences",
    "foreign_language",
    "other_units",
    "other",
}
QUALIFIER_TOKENS = {"HONORS", "H", "AP", "IB", "ADV", "ADVANCED", "PREAP", "PRE-AP"}
MIN_EXTRACTED_CHARACTERS = 300
MAX_ANCHOR_COURSE_LINES = 40
SCHOOL_GRADE_ALIASES = {
    "freshman": "Freshman",
    "sophomore": "Sophomore",
    "junior": "Junior",
    "senior": "Senior",
}


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


def _normalized_subject(value: Any) -> str:
    if not isinstance(value, str):
        return "other"
    normalized = value.strip().lower()
    return normalized if normalized in CATEGORIES else "other"


def _course_units(course: dict[str, Any]) -> float | None:
    units = _to_float(course.get("units"))
    if units is not None and units > 0:
        return units
    credit = _to_float(course.get("credit"))
    if credit is not None and credit > 0:
        return credit / 0.5
    return None


def _is_non_counted_grade(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().upper()
    if not normalized:
        return False
    return normalized in {"F", "U", "E"}


def _normalize_course_title_for_units(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.upper().strip()
    normalized = re.sub(r"\b(SEMESTER|SEM|S)[\s\-_:]*(1|2)\b", " ", normalized)
    normalized = re.sub(r"\b(FALL|SPRING|WINTER|SUMMER)\b", " ", normalized)
    normalized = re.sub(r"\b(Q1|Q2|Q3|Q4|TRI1|TRI2|TRI3)\b", " ", normalized)
    normalized = re.sub(r"\b(QUARTER|QTR|TRIMESTER|TERM)[\s\-_:]*(1|2|3|4)\b", " ", normalized)
    normalized = re.sub(r"\b(PERIOD|PD)\s*\d+\b", " ", normalized)
    normalized = re.sub(r"\b\d+(\.\d+)?\s*(CR|CREDIT|CREDITS)\b", " ", normalized)
    normalized = re.sub(r"\b(A|B)\b$", " ", normalized)
    normalized = re.sub(r"(\d)\s*[AB]\b", r"\1", normalized)
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _extract_qualifier_key(normalized_title: str) -> str:
    if not normalized_title:
        return ""
    words = set(normalized_title.split())
    qualifiers = sorted(token for token in QUALIFIER_TOKENS if token in words)
    return "|".join(qualifiers)


def _fallback_key_for_missing_title(course: dict[str, Any]) -> str:
    subject = _normalized_subject(course.get("subject"))
    units = _course_units(course)
    units_part = f"{units:.3f}" if units is not None else "none"
    grade = str(course.get("grade", "")).strip().upper()
    return f"MISSING|{subject}|{units_part}|{grade}"


def _dedupe_courses_for_units(courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for course in courses:
        title = str(course.get("course_title", "")).strip()
        normalized_title = _normalize_course_title_for_units(title)
        if normalized_title:
            qualifier_key = _extract_qualifier_key(normalized_title)
            dedupe_key = f"{normalized_title}||{qualifier_key}"
        else:
            dedupe_key = _fallback_key_for_missing_title(course)

        existing = deduped.get(dedupe_key)
        if existing is None:
            deduped[dedupe_key] = course
            continue

        existing_units = _course_units(existing) or 0.0
        candidate_units = _course_units(course) or 0.0
        if candidate_units > existing_units:
            deduped[dedupe_key] = course

    return [*deduped.values()]


def _extraction_warnings(extracted_text: str) -> list[str]:
    warnings: list[str] = []
    stripped = extracted_text.strip()
    if len(stripped) < MIN_EXTRACTED_CHARACTERS:
        warnings.append("Very little text was extracted; OCR quality may be low.")
    if stripped:
        alnum = sum(1 for ch in stripped if ch.isalnum())
        ratio = alnum / len(stripped)
        if ratio < 0.35:
            warnings.append("Extracted text appears noisy; layout/OCR errors are likely.")
    lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
    if lines:
        short_lines = sum(1 for line in lines if len(line) <= 2)
        if short_lines / len(lines) > 0.25:
            warnings.append("Many very short lines were detected; columns/tables may be fragmented.")
    return warnings


def _extract_pre_anchors(extracted_text: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", " ", extracted_text)
    lowered = compact.lower()

    unweighted_match = re.search(r"unweighted[^0-9]{0,24}(\d(?:\.\d{1,3})?)", lowered, flags=re.IGNORECASE)
    weighted_match = re.search(r"weighted[^0-9]{0,24}(\d(?:\.\d{1,3})?)", lowered, flags=re.IGNORECASE)

    course_lines: list[str] = []
    grade_line_pattern = re.compile(r"\b(A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|F|P|PASS|CR|S)\b", re.IGNORECASE)
    credit_pattern = re.compile(r"\b\d(?:\.\d{1,2})?\b")
    for raw_line in extracted_text.splitlines():
        line = raw_line.strip()
        if len(line) < 6:
            continue
        if not grade_line_pattern.search(line):
            continue
        if not credit_pattern.search(line):
            continue
        course_lines.append(line)
        if len(course_lines) >= MAX_ANCHOR_COURSE_LINES:
            break

    return {
        "gpa": {
            "unweighted_4_scale": float(unweighted_match.group(1)) if unweighted_match else None,
            "reported_weighted": float(weighted_match.group(1)) if weighted_match else None,
        },
        "course_line_candidates": course_lines,
    }


def _normalize_school_grade(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered == "unknown":
        return "Unknown"

    alias = SCHOOL_GRADE_ALIASES.get(lowered)
    if alias:
        return alias

    match = re.fullmatch(r"(?:grade\s*)?(9|10|11|12)", lowered)
    if match:
        return f"Grade {match.group(1)}"
    return None


def _extract_current_school_grade(extracted_text: str) -> str:
    lowered = extracted_text.lower()
    for token, normalized in SCHOOL_GRADE_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return normalized

    grade_match = re.search(
        r"\b(?:current\s+)?(?:grade|gr)\s*[:\-]?\s*(9|10|11|12)\b",
        lowered,
        flags=re.IGNORECASE,
    )
    if grade_match:
        return f"Grade {grade_match.group(1)}"

    standalone_match = re.search(r"\bgrade\s*(9|10|11|12)\b", lowered, flags=re.IGNORECASE)
    if standalone_match:
        return f"Grade {standalone_match.group(1)}"

    return "Unknown"


def analyze_transcript_content(filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    docintel_settings = load_azure_document_intelligence_settings()
    if not docintel_settings.enabled:
        raise HTTPException(
            status_code=422,
            detail="Could not parse file: Azure Document Intelligence is disabled. Set AZURE_DOC_INTEL_ENABLED=true.",
        )
    if docintel_settings.missing_required:
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not parse file: Missing Azure Document Intelligence settings: "
                f"{', '.join(docintel_settings.missing_required)}"
            ),
        )

    try:
        extracted_text = extract_text_with_azure_document_intelligence(
            data=data,
            content_type=content_type,
            settings=docintel_settings,
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("Unsupported file type"):
            raise HTTPException(status_code=415, detail=message) from exc
        logger.error("Parse failed for %s: %s", filename, message)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {message}") from exc
    except Exception as exc:
        logger.exception("Unexpected parse failure for %s", filename)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc

    if not extracted_text:
        raise HTTPException(status_code=422, detail="No text could be extracted from this document.")

    settings = load_azure_openai_settings()
    ai_result: dict[str, Any] | None = None
    ai_error: str | None = None
    if settings.enabled:
        try:
            anchors = _extract_pre_anchors(extracted_text)
            ai_result = analyze_transcript_with_azure_openai(
                extracted_text,
                settings,
                pre_extracted_anchors=anchors,
            )
        except Exception as exc:
            ai_error = str(exc)
            logger.exception("Transcript AI analysis failed for filename=%s", filename)

    courses = (ai_result or {}).get("courses", [])
    totals_by_category = {
        "english": 0,
        "mathematics": 0,
        "natural_sciences": 0,
        "social_sciences": 0,
        "foreign_language": 0,
        "other_units": 0,
        "other": 0,
    }
    unit_courses = _dedupe_courses_for_units([c for c in courses if isinstance(c, dict)]) if isinstance(courses, list) else []
    for course in unit_courses:
        if _is_non_counted_grade(course.get("grade")):
            continue
        subject = _normalized_subject(course.get("subject"))
        totals_by_category[subject] += 1
    gpa = (ai_result or {}).get("gpa", {})
    ai_school_grade = _normalize_school_grade((ai_result or {}).get("current_school_grade"))
    school_grade = ai_school_grade or _extract_current_school_grade(extracted_text)
    warnings = _extraction_warnings(extracted_text)
    if ai_error:
        warnings.append("Course classification timed out or failed; totals may be incomplete.")

    return {
        "filename": filename,
        "mime_type": content_type,
        "characters": len(extracted_text),
        "courses": courses if isinstance(courses, list) else [],
        "totals_by_category": totals_by_category,
        "unweighted_gpa": _to_float(gpa.get("unweighted_4_scale")),
        "current_school_grade": school_grade,
        "warnings": warnings,
        "extraction_provider": {
            "name": "azure_document_intelligence",
            "azure_doc_intel_model_id": docintel_settings.model_id,
        },
        "classification_provider": {
            "name": "azure_openai",
            "enabled": settings.enabled,
            "error": ai_error,
            "use_pre_extraction": True,
        },
    }
