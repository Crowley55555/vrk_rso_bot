from __future__ import annotations

from max_bot.config import (
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


def _inline_kb(buttons_rows: list[list[dict[str, str]]]) -> list[dict]:
    return [
        {
            "type": "inline_keyboard",
            "payload": {"buttons": buttons_rows},
        }
    ]


def _cb(text: str, payload: str) -> dict[str, str]:
    return {"type": "callback", "text": text, "payload": payload}


class KeyboardFactory:
    @staticmethod
    def admin_main_menu() -> list[dict]:
        return _inline_kb(
            [
                [_cb(ADD_TASK_BUTTON, "menu_add_task")],
                [
                    _cb(TASKS_TODO_BUTTON, "menu_todo"),
                    _cb(TASKS_IN_PROGRESS_BUTTON, "menu_progress"),
                ],
                [_cb(TASKS_DONE_BUTTON, "menu_done")],
                [
                    _cb(ACCIDENTS_BUTTON, "menu_accidents"),
                    _cb(LOGS_BUTTON, "menu_logs"),
                ],
            ]
        )

    @staticmethod
    def user_main_menu() -> list[dict]:
        return _inline_kb([[ _cb(REPORT_ACCIDENT_BUTTON, "menu_report_accident") ]])

    @staticmethod
    def get_accidents_submenu_keyboard() -> list[dict]:
        return _inline_kb(
            [
                [_cb("📋 Список аварий", "accidents_list")],
                [_cb("➕ Добавить аварию", "accidents_add")],
                [
                    _cb(BACK_BUTTON, "nav_back_accidents_menu"),
                    _cb(HOME_BUTTON, "nav_home"),
                ],
            ]
        )

    @staticmethod
    def navigation_menu(*, include_back: bool = True) -> list[dict]:
        rows: list[list[dict[str, str]]] = []
        if include_back:
            rows.append([_cb(BACK_BUTTON, "nav_back")])
        rows.append([_cb(HOME_BUTTON, "nav_home")])
        return _inline_kb(rows)

    @staticmethod
    def home_only_menu() -> list[dict]:
        return _inline_kb([[ _cb(HOME_BUTTON, "nav_home") ]])

    @staticmethod
    def inline_home_menu() -> list[dict]:
        return _inline_kb([[ _cb("🏠 Главное меню", "home_menu") ]])

    @staticmethod
    def task_list_keyboard(tasks: list[dict], sheet_key: str) -> list[dict]:
        keyboard = [
            [_cb(task["task_name"], f"task_{sheet_key}_{task['row_index']}")]
            for task in tasks
        ]
        return _inline_kb(keyboard)

    @staticmethod
    def task_detail_keyboard(sheet_key: str, row_index: int, *, is_admin: bool = False) -> list[dict]:
        keyboard: list[list[dict[str, str]]] = [
            [_cb("✏️ Редактировать", f"edit_{sheet_key}_{row_index}")]
        ]
        if sheet_key == "todo":
            keyboard.append([_cb("▶️ Взять в работу", f"take_{sheet_key}_{row_index}")])
            keyboard.append([_cb("✅ Отметить как выполненная", f"mark_done_{row_index}")])
        if sheet_key == "progress":
            keyboard.append([_cb("✅ Отметить как выполненное", f"complete_{sheet_key}_{row_index}")])
        if sheet_key == "accidents":
            keyboard.append([_cb("▶️ Взять в работу", f"take_accidents_{row_index}")])
            keyboard.append([_cb("✅ Отметить как выполненная", f"complete_accident_{row_index}")])
        if is_admin:
            label = "🗑 Удалить аварию" if sheet_key == "accidents" else "🗑 Удалить задачу"
            keyboard.append([_cb(label, f"delete_task_{sheet_key}_{row_index}")])
        return _inline_kb(keyboard)

    @staticmethod
    def delete_confirm_keyboard(sheet_key: str, row_index: int) -> list[dict]:
        return _inline_kb(
            [
                [
                    _cb("✅ Да, удалить", f"confirm_delete_{sheet_key}_{row_index}"),
                    _cb("❌ Отмена", f"cancel_delete_{sheet_key}_{row_index}"),
                ]
            ]
        )

    @staticmethod
    def log_list_keyboard(logs: list[dict]) -> list[dict]:
        keyboard = [[_cb(log["title"], f"log_{log['row_index']}")] for log in logs]
        return _inline_kb(keyboard)

    @staticmethod
    def back_to_logs_keyboard() -> list[dict]:
        return _inline_kb([[ _cb("◀️ Назад к логам", "back_to_logs") ]])
