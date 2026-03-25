from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from max_bot.config import (
    ACCIDENTS_BUTTON,
    ADD_TASK_BUTTON,
    APP_TIMEZONE,
    BACK_BUTTON,
    ACCIDENTS_SHEET,
    COMPLETED_SHEET,
    HOME_BUTTON,
    IN_PROGRESS_SHEET,
    LOGS_BUTTON,
    NOT_STARTED_SHEET,
    REPORT_ACCIDENT_BUTTON,
    Settings,
    SHEET_KEY_TO_NAME,
    TASKS_DONE_BUTTON,
    TASKS_IN_PROGRESS_BUTTON,
    TASKS_TODO_BUTTON,
)
from max_bot.keyboards import KeyboardFactory
from max_bot.max_api import MaxApi
from shared.api_client import SheetsServiceError, get_all_tasks


logger = logging.getLogger(__name__)


def get_user_display_name(user: Any) -> str:
    if user is None:
        return "ID: unknown"
    if getattr(user, "username", None) and str(user.username).strip():
        return f"@{user.username}"
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = " ".join((first, last)).strip()
    if full:
        return full
    return f"ID: {getattr(user, 'id', 'unknown')}"


@dataclass(slots=True)
class TaskView:
    sheet_key: str
    sheet_name: str
    row_index: int
    date: str
    task_name: str
    comments: str
    responsible: str
    deadline: str
    added_by: str


class TextFormatter:
    @staticmethod
    def escape(value: str) -> str:
        s = value or "—"
        return (
            s.replace("\\", "\\\\")
            .replace("*", "\\*")
            .replace("_", "\\_")
            .replace("`", "\\`")
            .replace("[", "\\[")
        )

    @classmethod
    def task_details(cls, task: TaskView) -> str:
        if task.sheet_key == "accidents":
            lines = [
                f"🚨 **{cls.escape(task.task_name)}**",
                f"📅 Дата: {cls.escape(task.date)}",
                f"📝 Подробное описание: {cls.escape(task.comments)}",
                f"👤 Ответственные: {cls.escape(task.responsible)}",
                f"⚡ Срочность: {cls.escape(task.deadline)}",
                f"👤 Кто сообщил: {cls.escape(task.added_by)}",
            ]
            return "\n".join(lines)

        lines = [
            f"📌 **{cls.escape(task.task_name)}**",
            f"📅 Дата: {cls.escape(task.date)}",
            f"💬 Комментарии: {cls.escape(task.comments)}",
            f"👤 Ответственные: {cls.escape(task.responsible)}",
            f"⏰ Срок: {cls.escape(task.deadline)}",
            f"👤 Кто добавил: {cls.escape(task.added_by)}",
        ]
        return "\n".join(lines)


class TaskMapper:
    @staticmethod
    def from_sheet_row(sheet_key: str, row: dict) -> TaskView:
        return TaskView(
            sheet_key=sheet_key,
            sheet_name=SHEET_KEY_TO_NAME[sheet_key],
            row_index=int(row["row_index"]),
            date=row.get("Дата добавления") or row.get("Дата") or "",
            task_name=(
                row.get("Краткое описание аварии, на каком участке произошла", "")
                if sheet_key == "accidents"
                else row.get("Наименование задачи", "")
            ),
            comments=(
                row.get("Подробное описание произошедшего", "")
                if sheet_key == "accidents"
                else row.get("Комментарии") or row.get("Коментарии") or ""
            ),
            responsible=row.get("Ответственные") or row.get("column_4") or "",
            deadline=(
                row.get("Срочность ремонта", "")
                if sheet_key == "accidents"
                else row.get("Срок выполнения") or row.get("Срок") or row.get("column_5") or ""
            ),
            added_by=row.get("Кто добавил", ""),
        )


class MaxMessageManager:
    STORAGE_KEY = "messages_to_delete"
    LAST_BOT_MESSAGE_KEY = "last_bot_message_id"

    def __init__(self, max_api: "MaxApi") -> None:
        self._api = max_api

    def ensure_storage(self, user_data: dict[str, Any]) -> list[str]:
        return user_data.setdefault(self.STORAGE_KEY, [])

    def remember_message(self, user_data: dict[str, Any], message_id: str | None) -> None:
        if message_id:
            self.ensure_storage(user_data).append(message_id)

    def remember_user_message(self, ctx: "MaxCtx") -> None:
        if ctx.incoming_message_mid:
            self.remember_message(ctx.user_data, ctx.incoming_message_mid)

    def forget_message(self, user_data: dict[str, Any], message_id: str | None) -> None:
        if message_id is None:
            return
        storage = self.ensure_storage(user_data)
        user_data[self.STORAGE_KEY] = [m for m in storage if m != message_id]
        if user_data.get(self.LAST_BOT_MESSAGE_KEY) == message_id:
            user_data[self.LAST_BOT_MESSAGE_KEY] = None

    async def delete_message(self, user_id: int, user_data: dict[str, Any], message_id: str | None) -> None:
        if message_id is None:
            return
        await self._api.delete_message(message_id)
        self.forget_message(user_data, message_id)

    async def delete_step_messages(self, ctx: "MaxCtx") -> None:
        last_bot = ctx.user_data.get(self.LAST_BOT_MESSAGE_KEY)
        await self.delete_message(ctx.user_id, ctx.user_data, last_bot)
        ctx.user_data[self.LAST_BOT_MESSAGE_KEY] = None
        if ctx.incoming_message_mid:
            self.remember_message(ctx.user_data, ctx.incoming_message_mid)
            await self.delete_message(ctx.user_id, ctx.user_data, ctx.incoming_message_mid)

    async def cleanup_session(self, user_id: int, user_data: dict[str, Any]) -> None:
        storage = self.ensure_storage(user_data)
        for mid in list(dict.fromkeys(storage)):
            await self.delete_message(user_id, user_data, mid)
        user_data[self.STORAGE_KEY] = []
        user_data[self.LAST_BOT_MESSAGE_KEY] = None


@dataclass
class MaxCtx:
    user_id: int
    user_data: dict[str, Any]
    max_api: MaxApi
    message_manager: MaxMessageManager
    text: str | None = None
    incoming_message_mid: str | None = None
    sender: dict[str, Any] | None = None
    callback_payload: str | None = None
    callback_id: str | None = None
    callback_message_mid: str | None = None

    @property
    def user_proxy(self) -> SimpleNamespace:
        s = self.sender or {}
        return SimpleNamespace(
            id=self.user_id,
            username=s.get("username"),
            first_name=s.get("first_name"),
            last_name=s.get("last_name"),
        )

    @property
    def callback_proxy(self) -> SimpleNamespace | None:
        if self.callback_payload is None:
            return None
        return SimpleNamespace(data=self.callback_payload)

    async def answer_callback(self) -> None:
        if self.callback_id:
            await self.max_api.answer_callback(self.callback_id, self.user_id)


class BaseMaxHandler:
    def __init__(self, settings: Settings, message_manager: MaxMessageManager, max_api: MaxApi) -> None:
        self.settings = settings
        self.message_manager = message_manager
        self.max_api = max_api

    @staticmethod
    def now_date() -> str:
        return datetime.now(APP_TIMEZONE).strftime("%d.%m.%Y")

    @staticmethod
    def now_datetime_minutes() -> str:
        return datetime.now(APP_TIMEZONE).strftime("%d.%m.%Y %H:%M")

    def is_admin_user(self, user_id: int | None) -> bool:
        return self.settings.is_admin(user_id)

    def is_admin_ctx(self, ctx: MaxCtx) -> bool:
        return self.settings.is_admin(ctx.user_id)

    def _merge_attachments(
        self,
        primary: list[dict[str, Any]] | None,
        extra: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        if not primary and not extra:
            return None
        out: list[dict[str, Any]] = []
        if primary:
            out.extend(primary)
        if extra:
            out.extend(extra)
        return out or None

    async def send_text(
        self,
        ctx: MaxCtx,
        text: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        remember_as_last: bool = False,
        add_navigation: bool = False,
    ) -> str | None:
        nav = KeyboardFactory.navigation_menu() if add_navigation else None
        merged = self._merge_attachments(attachments, nav)
        mid = await self.max_api.send_message(
            ctx.user_id,
            text=text,
            attachments=merged,
            format_="markdown",
        )
        if mid:
            self.message_manager.remember_message(ctx.user_data, mid)
        if remember_as_last and mid:
            ctx.user_data[MaxMessageManager.LAST_BOT_MESSAGE_KEY] = mid
        return mid

    async def send_preformatted_text(
        self,
        ctx: MaxCtx,
        text: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        remember_as_last: bool = False,
        add_navigation: bool = False,
    ) -> str | None:
        nav = KeyboardFactory.navigation_menu() if add_navigation else None
        merged = self._merge_attachments(attachments, nav)
        mid = await self.max_api.send_message(
            ctx.user_id,
            text=text,
            attachments=merged,
            format_="markdown",
        )
        if mid:
            self.message_manager.remember_message(ctx.user_data, mid)
        if remember_as_last and mid:
            ctx.user_data[MaxMessageManager.LAST_BOT_MESSAGE_KEY] = mid
        return mid

    async def show_main_menu(self, user_id: int, user_data: dict[str, Any]) -> None:
        ctx = MaxCtx(
            user_id=user_id,
            user_data=user_data,
            max_api=self.max_api,
            message_manager=self.message_manager,
        )
        if self.settings.is_admin(user_id):
            await self.send_text(
                ctx,
                "Выберите что сделать",
                attachments=KeyboardFactory.admin_main_menu(),
            )
            return
        await self.send_text(
            ctx,
            "Выберите действие",
            attachments=KeyboardFactory.user_main_menu(),
        )

    async def show_error(
        self,
        ctx: MaxCtx,
        message: str = "Не удалось выполнить операцию. Попробуйте позже.",
    ) -> None:
        await self.send_text(ctx, message, attachments=KeyboardFactory.home_only_menu())

    async def fetch_task(self, sheet_key: str, row_index: int) -> TaskView | None:
        rows = await get_all_tasks(SHEET_KEY_TO_NAME[sheet_key])
        for row in rows:
            if int(row["row_index"]) == row_index:
                return TaskMapper.from_sheet_row(sheet_key, row)
        return None


class CommonHandlersMax(BaseMaxHandler):
    async def start_clear(self, user_id: int, user_data: dict[str, Any]) -> None:
        user_data.clear()
        await self.message_manager.cleanup_session(user_id, user_data)
        await self.show_main_menu(user_id, user_data)

    async def cancel_flow(self, ctx: MaxCtx) -> int:
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        ctx.user_data.clear()
        await self.send_text(ctx, "Действие отменено")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return -1

    async def go_home(self, ctx: MaxCtx) -> int:
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("selected_task", None)
        ctx.user_data.pop("current_task", None)
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return -1

    async def go_home_from_callback(self, ctx: MaxCtx) -> None:
        """После ответа на callback в main: очистка и главное меню."""

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("selected_task", None)
        ctx.user_data.pop("current_task", None)
        await self.show_main_menu(ctx.user_id, ctx.user_data)

    async def show_todo_tasks(self, ctx: MaxCtx) -> None:
        await self._show_task_list(ctx, "todo")

    async def show_in_progress_tasks(self, ctx: MaxCtx) -> None:
        await self._show_task_list(ctx, "progress")

    async def show_done_tasks(self, ctx: MaxCtx) -> None:
        await self._show_task_list(ctx, "done")

    async def show_accident_tasks(self, ctx: MaxCtx) -> None:
        await self._show_task_list(ctx, "accidents")

    async def show_task_card(self, ctx: MaxCtx) -> None:
        payload = ctx.callback_payload or ""
        _, sheet_key, row_index_raw = payload.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу из Google Sheets.")
            return

        if task is None:
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self.send_text(
                ctx,
                (
                    "Авария не найдена. Возможно, она уже была изменена."
                    if sheet_key == "accidents"
                    else "Задача не найдена. Возможно, она уже была изменена."
                ),
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        ctx.user_data["selected_task"] = {"sheet_key": sheet_key, "row_index": row_index}
        ctx.user_data["current_task"] = {
            "sheet_name": task.sheet_name,
            "sheet_key": sheet_key,
            "row_index": row_index,
            "A": task.date or "",
            "B": task.task_name or "",
            "C": task.comments or "",
            "D": task.responsible or "",
            "E": task.deadline or "",
            "F": task.added_by or "",
        }

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        is_adm = self.is_admin_ctx(ctx)
        await self.send_preformatted_text(
            ctx,
            TextFormatter.task_details(task),
            attachments=KeyboardFactory.task_detail_keyboard(sheet_key, row_index, is_admin=is_adm),
        )
        await self.send_text(
            ctx,
            "Используйте кнопки ниже для навигации",
            attachments=KeyboardFactory.navigation_menu(),
        )

    async def _show_task_list(self, ctx: MaxCtx, sheet_key: str) -> None:
        sheet_name = SHEET_KEY_TO_NAME[sheet_key]
        try:
            tasks = await get_all_tasks(sheet_name)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить список задач.")
            return

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)

        if not tasks:
            await self.send_text(
                ctx,
                "Список аварий пуст" if sheet_key == "accidents" else "Список задач пуст",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        task_views = [TaskMapper.from_sheet_row(sheet_key, row) for row in tasks]
        latest_tasks = task_views[-30:]
        note = ""
        if len(task_views) > 30:
            note = "\n\nПоказаны последние 30 аварий" if sheet_key == "accidents" else "\n\nПоказаны последние 30 задач"
        payload = [{"task_name": t.task_name or "Без названия", "row_index": t.row_index} for t in latest_tasks]

        await self.send_text(
            ctx,
            f"{'Выберите аварию' if sheet_key == 'accidents' else 'Выберите задачу'}{note}",
            attachments=KeyboardFactory.task_list_keyboard(payload, sheet_key),
        )
        await self.send_text(
            ctx,
            "Используйте кнопки ниже для навигации",
            attachments=KeyboardFactory.navigation_menu(),
        )
