from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.course_classifier import classify_course_titles
from app.services.course_mapping_store import list_unknown_titles, resolve_unknown_title, upsert_course_mapping
from app.services.request_debug import RequestTracer
from app.services.course_taxonomy import CANONICAL_SUBJECTS

router = APIRouter()


class CourseMappingUpsertRequest(BaseModel):
    raw_title: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    school_id: str = ""
    source: str = "manual"
    confidence: float = 1.0


class ClassifyTitlesRequest(BaseModel):
    titles: list[str] = Field(min_length=1)
    school_id: str = ""


class ResolveUnknownRequest(BaseModel):
    subject: str = Field(min_length=1)
    note: str = ""
    create_mapping: bool = True


@router.get("/units/taxonomy")
def units_taxonomy() -> dict[str, Any]:
    return {"subjects": CANONICAL_SUBJECTS}


@router.post("/units/mappings/upsert")
def units_mappings_upsert(request: CourseMappingUpsertRequest, debug: bool = False) -> dict[str, Any]:
    tracer = RequestTracer("units_mapping_upsert")
    tracer.step(
        "request_received",
        raw_title=request.raw_title,
        subject=request.subject,
        school_id=request.school_id,
        source=request.source,
    )
    try:
        record = upsert_course_mapping(
            raw_title=request.raw_title,
            subject=request.subject,
            school_id=request.school_id,
            source=request.source,
            confidence=request.confidence,
        )
        tracer.step("mapping_upserted", mapping_id=record.get("id"), normalized_title=record.get("normalized_title"))
    except ValueError as exc:
        tracer.step("validation_failed", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response: dict[str, Any] = {"mapping": record}
    tracer.step("response_ready", response_fields=list(response.keys()))
    if debug:
        response["debug"] = tracer.payload()
    return response


@router.post("/units/classify-titles")
def units_classify_titles(request: ClassifyTitlesRequest, debug: bool = False) -> dict[str, Any]:
    tracer = RequestTracer("units_classify_titles")
    tracer.step("request_received", title_count=len(request.titles), school_id=request.school_id)
    response = classify_course_titles(raw_titles=request.titles, school_id=request.school_id)
    tracer.step(
        "classification_complete",
        classified_count=len(response.get("classified_courses", [])),
        unknown_count=response.get("unknown_count", 0),
    )
    tracer.step("response_ready", response_fields=list(response.keys()))
    if debug:
        response["debug"] = tracer.payload()
    return response


@router.get("/units/unknowns")
def units_unknowns(
    school_id: str = "",
    status: str = "open",
    limit: int = 100,
    debug: bool = False,
) -> dict[str, Any]:
    tracer = RequestTracer("units_unknowns_list")
    tracer.step("request_received", school_id=school_id, status=status, limit=limit)
    unknowns = list_unknown_titles(school_id=school_id, status=status, limit=limit)
    tracer.step("unknowns_loaded", count=len(unknowns))
    response: dict[str, Any] = {
        "unknowns": unknowns,
        "status": status,
        "school_id": school_id,
    }
    tracer.step("response_ready", response_fields=list(response.keys()))
    if debug:
        response["debug"] = tracer.payload()
    return response


@router.post("/units/unknowns/{unknown_id}/resolve")
def units_unknowns_resolve(unknown_id: int, request: ResolveUnknownRequest, debug: bool = False) -> dict[str, Any]:
    tracer = RequestTracer("units_unknown_resolve")
    tracer.step(
        "request_received",
        unknown_id=unknown_id,
        subject=request.subject,
        create_mapping=request.create_mapping,
    )
    try:
        record = resolve_unknown_title(
            unknown_id=unknown_id,
            subject=request.subject,
            note=request.note,
            create_mapping=request.create_mapping,
        )
        tracer.step("unknown_resolved", status=record.get("status"), school_id=record.get("school_id"))
    except ValueError as exc:
        tracer.step("validation_failed", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response: dict[str, Any] = {"resolved": record}
    tracer.step("response_ready", response_fields=list(response.keys()))
    if debug:
        response["debug"] = tracer.payload()
    return response
