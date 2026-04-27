from __future__ import annotations

import json
from typing import TypedDict

from app.services.course_taxonomy import CANONICAL_SUBJECTS, normalize_course_title
from app.settings import AzureOpenAISettings


class CourseProbabilities(TypedDict):
    raw_title: str
    normalized_title: str
    probabilities: dict[str, float]


def _system_prompt() -> str:
    return (
        "You are an academic transcript course classifier. "
        "Return only JSON. No markdown."
    )


def _user_prompt(raw_titles: list[str]) -> str:
    subjects = ", ".join(CANONICAL_SUBJECTS)
    titles = "\n".join(f"- {title}" for title in raw_titles)
    return (
        "Classify each course title into subject probabilities.\n"
        f"Allowed subjects: {subjects}\n"
        "Return JSON with this exact shape:\n"
        "{\n"
        '  "courses": [\n'
        "    {\n"
        '      "raw_title": string,\n'
        '      "probabilities": {\n'
        '        "english": number,\n'
        '        "mathematics": number,\n'
        '        "natural_sciences": number,\n'
        '        "social_sciences": number,\n'
        '        "other_units": number\n'
        "      }\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- every probability must be between 0 and 1\n"
        "- probabilities should sum to 1\n"
        "- if uncertain, spread probability rather than forcing one class\n\n"
        f"Course titles:\n{titles}"
    )


def _normalize_probabilities(probabilities: dict[str, float]) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for subject in CANONICAL_SUBJECTS:
        value = probabilities.get(subject, 0.0)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        cleaned[subject] = max(0.0, min(1.0, numeric))

    total = sum(cleaned.values())
    if total <= 0:
        uniform = 1.0 / float(len(CANONICAL_SUBJECTS))
        return {subject: uniform for subject in CANONICAL_SUBJECTS}
    return {subject: cleaned[subject] / total for subject in CANONICAL_SUBJECTS}


def classify_titles_with_azure_openai(
    raw_titles: list[str],
    settings: AzureOpenAISettings,
) -> dict[str, dict[str, float]]:
    if not settings.enabled:
        raise ValueError("Azure OpenAI is disabled.")
    if settings.missing_required:
        raise ValueError(f"Missing Azure OpenAI settings: {', '.join(settings.missing_required)}")

    try:
        from openai import AzureOpenAI
    except Exception as exc:
        raise ValueError("OpenAI SDK is not installed. Run pip install -r backend/requirements.txt.") from exc

    client = AzureOpenAI(
        api_key=settings.api_key,
        api_version=settings.api_version,
        azure_endpoint=settings.endpoint,
    )

    completion = client.chat.completions.create(
        model=settings.deployment,
        temperature=settings.temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(raw_titles)},
        ],
    )

    content = completion.choices[0].message.content or "{}"
    payload = json.loads(content)
    courses = payload.get("courses", [])
    by_normalized_title: dict[str, dict[str, float]] = {}
    for item in courses:
        raw_title = str(item.get("raw_title", "")).strip()
        if not raw_title:
            continue
        normalized_title = normalize_course_title(raw_title)
        probabilities = _normalize_probabilities(item.get("probabilities", {}))
        by_normalized_title[normalized_title] = probabilities
    return by_normalized_title
