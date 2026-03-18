from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.config import (
    ACCIDENTS_BUTTON,
    ADD_TASK_BUTTON,
    BACK_BUTTON,
    HOME_BUTTON,
    LOGS_BUTTON,
    REPORT_ACCIDENT_BUTTON,
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
                [KeyboardButton(ACCIDENTS_BUTTON), KeyboardButton(LOGS_BUTTON)],
            ],
            resize_keyboard=True,
        )

    @staticmethod
    def user_main_menu() -> ReplyKeyboardMarkup:
        """Возвращает главное меню обычного пользователя."""

        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(REPORT_ACCIDENT_BUTTON)]],
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
    def task_detail_keyboard(
        sheet_key: str, row_index: int, *, is_admin: bool = False
    ) -> InlineKeyboardMarkup:
        """Создаёт inline-клавиатуру карточки задачи. Кнопка удаления только для администраторов."""

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
        if sheet_key == "accidents":
            keyboard.append(
                [InlineKeyboardButton("▶️ Взять в работу", callback_data=f"take_accident_{row_index}")]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "✅ Отметить как выполненная",
                        callback_data=f"complete_accident_{row_index}",
                    )
                ]
            )
        if is_admin:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "🗑 Удалить аварию" if sheet_key == "accidents" else "🗑 Удалить задачу",
                        callback_data=f"delete_task_{sheet_key}_{row_index}",
                    )
                ]
            )
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def delete_confirm_keyboard(sheet_key: str, row_index: int) -> InlineKeyboardMarkup:
        """Клавиатура подтверждения удаления задачи."""

        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Да, удалить",
                        callback_data=f"confirm_delete_{sheet_key}_{row_index}",
                    ),
                    InlineKeyboardButton(
                        "❌ Отмена",
                        callback_data=f"cancel_delete_{sheet_key}_{row_index}",
                    ),
                ]
            ]
        )

    @staticmethod
    def log_list_keyboard(logs: list[dict]) -> InlineKeyboardMarkup:
        """Создаёт inline-клавиатуру со списком записей лога."""

        keyboard = [
            [
                InlineKeyboardButton(
                    text=log["title"],
                    callback_data=f"log_{log['row_index']}",
                )
            ]
            for log in logs
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def back_to_logs_keyboard() -> InlineKeyboardMarkup:
        """Клавиатура возврата к списку логов."""

        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Назад к логам", callback_data="back_to_logs")]]
        )

    @staticmethod
    def inline_home_menu() -> InlineKeyboardMarkup:
        """Возвращает inline-клавиатуру с одной кнопкой «Главное меню»."""

        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Главное меню", callback_data="home_menu")]]
        )
