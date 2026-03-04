from __future__ import annotations

import logging

from app.parsers.docx_parser import extract_docx_text
from app.parsers.pdf_parser import extract_pdf_text
from app.parsers.text_parser import extract_text_like

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html"}
logger = logging.getLogger(__name__)


def parse_document_bytes(data: bytes, suffix: str, content_type: str) -> str:
    if suffix == ".pdf" or content_type == "application/pdf":
        logger.debug("Selected parser=pdf suffix=%s content_type=%s bytes=%d", suffix, content_type, len(data))
        return extract_pdf_text(data)
    if suffix == ".docx":
        logger.debug("Selected parser=docx suffix=%s content_type=%s bytes=%d", suffix, content_type, len(data))
        return extract_docx_text(data)
    if suffix in TEXT_EXTENSIONS or content_type.startswith("text/"):
        logger.debug(
            "Selected parser=text_like suffix=%s content_type=%s bytes=%d",
            suffix,
            content_type,
            len(data),
        )
        return extract_text_like(data, suffix)
    logger.debug("No parser for suffix=%s content_type=%s", suffix, content_type)
    raise ValueError(
        "Unsupported file type. Supported: PDF, DOCX, TXT, MD, CSV, JSON, YAML, XML, HTML."
    )
