from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.document_intelligence import extract_text_with_azure_document_intelligence
from app.services.request_debug import RequestTracer
from app.services.transcript_ai import analyze_transcript_with_azure_openai
from app.settings import load_azure_document_intelligence_settings, load_azure_openai_settings

logger = logging.getLogger(__name__)

router = APIRouter()

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

    required_units = (ai_result or {}).get("required_units", {})
    totals_by_category = {
        "mathematics": _to_float(required_units.get("mathematics")),
        "natural_sciences": _to_float(required_units.get("natural_sciences")),
        "social_sciences": _to_float(required_units.get("social_sciences")),
        "foreign_language": _to_float(required_units.get("foreign_language")),
        "other": _to_float(required_units.get("other")),
    }
    gpa = (ai_result or {}).get("gpa", {})

    response = {
        "filename": filename,
        "mime_type": content_type,
        "characters": len(extracted_text),
        "courses": (ai_result or {}).get("courses", []),
        "totals_by_category": totals_by_category,
        "unweighted_gpa": _to_float(gpa.get("unweighted_4_scale")),
    }
    tracer.step("response_ready", response_fields=list(response.keys()))
    return response
