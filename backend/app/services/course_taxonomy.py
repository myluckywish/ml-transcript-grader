from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

CANONICAL_SUBJECTS: Final[list[str]] = [
    "english",
    "mathematics",
    "natural_sciences",
    "social_sciences",
    "other_units",
]

SUBJECT_ALIASES: Final[dict[str, str]] = {
    "english": "english",
    "mathematics": "mathematics",
    "math": "mathematics",
    "natural_sciences": "natural_sciences",
    "science": "natural_sciences",
    "social_sciences": "social_sciences",
    "social_studies": "social_sciences",
    "other_units": "other_units",
    "electives": "other_units",
    "foreign_language": "other_units",
}


@dataclass(frozen=True)
class SubjectRule:
    subject: str
    pattern: re.Pattern[str]


SUBJECT_RULES: Final[list[SubjectRule]] = [
    SubjectRule("mathematics", re.compile(r"\b(MATH|ALGEBRA|GEOMETRY|CALCULUS|TRIGONOMETRY|STATISTICS|PRECALCULUS)\b")),
    SubjectRule("natural_sciences", re.compile(r"\b(SCIENCE|BIOLOGY|CHEMISTRY|PHYSICS|ANATOMY|ENVIRONMENTAL)\b")),
    SubjectRule("english", re.compile(r"\b(ENGLISH|LANGUAGE ARTS|LITERATURE|COMPOSITION|RHETORIC)\b")),
    SubjectRule("social_sciences", re.compile(r"\b(HISTORY|GOVERNMENT|CIVICS|GEOGRAPHY|ECONOMICS|SOCIOLOGY|PSYCHOLOGY)\b")),
    SubjectRule("other_units", re.compile(r"\b(SPANISH|FRENCH|GERMAN|LATIN|MANDARIN|CHINESE|JAPANESE|ARABIC)\b")),
    SubjectRule("other_units", re.compile(r"\b(ART|MUSIC|BAND|CHOIR|DRAMA|THEATER|BUSINESS|COMPUTER SCIENCE|ENGINEERING|HEALTH|PE)\b")),
]


ABBREVIATION_REPLACEMENTS: Final[dict[str, str]] = {
    "ALG": "ALGEBRA",
    "GEO": "GEOMETRY",
    "TRIG": "TRIGONOMETRY",
    "CALC": "CALCULUS",
    "STAT": "STATISTICS",
    "BIO": "BIOLOGY",
    "CHEM": "CHEMISTRY",
    "PHYS": "PHYSICS",
    "ENG": "ENGLISH",
    "LIT": "LITERATURE",
    "GOVT": "GOVERNMENT",
    "ECON": "ECONOMICS",
}

LEVEL_TOKENS: Final[set[str]] = {
    "AP",
    "IB",
    "HON",
    "HONORS",
    "H",
    "ADV",
    "ADVANCED",
    "REGENTS",
}


def normalize_course_title(raw_title: str) -> str:
    title = raw_title.upper().strip()
    title = title.replace("&", " AND ")
    title = re.sub(r"[^A-Z0-9\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()

    tokens = title.split(" ")
    normalized_tokens: list[str] = []
    for token in tokens:
        replaced = ABBREVIATION_REPLACEMENTS.get(token, token)
        if replaced in LEVEL_TOKENS:
            continue
        normalized_tokens.append(replaced)

    return " ".join(normalized_tokens)


def guess_subject_from_rules(normalized_title: str) -> str | None:
    for rule in SUBJECT_RULES:
        if rule.pattern.search(normalized_title):
            return rule.subject
    return None


def canonicalize_subject(subject: str) -> str | None:
    normalized = subject.strip().lower()
    return SUBJECT_ALIASES.get(normalized)


def seed_mappings() -> list[tuple[str, str]]:
    return [
        ("ENGLISH 9", "english"),
        ("ENGLISH 10", "english"),
        ("ENGLISH 11", "english"),
        ("ENGLISH 12", "english"),
        ("ALGEBRA 1", "mathematics"),
        ("ALGEBRA 2", "mathematics"),
        ("GEOMETRY", "mathematics"),
        ("PRECALCULUS", "mathematics"),
        ("CALCULUS", "mathematics"),
        ("BIOLOGY", "natural_sciences"),
        ("CHEMISTRY", "natural_sciences"),
        ("PHYSICS", "natural_sciences"),
        ("WORLD HISTORY", "social_sciences"),
        ("US HISTORY", "social_sciences"),
        ("CIVICS", "social_sciences"),
        ("ECONOMICS", "social_sciences"),
        ("SPANISH 1", "other_units"),
        ("SPANISH 2", "other_units"),
        ("FRENCH 1", "other_units"),
        ("ART", "other_units"),
        ("MUSIC", "other_units"),
        ("COMPUTER SCIENCE", "other_units"),
        ("PHYSICAL EDUCATION", "other_units"),
    ]
