"""Tests for course deduplication and title normalization."""

import pytest

from app.services.transcript_pipeline import (
    _dedupe_courses_for_units,
    _group_courses_for_units,
    _normalize_course_title_for_units,
    _resolved_credit_for_course_group,
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
        # Roman numerals normalize to course numbers for stronger matching
        ("English I", "ENGLISH 1"),
        ("English II", "ENGLISH 2"),
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


def test_dedupe_roman_and_arabic_numerals() -> None:
    courses = [_course("English I", 0.5), _course("English 1", 0.5)]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 1


def test_dedupe_abbreviation_and_rigor_variants() -> None:
    courses = [_course("Eng 1 H", 0.5), _course("Honors English I", 0.5)]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 1


def test_dedupe_fuzzy_ocr_variant() -> None:
    courses = [_course("Biology", 0.5), _course("Bi0logy", 0.5)]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 1


def test_dedupe_does_not_collapse_different_rigor() -> None:
    courses = [_course("Biology", 1.0), _course("AP Biology", 1.0)]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 2


def test_dedupe_does_not_collapse_different_levels() -> None:
    courses = [_course("English 9", 1.0), _course("English 10", 1.0)]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 2


def test_dedupe_ignores_school_course_codes_across_terms() -> None:
    courses = [
        _course("MA301 GEOMETRY 9 (S1)B", 0.5),
        _course("MA311 GEOMETRY 9 (S2)", 0.5),
    ]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 1


def test_dedupe_ignores_generalized_department_codes() -> None:
    courses = [
        _course("EN211 English 10 A", 0.5),
        _course("EN221 English 10 B", 0.5),
    ]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 1


def test_dedupe_preserves_level_even_with_course_codes() -> None:
    courses = [
        _course("EN211 English 9", 1.0),
        _course("EN221 English 10", 1.0),
    ]
    result = _dedupe_courses_for_units(courses)
    assert len(result) == 2


def test_resolved_credit_for_two_completed_semesters() -> None:
    courses = [
        _course("Geometry S1", 1.0),
        _course("Geometry S2", 1.0),
    ]
    grouped = _group_courses_for_units(courses)
    assert len(grouped) == 1
    assert _resolved_credit_for_course_group(grouped[0]) == 1.0


def test_resolved_credit_for_partial_completed_course() -> None:
    courses = [_course("Geometry S1", 1.0)]
    grouped = _group_courses_for_units(courses)
    assert len(grouped) == 1
    assert _resolved_credit_for_course_group(grouped[0]) == 0.5


def test_resolved_credit_does_not_double_count_duplicate_full_year_rows() -> None:
    courses = [
        _course("English 10", 2.0),
        _course("English 10", 2.0),
    ]
    grouped = _group_courses_for_units(courses)
    assert len(grouped) == 1
    assert _resolved_credit_for_course_group(grouped[0]) == 1.0


def test_resolved_credit_sums_distinct_partial_values_in_same_group() -> None:
    courses = [
        _course("Biology A", 1.0),
        {"course_title": "Biology B", "credit": 0.25, "subject": "mathematics", "grade": "A"},
    ]
    grouped = _group_courses_for_units(courses)
    assert len(grouped) == 1
    assert _resolved_credit_for_course_group(grouped[0]) == 0.75
