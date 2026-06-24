from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.settings import AzureDocumentIntelligenceSettings


def _join_endpoint(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload) if payload else {}
    return parsed if isinstance(parsed, dict) else {}


def _flatten_content(result_payload: dict[str, Any]) -> str:
    pages = result_payload.get("analyzeResult", {}).get("pages", [])
    lines: list[str] = []
    for page in pages:
        for line in page.get("lines", []):
            text = line.get("content")
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
    if lines:
        return "\n".join(lines)

    content = result_payload.get("analyzeResult", {}).get("content")
    if isinstance(content, str) and content.strip():
        return content
    return ""


def _build_structured_content(result_payload: dict[str, Any]) -> dict[str, Any]:
    analyze_result = result_payload.get("analyzeResult", {})
    pages_payload = analyze_result.get("pages", [])
    tables_payload = analyze_result.get("tables", [])

    pages: list[dict[str, Any]] = []
    for page in pages_payload:
        page_number = page.get("pageNumber")
        width = page.get("width")
        height = page.get("height")
        unit = page.get("unit")
        lines: list[dict[str, Any]] = []
        for line in page.get("lines", []):
            text = line.get("content")
            if not isinstance(text, str) or not text.strip():
                continue
            lines.append(
                {
                    "text": text.strip(),
                    "polygon": line.get("polygon"),
                    "spans": line.get("spans"),
                }
            )
        pages.append(
            {
                "page_number": page_number,
                "width": width,
                "height": height,
                "unit": unit,
                "lines": lines,
            }
        )

    tables: list[dict[str, Any]] = []
    for idx, table in enumerate(tables_payload, start=1):
        cells_payload = table.get("cells", [])
        cells: list[dict[str, Any]] = []
        for cell in cells_payload:
            text = cell.get("content")
            if not isinstance(text, str):
                continue
            cells.append(
                {
                    "row_index": cell.get("rowIndex"),
                    "column_index": cell.get("columnIndex"),
                    "row_span": cell.get("rowSpan", 1),
                    "column_span": cell.get("columnSpan", 1),
                    "kind": cell.get("kind"),
                    "text": text.strip(),
                }
            )
        tables.append(
            {
                "table_index": idx,
                "row_count": table.get("rowCount"),
                "column_count": table.get("columnCount"),
                "cells": cells,
            }
        )

    return {
        "text": _flatten_content(result_payload),
        "pages": pages,
        "tables": tables,
    }


def _extract_result_payload(
    data: bytes,
    content_type: str,
    settings: AzureDocumentIntelligenceSettings,
) -> dict[str, Any]:
    if not settings.enabled:
        raise ValueError("Azure Document Intelligence is disabled.")
    if settings.missing_required:
        raise ValueError(f"Missing Azure Document Intelligence settings: {', '.join(settings.missing_required)}")

    api_version = urllib.parse.quote(settings.api_version, safe="")
    model_id = urllib.parse.quote(settings.model_id, safe="")
    analyze_path = f"documentintelligence/documentModels/{model_id}:analyze?api-version={api_version}"
    analyze_url = _join_endpoint(settings.endpoint, analyze_path)

    headers = {
        "Ocp-Apim-Subscription-Key": settings.api_key,
        "Content-Type": content_type or "application/octet-stream",
    }
    request = urllib.request.Request(analyze_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status_code = response.getcode()
            operation_location = response.headers.get("Operation-Location")
            immediate_payload_raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise ValueError(f"Document Intelligence request failed: {exc.code} {detail}") from exc

    if status_code == 200 and immediate_payload_raw:
        immediate_payload = json.loads(immediate_payload_raw)
        extracted = _flatten_content(immediate_payload)
        if extracted.strip():
            return immediate_payload if isinstance(immediate_payload, dict) else {}

    if not operation_location:
        raise ValueError("Document Intelligence did not return an operation URL.")

    poll_headers = {"Ocp-Apim-Subscription-Key": settings.api_key}
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        if elapsed > settings.timeout_seconds:
            raise ValueError("Document Intelligence timed out while waiting for analysis.")

        payload = _request_json(operation_location, poll_headers)
        status = str(payload.get("status", "")).lower()
        if status == "succeeded":
            extracted = _flatten_content(payload)
            if extracted.strip():
                return payload
            raise ValueError("Document Intelligence returned no readable text.")
        if status in {"failed", "canceled"}:
            message = payload.get("error", {}).get("message", "analysis failed")
            raise ValueError(f"Document Intelligence analysis failed: {message}")

        time.sleep(settings.poll_interval_seconds)


def extract_document_with_azure_document_intelligence(
    data: bytes,
    content_type: str,
    settings: AzureDocumentIntelligenceSettings,
) -> dict[str, Any]:
    payload = _extract_result_payload(data=data, content_type=content_type, settings=settings)
    return _build_structured_content(payload)


def extract_text_with_azure_document_intelligence(
    data: bytes,
    content_type: str,
    settings: AzureDocumentIntelligenceSettings,
) -> str:
    document = extract_document_with_azure_document_intelligence(
        data=data,
        content_type=content_type,
        settings=settings,
    )
    return str(document.get("text", "")).strip()
