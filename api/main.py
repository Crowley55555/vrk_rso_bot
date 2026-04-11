from __future__ import annotations

import logging
import sys

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.config import load_settings
from api.routers.sheets import router as sheets_router
from api.services.sheets_service import GoogleSheetsService


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger(__name__)
    try:
        settings = load_settings()
    except Exception as e:
        log.exception("Ошибка загрузки настроек API: %s", e)
        raise

    app.state.api_key = settings.api_key
    app.state.sheets_service = GoogleSheetsService(settings)
    log.info("API Google Sheets инициализирован.")
    yield
    log.info("Остановка API.")


configure_logging()

app = FastAPI(
    title="VRK RSO Sheets API",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(sheets_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
