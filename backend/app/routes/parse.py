from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.document_parser import parse_document_bytes
from app.services.request_debug import RequestTracer

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/parse")
async def parse_document(file: UploadFile = File(...), debug: bool = False) -> dict[str, Any]:
    tracer = RequestTracer("parse_document")
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    content_type = file.content_type or "application/octet-stream"
    tracer.step("metadata_collected", filename=filename, suffix=suffix, content_type=content_type)
    data = await file.read()
    tracer.step("file_read", byte_count=len(data))
    logger.debug(
        "Received upload filename=%s suffix=%s content_type=%s bytes=%d",
        filename,
        suffix,
        content_type,
        len(data),
    )
    if not data:
        logger.warning("Rejected empty upload filename=%s", filename)
        tracer.step("validation_failed", reason="empty_upload")
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        parse_result = parse_document_bytes(data, suffix, content_type)
        extracted_text = parse_result["extracted_text"]
        tracer.step("document_parsed", extracted_characters=len(extracted_text))
        logger.debug(
            "Parsed document filename=%s extracted_chars=%d",
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
        "parsed_content": parse_result["parsed_content"],
    }
    tracer.step("response_ready", response_fields=list(response.keys()))
    if debug:
        response["debug"] = tracer.payload()
    return response
