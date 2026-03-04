from __future__ import annotations

import io

import pdfplumber


def extract_pdf_text(data: bytes) -> str:
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n\n".join(pages).strip()
