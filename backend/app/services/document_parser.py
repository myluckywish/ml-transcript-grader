from __future__ import annotations

import json
import logging
from typing import Any, Literal, TypedDict

from app.parsers.docx_parser import extract_docx_text
from app.parsers.pdf_parser import extract_pdf_text
from app.parsers.text_parser import extract_text_like

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html"}
logger = logging.getLogger(__name__)


class ParsedContent(TypedDict):
    content_kind: Literal["structured_json", "plain_text"]
    json: dict[str, Any] | list[Any] | None
    text: str
    lines: list[str]
    paragraphs: list[str]


class ParseResult(TypedDict):
    extracted_text: str
    parsed_content: ParsedContent


def _split_paragraphs(text: str) -> list[str]:
    return [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]


def _build_text_result(text: str) -> ParseResult:
    return {
        "extracted_text": text,
        "parsed_content": {
            "content_kind": "plain_text",
            "json": None,
            "text": text,
            "lines": text.splitlines(),
            "paragraphs": _split_paragraphs(text),
        },
    }


def _build_json_result(parsed_json: dict[str, Any] | list[Any]) -> ParseResult:
    pretty_text = json.dumps(parsed_json, indent=2, ensure_ascii=False)
    return {
        "extracted_text": pretty_text,
        "parsed_content": {
            "content_kind": "structured_json",
            "json": parsed_json,
            "text": pretty_text,
            "lines": pretty_text.splitlines(),
            "paragraphs": _split_paragraphs(pretty_text),
        },
    }


def parse_document_bytes(data: bytes, suffix: str, content_type: str) -> ParseResult:
    if suffix == ".pdf" or content_type == "application/pdf":
        logger.debug("Selected parser=pdf suffix=%s content_type=%s bytes=%d", suffix, content_type, len(data))
        return _build_text_result(extract_pdf_text(data))
    if suffix == ".docx":
        logger.debug("Selected parser=docx suffix=%s content_type=%s bytes=%d", suffix, content_type, len(data))
        return _build_text_result(extract_docx_text(data))
    if suffix in TEXT_EXTENSIONS or content_type.startswith("text/"):
        logger.debug(
            "Selected parser=text_like suffix=%s content_type=%s bytes=%d",
            suffix,
            content_type,
            len(data),
        )
        extracted_text = extract_text_like(data, suffix)
        if suffix == ".json" or content_type == "application/json":
            try:
                parsed_json: dict[str, Any] | list[Any] = json.loads(extracted_text)
                return _build_json_result(parsed_json)
            except json.JSONDecodeError:
                logger.debug("JSON parse failed; returning plain text output.")
        return _build_text_result(extracted_text)
    logger.debug("No parser for suffix=%s content_type=%s", suffix, content_type)
    raise ValueError(
        "Unsupported file type. Supported: PDF, DOCX, TXT, MD, CSV, JSON, YAML, XML, HTML."
    )
