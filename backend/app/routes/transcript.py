from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.document_intelligence import extract_text_with_azure_document_intelligence
from app.services.transcript_ai import analyze_transcript_with_azure_openai
from app.settings import load_azure_document_intelligence_settings, load_azure_openai_settings

logger = logging.getLogger(__name__)

router = APIRouter()

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


def _normalize_course_title_for_units(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.upper().strip()
    normalized = re.sub(r"\b(SEMESTER|SEM|S)[\s\-_:]*(1|2)\b", " ", normalized)
    normalized = re.sub(r"\b(FALL|SPRING|WINTER|SUMMER)\b", " ", normalized)
    normalized = re.sub(r"\b(Q1|Q2|Q3|Q4|TRI1|TRI2|TRI3)\b", " ", normalized)
    normalized = re.sub(r"\b(QUARTER|QTR|TRIMESTER|TERM)[\s\-_:]*(1|2|3|4)\b", " ", normalized)
    normalized = re.sub(r"\b(PERIOD|PD)\s*\d+\b", " ", normalized)
    # Remove common trailing credit tokens if OCR/model included them in title.
    normalized = re.sub(r"\b\d+(\.\d+)?\s*(CR|CREDIT|CREDITS)\b", " ", normalized)
    # Treat A/B suffixes as semester variants for unit counting.
    normalized = re.sub(r"\b(A|B)\b$", " ", normalized)
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

        # Count once policy: keep the row with larger unit value.
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

    anchors: dict[str, Any] = {
        "gpa": {
            "unweighted_4_scale": float(unweighted_match.group(1)) if unweighted_match else None,
            "reported_weighted": float(weighted_match.group(1)) if weighted_match else None,
        },
        "course_line_candidates": course_lines,
    }
    return anchors


@router.post("/transcript/analyze")
async def analyze_transcript(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = file.filename or "unknown"
    content_type = file.content_type or "application/octet-stream"
    data = await file.read()
    logger.debug(
        "Received transcript upload filename=%s content_type=%s bytes=%d",
        filename,
        content_type,
        len(data),
    )
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        docintel_settings = load_azure_document_intelligence_settings()
        if not docintel_settings.enabled:
            raise ValueError("Azure Document Intelligence is disabled. Set AZURE_DOC_INTEL_ENABLED=true.")
        if docintel_settings.missing_required:
            raise ValueError(
                f"Missing Azure Document Intelligence settings: {', '.join(docintel_settings.missing_required)}"
            )

        extracted_text = extract_text_with_azure_document_intelligence(
            data=data,
            content_type=content_type,
            settings=docintel_settings,
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("Unsupported file type"):
            raise HTTPException(status_code=415, detail=message) from exc
        raise HTTPException(status_code=422, detail=f"Could not parse file: {message}") from exc
    except Exception as exc:
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
    else:
        logger.info("Transcript AI analysis skipped because provider is disabled.")

    courses = (ai_result or {}).get("courses", [])
    totals_by_category = {
        "english": 0.0,
        "mathematics": 0.0,
        "natural_sciences": 0.0,
        "social_sciences": 0.0,
        "foreign_language": 0.0,
        "other_units": 0.0,
        "other": 0.0,
    }
    unit_courses = _dedupe_courses_for_units([c for c in courses if isinstance(c, dict)]) if isinstance(courses, list) else []
    for course in unit_courses:
        if not isinstance(course, dict):
            continue
        units = _course_units(course)
        if units is None:
            continue
        subject = _normalized_subject(course.get("subject"))
        totals_by_category[subject] += units

    totals_by_category = {key: round(value, 3) for key, value in totals_by_category.items()}
    gpa = (ai_result or {}).get("gpa", {})
    warnings = _extraction_warnings(extracted_text)
    if ai_error:
        warnings.append("Course classification timed out or failed; totals may be incomplete.")

    response = {
        "filename": filename,
        "mime_type": content_type,
        "characters": len(extracted_text),
        "courses": courses if isinstance(courses, list) else [],
        "totals_by_category": totals_by_category,
        "unweighted_gpa": _to_float(gpa.get("unweighted_4_scale")),
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
    return response
