from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from telegram import Message, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes

from bot.config import (
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
from bot.keyboards import KeyboardFactory
from bot.sheets import SheetsServiceError, get_all_tasks


logger = logging.getLogger(__name__)


def get_user_display_name(user) -> str:
    """Возвращает отображаемое имя пользователя: @username, имя фамилия или ID: {id}."""

    if user is None:
        return "ID: unknown"
    if getattr(user, "username", None) and str(user.username).strip():
        return f"@{user.username}"
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = " ".join((first, last)).strip()
    if full:
        return full
    return f"ID: {user.id}"


@dataclass(slots=True)
class TaskView:
    """Нормализованное представление задачи для интерфейса бота."""

    sheet_key: str
    sheet_name: str
    row_index: int
    date: str
    task_name: str
    comments: str
    responsible: str
    deadline: str
    added_by: str


class MessageManager:
    """Управляет удалением сообщений в рамках текущей пользовательской сессии."""

    STORAGE_KEY = "messages_to_delete"
    LAST_BOT_MESSAGE_KEY = "last_bot_message_id"

    def ensure_storage(self, context: ContextTypes.DEFAULT_TYPE) -> list[int]:
        """Гарантирует наличие хранилища ID сообщений."""

        return context.user_data.setdefault(self.STORAGE_KEY, [])

    def remember_message(self, context: ContextTypes.DEFAULT_TYPE, message_id: int) -> None:
        """Сохраняет ID сообщения для последующего удаления."""

        storage = self.ensure_storage(context)
        storage.append(message_id)

    def remember_user_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Сохраняет ID пользовательского сообщения."""

        if update.message:
            self.remember_message(context, update.message.message_id)

    def forget_message(self, context: ContextTypes.DEFAULT_TYPE, message_id: int | None) -> None:
        """Удаляет ID сообщения из внутреннего хранилища текущей сессии."""

        if message_id is None:
            return

        storage = self.ensure_storage(context)
        context.user_data[self.STORAGE_KEY] = [stored_id for stored_id in storage if stored_id != message_id]
        if context.user_data.get(self.LAST_BOT_MESSAGE_KEY) == message_id:
            context.user_data[self.LAST_BOT_MESSAGE_KEY] = None

    async def delete_message(
        self,
        chat_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        message_id: int | None,
    ) -> None:
        """Удаляет сообщение, если это возможно."""

        if message_id is None:
            return

        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            self.forget_message(context, message_id)
        except BadRequest as error:  # pragma: no cover - зависит от Telegram API
            self.forget_message(context, message_id)
            logger.debug("Сообщение %s не удалено: %s", message_id, error)
        except Exception as error:  # pragma: no cover - зависит от Telegram API
            logger.debug("Сообщение %s не удалено: %s", message_id, error)

    async def delete_step_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Удаляет последнее сообщение бота и текущее сообщение пользователя."""

        chat_id = update.effective_chat.id
        last_bot_message_id = context.user_data.get(self.LAST_BOT_MESSAGE_KEY)
        await self.delete_message(chat_id, context, last_bot_message_id)
        context.user_data[self.LAST_BOT_MESSAGE_KEY] = None

        if update.message:
            self.remember_user_message(update, context)
            await self.delete_message(chat_id, context, update.message.message_id)

    async def cleanup_session(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Удаляет все сообщения, накопленные за сессию."""

        storage = self.ensure_storage(context)
        for message_id in list(dict.fromkeys(storage)):
            await self.delete_message(chat_id, context, message_id)
        context.user_data[self.STORAGE_KEY] = []
        context.user_data[self.LAST_BOT_MESSAGE_KEY] = None


class TextFormatter:
    """Готовит текст к безопасной отправке с MarkdownV2."""

    @staticmethod
    def escape(value: str) -> str:
        """Экранирует строку для MarkdownV2."""

        return escape_markdown(value or "—", version=2)

    @classmethod
    def task_details(cls, task: TaskView) -> str:
        """Форматирует карточку задачи."""

        if task.sheet_key == "accidents":
            lines = [
                f"🚨 *{cls.escape(task.task_name)}*",
                f"📅 Дата: {cls.escape(task.date)}",
                f"📝 Подробное описание: {cls.escape(task.comments)}",
                f"👤 Ответственные: {cls.escape(task.responsible)}",
                f"⚡ Срочность: {cls.escape(task.deadline)}",
                f"👤 Кто сообщил: {cls.escape(task.added_by)}",
            ]
            return "\n".join(lines)

        lines = [
            f"📌 {cls.escape(task.task_name)}",
            f"📅 Дата: {cls.escape(task.date)}",
            f"💬 Комментарии: {cls.escape(task.comments)}",
            f"👤 Ответственные: {cls.escape(task.responsible)}",
            f"⏰ Срок: {cls.escape(task.deadline)}",
            f"👤 Кто добавил: {cls.escape(task.added_by)}",
        ]
        return "\n".join(lines)


class TaskMapper:
    """Преобразует строки Google Sheets в единый формат для бота."""

    @staticmethod
    def from_sheet_row(sheet_key: str, row: dict) -> TaskView:
        """Преобразует сырую строку листа в нормализованный объект."""

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


class BaseHandler:
    """Базовый класс обработчиков с общими служебными методами."""

    def __init__(self, settings: Settings, message_manager: MessageManager) -> None:
        self.settings = settings
        self.message_manager = message_manager

    @staticmethod
    def now_date() -> str:
        """Возвращает текущую дату в формате таблицы."""

        return datetime.now(APP_TIMEZONE).strftime("%d.%m.%Y")

    @staticmethod
    def now_datetime_minutes() -> str:
        """Возвращает текущие дату и время с точностью до минут в GMT+3."""

        return datetime.now(APP_TIMEZONE).strftime("%d.%m.%Y %H:%M")

    def is_admin(self, update: Update) -> bool:
        """Проверяет права администратора по Telegram ID."""

        return self.settings.is_admin(update.effective_user.id if update.effective_user else None)

    async def send_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text: str,
        reply_markup: ReplyKeyboardMarkup | None = None,
        remember_as_last: bool = False,
    ) -> Message:
        """Отправляет текст, сохраняя ID сообщения для будущего удаления."""

        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=escape_markdown(text, version=2),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup,
        )
        self.message_manager.remember_message(context, message.message_id)
        if remember_as_last:
            context.user_data[MessageManager.LAST_BOT_MESSAGE_KEY] = message.message_id
        return message

    async def send_preformatted_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text: str,
        reply_markup=None,
        remember_as_last: bool = False,
    ) -> Message:
        """Отправляет заранее отформатированный MarkdownV2-текст."""

        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup,
        )
        self.message_manager.remember_message(context, message.message_id)
        if remember_as_last:
            context.user_data[MessageManager.LAST_BOT_MESSAGE_KEY] = message.message_id
        return message

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает главное меню в зависимости от роли пользователя."""

        if self.is_admin(update):
            await self.send_text(
                update,
                context,
                "Выберите что сделать",
                reply_markup=KeyboardFactory.admin_main_menu(),
            )
            return

        await self.send_text(
            update,
            context,
            "Выберите действие",
            reply_markup=KeyboardFactory.user_main_menu(),
        )

    async def show_error(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        message: str = "Не удалось выполнить операцию. Попробуйте позже.",
    ) -> None:
        """Показывает пользователю понятное сообщение об ошибке."""

        await self.send_text(
            update,
            context,
            message,
            reply_markup=KeyboardFactory.home_only_menu(),
        )

    async def fetch_task(self, sheet_key: str, row_index: int) -> TaskView | None:
        """Находит задачу по листу и номеру строки."""

        rows = await get_all_tasks(SHEET_KEY_TO_NAME[sheet_key])
        for row in rows:
            if int(row["row_index"]) == row_index:
                return TaskMapper.from_sheet_row(sheet_key, row)
        return None


class CommonHandlers(BaseHandler):
    """Общие обработчики: старт, меню, список задач и карточка задачи."""

    async def start_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает главное меню администратора."""

        context.user_data.clear()
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.show_main_menu(update, context)

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сбрасывает активную сессию и возвращает пользователя в меню."""

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        context.user_data.clear()
        await self.send_text(update, context, "Действие отменено")
        await self.show_main_menu(update, context)
        return -1

    async def go_home(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Возвращает пользователя в главное меню."""

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("selected_task", None)
        context.user_data.pop("current_task", None)
        await self.show_main_menu(update, context)
        return -1

    async def go_home_inline_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик нажатия inline-кнопки «Главное меню» (после подтверждений и т.п.)."""

        if update.callback_query:
            await update.callback_query.answer()
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("selected_task", None)
        context.user_data.pop("current_task", None)
        await self.show_main_menu(update, context)

    async def show_todo_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает список задач из листа 'Не начатые'."""

        await self._show_task_list(update, context, "todo")

    async def show_in_progress_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает список задач из листа 'В работе'."""

        await self._show_task_list(update, context, "progress")

    async def show_done_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает список задач из листа 'Выполненные'."""

        await self._show_task_list(update, context, "done")

    async def show_accident_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает список аварий из листа 'Аварии'."""

        await self._show_task_list(update, context, "accidents")

    async def show_task_card(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает карточку выбранной задачи или аварии."""

        query = update.callback_query
        await query.answer()

        _, sheet_key, row_index_raw = query.data.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить задачу из Google Sheets.")
            return

        if task is None:
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self.send_text(
                update,
                context,
                (
                    "Авария не найдена. Возможно, она уже была изменена."
                    if sheet_key == "accidents"
                    else "Задача не найдена. Возможно, она уже была изменена."
                ),
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return

        context.user_data["selected_task"] = {
            "sheet_key": sheet_key,
            "row_index": row_index,
        }
        context.user_data["current_task"] = {
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

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        is_admin = self.is_admin(update)
        await self.send_preformatted_text(
            update,
            context,
            TextFormatter.task_details(task),
            reply_markup=KeyboardFactory.task_detail_keyboard(
                sheet_key, row_index, is_admin=is_admin
            ),
        )
        await self.send_text(
            update,
            context,
            "Используйте кнопки ниже для навигации",
            reply_markup=KeyboardFactory.navigation_menu(),
        )

    async def _show_task_list(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        sheet_key: str,
    ) -> None:
        """Показывает пользователю список задач выбранного листа."""

        sheet_name = SHEET_KEY_TO_NAME[sheet_key]
        try:
            tasks = await get_all_tasks(sheet_name)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить список задач.")
            return

        await self.message_manager.cleanup_session(update.effective_chat.id, context)

        if not tasks:
            await self.send_text(
                update,
                context,
                "Список аварий пуст" if sheet_key == "accidents" else "Список задач пуст",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return

        task_views = [TaskMapper.from_sheet_row(sheet_key, row) for row in tasks]
        latest_tasks = task_views[-30:]

        note = ""
        if len(task_views) > 30:
            note = "\n\nПоказаны последние 30 аварий" if sheet_key == "accidents" else "\n\nПоказаны последние 30 задач"

        payload = [{"task_name": task.task_name or "Без названия", "row_index": task.row_index} for task in latest_tasks]

        await self.send_text(
            update,
            context,
            f"{'Выберите аварию' if sheet_key == 'accidents' else 'Выберите задачу'}{note}",
            reply_markup=KeyboardFactory.task_list_keyboard(payload, sheet_key),
        )
        await self.send_text(
            update,
            context,
            "Используйте кнопки ниже для навигации",
            reply_markup=KeyboardFactory.navigation_menu(),
        )


ADMIN_MENU_PATTERN = rf"^({TASKS_TODO_BUTTON}|{TASKS_IN_PROGRESS_BUTTON}|{TASKS_DONE_BUTTON}|{ACCIDENTS_BUTTON}|{LOGS_BUTTON})$"
ADD_TASK_PATTERN = rf"^({ADD_TASK_BUTTON}|Добавить задачу)$"
REPORT_ACCIDENT_PATTERN = rf"^({REPORT_ACCIDENT_BUTTON}|Сообщить об аварии)$"
ACCIDENTS_PATTERN = rf"^{ACCIDENTS_BUTTON}$"
LOGS_PATTERN = rf"^{LOGS_BUTTON}$"
BACK_PATTERN = rf"^{BACK_BUTTON}$"
HOME_PATTERN = rf"^{HOME_BUTTON}$"
NOT_STARTED_PATTERN = rf"^{NOT_STARTED_SHEET}$"
IN_PROGRESS_PATTERN = rf"^{IN_PROGRESS_SHEET}$"
COMPLETED_PATTERN = rf"^{COMPLETED_SHEET}$"
ACCIDENTS_SHEET_PATTERN = rf"^{ACCIDENTS_SHEET}$"
