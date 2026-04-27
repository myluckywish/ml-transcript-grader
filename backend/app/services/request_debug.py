from __future__ import annotations

import logging
import time
import uuid
from typing import Any, TypedDict


logger = logging.getLogger(__name__)


class DebugStep(TypedDict):
    step: int
    label: str
    elapsed_ms: int
    meta: dict[str, Any]


class RequestTracer:
    def __init__(self, flow: str) -> None:
        self.flow = flow
        self.request_id = str(uuid.uuid4())
        self._start = time.perf_counter()
        self._steps: list[DebugStep] = []

    def step(self, label: str, **meta: Any) -> None:
        elapsed = int((time.perf_counter() - self._start) * 1000)
        entry: DebugStep = {
            "step": len(self._steps) + 1,
            "label": label,
            "elapsed_ms": elapsed,
            "meta": meta,
        }
        self._steps.append(entry)
        logger.debug("[%s][%s] step=%d label=%s meta=%s", self.flow, self.request_id, entry["step"], label, meta)

    def payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "flow": self.flow,
            "steps": self._steps,
            "total_elapsed_ms": int((time.perf_counter() - self._start) * 1000),
        }

