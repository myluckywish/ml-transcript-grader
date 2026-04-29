from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.document_intelligence import extract_text_with_azure_document_intelligence
from app.services.request_debug import RequestTracer
from app.services.transcript_ai import analyze_transcript_with_azure_openai
from app.settings import load_azure_document_intelligence_settings, load_azure_openai_settings

logger = logging.getLogger(__name__)

router = APIRouter()

CATEGORIES = {
    "mathematics",
    "natural_sciences",
    "social_sciences",
    "foreign_language",
    "other",
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


def _normalize_course_title_for_units(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.upper().strip()
    normalized = re.sub(r"\b(SEMESTER|SEM|S)\s*(1|2)\b", " ", normalized)
    normalized = re.sub(r"\b(FALL|SPRING|WINTER|SUMMER)\b", " ", normalized)
    normalized = re.sub(r"\b(Q1|Q2|Q3|Q4|TRI1|TRI2|TRI3)\b", " ", normalized)
    normalized = re.sub(r"\b(QUARTER|QTR|TRIMESTER)\s*(1|2|3|4)\b", " ", normalized)
    normalized = re.sub(r"\b(PERIOD|PD)\s*\d+\b", " ", normalized)
    # Remove common trailing credit/grade tokens if OCR/model included them in title.
    normalized = re.sub(r"\b\d+(\.\d+)?\s*(CR|CREDIT|CREDITS)\b", " ", normalized)
    normalized = re.sub(r"\b(A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|F)\b$", " ", normalized)
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _dedupe_courses_for_units(courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for course in courses:
        title = str(course.get("course_title", "")).strip()
        normalized_title = _normalize_course_title_for_units(title)
        if not normalized_title:
            passthrough.append(course)
            continue

        existing = deduped.get(normalized_title)
        if existing is None:
            deduped[normalized_title] = course
            continue

        # Count once policy: keep the row with larger unit value.
        existing_units = _course_units(existing) or 0.0
        candidate_units = _course_units(course) or 0.0
        if candidate_units > existing_units:
            deduped[normalized_title] = course

    return [*deduped.values(), *passthrough]


@router.post("/transcript/analyze")
async def analyze_transcript(file: UploadFile = File(...), debug: bool = False) -> dict[str, Any]:
    tracer = RequestTracer("transcript_analyze")
    filename = file.filename or "unknown"
    content_type = file.content_type or "application/octet-stream"
    tracer.step("metadata_collected", filename=filename, content_type=content_type)
    data = await file.read()
    tracer.step("file_read", byte_count=len(data))
    logger.debug(
        "Received transcript upload filename=%s content_type=%s bytes=%d",
        filename,
        content_type,
        len(data),
    )
    if not data:
        tracer.step("validation_failed", reason="empty_upload")
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        docintel_settings = load_azure_document_intelligence_settings()
        tracer.step(
            "docintel_config_loaded",
            enabled=docintel_settings.enabled,
            configured=len(docintel_settings.missing_required) == 0,
            missing_settings=docintel_settings.missing_required,
            model_id=docintel_settings.model_id,
        )

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
        tracer.step("text_extracted_docintel", extracted_characters=len(extracted_text))
    except ValueError as exc:
        message = str(exc)
        tracer.step("parse_failed", error=message)
        if message.startswith("Unsupported file type"):
            raise HTTPException(status_code=415, detail=message) from exc
        raise HTTPException(status_code=422, detail=f"Could not parse file: {message}") from exc
    except Exception as exc:
        tracer.step("parse_failed", error=str(exc))
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc
    if not extracted_text:
        tracer.step("validation_failed", reason="empty_extracted_text")
        raise HTTPException(status_code=422, detail="No text could be extracted from this document.")

    settings = load_azure_openai_settings()
    tracer.step(
        "provider_config_loaded",
        enabled=settings.enabled,
        configured=len(settings.missing_required) == 0,
        missing_settings=settings.missing_required,
    )
    ai_result: dict[str, Any] | None = None
    if settings.enabled:
        try:
            ai_result = analyze_transcript_with_azure_openai(extracted_text, settings)
            tracer.step("ai_analysis_completed", has_analysis=ai_result is not None)
        except Exception as exc:
            tracer.step("ai_analysis_failed", error=str(exc))
            logger.exception("Transcript AI analysis failed for filename=%s", filename)
    else:
        tracer.step("ai_skipped", reason="provider_disabled")

    courses = (ai_result or {}).get("courses", [])
    totals_by_category = {
        "mathematics": 0.0,
        "natural_sciences": 0.0,
        "social_sciences": 0.0,
        "foreign_language": 0.0,
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

    response = {
        "filename": filename,
        "mime_type": content_type,
        "characters": len(extracted_text),
        "courses": courses if isinstance(courses, list) else [],
        "totals_by_category": totals_by_category,
        "unweighted_gpa": _to_float(gpa.get("unweighted_4_scale")),
    }
    tracer.step("response_ready", response_fields=list(response.keys()))
    return response
