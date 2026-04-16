from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


NOT_STARTED_SHEET = "Не начатые"
IN_PROGRESS_SHEET = "В работе"
COMPLETED_SHEET = "Выполненные"
ACCIDENTS_SHEET = "Аварии"
LOG_SHEET = "Лог"

APP_TIMEZONE = timezone(timedelta(hours=3))


@dataclass(frozen=True, slots=True)
class Settings:
    """Настройки API-сервиса хранения задач."""

    api_key: str
    sqlite_path: Path
    excel_path: Path
    yandex_disk_token: Optional[str]
    yandex_disk_remote_path: str
    disk_check_interval: int
    yadisk_upload_interval: int


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def load_settings() -> Settings:
    base_dir = Path(__file__).resolve().parent.parent
    load_dotenv(base_dir / ".env")

    api_key = os.getenv("API_KEY", "").strip()
    sqlite_raw = os.getenv("SQLITE_PATH", "").strip()
    excel_raw = os.getenv("EXCEL_PATH", "").strip()
    yandex_disk_token = os.getenv("YANDEX_DISK_TOKEN", "").strip()
    yandex_disk_remote_path = os.getenv("YANDEX_DISK_REMOTE_PATH", "").strip()
    disk_check_interval_raw = os.getenv("DISK_CHECK_INTERVAL", "30").strip()
    yadisk_upload_interval_raw = os.getenv("YADISK_UPLOAD_INTERVAL", "300").strip()

    if not api_key:
        raise ValueError("Не задана переменная окружения API_KEY.")
    if not sqlite_raw:
        raise ValueError("Не задана переменная окружения SQLITE_PATH.")
    if not excel_raw:
        raise ValueError("Не задана переменная окружения EXCEL_PATH.")
    if not yandex_disk_remote_path:
        raise ValueError("Не задана переменная окружения YANDEX_DISK_REMOTE_PATH.")

    sqlite_path = _resolve_path(base_dir, sqlite_raw)
    excel_path = _resolve_path(base_dir, excel_raw)

    if not excel_path.exists():
        raise ValueError(
            f"Файл EXCEL_PATH не найден: {excel_path}. "
            "Создайте файл заранее или выполните scripts/init_db.py."
        )
    if not excel_path.is_file():
        raise ValueError(f"EXCEL_PATH должен указывать на файл, получено: {excel_path}.")

    try:
        disk_check_interval = int(disk_check_interval_raw)
    except ValueError as error:
        raise ValueError("DISK_CHECK_INTERVAL должен быть целым числом.") from error

    try:
        yadisk_upload_interval = int(yadisk_upload_interval_raw)
    except ValueError as error:
        raise ValueError("YADISK_UPLOAD_INTERVAL должен быть целым числом.") from error

    if disk_check_interval <= 0:
        raise ValueError("DISK_CHECK_INTERVAL должен быть больше 0.")
    if yadisk_upload_interval <= 0:
        raise ValueError("YADISK_UPLOAD_INTERVAL должен быть больше 0.")

    return Settings(
        api_key=api_key,
        sqlite_path=sqlite_path,
        excel_path=excel_path,
        yandex_disk_token=yandex_disk_token or None,
        yandex_disk_remote_path=yandex_disk_remote_path,
        disk_check_interval=disk_check_interval,
        yadisk_upload_interval=yadisk_upload_interval,
    )
