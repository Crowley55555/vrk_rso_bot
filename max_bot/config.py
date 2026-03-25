from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from shared.local_dev import warn_if_api_base_url_uses_docker_hostname


NOT_STARTED_SHEET = "Не начатые"
IN_PROGRESS_SHEET = "В работе"
COMPLETED_SHEET = "Выполненные"
ACCIDENTS_SHEET = "Аварии"
LOG_SHEET = "Лог"

SHEET_KEY_TO_NAME = {
    "todo": NOT_STARTED_SHEET,
    "progress": IN_PROGRESS_SHEET,
    "done": COMPLETED_SHEET,
    "accidents": ACCIDENTS_SHEET,
}

ADD_TASK_BUTTON = "➕ Добавить задачу"
REPORT_ACCIDENT_BUTTON = "🚨 Сообщить об аварии"
TASKS_TODO_BUTTON = "📋 Задачи к выполнению"
TASKS_IN_PROGRESS_BUTTON = "🔄 В работе"
TASKS_DONE_BUTTON = "✅ Выполненные задачи"
ACCIDENTS_BUTTON = "🚨 Аварии"
LOGS_BUTTON = "📊 Логи"
BACK_BUTTON = "◀️ Назад"
HOME_BUTTON = "🏠 Главное меню"

APP_TIMEZONE = timezone(timedelta(hours=3))


def normalize_max_button_text(text: str) -> str:
    """Убирает вариативность пробелов и невидимых символов в тексте с кнопки MAX."""

    return " ".join(
        (text or "")
        .replace("\ufe0f", "")
        .replace("\u200b", "")
        .strip()
        .split()
    )


def is_max_report_accident_text(text: str | None) -> bool:
    """Текст как у кнопки «Сообщить об аварии» (callback type message или ручной ввод)."""

    if not text:
        return False
    n = normalize_max_button_text(text)
    if not n:
        return False
    if n == normalize_max_button_text(REPORT_ACCIDENT_BUTTON):
        return True
    return n == normalize_max_button_text("Сообщить об аварии")

MAX_API_BASE_DEFAULT = "https://platform-api.max.ru"


def _load_env_files() -> None:
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / ".env", here.parent.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def _parse_admin_ids(raw_value: str) -> tuple[int, ...]:
    if not raw_value.strip():
        return tuple()
    admin_ids: list[int] = []
    for item in raw_value.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        admin_ids.append(int(stripped))
    return tuple(admin_ids)


@dataclass(frozen=True, slots=True)
class Settings:
    max_bot_token: str
    admin_ids: tuple[int, ...]
    base_dir: Path
    api_base_url: str
    api_key: str
    max_api_base: str

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


def load_settings() -> Settings:
    _load_env_files()
    base_dir = Path(__file__).resolve().parent

    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    raw_max = os.getenv("MAX_ADMIN_IDS", "").strip()
    raw_legacy = os.getenv("ADMIN_IDS", "").strip()
    admin_ids = _parse_admin_ids(raw_max or raw_legacy)

    api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000").strip().rstrip("/")
    api_key = os.getenv("API_KEY", "").strip()
    warn_if_api_base_url_uses_docker_hostname(api_base_url)
    max_api_base = os.getenv("MAX_API_BASE", MAX_API_BASE_DEFAULT).strip().rstrip("/")

    if not token:
        raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN.")
    if not api_key:
        raise ValueError("Не задана переменная окружения API_KEY.")

    return Settings(
        max_bot_token=token,
        admin_ids=admin_ids,
        base_dir=base_dir,
        api_base_url=api_base_url,
        api_key=api_key,
        max_api_base=max_api_base,
    )
