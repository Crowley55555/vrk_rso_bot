from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


NOT_STARTED_SHEET = "Не начатые"
IN_PROGRESS_SHEET = "В работе"
COMPLETED_SHEET = "Выполненные"

SHEET_KEY_TO_NAME = {
    "todo": NOT_STARTED_SHEET,
    "progress": IN_PROGRESS_SHEET,
    "done": COMPLETED_SHEET,
}

ADD_TASK_BUTTON = "➕ Добавить задачу"
TASKS_TODO_BUTTON = "📋 Задачи к выполнению"
TASKS_IN_PROGRESS_BUTTON = "🔄 В работе"
TASKS_DONE_BUTTON = "✅ Выполненные задачи"
BACK_BUTTON = "◀️ Назад"
HOME_BUTTON = "🏠 Главное меню"


@dataclass(frozen=True, slots=True)
class Settings:
    """Настройки приложения, загружаемые из переменных окружения."""

    bot_token: str
    admin_ids: tuple[int, ...]
    spreadsheet_id: str
    google_credentials_file: Path
    base_dir: Path

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


def load_settings() -> Settings:
    """Загружает настройки из `.env` и валидирует обязательные поля."""

    base_dir = Path(__file__).resolve().parent.parent
    load_dotenv(base_dir / ".env")

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    spreadsheet_id = os.getenv("SPREADSHEET_ID", "").strip()
    credentials_raw = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

    if not bot_token:
        raise ValueError("Не задана переменная окружения BOT_TOKEN.")
    if not spreadsheet_id:
        raise ValueError("Не задана переменная окружения SPREADSHEET_ID.")
    if not credentials_raw:
        raise ValueError("Не задана переменная окружения GOOGLE_CREDENTIALS_FILE.")

    credentials_path = Path(credentials_raw)
    if not credentials_path.is_absolute():
        credentials_path = (base_dir / credentials_path).resolve()
    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Файл сервисного аккаунта не найден: {credentials_path}"
        )

    return Settings(
        bot_token=bot_token,
        admin_ids=admin_ids,
        spreadsheet_id=spreadsheet_id,
        google_credentials_file=credentials_path,
        base_dir=base_dir,
    )
