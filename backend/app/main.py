from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.parse import router as parse_router
from app.routes.transcript import router as transcript_router
from app.routes.units import router as units_router
from app.services.course_mapping_store import initialize_store

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Document Parser API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(parse_router)
app.include_router(transcript_router)
app.include_router(units_router)


@app.get("/health")
def health() -> dict[str, str]:
    logger.debug("Health check requested.")
    return {"status": "ok"}

@app.on_event("startup")
def on_startup() -> None:
    initialize_store()
