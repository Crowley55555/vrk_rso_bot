from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv


NOT_STARTED_SHEET = "Не начатые"
IN_PROGRESS_SHEET = "В работе"
COMPLETED_SHEET = "Выполненные"
ACCIDENTS_SHEET = "Аварии"
LOG_SHEET = "Лог"

APP_TIMEZONE = timezone(timedelta(hours=3))


@dataclass(frozen=True, slots=True)
class Settings:
    """Настройки API-сервиса Google Sheets."""

    api_key: str
    spreadsheet_id: str
    google_credentials_file: Path


def load_settings() -> Settings:
    base_dir = Path(__file__).resolve().parent.parent
    load_dotenv(base_dir / ".env")

    api_key = os.getenv("API_KEY", "").strip()
    spreadsheet_id = os.getenv("SPREADSHEET_ID", "").strip()
    credentials_raw = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()

    if not api_key:
        raise ValueError("Не задана переменная окружения API_KEY.")
    if not spreadsheet_id:
        raise ValueError("Не задана переменная окружения SPREADSHEET_ID.")
    if not credentials_raw:
        raise ValueError("Не задана переменная окружения GOOGLE_CREDENTIALS_FILE.")

    credentials_path = Path(credentials_raw)
    if not credentials_path.is_absolute():
        credentials_path = (base_dir / credentials_path).resolve()

    return Settings(
        api_key=api_key,
        spreadsheet_id=spreadsheet_id,
        google_credentials_file=credentials_path,
    )
