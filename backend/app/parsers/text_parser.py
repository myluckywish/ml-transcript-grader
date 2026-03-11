from __future__ import annotations


def extract_text_like(data: bytes, suffix: str) -> str:
    _ = suffix
    return data.decode("utf-8", errors="replace").strip()
