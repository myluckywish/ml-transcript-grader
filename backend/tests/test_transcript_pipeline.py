"""Tests for course deduplication and title normalization."""

import pytest

from app.services.transcript_pipeline import (
    _dedupe_courses_for_units,
    _normalize_course_title_for_units,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Semester suffix glued to digit: the main regression case from meeting
        ("Algebra 1A", "ALGEBRA 1"),
        ("Algebra 1B", "ALGEBRA 1"),
        ("Algebra 2A", "ALGEBRA 2"),
        ("Algebra 2B", "ALGEBRA 2"),
        # Space-separated A/B (previously handled)
        ("Algebra 1 A", "ALGEBRA 1"),
        ("Algebra 1 B", "ALGEBRA 1"),
        # Explicit semester markers
        ("Algebra 1 Semester 1", "ALGEBRA 1"),
        ("Algebra 1 Semester 2", "ALGEBRA 1"),
        ("English 10 Sem 1", "ENGLISH 10"),
        ("English 10 Sem 2", "ENGLISH 10"),
        # Season markers
        ("Chemistry Fall", "CHEMISTRY"),
        ("Chemistry Spring", "CHEMISTRY"),
        # AP courses: "AB" is one token (no word boundary mid-token), so it is preserved
        ("AP Calculus AB", "AP CALCULUS AB"),
        ("AP English Language", "AP ENGLISH LANGUAGE"),
        # Plain courses unchanged by the new rule
        ("English I", "ENGLISH I"),
        ("Physics", "PHYSICS"),
        ("US History", "US HISTORY"),
    ],
)
def test_normalize_strips_semester_variants(raw: str, expected: str) -> None:
    assert _normalize_course_title_for_units(raw) == expected


def _course(title: str, units: float = 0.5) -> dict:
    return {"course_title": title, "units": units, "subject": "mathematics", "grade": "A"}


def test_dedupe_algebra_1a_1b() -> None:
    """Algebra 1A + 1B (two 0.5-unit semester entries) should deduplicate to one course."""
    courses = [_course("Algebra 1A", 0.5), _course("Algebra 1B", 0.5)]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 1


def test_dedupe_semester_1_2() -> None:
    """'Chemistry Semester 1' + 'Chemistry Semester 2' should collapse to one entry."""
    courses = [_course("Chemistry Semester 1", 0.5), _course("Chemistry Semester 2", 0.5)]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 1


def test_dedupe_distinct_courses_preserved() -> None:
    """Different math courses must not collapse into each other."""
    courses = [
        _course("Algebra 1", 1.0),
        _course("Algebra 2", 1.0),
        _course("Geometry", 1.0),
    ]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 3


def test_dedupe_keeps_higher_unit_entry() -> None:
    """When the same course appears with different units, keep the higher-unit entry."""
    courses = [_course("English I", 0.5), _course("English I", 1.0)]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 1
    assert result[0]["units"] == 1.0
