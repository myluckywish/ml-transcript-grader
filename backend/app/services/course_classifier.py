from __future__ import annotations

import logging
import re
from typing import Any, TypedDict

from app.services.course_ai_classifier import classify_titles_with_azure_openai
from app.services.course_mapping_store import find_course_mapping, queue_unknown_title
from app.services.course_taxonomy import CANONICAL_SUBJECTS, guess_subject_from_rules, normalize_course_title
from app.settings import load_azure_openai_settings

logger = logging.getLogger(__name__)
NON_COURSE_PATTERNS = [
    re.compile(r"\b(freshman|sophomore|sophmore|junior|senior)\b", re.IGNORECASE),
    re.compile(r"\b(academic achievement|academic achievements|honor roll|awards?)\b", re.IGNORECASE),
    re.compile(r"\b(address|street|st\.|avenue|ave\.|city|state|zip)\b", re.IGNORECASE),
    re.compile(r"\b(student id|id number|date of birth|dob)\b", re.IGNORECASE),
    re.compile(r"\b(counselor|principal|registrar|signature)\b", re.IGNORECASE),
]
NON_COURSE_EXACT = {
    "english",
    "math",
    "mathematics",
    "science",
    "social studies",
    "social science",
}


class ClassifiedCourse(TypedDict):
    raw_title: str
    normalized_title: str
    subject: str | None
    method: str
    confidence: float
    mapping_id: int | None
    unknown_queue_id: int | None
    subject_probabilities: dict[str, float]


def _one_hot(subject: str) -> dict[str, float]:
    return {key: 1.0 if key == subject else 0.0 for key in CANONICAL_SUBJECTS}


def _top_subject(probabilities: dict[str, float]) -> tuple[str, float]:
    best_subject = CANONICAL_SUBJECTS[0]
    best_score = float(probabilities.get(best_subject, 0.0))
    for subject in CANONICAL_SUBJECTS[1:]:
        score = float(probabilities.get(subject, 0.0))
        if score > best_score:
            best_subject = subject
            best_score = score
    return best_subject, best_score


def _is_non_course_title(normalized_title: str) -> bool:
    if not normalized_title:
        return True
    title_lower = normalized_title.lower()
    if title_lower in NON_COURSE_EXACT:
        return True
    if re.fullmatch(r"\d{4}", normalized_title):
        return True
    for pattern in NON_COURSE_PATTERNS:
        if pattern.search(normalized_title):
            return True
    return False


def classify_course_title(
    raw_title: str,
    school_id: str = "",
    ai_probabilities_by_title: dict[str, dict[str, float]] | None = None,
) -> ClassifiedCourse:
    normalized = normalize_course_title(raw_title)
    if not normalized:
        unknown = queue_unknown_title(raw_title=raw_title, normalized_title=normalized, school_id=school_id)
        return ClassifiedCourse(
            raw_title=raw_title,
            normalized_title=normalized,
            subject=None,
            method="unknown_queued",
            confidence=0.0,
            mapping_id=None,
            unknown_queue_id=unknown.get("id"),
            subject_probabilities={subject: 0.0 for subject in CANONICAL_SUBJECTS},
        )
    if _is_non_course_title(normalized):
        return ClassifiedCourse(
            raw_title=raw_title,
            normalized_title=normalized,
            subject=None,
            method="non_course_filtered",
            confidence=0.0,
            mapping_id=None,
            unknown_queue_id=None,
            subject_probabilities={subject: 0.0 for subject in CANONICAL_SUBJECTS},
        )

    mapping = find_course_mapping(normalized_title=normalized, school_id=school_id)
    if mapping is not None:
        mapped_subject = str(mapping.get("subject"))
        return ClassifiedCourse(
            raw_title=raw_title,
            normalized_title=normalized,
            subject=mapped_subject,
            method="mapping_lookup",
            confidence=float(mapping.get("confidence", 1.0)),
            mapping_id=mapping.get("id"),
            unknown_queue_id=None,
            subject_probabilities=_one_hot(mapped_subject),
        )

    if ai_probabilities_by_title is not None:
        ai_probabilities = ai_probabilities_by_title.get(normalized)
        if ai_probabilities:
            best_subject, best_score = _top_subject(ai_probabilities)
            return ClassifiedCourse(
                raw_title=raw_title,
                normalized_title=normalized,
                subject=best_subject,
                method="ai_probabilities",
                confidence=best_score,
                mapping_id=None,
                unknown_queue_id=None,
                subject_probabilities=ai_probabilities,
            )

    rules_subject = guess_subject_from_rules(normalized)
    rules_confidence = 0.65
    if rules_subject is not None:
        return ClassifiedCourse(
            raw_title=raw_title,
            normalized_title=normalized,
            subject=rules_subject,
            method="rules",
            confidence=rules_confidence,
            mapping_id=None,
            unknown_queue_id=None,
            subject_probabilities=_one_hot(rules_subject),
        )

    unknown = queue_unknown_title(raw_title=raw_title, normalized_title=normalized, school_id=school_id)
    return ClassifiedCourse(
        raw_title=raw_title,
        normalized_title=normalized,
        subject=None,
        method="unknown_queued",
        confidence=0.0,
        mapping_id=None,
        unknown_queue_id=unknown.get("id"),
        subject_probabilities={subject: 0.0 for subject in CANONICAL_SUBJECTS},
    )


def classify_course_titles(raw_titles: list[str], school_id: str = "") -> dict[str, Any]:
    settings = load_azure_openai_settings()
    ai_probabilities_by_title: dict[str, dict[str, float]] | None = None
    ai_error: str | None = None
    if settings.enabled and not settings.missing_required:
        try:
            ai_probabilities_by_title = classify_titles_with_azure_openai(raw_titles=raw_titles, settings=settings)
        except Exception as exc:
            ai_error = str(exc)
            logger.exception("AI course classification failed")

    results = [
        classify_course_title(
            raw_title=title,
            school_id=school_id,
            ai_probabilities_by_title=ai_probabilities_by_title,
        )
        for title in raw_titles
    ]

    counts: dict[str, float] = {}
    unresolved: list[ClassifiedCourse] = []
    for result in results:
        subject = result.get("subject")
        if subject is None:
            unresolved.append(result)
            continue
        counts[subject] = counts.get(subject, 0.0) + 1.0

    return {
        "school_id": school_id,
        "classified_courses": results,
        "unit_counts": counts,
        "unknown_count": len(unresolved),
        "ai_provider": {
            "name": "azure_openai",
            "enabled": settings.enabled,
            "configured": len(settings.missing_required) == 0,
            "missing_settings": settings.missing_required,
            "ai_error": ai_error,
        },
    }
