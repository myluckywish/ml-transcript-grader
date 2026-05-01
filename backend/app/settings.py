from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency fallback
    load_dotenv = None

if load_dotenv is not None:
    backend_dir = Path(__file__).resolve().parents[1]
    project_root_dir = Path(__file__).resolve().parents[2]
    load_dotenv(backend_dir / ".env", override=False)
    load_dotenv(project_root_dir / ".env", override=False)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AzureOpenAISettings:
    enabled: bool
    endpoint: str
    api_key: str
    api_version: str
    deployment: str
    temperature: float
    request_timeout_seconds: float

    @property
    def missing_required(self) -> list[str]:
        missing: list[str] = []
        if not self.endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not self.api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        if not self.api_version:
            missing.append("AZURE_OPENAI_API_VERSION")
        if not self.deployment:
            missing.append("AZURE_OPENAI_DEPLOYMENT")
        return missing


@dataclass(frozen=True)
class AzureDocumentIntelligenceSettings:
    enabled: bool
    endpoint: str
    api_key: str
    api_version: str
    model_id: str
    poll_interval_seconds: float
    timeout_seconds: float

    @property
    def missing_required(self) -> list[str]:
        missing: list[str] = []
        if not self.endpoint:
            missing.append("AZURE_DOC_INTEL_ENDPOINT")
        if not self.api_key:
            missing.append("AZURE_DOC_INTEL_API_KEY")
        if not self.api_version:
            missing.append("AZURE_DOC_INTEL_API_VERSION")
        if not self.model_id:
            missing.append("AZURE_DOC_INTEL_MODEL_ID")
        return missing


def load_azure_openai_settings() -> AzureOpenAISettings:
    temperature_raw = os.getenv("AZURE_OPENAI_TEMPERATURE", "0")
    timeout_raw = os.getenv("AZURE_OPENAI_TIMEOUT_SECONDS", "45")
    try:
        temperature = float(temperature_raw)
    except ValueError:
        temperature = 0.0
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        timeout_seconds = 45.0

    return AzureOpenAISettings(
        enabled=_env_bool("AZURE_OPENAI_ENABLED", default=False),
        endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "").strip(),
        api_key=os.getenv("AZURE_OPENAI_API_KEY", "").strip(),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21").strip(),
        deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip(),
        temperature=temperature,
        request_timeout_seconds=max(5.0, timeout_seconds),
    )


def load_azure_document_intelligence_settings() -> AzureDocumentIntelligenceSettings:
    poll_interval_raw = os.getenv("AZURE_DOC_INTEL_POLL_INTERVAL_SECONDS", "1.0")
    timeout_raw = os.getenv("AZURE_DOC_INTEL_TIMEOUT_SECONDS", "45")
    try:
        poll_interval = float(poll_interval_raw)
    except ValueError:
        poll_interval = 1.0
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        timeout_seconds = 45.0

    return AzureDocumentIntelligenceSettings(
        enabled=_env_bool("AZURE_DOC_INTEL_ENABLED", default=False),
        endpoint=os.getenv("AZURE_DOC_INTEL_ENDPOINT", "").strip(),
        api_key=os.getenv("AZURE_DOC_INTEL_API_KEY", "").strip(),
        api_version=os.getenv("AZURE_DOC_INTEL_API_VERSION", "2024-11-30").strip(),
        model_id=os.getenv("AZURE_DOC_INTEL_MODEL_ID", "prebuilt-read").strip(),
        poll_interval_seconds=max(0.2, poll_interval),
        timeout_seconds=max(5.0, timeout_seconds),
    )
