from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from typing import Any

from fastapi import HTTPException

from app.services.transcript_pipeline import analyze_transcript_content

TRANSCRIPT_WORKERS = max(1, int(os.getenv("TRANSCRIPT_WORKERS", "10")))
MAX_BATCH_FILES = 10
MAX_TRANSCRIPT_JOBS = max(100, int(os.getenv("MAX_TRANSCRIPT_JOBS", "1000")))
JOB_RETENTION_SECONDS = max(60, int(os.getenv("JOB_RETENTION_SECONDS", "3600")))

_jobs_lock = threading.Lock()
_job_queue: queue.Queue[str] = queue.Queue()
_jobs: dict[str, dict[str, Any]] = {}
_batches: dict[str, dict[str, Any]] = {}
_workers_started = False


def _update_job(job_id: str, **changes: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(changes)
        job["updated_at"] = time.time()


def _cleanup_expired_locked(now: float) -> None:
    stale_ids = [
        job_id
        for job_id, job in _jobs.items()
        if job.get("status") in {"succeeded", "failed"} and (now - float(job.get("updated_at", now))) > JOB_RETENTION_SECONDS
    ]
    for job_id in stale_ids:
        _jobs.pop(job_id, None)

    stale_batches = []
    for batch_id, batch in _batches.items():
        job_ids = batch.get("job_ids", [])
        if all(job_id not in _jobs for job_id in job_ids):
            stale_batches.append(batch_id)
    for batch_id in stale_batches:
        _batches.pop(batch_id, None)

    if len(_jobs) > MAX_TRANSCRIPT_JOBS:
        candidates = sorted(
            (
                (job_id, float(job.get("updated_at", 0.0)))
                for job_id, job in _jobs.items()
                if job.get("status") in {"succeeded", "failed"}
            ),
            key=lambda item: item[1],
        )
        for job_id, _ in candidates[: max(0, len(_jobs) - MAX_TRANSCRIPT_JOBS)]:
            _jobs.pop(job_id, None)


def _worker_loop() -> None:
    while True:
        job_id = _job_queue.get()
        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job:
            _job_queue.task_done()
            continue

        _update_job(job_id, status="running", error=None)
        try:
            result = analyze_transcript_content(
                filename=job["filename"],
                content_type=job["content_type"],
                data=job["data"],
            )
            _update_job(job_id, status="succeeded", result=result)
        except HTTPException as exc:
            _update_job(job_id, status="failed", error=str(exc.detail))
        except Exception as exc:
            _update_job(job_id, status="failed", error=str(exc))
        finally:
            with _jobs_lock:
                existing = _jobs.get(job_id)
                if existing is not None:
                    existing.pop("data", None)
                _cleanup_expired_locked(time.time())
            _job_queue.task_done()


def ensure_workers_started() -> None:
    global _workers_started
    if _workers_started:
        return
    with _jobs_lock:
        if _workers_started:
            return
        for _ in range(TRANSCRIPT_WORKERS):
            thread = threading.Thread(target=_worker_loop, daemon=True)
            thread.start()
        _workers_started = True


def submit_single_job(filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    ensure_workers_started()
    job_id = uuid.uuid4().hex
    now = time.time()
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "filename": filename,
            "content_type": content_type,
            "created_at": now,
            "updated_at": now,
            "error": None,
            "result": None,
            "data": data,
        }
        _cleanup_expired_locked(now)
    _job_queue.put(job_id)
    return {"job_id": job_id, "status": "queued"}


def submit_batch_jobs(uploaded_files: list[tuple[str, str, bytes]]) -> dict[str, Any]:
    if not uploaded_files:
        raise HTTPException(status_code=400, detail="No files were provided.")
    if len(uploaded_files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_BATCH_FILES} files are allowed per batch.")

    ensure_workers_started()
    batch_id = uuid.uuid4().hex
    now = time.time()
    queued_jobs: list[dict[str, Any]] = []
    skipped_files: list[dict[str, Any]] = []

    for filename, content_type, data in uploaded_files:
        if not data:
            skipped_files.append({"filename": filename, "content_type": content_type, "error": "Uploaded file is empty."})
            continue

        job_id = uuid.uuid4().hex
        with _jobs_lock:
            _jobs[job_id] = {
                "id": job_id,
                "batch_id": batch_id,
                "status": "queued",
                "filename": filename,
                "content_type": content_type,
                "created_at": now,
                "updated_at": now,
                "error": None,
                "result": None,
                "data": data,
            }
        _job_queue.put(job_id)
        queued_jobs.append({"job_id": job_id, "filename": filename, "content_type": content_type, "status": "queued"})

    with _jobs_lock:
        _batches[batch_id] = {
            "id": batch_id,
            "created_at": now,
            "updated_at": now,
            "job_ids": [j["job_id"] for j in queued_jobs],
            "total_files": len(uploaded_files),
            "skipped_files": skipped_files,
        }
        _cleanup_expired_locked(now)

    return {
        "batch_id": batch_id,
        "status": "queued",
        "total_files": len(uploaded_files),
        "queued_jobs": queued_jobs,
        "skipped_files": skipped_files,
    }


def get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        return {
            "job_id": job["id"],
            "status": job["status"],
            "filename": job["filename"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "error": job.get("error"),
            "result": job.get("result"),
        }


def get_batch(batch_id: str) -> dict[str, Any]:
    with _jobs_lock:
        batch = _batches.get(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found.")

        jobs = [job for job_id in batch["job_ids"] if (job := _jobs.get(job_id))]
        skipped_files = batch.get("skipped_files", [])

        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "skipped": len(skipped_files)}
        for job in jobs:
            status = job.get("status", "")
            if status in counts:
                counts[status] += 1

        done_count = counts["succeeded"] + counts["failed"] + counts["skipped"]
        total_count = len(jobs) + len(skipped_files)
        progress_percent = round((done_count / total_count) * 100, 2) if total_count else 100.0

        if counts["running"] > 0:
            batch_status = "running"
        elif counts["queued"] > 0:
            batch_status = "queued"
        elif counts["failed"] > 0:
            batch_status = "completed_with_errors"
        else:
            batch_status = "succeeded"

        return {
            "batch_id": batch["id"],
            "status": batch_status,
            "created_at": batch["created_at"],
            "updated_at": max([batch["updated_at"], *[job["updated_at"] for job in jobs]]),
            "total_files": batch["total_files"],
            "progress": {"completed": done_count, "total": total_count, "percent": progress_percent, **counts},
            "jobs": [
                {
                    "job_id": job["id"],
                    "status": job["status"],
                    "filename": job["filename"],
                    "created_at": job["created_at"],
                    "updated_at": job["updated_at"],
                    "error": job.get("error"),
                    "result": job.get("result"),
                }
                for job in jobs
            ],
            "skipped_files": skipped_files,
        }
