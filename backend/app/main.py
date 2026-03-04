from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.services.document_parser import parse_document_bytes

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Document Parser API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    logger.debug("Health check requested.")
    return {"status": "ok"}


@app.post("/parse")
async def parse_document(file: UploadFile = File(...)) -> dict[str, str | int]:
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    content_type = file.content_type or "application/octet-stream"
    data = await file.read()
    logger.debug(
        "Received upload filename=%s suffix=%s content_type=%s bytes=%d",
        filename,
        suffix,
        content_type,
        len(data),
    )
    if not data:
        logger.warning("Rejected empty upload filename=%s", filename)
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        extracted_text = parse_document_bytes(data, suffix, content_type)
        logger.debug(
            "Parsed document filename=%s extracted_chars=%d",
            filename,
            len(extracted_text),
        )
    except ValueError as exc:
        message = str(exc)
        logger.exception("Known parse failure for filename=%s: %s", filename, message)
        if message.startswith("Unsupported file type"):
            raise HTTPException(status_code=415, detail=message) from exc
        raise HTTPException(status_code=422, detail=f"Could not parse file: {message}") from exc
    except Exception as exc:
        logger.exception("Unexpected parse failure for filename=%s", filename)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc

    if not extracted_text:
        logger.warning("Parsed empty text for filename=%s", filename)
        raise HTTPException(status_code=422, detail="No text could be extracted from this document.")

    return {
        "filename": filename,
        "mime_type": content_type,
        "extracted_text": extracted_text,
        "characters": len(extracted_text),
    }
