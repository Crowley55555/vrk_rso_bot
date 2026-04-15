from __future__ import annotations

import logging
import sys

from contextlib import asynccontextmanager

from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
    scheduler = AsyncIOScheduler()
    try:
        settings = load_settings()
    except Exception as e:
        log.exception("Ошибка загрузки настроек API: %s", e)
        raise

    app.state.api_key = settings.api_key
    app.state.sheets_service = GoogleSheetsService(settings)
    app.state.scheduler = scheduler

    if settings.yandex_disk_token:
        scheduler.add_job(
            app.state.sheets_service.check_disk_changes,
            trigger="interval",
            seconds=settings.disk_check_interval,
            id="check_disk_changes",
            replace_existing=True,
        )
        scheduler.add_job(
            app.state.sheets_service.upload_to_yadisk,
            trigger="interval",
            seconds=settings.yadisk_upload_interval,
            id="upload_to_yadisk_backup",
            replace_existing=True,
        )
        scheduler.start()
        log.info("Планировщик синхронизации с Яндекс Диском запущен.")
    else:
        log.warning(
            "YANDEX_DISK_TOKEN не задан: синхронизация с Яндекс Диском отключена, "
            "работаем только с SQLite и локальным xlsx."
        )

    log.info("API сервиса задач инициализирован.")
    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await app.state.sheets_service.close()
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
