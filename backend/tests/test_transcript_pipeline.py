"""Tests for course deduplication and title normalization."""

import pytest

from app.services.transcript_pipeline import (
    _build_course_debug_rows,
    _build_group_debug_rows,
    _dedupe_courses_for_units,
    _extract_pre_anchors,
    _extract_structured_courses_from_ocr,
    _group_courses_for_units,
    _merge_ai_and_deterministic_courses,
    _normalize_course_title_for_units,
    _resolved_credit_breakdown_for_course_group,
    _resolved_credit_for_course_group,
    _unique_course_credit_key,
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


def test_resolved_credit_breakdown_reports_resolution_strategy() -> None:
    courses = [
        _course("Geometry S1", 1.0),
        _course("Geometry S2", 1.0),
    ]
    grouped = _group_courses_for_units(courses)
    breakdown = _resolved_credit_breakdown_for_course_group(grouped[0])
    assert breakdown["resolved_credit"] == 1.0
    assert breakdown["resolution_strategy"] == "term_total_capped_by_full_year"
    assert breakdown["term_credits"] == {"S1": 0.5, "S2": 0.5}


def test_course_debug_rows_expose_subject_bucket_and_dropdown_visibility() -> None:
    rows = _build_course_debug_rows(
        [
            {"course_title": "Personalized MathP", "subject": "other", "grade": "A", "credit": 0.5},
            {"course_title": "English 10", "subject": "english", "grade": "F", "credit": 0.5},
        ]
    )
    assert rows[0]["subject_bucket"] == "other"
    assert rows[0]["dropdown_visible_in_subject"] == "other"
    assert rows[1]["is_non_counted_grade"] is True
    assert rows[1]["dropdown_visible_in_subject"] is None


def test_group_debug_rows_include_all_titles_and_credit_breakdown() -> None:
    courses = [
        {"course_title": "MA211MYP IB MYP AlgebraB", "subject": "mathematics", "grade": "A", "credit": 0.5},
        {"course_title": "MA221MYP IB MYP AlgebraA", "subject": "mathematics", "grade": "A", "credit": 0.5},
    ]
    groups = _build_group_debug_rows(_group_courses_for_units(courses))
    assert len(groups) == 1
    assert groups[0]["course_titles"] == [
        "MA211MYP IB MYP AlgebraB",
        "MA221MYP IB MYP AlgebraA",
    ]
    assert groups[0]["credit_breakdown"]["resolved_credit"] == 0.5


def test_unique_course_credit_key_preserves_literal_title_distinction() -> None:
    left = _unique_course_credit_key(
        {"course_title": "MA211MYP IB MYP AlgebraB", "subject": "mathematics", "grade": "A"}
    )
    right = _unique_course_credit_key(
        {"course_title": "MA221MYP IB MYP AlgebraA", "subject": "mathematics", "grade": "A"}
    )
    assert left != right


def test_unique_course_credit_key_dedupes_exact_same_course_row() -> None:
    left = _unique_course_credit_key(
        {"course_title": "IB DP Math Applications", "subject": "mathematics", "grade": "A"}
    )
    right = _unique_course_credit_key(
        {"course_title": "IB DP Math Applications", "subject": "mathematics", "grade": "A"}
    )
    assert left == right


def test_extract_pre_anchors_parses_subject_credit_summary_from_ocr() -> None:
    extracted_text = """
Credit Summary
High School Credit
Attempted Earned
FOREIGN LANGUAGE
3.500
3.500
HEALTH
0.500
0.500
MATH
4.500
4.500
OTHER ELECTIVE
6.875
6.875
PHYSICAL EDUCATION
1.000
1.000
SCIENCE
3.500
3.500
SOCIAL STUDIES
4.000
4.000
"""
    anchors = _extract_pre_anchors(extracted_text)
    assert anchors["subject_credit_summary"]["mathematics"] == {"attempted": 4.5, "earned": 4.5}
    assert anchors["subject_credit_summary"]["foreign_language"] == {"attempted": 3.5, "earned": 3.5}
    assert anchors["subject_credit_summary"]["other_units"] == {"attempted": 6.875, "earned": 6.875}


def test_extract_pre_anchors_stitches_wrapped_course_rows() -> None:
    extracted_text = """
MA211MYP IB MYP ALGEBRA
A 0.5000 0.5 (S1)
MA221MYP IB MYP ALGEBRA (S2)
A 0.5000 0.5
"""
    anchors = _extract_pre_anchors(extracted_text)
    assert anchors["course_line_candidates"] == [
        "MA211MYP IB MYP ALGEBRA A 0.5000 0.5 (S1)",
        "MA221MYP IB MYP ALGEBRA (S2) A 0.5000 0.5",
    ]


def test_extract_structured_courses_from_ocr_recovers_math_rows() -> None:
    extracted_text = """
MA211MYP IB MYP ALGEBRA
A 0.5000 0.5 (S1)
MA221MYP IB MYP ALGEBRA (S2)
A 0.5000 0.5
MA501MYP IB MYP ALGEBRA 2
& TRIG(S1)
A 0.5000 0.5
MA511MYP IB MYP ALGEBRA 2
& TRIG(S2)
A 0.5000 0.5
MA842DPWGPA IB DP MATH B
APPLICATIONS (S1)
A 0.5000 0.5
MA843DPWGPA IB DP MATH B
APPLICATIONS (S2)
A 0.5000 0.5
"""
    courses = _extract_structured_courses_from_ocr(extracted_text)
    assert [course["course_title"] for course in courses] == [
        "IB MYP ALGEBRA (S1)",
        "IB MYP ALGEBRA (S2)",
        "IB MYP ALGEBRA 2 & TRIG(S1)",
        "IB MYP ALGEBRA 2 & TRIG(S2)",
        "IB DP MATH B APPLICATIONS (S1)",
        "IB DP MATH B APPLICATIONS (S2)",
    ]
    assert all(course["subject"] == "mathematics" for course in courses)
    assert all(course["credit"] == 0.5 for course in courses)


def test_merge_ai_and_deterministic_courses_adds_missing_semesters() -> None:
    ai_courses = [
        {"course_title": "IB MYP Geometry", "subject": "mathematics", "grade": "A", "credit": 0.5},
        {"course_title": "IB DP Math Applications", "subject": "mathematics", "grade": "B", "credit": 0.5},
    ]
    deterministic_courses = [
        {"course_title": "IB MYP Geometry", "subject": "mathematics", "grade": "A", "credit": 0.5},
        {"course_title": "IB MYP Algebra", "subject": "mathematics", "grade": "A", "credit": 0.5},
        {"course_title": "IB MYP Algebra 2 & Trig", "subject": "mathematics", "grade": "A", "credit": 0.5},
        {"course_title": "IB DP Math Applications (S3)", "subject": "mathematics", "grade": "A", "credit": 0.5},
    ]
    merged = _merge_ai_and_deterministic_courses(ai_courses, deterministic_courses)
    assert [course["course_title"] for course in merged] == [
        "IB MYP Geometry",
        "IB DP Math Applications",
        "IB MYP Algebra",
        "IB MYP Algebra 2 & Trig",
        "IB DP Math Applications (S3)",
    ]
