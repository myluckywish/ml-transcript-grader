from __future__ import annotations

import json


def extract_text_like(data: bytes, suffix: str) -> str:
    decoded = data.decode("utf-8", errors="replace")
    if suffix == ".json":
        try:
            obj = json.loads(decoded)
            return json.dumps(obj, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return decoded.strip()
