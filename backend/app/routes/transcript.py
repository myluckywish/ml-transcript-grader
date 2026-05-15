from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, UploadFile

from app.services.transcript_jobs import get_batch, get_job, submit_batch_jobs, submit_single_job
from app.services.transcript_pipeline import analyze_transcript_content

logger = logging.getLogger(__name__)
router = APIRouter()


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
    return analyze_transcript_content(filename=filename, content_type=content_type, data=data)


@router.post("/transcript/analyze/submit")
async def submit_transcript_analysis(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = file.filename or "unknown"
    content_type = file.content_type or "application/octet-stream"
    data = await file.read()
    return submit_single_job(filename=filename, content_type=content_type, data=data)


@router.post("/transcript/batches/submit")
async def submit_transcript_batch(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    uploaded_files: list[tuple[str, str, bytes]] = []
    for file in files:
        filename = file.filename or "unknown"
        content_type = file.content_type or "application/octet-stream"
        data = await file.read()
        uploaded_files.append((filename, content_type, data))
    return submit_batch_jobs(uploaded_files)


@router.get("/transcript/jobs/{job_id}")
async def get_transcript_analysis_job(job_id: str) -> dict[str, Any]:
    return get_job(job_id)


@router.get("/transcript/batches/{batch_id}")
async def get_transcript_batch(batch_id: str) -> dict[str, Any]:
    return get_batch(batch_id)
