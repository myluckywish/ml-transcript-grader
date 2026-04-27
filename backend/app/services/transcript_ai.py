from __future__ import annotations

import json
from typing import Any, TypedDict

from app.settings import AzureOpenAISettings


class TranscriptAIResult(TypedDict):
    required_units: dict[str, Any]
    gpa: dict[str, Any]
    confidence: float | None
    notes: list[str]


def _system_prompt() -> str:
    return (
        "You are a transcript evaluation assistant. "
        "Return only JSON. No markdown. "
        "Extract required academic units and normalize weighted GPA to unweighted 4.0 scale."
    )


def _user_prompt(extracted_text: str) -> str:
    return (
        "Analyze the transcript text and output JSON with this exact shape:\n"
        "{\n"
        '  "required_units": {\n'
        '    "english": number_or_null,\n'
        '    "mathematics": number_or_null,\n'
        '    "natural_sciences": number_or_null,\n'
        '    "social_sciences": number_or_null,\n'
        '    "other_units": number_or_null,\n'
        '    "total": number_or_null\n'
        "  },\n"
        '  "gpa": {\n'
        '    "reported_weighted": number_or_null,\n'
        '    "unweighted_4_scale": number_or_null,\n'
        '    "method": string,\n'
        '    "scale_detected": string_or_null\n'
        "  },\n"
        '  "confidence": number_between_0_and_1_or_null,\n'
        '  "notes": [string]\n'
        "}\n\n"
        "If data is missing, use null and explain briefly in notes.\n\n"
        f"Transcript text:\n{extracted_text}"
    )


def analyze_transcript_with_azure_openai(
    extracted_text: str,
    settings: AzureOpenAISettings,
) -> TranscriptAIResult:
    if not settings.enabled:
        raise ValueError("Azure OpenAI is disabled.")

    missing = settings.missing_required
    if missing:
        raise ValueError(f"Missing Azure OpenAI settings: {', '.join(missing)}")

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
            {"role": "user", "content": _user_prompt(extracted_text)},
        ],
    )

    content = completion.choices[0].message.content or "{}"
    payload = json.loads(content)
    return TranscriptAIResult(
        required_units=payload.get("required_units", {}),
        gpa=payload.get("gpa", {}),
        confidence=payload.get("confidence"),
        notes=payload.get("notes", []),
    )
