from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.document_parser import parse_document_bytes
from app.services.document_intelligence import extract_text_with_azure_document_intelligence
from app.services.request_debug import RequestTracer
from app.services.transcript_ai import analyze_transcript_with_azure_openai
from app.settings import load_azure_document_intelligence_settings, load_azure_openai_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/transcript/analyze")
async def analyze_transcript(file: UploadFile = File(...), debug: bool = False) -> dict[str, Any]:
    tracer = RequestTracer("transcript_analyze")
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    content_type = file.content_type or "application/octet-stream"
    tracer.step("metadata_collected", filename=filename, suffix=suffix, content_type=content_type)
    data = await file.read()
    tracer.step("file_read", byte_count=len(data))
    logger.debug(
        "Received transcript upload filename=%s suffix=%s content_type=%s bytes=%d",
        filename,
        suffix,
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

        extraction_provider = "local_parser"
        extraction_error: str | None = None
        extracted_text = ""
        if docintel_settings.enabled and len(docintel_settings.missing_required) == 0:
            try:
                extracted_text = extract_text_with_azure_document_intelligence(
                    data=data,
                    content_type=content_type,
                    settings=docintel_settings,
                )
                extraction_provider = "azure_document_intelligence"
                tracer.step("text_extracted_docintel", extracted_characters=len(extracted_text))
            except Exception as exc:
                extraction_error = str(exc)
                tracer.step("docintel_failed_fallback", error=extraction_error)

        if not extracted_text:
            parse_result = parse_document_bytes(data, suffix, content_type)
            extracted_text = parse_result["extracted_text"]
            tracer.step("text_extracted_local", extracted_characters=len(extracted_text))
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
    ai_error: str | None = None

    if settings.enabled:
        try:
            ai_result = analyze_transcript_with_azure_openai(extracted_text, settings)
            tracer.step("ai_analysis_completed", has_analysis=ai_result is not None)
        except Exception as exc:
            ai_error = str(exc)
            tracer.step("ai_analysis_failed", error=ai_error)
            logger.exception("Transcript AI analysis failed for filename=%s", filename)
    else:
        tracer.step("ai_skipped", reason="provider_disabled")

    response = {
        "filename": filename,
        "mime_type": content_type,
        "characters": len(extracted_text),
        "extracted_text": extracted_text,
        "analysis": ai_result,
        "analysis_error": ai_error,
        "extraction_provider": {
            "name": extraction_provider,
            "azure_doc_intel": {
                "enabled": docintel_settings.enabled,
                "configured": len(docintel_settings.missing_required) == 0,
                "missing_settings": docintel_settings.missing_required,
                "model_id": docintel_settings.model_id,
                "error": extraction_error,
            },
        },
        "ai_provider": {
            "name": "azure_openai",
            "enabled": settings.enabled,
            "configured": len(settings.missing_required) == 0,
            "missing_settings": settings.missing_required,
            "api_version": settings.api_version,
            "deployment": settings.deployment,
        },
    }
    tracer.step("response_ready", response_fields=list(response.keys()))
    if debug:
        response["debug"] = tracer.payload()
    return response
