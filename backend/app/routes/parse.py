from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.document_intelligence import extract_document_with_azure_document_intelligence
from app.services.request_debug import RequestTracer
from app.settings import load_azure_document_intelligence_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/parse")
async def parse_document(file: UploadFile = File(...), debug: bool = False) -> dict[str, Any]:
    tracer = RequestTracer("parse_document")
    filename = file.filename or "unknown"
    content_type = file.content_type or "application/octet-stream"
    tracer.step("metadata_collected", filename=filename, content_type=content_type)
    data = await file.read()
    tracer.step("file_read", byte_count=len(data))
    logger.debug(
        "Received upload filename=%s content_type=%s bytes=%d",
        filename,
        content_type,
        len(data),
    )
    if not data:
        logger.warning("Rejected empty upload filename=%s", filename)
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

        extracted_document = extract_document_with_azure_document_intelligence(
            data=data,
            content_type=content_type,
            settings=docintel_settings,
        )
        extracted_text = str(extracted_document.get("text", ""))
        tracer.step("text_extracted_docintel", extracted_characters=len(extracted_text))
        logger.debug(
            "Parsed document via Azure Document Intelligence filename=%s extracted_chars=%d",
            filename,
            len(extracted_text),
        )
    except ValueError as exc:
        message = str(exc)
        tracer.step("parse_failed", error=message)
        logger.exception("Known parse failure for filename=%s: %s", filename, message)
        if message.startswith("Unsupported file type"):
            raise HTTPException(status_code=415, detail=message) from exc
        raise HTTPException(status_code=422, detail=f"Could not parse file: {message}") from exc
    except Exception as exc:
        tracer.step("parse_failed", error=str(exc))
        logger.exception("Unexpected parse failure for filename=%s", filename)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc

    if not extracted_text:
        logger.warning("Parsed empty text for filename=%s", filename)
        tracer.step("validation_failed", reason="empty_extracted_text")
        raise HTTPException(status_code=422, detail="No text could be extracted from this document.")

    response = {
        "filename": filename,
        "mime_type": content_type,
        "extracted_text": extracted_text,
        "characters": len(extracted_text),
        "parsed_content": {
            "content_kind": "plain_text",
            "json": None,
            "text": extracted_text,
            "lines": extracted_text.splitlines(),
            "paragraphs": [chunk.strip() for chunk in extracted_text.split("\n\n") if chunk.strip()],
            "document_structure": extracted_document,
        },
        "extraction_provider": {
            "name": "azure_document_intelligence",
            "azure_doc_intel": {
                "enabled": docintel_settings.enabled,
                "configured": len(docintel_settings.missing_required) == 0,
                "missing_settings": docintel_settings.missing_required,
                "model_id": docintel_settings.model_id,
                "error": None,
            },
        },
    }
    tracer.step("response_ready", response_fields=list(response.keys()))
    if debug:
        response["debug"] = tracer.payload()
    return response
