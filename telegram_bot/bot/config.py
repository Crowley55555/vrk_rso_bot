from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

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


def _load_env_files() -> None:
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / ".env", here.parent.parent / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


@dataclass(frozen=True, slots=True)
class Settings:
    """Настройки Telegram-бота, загружаемые из переменных окружения."""

    bot_token: str
    admin_ids: tuple[int, ...]
    base_dir: Path
    api_base_url: str
    api_key: str
    telegram_proxy: str | None

    def is_admin(self, user_id: int | None) -> bool:
        """Проверяет, является ли пользователь администратором."""

        return user_id is not None and user_id in self.admin_ids


def _parse_admin_ids(raw_value: str) -> tuple[int, ...]:
    """Преобразует строку с ID администраторов в кортеж целых чисел."""

    if not raw_value.strip():
        return tuple()

    admin_ids: list[int] = []
    for item in raw_value.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        admin_ids.append(int(stripped))
    return tuple(admin_ids)


def resolve_telegram_proxy_from_env() -> str | None:
    """Возвращает URL HTTPS/HTTP proxy для запросов к Telegram Bot API.

    Приоритет: ALL_PROXY, затем HTTPS_PROXY, затем HTTP_PROXY (как в типичных CLI-утилитах).
    """

    for key in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        raw = os.getenv(key, "").strip()
        if raw:
            return raw
    return None


def safe_telegram_proxy_log_hint(proxy_url: str) -> str:
    """Краткое описание proxy без логина и пароля (только схема и хост[:порт])."""

    parsed = urlparse(proxy_url)
    host = parsed.hostname or "?"
    scheme = parsed.scheme or "?"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{host}{port}"


def load_settings() -> Settings:
    """Загружает настройки из `.env` и валидирует обязательные поля."""

    _load_env_files()

    base_dir = Path(__file__).resolve().parent.parent

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    raw_tg_admins = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()
    raw_legacy = os.getenv("ADMIN_IDS", "").strip()
    admin_ids = _parse_admin_ids(raw_tg_admins or raw_legacy)

    api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000").strip().rstrip("/")
    api_key = os.getenv("API_KEY", "").strip()
    telegram_proxy = resolve_telegram_proxy_from_env()

    warn_if_api_base_url_uses_docker_hostname(api_base_url)

    if not bot_token:
        raise ValueError("Не задана переменная окружения BOT_TOKEN.")
    if not api_key:
        raise ValueError("Не задана переменная окружения API_KEY.")

    return Settings(
        bot_token=bot_token,
        admin_ids=admin_ids,
        base_dir=base_dir,
        api_base_url=api_base_url,
        api_key=api_key,
        telegram_proxy=telegram_proxy,
    )
