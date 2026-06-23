from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

from fastapi import HTTPException

from app.services.document_intelligence import extract_text_with_azure_document_intelligence
from app.services.request_debug import RequestTracer
from app.services.transcript_ai import analyze_transcript_with_azure_openai
from app.settings import load_azure_document_intelligence_settings, load_azure_openai_settings

logger = logging.getLogger(__name__)

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
RIGOR_TOKEN_MAP = {
    "H": "HONORS",
    "HONORS": "HONORS",
    "AP": "AP",
    "IB": "IB",
    "ADV": "ADVANCED",
    "ADVANCED": "ADVANCED",
    "PREAP": "PREAP",
    "PRE-AP": "PREAP",
}
TITLE_ABBREVIATIONS = {
    "ENG": "ENGLISH",
    "ALG": "ALGEBRA",
    "BIO": "BIOLOGY",
    "CHEM": "CHEMISTRY",
    "PHYS": "PHYSICS",
    "GEO": "GEOMETRY",
    "HIST": "HISTORY",
    "GOV": "GOVERNMENT",
}
ROMAN_NUMERAL_MAP = {
    "I": "1",
    "II": "2",
    "III": "3",
    "IV": "4",
    "V": "5",
    "VI": "6",
    "VII": "7",
    "VIII": "8",
    "IX": "9",
    "X": "10",
}
COURSE_CODE_PATTERN = re.compile(r"^[A-Z]{1,4}\d{2,4}[A-Z]?$")
FUZZY_DEDUPE_THRESHOLD = 0.85
MIN_EXTRACTED_CHARACTERS = 300
MAX_ANCHOR_COURSE_LINES = 40
SCHOOL_GRADE_ALIASES = {
    "freshman": "Freshman",
    "sophomore": "Sophomore",
    "junior": "Junior",
    "senior": "Senior",
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


def _is_non_counted_grade(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().upper()
    if not normalized:
        return False
    return normalized in {"F", "U", "E"}


def _normalize_course_title_for_units(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.upper().strip()
    normalized = re.sub(
        r"\b(I|II|III|IV|V|VI|VII|VIII|IX|X)\b",
        lambda match: ROMAN_NUMERAL_MAP[match.group(1)],
        normalized,
    )
    normalized = re.sub(r"\b(SEMESTER|SEM|S)[\s\-_:]*(1|2)\b", " ", normalized)
    normalized = re.sub(r"\b(FALL|SPRING|WINTER|SUMMER)\b", " ", normalized)
    normalized = re.sub(r"\b(Q1|Q2|Q3|Q4|TRI1|TRI2|TRI3)\b", " ", normalized)
    normalized = re.sub(r"\b(QUARTER|QTR|TRIMESTER|TERM)[\s\-_:]*(1|2|3|4)\b", " ", normalized)
    normalized = re.sub(r"\b(PERIOD|PD)\s*\d+\b", " ", normalized)
    normalized = re.sub(r"\b\d+(\.\d+)?\s*(CR|CREDIT|CREDITS)\b", " ", normalized)
    normalized = re.sub(r"\b(A|B)\b$", " ", normalized)
    normalized = re.sub(r"(\d)\s*[AB]\b", r"\1", normalized)
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _extract_qualifier_key(normalized_title: str) -> str:
    if not normalized_title:
        return ""
    words = set(normalized_title.split())
    qualifiers = sorted(token for token in QUALIFIER_TOKENS if token in words)
    return "|".join(qualifiers)


def _extract_term_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.upper()
    patterns = (
        (r"\b(SEMESTER|SEM|S)[\s\-_:]*(1|2)\b", "S{}"),
        (r"\b(Q)(1|2|3|4)\b", "Q{}"),
        (r"\b(TRI)(1|2|3)\b", "TRI{}"),
        (r"\b(QUARTER|QTR|TRIMESTER|TERM)[\s\-_:]*(1|2|3|4)\b", "T{}"),
    )
    for pattern, template in patterns:
        match = re.search(pattern, normalized)
        if match:
            return template.format(match.group(2))

    suffix_match = re.search(r"(\d)\s*([AB])\b", normalized)
    if suffix_match:
        return suffix_match.group(2)
    trailing_match = re.search(r"\(([^\)]*?)([AB])\)\s*$", normalized)
    if trailing_match:
        return trailing_match.group(2)
    final_match = re.search(r"\b([AB])\b\s*$", normalized)
    if final_match:
        return final_match.group(1)
    return ""


def _canonicalize_identity_tokens(normalized_title: str) -> tuple[str, str, str]:
    if not normalized_title:
        return "", "", ""

    tokens = normalized_title.split()
    rigor_tokens: list[str] = []
    base_tokens: list[str] = []
    level_tokens: list[str] = []

    for token in tokens:
        mapped_rigor = RIGOR_TOKEN_MAP.get(token)
        if mapped_rigor:
            rigor_tokens.append(mapped_rigor)
            continue

        expanded = TITLE_ABBREVIATIONS.get(token, token)
        if COURSE_CODE_PATTERN.fullmatch(expanded):
            continue
        if re.fullmatch(r"\d+", expanded):
            level_tokens.append(expanded)
            continue
        base_tokens.append(expanded)

    rigor_key = "|".join(sorted(set(rigor_tokens)))
    base_key = " ".join(base_tokens)
    level_key = " ".join(level_tokens)
    return base_key, level_key, rigor_key


def _course_identity(course: dict[str, Any]) -> tuple[str, str, str]:
    title = str(course.get("course_title", "")).strip()
    normalized_title = _normalize_course_title_for_units(title)
    if not normalized_title:
        return "", "", ""
    return _canonicalize_identity_tokens(normalized_title)


def _units_are_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_units = _course_units(left)
    right_units = _course_units(right)
    if left_units is None or right_units is None:
        return True
    return abs(left_units - right_units) <= 0.51 or max(left_units, right_units) >= 1.0


def _should_fuzzy_dedupe(
    course: dict[str, Any],
    candidate: dict[str, Any],
    identity: tuple[str, str, str],
    candidate_identity: tuple[str, str, str],
) -> bool:
    base_key, level_key, rigor_key = identity
    candidate_base, candidate_level, candidate_rigor = candidate_identity
    if not base_key or not candidate_base:
        return False
    if level_key != candidate_level or rigor_key != candidate_rigor:
        return False
    if _normalized_subject(course.get("subject")) != _normalized_subject(candidate.get("subject")):
        return False
    if not _units_are_compatible(course, candidate):
        return False
    similarity = SequenceMatcher(a=base_key, b=candidate_base).ratio()
    return similarity >= FUZZY_DEDUPE_THRESHOLD


def _fallback_key_for_missing_title(course: dict[str, Any]) -> str:
    subject = _normalized_subject(course.get("subject"))
    units = _course_units(course)
    units_part = f"{units:.3f}" if units is not None else "none"
    grade = str(course.get("grade", "")).strip().upper()
    return f"MISSING|{subject}|{units_part}|{grade}"


def _group_courses_for_units(courses: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    identities: dict[str, tuple[str, str, str]] = {}
    for course in courses:
        title = str(course.get("course_title", "")).strip()
        normalized_title = _normalize_course_title_for_units(title)
        if normalized_title:
            base_key, level_key, rigor_key = _canonicalize_identity_tokens(normalized_title)
            qualifier_key = _extract_qualifier_key(normalized_title)
            dedupe_key = f"{base_key}||{level_key}||{rigor_key or qualifier_key}"
        else:
            dedupe_key = _fallback_key_for_missing_title(course)

        existing_group = grouped.get(dedupe_key)
        if existing_group is None:
            course_identity = _course_identity(course)
            matched_key = None
            for existing_key, existing_group in grouped.items():
                representative = max(existing_group, key=lambda item: _course_units(item) or 0.0)
                existing_identity = identities.get(existing_key) or _course_identity(representative)
                if _should_fuzzy_dedupe(course, representative, course_identity, existing_identity):
                    matched_key = existing_key
                    break
            if matched_key is None:
                grouped[dedupe_key] = [course]
                identities[dedupe_key] = course_identity
                continue
            dedupe_key = matched_key
            existing_group = grouped.get(dedupe_key)

        if existing_group is None:
            grouped[dedupe_key] = [course]
        else:
            existing_group.append(course)
        if dedupe_key not in identities:
            identities[dedupe_key] = _course_identity(course)

    return [*grouped.values()]


def _dedupe_courses_for_units(courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for group in _group_courses_for_units(courses):
        representative = max(group, key=lambda item: _course_units(item) or 0.0)
        deduped.append(representative)
    return deduped


def _resolved_credit_for_course_group(course_group: list[dict[str, Any]]) -> float:
    return _resolved_credit_breakdown_for_course_group(course_group)["resolved_credit"]


def _resolved_credit_breakdown_for_course_group(course_group: list[dict[str, Any]]) -> dict[str, Any]:
    unique_values: list[float] = []
    term_credits: dict[str, float] = {}
    has_year_credit = False
    considered_courses: list[dict[str, Any]] = []
    ignored_courses: list[dict[str, Any]] = []
    for course in course_group:
        credit = _to_float(course.get("credit"))
        if credit is None or credit <= 0:
            units = _to_float(course.get("units"))
            if units is None or units <= 0:
                ignored_courses.append(
                    {
                        "course_title": str(course.get("course_title", "")).strip() or "Unnamed",
                        "reason": "missing_credit_and_units",
                    }
                )
                continue
            credit = units * 0.5
        if credit >= 1.0:
            has_year_credit = True
        rounded_credit = round(credit, 3)
        term_key = _extract_term_key(course.get("course_title"))
        considered_courses.append(
            {
                "course_title": str(course.get("course_title", "")).strip() or "Unnamed",
                "credit_used": rounded_credit,
                "term_key": term_key or None,
                "grade": course.get("grade"),
            }
        )
        if term_key:
            term_credits[term_key] = max(term_credits.get(term_key, 0.0), rounded_credit)
        elif rounded_credit not in unique_values:
            unique_values.append(rounded_credit)

    resolved_credit = 0.0
    resolution_strategy = "empty_group"
    if term_credits:
        term_total = round(sum(term_credits.values()), 3)
        if has_year_credit:
            resolved_credit = max(term_total, max(unique_values or [0.0]))
            resolution_strategy = "term_total_capped_by_full_year"
        else:
            resolved_credit = round(term_total + sum(unique_values), 3)
            resolution_strategy = "term_total_plus_unique_non_term"
    elif unique_values:
        if has_year_credit:
            resolved_credit = max(unique_values)
            resolution_strategy = "max_full_year_unique_value"
        else:
            resolved_credit = round(sum(unique_values), 3)
            resolution_strategy = "sum_unique_non_term_values"

    return {
        "resolved_credit": round(resolved_credit, 3),
        "resolution_strategy": resolution_strategy,
        "has_year_credit": has_year_credit,
        "term_credits": term_credits,
        "unique_non_term_credits": unique_values,
        "considered_courses": considered_courses,
        "ignored_courses": ignored_courses,
    }


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

    return {
        "gpa": {
            "unweighted_4_scale": float(unweighted_match.group(1)) if unweighted_match else None,
            "reported_weighted": float(weighted_match.group(1)) if weighted_match else None,
        },
        "course_line_candidates": course_lines,
    }


def _normalize_school_grade(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered == "unknown":
        return "Unknown"

    alias = SCHOOL_GRADE_ALIASES.get(lowered)
    if alias:
        return alias

    match = re.fullmatch(r"(?:grade\s*)?(9|10|11|12)", lowered)
    if match:
        return f"Grade {match.group(1)}"
    return None


def _extract_current_school_grade(extracted_text: str) -> str:
    lowered = extracted_text.lower()
    for token, normalized in SCHOOL_GRADE_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return normalized

    grade_match = re.search(
        r"\b(?:current\s+)?(?:grade|gr)\s*[:\-]?\s*(9|10|11|12)\b",
        lowered,
        flags=re.IGNORECASE,
    )
    if grade_match:
        return f"Grade {grade_match.group(1)}"

    standalone_match = re.search(r"\bgrade\s*(9|10|11|12)\b", lowered, flags=re.IGNORECASE)
    if standalone_match:
        return f"Grade {standalone_match.group(1)}"

    return "Unknown"


def _build_course_debug_rows(courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, course in enumerate(courses, start=1):
        title = str(course.get("course_title", "")).strip()
        raw_subject = course.get("subject")
        subject_bucket = _normalized_subject(raw_subject)
        rows.append(
            {
                "index": index,
                "course_title": title or "Unnamed",
                "normalized_title": _normalize_course_title_for_units(title),
                "raw_subject": raw_subject,
                "subject_bucket": subject_bucket,
                "grade": course.get("grade"),
                "is_non_counted_grade": _is_non_counted_grade(course.get("grade")),
                "credit": _to_float(course.get("credit")),
                "units": _to_float(course.get("units")),
                "resolved_units": _course_units(course),
                "term_key": _extract_term_key(title) or None,
                "dropdown_visible_in_subject": None
                if _is_non_counted_grade(course.get("grade"))
                else subject_bucket,
            }
        )
    return rows


def _build_group_debug_rows(grouped_courses: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for index, group in enumerate(grouped_courses, start=1):
        representative = max(group, key=lambda item: _course_units(item) or 0.0)
        counted_group = [course for course in group if not _is_non_counted_grade(course.get("grade"))]
        breakdown = _resolved_credit_breakdown_for_course_group(counted_group) if counted_group else None
        groups.append(
            {
                "group_index": index,
                "representative_title": str(representative.get("course_title", "")).strip() or "Unnamed",
                "representative_raw_subject": representative.get("subject"),
                "representative_subject_bucket": _normalized_subject(representative.get("subject")),
                "normalized_identity": _course_identity(representative),
                "course_titles": [str(course.get("course_title", "")).strip() or "Unnamed" for course in group],
                "counted_course_titles": [
                    str(course.get("course_title", "")).strip() or "Unnamed" for course in counted_group
                ],
                "non_counted_course_titles": [
                    str(course.get("course_title", "")).strip() or "Unnamed"
                    for course in group
                    if _is_non_counted_grade(course.get("grade"))
                ],
                "resolved_credit": breakdown["resolved_credit"] if breakdown else 0.0,
                "credit_breakdown": breakdown,
            }
        )
    return groups


def _unique_course_credit_key(course: dict[str, Any]) -> str:
    title = str(course.get("course_title", "")).strip().upper()
    subject = _normalized_subject(course.get("subject"))
    grade = str(course.get("grade", "")).strip().upper()
    return f"{title}|{subject}|{grade}"


def analyze_transcript_content(
    filename: str,
    content_type: str,
    data: bytes,
    debug: bool = False,
) -> dict[str, Any]:
    tracer = RequestTracer("transcript_analyze")
    tracer.step("request_received", filename=filename, content_type=content_type, byte_count=len(data))
    if not data:
        tracer.step("validation_failed", reason="empty_upload")
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    docintel_settings = load_azure_document_intelligence_settings()
    tracer.step(
        "docintel_config_loaded",
        enabled=docintel_settings.enabled,
        configured=len(docintel_settings.missing_required) == 0,
        missing_settings=docintel_settings.missing_required,
        model_id=docintel_settings.model_id,
    )
    if not docintel_settings.enabled:
        tracer.step("analysis_failed", stage="text_extraction", error="docintel_disabled")
        raise HTTPException(
            status_code=422,
            detail="Could not parse file: Azure Document Intelligence is disabled. Set AZURE_DOC_INTEL_ENABLED=true.",
        )
    if docintel_settings.missing_required:
        tracer.step("analysis_failed", stage="text_extraction", error="docintel_missing_settings")
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not parse file: Missing Azure Document Intelligence settings: "
                f"{', '.join(docintel_settings.missing_required)}"
            ),
        )

    try:
        extracted_text = extract_text_with_azure_document_intelligence(
            data=data,
            content_type=content_type,
            settings=docintel_settings,
        )
        tracer.step("text_extracted", extracted_characters=len(extracted_text))
    except ValueError as exc:
        message = str(exc)
        tracer.step("analysis_failed", stage="text_extraction", error=message)
        if message.startswith("Unsupported file type"):
            raise HTTPException(status_code=415, detail=message) from exc
        logger.error("Parse failed for %s: %s", filename, message)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {message}") from exc
    except Exception as exc:
        tracer.step("analysis_failed", stage="text_extraction", error=str(exc))
        logger.exception("Unexpected parse failure for %s", filename)
        raise HTTPException(status_code=422, detail=f"Could not parse file: {exc}") from exc

    if not extracted_text:
        tracer.step("analysis_failed", stage="text_extraction", error="empty_extracted_text")
        raise HTTPException(status_code=422, detail="No text could be extracted from this document.")

    settings = load_azure_openai_settings()
    tracer.step(
        "ai_config_loaded",
        enabled=settings.enabled,
        configured=len(settings.missing_required) == 0,
        missing_settings=settings.missing_required,
    )
    ai_result: dict[str, Any] | None = None
    ai_error: str | None = None
    anchors = _extract_pre_anchors(extracted_text)
    tracer.step(
        "pre_extraction_anchors_built",
        candidate_course_line_count=len(anchors.get("course_line_candidates", [])),
        candidate_course_lines_preview=anchors.get("course_line_candidates", [])[:10],
        gpa_anchor=anchors.get("gpa"),
    )
    if settings.enabled:
        try:
            ai_result = analyze_transcript_with_azure_openai(
                extracted_text,
                settings,
                pre_extracted_anchors=anchors,
            )
            tracer.step(
                "ai_structuring_complete",
                returned_course_count=len((ai_result or {}).get("courses", [])),
                returned_grade=(ai_result or {}).get("current_school_grade"),
            )
        except Exception as exc:
            ai_error = str(exc)
            tracer.step("analysis_failed", stage="ai_structuring", error=ai_error)
            logger.exception("Transcript AI analysis failed for filename=%s", filename)

    courses = (ai_result or {}).get("courses", [])
    totals_by_category = {
        "english": 0,
        "mathematics": 0,
        "natural_sciences": 0,
        "social_sciences": 0,
        "foreign_language": 0,
        "other_units": 0,
        "other": 0,
    }
    credits_by_category = {
        "english": 0.0,
        "mathematics": 0.0,
        "natural_sciences": 0.0,
        "social_sciences": 0.0,
        "foreign_language": 0.0,
        "other_units": 0.0,
        "other": 0.0,
    }
    grouped_courses = _group_courses_for_units([c for c in courses if isinstance(c, dict)]) if isinstance(courses, list) else []
    tracer.step(
        "course_grouping_complete",
        raw_course_count=len([c for c in courses if isinstance(c, dict)]) if isinstance(courses, list) else 0,
        grouped_course_count=len(grouped_courses),
    )
    unit_courses = [max(group, key=lambda item: _course_units(item) or 0.0) for group in grouped_courses]
    for course in unit_courses:
        if _is_non_counted_grade(course.get("grade")):
            continue
        subject = _normalized_subject(course.get("subject"))
        totals_by_category[subject] += 1
    seen_credit_keys: set[str] = set()
    counted_credit_courses = [course for course in courses if isinstance(course, dict)] if isinstance(courses, list) else []
    for course in counted_credit_courses:
        if _is_non_counted_grade(course.get("grade")):
            continue
        credit_key = _unique_course_credit_key(course)
        if credit_key in seen_credit_keys:
            continue
        seen_credit_keys.add(credit_key)
        subject = _normalized_subject(course.get("subject"))
        credits_by_category[subject] += 0.5
    gpa = (ai_result or {}).get("gpa", {})
    ai_school_grade = _normalize_school_grade((ai_result or {}).get("current_school_grade"))
    school_grade = ai_school_grade or _extract_current_school_grade(extracted_text)
    warnings = _extraction_warnings(extracted_text)
    if ai_error:
        warnings.append("Course classification timed out or failed; totals may be incomplete.")

    tracer.step(
        "category_totals_resolved",
        totals_by_category=totals_by_category,
        credits_by_category={key: round(value, 3) for key, value in credits_by_category.items()},
        warnings=warnings,
    )
    response = {
        "filename": filename,
        "mime_type": content_type,
        "characters": len(extracted_text),
        "courses": courses if isinstance(courses, list) else [],
        "totals_by_category": totals_by_category,
        "credits_by_category": {key: round(value, 3) for key, value in credits_by_category.items()},
        "unweighted_gpa": _to_float(gpa.get("unweighted_4_scale")),
        "current_school_grade": school_grade,
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
    tracer.step("response_ready", response_fields=list(response.keys()))
    if debug:
        response["debug"] = {
            **tracer.payload(),
            "pre_extracted_anchors": anchors,
            "course_diagnostics": _build_course_debug_rows(
                [course for course in courses if isinstance(course, dict)] if isinstance(courses, list) else []
            ),
            "group_diagnostics": _build_group_debug_rows(grouped_courses),
            "ai_result_summary": {
                "course_count": len([course for course in courses if isinstance(course, dict)])
                if isinstance(courses, list)
                else 0,
                "current_school_grade_raw": (ai_result or {}).get("current_school_grade"),
                "gpa_raw": gpa,
                "ai_error": ai_error,
            },
        }
    return response
