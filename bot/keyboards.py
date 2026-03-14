from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.config import (
    ADD_TASK_BUTTON,
    BACK_BUTTON,
    HOME_BUTTON,
    TASKS_DONE_BUTTON,
    TASKS_IN_PROGRESS_BUTTON,
    TASKS_TODO_BUTTON,
)


class KeyboardFactory:
    """Фабрика клавиатур для разных сценариев бота."""

    @staticmethod
    def admin_main_menu() -> ReplyKeyboardMarkup:
        """Возвращает главное меню администратора."""

        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(ADD_TASK_BUTTON)],
                [KeyboardButton(TASKS_TODO_BUTTON), KeyboardButton(TASKS_IN_PROGRESS_BUTTON)],
                [KeyboardButton(TASKS_DONE_BUTTON)],
            ],
            resize_keyboard=True,
        )

    @staticmethod
    def user_main_menu() -> ReplyKeyboardMarkup:
        """Возвращает главное меню обычного пользователя."""

        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(ADD_TASK_BUTTON)]],
            resize_keyboard=True,
        )

    @staticmethod
    def navigation_menu(include_back: bool = True) -> ReplyKeyboardMarkup:
        """Возвращает клавиатуру навигации для диалогов."""

        rows: list[list[KeyboardButton]] = []
        if include_back:
            rows.append([KeyboardButton(BACK_BUTTON)])
        rows.append([KeyboardButton(HOME_BUTTON)])
        return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

    @staticmethod
    def home_only_menu() -> ReplyKeyboardMarkup:
        """Возвращает клавиатуру только с кнопкой возврата в главное меню."""

        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(HOME_BUTTON)]],
            resize_keyboard=True,
        )

    @staticmethod
    def task_list_keyboard(tasks: list[dict], sheet_key: str) -> InlineKeyboardMarkup:
        """Создаёт inline-клавиатуру со списком задач."""

        keyboard = [
            [
                InlineKeyboardButton(
                    text=task["task_name"],
                    callback_data=f"task_{sheet_key}_{task['row_index']}",
                )
            ]
            for task in tasks
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def task_detail_keyboard(sheet_key: str, row_index: int) -> InlineKeyboardMarkup:
        """Создаёт inline-клавиатуру карточки задачи."""

        keyboard: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{sheet_key}_{row_index}")]
        ]
        if sheet_key == "todo":
            keyboard.append(
                [InlineKeyboardButton("▶️ Взять в работу", callback_data=f"take_{sheet_key}_{row_index}")]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "✅ Отметить как выполненная",
                        callback_data=f"mark_done_{row_index}",
                    )
                ]
            )
        if sheet_key == "progress":
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "✅ Отметить как выполненное",
                        callback_data=f"complete_{sheet_key}_{row_index}",
                    )
                ]
            )
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def inline_home_menu() -> InlineKeyboardMarkup:
        """Возвращает inline-клавиатуру с одной кнопкой «Главное меню»."""

        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Главное меню", callback_data="home_menu")]]
        )
