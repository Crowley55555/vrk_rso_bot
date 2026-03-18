from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from bot.config import ACCIDENTS_SHEET, COMPLETED_SHEET, IN_PROGRESS_SHEET, LOG_SHEET, NOT_STARTED_SHEET, Settings, SHEET_KEY_TO_NAME
from bot.keyboards import KeyboardFactory
from bot.sheets import (
    SheetsServiceError,
    append_task,
    delete_row,
    get_all_tasks,
    move_task,
    update_cell,
    write_log,
)
from bot.states import AdminStates

from .common import (
    ACCIDENTS_PATTERN,
    ADD_TASK_PATTERN,
    BACK_PATTERN,
    HOME_PATTERN,
    BaseHandler,
    TaskMapper,
    TextFormatter,
    get_user_display_name,
)


logger = logging.getLogger(__name__)

ACCIDENTS_LIST_BUTTON = "📋 Список аварий"
ADD_ACCIDENT_BUTTON = "➕ Добавить аварию"


class AdminTaskHandler(BaseHandler):
    """Административные диалоги управления задачами."""

    def build(self):
        """Создаёт `ConversationHandler` для административных сценариев."""

        admin_filter = filters.User(user_id=list(self.settings.admin_ids))
        user_text_filter = filters.TEXT & ~filters.COMMAND & ~filters.Regex(BACK_PATTERN) & ~filters.Regex(HOME_PATTERN)

        return ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(ADD_TASK_PATTERN) & admin_filter, self.start_add_task),
                MessageHandler(filters.Regex(ACCIDENTS_PATTERN) & admin_filter, self.show_accidents_menu),
                CallbackQueryHandler(self.start_edit_task, pattern=r"^edit_(todo|progress|done|accidents)_\d+$"),
                CallbackQueryHandler(self.start_take_in_work, pattern=r"^take_(todo|accidents)_\d+$"),
                CallbackQueryHandler(self.complete_task, pattern=r"^complete_(progress)_\d+$"),
                CallbackQueryHandler(self.complete_accident, pattern=r"^complete_accident_\d+$"),
                CallbackQueryHandler(self.mark_done_from_todo, pattern=r"^mark_done_\d+$"),
            ],
            states={
                AdminStates.ACCIDENTS_MENU: [
                    MessageHandler(filters.Regex(rf"^{ACCIDENTS_LIST_BUTTON}$"), self.show_accident_tasks_from_menu),
                    MessageHandler(filters.Regex(rf"^{ADD_ACCIDENT_BUTTON}$"), self.start_add_accident),
                    CallbackQueryHandler(self.show_task_card, pattern=r"^task_accidents_\d+$"),
                    CallbackQueryHandler(self.start_edit_task, pattern=r"^edit_accidents_\d+$"),
                    CallbackQueryHandler(self.start_take_in_work, pattern=r"^take_accidents_\d+$"),
                    CallbackQueryHandler(self.complete_accident, pattern=r"^complete_accident_\d+$"),
                    CallbackQueryHandler(self.show_delete_confirmation, pattern=r"^delete_task_accidents_\d+$"),
                    CallbackQueryHandler(self.confirm_delete_task, pattern=r"^confirm_delete_accidents_\d+$"),
                    CallbackQueryHandler(self.cancel_delete_task, pattern=r"^cancel_delete_accidents_\d+$"),
                ],
                AdminStates.ADD_TASK_NAME: [MessageHandler(user_text_filter, self.receive_task_name)],
                AdminStates.ADD_COMMENTS: [MessageHandler(user_text_filter, self.receive_comments)],
                AdminStates.ADD_RESPONSIBLE: [MessageHandler(user_text_filter, self.receive_responsible)],
                AdminStates.ADD_FULL_NAME: [MessageHandler(user_text_filter, self.receive_full_name)],
                AdminStates.ADD_DEADLINE: [MessageHandler(user_text_filter, self.finish_add_task)],
                AdminStates.ADMIN_ACCIDENT_SHORT: [MessageHandler(user_text_filter, self.receive_admin_accident_short)],
                AdminStates.ADMIN_ACCIDENT_DETAIL: [MessageHandler(user_text_filter, self.receive_admin_accident_detail)],
                AdminStates.ADMIN_ACCIDENT_RESPONSIBLE: [MessageHandler(user_text_filter, self.receive_admin_accident_responsible)],
                AdminStates.ADMIN_ACCIDENT_URGENCY: [MessageHandler(user_text_filter, self.receive_admin_accident_urgency)],
                AdminStates.ADMIN_ACCIDENT_WHO: [MessageHandler(user_text_filter, self.finish_add_accident)],
                AdminStates.EDIT_TASK_NAME: [MessageHandler(user_text_filter, self.receive_edit_task_name)],
                AdminStates.EDIT_COMMENTS: [MessageHandler(user_text_filter, self.receive_edit_comment)],
                AdminStates.EDIT_DEADLINE: [MessageHandler(user_text_filter, self.receive_edit_deadline)],
                AdminStates.EDIT_RESPONSIBLE: [MessageHandler(user_text_filter, self.receive_edit_responsible)],
                AdminStates.EDIT_ACCIDENT_C: [MessageHandler(user_text_filter, self.receive_edit_accident_description)],
                AdminStates.EDIT_ACCIDENT_E: [MessageHandler(user_text_filter, self.receive_edit_accident_urgency)],
                AdminStates.TAKE_IN_WORK_COMMENTS: [MessageHandler(user_text_filter, self.receive_take_comment)],
                AdminStates.TAKE_IN_WORK_RESPONSIBLE: [
                    MessageHandler(user_text_filter, self.finish_take_in_work)
                ],
            },
            fallbacks=[
                MessageHandler(filters.Regex(BACK_PATTERN), self.go_back),
                MessageHandler(filters.Regex(HOME_PATTERN), self.go_home),
                CommandHandler("cancel", self.cancel),
            ],
            name="admin_task_conversation",
            persistent=False,
        )

    async def safe_delete_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        message_id: int | None,
    ) -> None:
        """Безопасно удаляет сообщение через общий менеджер сообщений."""

        if update.effective_chat is None:
            return
        await self.message_manager.delete_message(update.effective_chat.id, context, message_id)

    async def show_accidents_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Показывает администратору reply-подменю раздела аварий."""

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        context.user_data["flow_mode"] = "accidents_menu"
        context.user_data.pop("flow_data", None)
        context.user_data.pop("current_task", None)

        if update.message:
            self.message_manager.remember_user_message(update, context)
            await self.safe_delete_message(update, context, update.message.message_id)

        return await self._send_accidents_menu(update, context)

    async def _send_accidents_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отправляет сообщение подменю раздела аварий."""

        context.user_data["current_state"] = AdminStates.ACCIDENTS_MENU
        await self.send_text(
            update,
            context,
            "🚨 Раздел Аварии. Выберите действие:",
            reply_markup=KeyboardFactory.get_accidents_submenu_keyboard(),
            remember_as_last=True,
        )
        return AdminStates.ACCIDENTS_MENU

    async def show_accident_tasks_from_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Открывает список аварий из подменю раздела аварий."""

        await self.message_manager.delete_step_messages(update, context)
        context.user_data["flow_mode"] = "accidents_list"
        await self._show_task_list_for_sheet(update, context, "accidents")
        context.user_data["current_state"] = AdminStates.ACCIDENTS_MENU
        return AdminStates.ACCIDENTS_MENU

    async def show_accident_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает список аварий."""

        await self._show_task_list_for_sheet(update, context, "accidents")

    async def show_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает последние записи листа 'Лог'."""

        try:
            rows = await get_all_tasks(LOG_SHEET)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить лог.")
            return

        context.user_data["current_state"] = AdminStates.VIEW_LOGS
        await self.message_manager.cleanup_session(update.effective_chat.id, context)

        if not rows:
            await self.send_text(
                update,
                context,
                "Лог пуст",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return

        latest_rows = list(reversed(rows))[:50]
        payload = [
            {
                "row_index": int(row["row_index"]),
                "title": f"{row.get('Дата и время', '')} | {row.get('Действие', '')}",
            }
            for row in latest_rows
        ]

        await self.send_text(
            update,
            context,
            "Выберите запись лога",
            reply_markup=KeyboardFactory.log_list_keyboard(payload),
        )
        await self.send_text(
            update,
            context,
            "Используйте кнопки ниже для навигации",
            reply_markup=KeyboardFactory.navigation_menu(),
        )

    async def show_log_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает карточку выбранной записи лога."""

        query = update.callback_query
        await query.answer()
        row_index = int(query.data.split("_")[-1])

        try:
            row = await self._fetch_row(LOG_SHEET, row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить запись лога.")
            return
        if row is None:
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self.send_text(
                update,
                context,
                "Запись лога не найдена.",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return

        text = (
            "📊 *Запись лога*\n\n"
            f"📅 Дата и время: {TextFormatter.escape(row.get('Дата и время', ''))}\n"
            f"👤 Кто: {TextFormatter.escape(row.get('Кто', ''))}\n"
            f"⚙️ Действие: {TextFormatter.escape(row.get('Действие', ''))}\n"
            f"📌 Задача\\/Авария: {TextFormatter.escape(row.get('Задача', ''))}\n"
            f"📋 Лист: {TextFormatter.escape(row.get('Лист', ''))}\n"
            f"📝 Подробности: {TextFormatter.escape(row.get('Подробности', ''))}"
        )

        context.user_data["current_state"] = AdminStates.VIEW_LOG_DETAIL
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.send_preformatted_text(
            update,
            context,
            text,
            reply_markup=KeyboardFactory.back_to_logs_keyboard(),
        )
        await self.send_text(
            update,
            context,
            "Используйте кнопки ниже для навигации",
            reply_markup=KeyboardFactory.navigation_menu(),
        )

    async def back_to_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Возвращает администратора к списку логов."""

        if update.callback_query:
            await update.callback_query.answer()
        await self.show_logs(update, context)

    async def start_add_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Запускает административный сценарий добавления задачи."""

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        context.user_data["flow_mode"] = "admin_add"
        context.user_data["flow_data"] = {}

        if update.message:
            self.message_manager.remember_user_message(update, context)
            await self.message_manager.delete_message(
                update.effective_chat.id,
                context,
                update.message.message_id,
            )

        await self._ask_add_task_name(update, context)
        return AdminStates.ADD_TASK_NAME

    async def receive_task_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет название задачи и спрашивает комментарий."""

        context.user_data.setdefault("flow_data", {})["task_name"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_add_comments(update, context)
        return AdminStates.ADD_COMMENTS

    async def receive_comments(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет комментарий и спрашивает ответственных."""

        raw_value = update.message.text.strip()
        context.user_data.setdefault("flow_data", {})["comments"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_add_responsible(update, context)
        return AdminStates.ADD_RESPONSIBLE

    async def receive_responsible(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет ответственных и спрашивает ФИО автора."""

        raw_value = update.message.text.strip()
        context.user_data.setdefault("flow_data", {})["responsible"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_add_full_name(update, context)
        return AdminStates.ADD_FULL_NAME

    async def receive_full_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет ФИО автора и спрашивает срок."""

        context.user_data.setdefault("flow_data", {})["full_name"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_add_deadline(update, context)
        return AdminStates.ADD_DEADLINE

    async def finish_add_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет административную задачу в лист 'Не начатые'."""

        raw_deadline = update.message.text.strip()
        flow_data = context.user_data.setdefault("flow_data", {})
        flow_data["deadline"] = "" if raw_deadline == "-" else raw_deadline

        row_data = [
            self.now_date(),
            flow_data["task_name"],
            flow_data.get("comments", ""),
            flow_data.get("responsible", ""),
            flow_data.get("deadline", ""),
            flow_data["full_name"],
        ]

        try:
            await append_task(NOT_STARTED_SHEET, row_data)
        except SheetsServiceError:
            await self.message_manager.delete_step_messages(update, context)
            await self.show_error(update, context, "Не удалось сохранить задачу. Попробуйте позже.")
            return ConversationHandler.END

        who = get_user_display_name(update.effective_user)
        details = f"Срок: {flow_data.get('deadline') or ''}. Ответственные: {flow_data.get('responsible') or ''}. Кто добавил: {flow_data['full_name']}"
        await write_log(who, "Добавлена задача", flow_data["task_name"], NOT_STARTED_SHEET, details)

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        await self.send_text(update, context, "Задача успешно добавлена")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def start_add_accident(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Запускает административный сценарий добавления аварии."""

        await self.message_manager.delete_step_messages(update, context)
        context.user_data["flow_mode"] = "admin_add_accident"
        context.user_data["flow_data"] = {}
        await self._ask_admin_accident_short(update, context)
        return AdminStates.ADMIN_ACCIDENT_SHORT

    async def receive_admin_accident_short(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет краткое описание аварии и участок."""

        context.user_data.setdefault("flow_data", {})["accident_short"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_admin_accident_detail(update, context)
        return AdminStates.ADMIN_ACCIDENT_DETAIL

    async def receive_admin_accident_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет подробное описание аварии."""

        raw_value = update.message.text.strip()
        context.user_data.setdefault("flow_data", {})["accident_detail"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_admin_accident_responsible(update, context)
        return AdminStates.ADMIN_ACCIDENT_RESPONSIBLE

    async def receive_admin_accident_responsible(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет ответственных по аварии."""

        raw_value = update.message.text.strip()
        context.user_data.setdefault("flow_data", {})["accident_responsible"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_admin_accident_urgency(update, context)
        return AdminStates.ADMIN_ACCIDENT_URGENCY

    async def receive_admin_accident_urgency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет срочность ремонта и спрашивает ФИО."""

        raw_value = update.message.text.strip()
        context.user_data.setdefault("flow_data", {})["accident_urgency"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_admin_accident_who(update, context)
        return AdminStates.ADMIN_ACCIDENT_WHO

    async def finish_add_accident(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет аварию, добавленную администратором, в лист 'Аварии'."""

        flow_data = context.user_data.setdefault("flow_data", {})
        accident_short = str(flow_data.get("accident_short", "")).strip()
        accident_detail = str(flow_data.get("accident_detail", "")).strip()
        accident_responsible = str(flow_data.get("accident_responsible", "")).strip()
        accident_urgency = str(flow_data.get("accident_urgency", "")).strip()
        accident_who = update.message.text.strip()
        flow_data["accident_who"] = accident_who

        row_data = [
            self.now_datetime_minutes(),
            accident_short,
            accident_detail,
            accident_responsible,
            accident_urgency,
            accident_who,
        ]

        try:
            await append_task(ACCIDENTS_SHEET, row_data)
        except SheetsServiceError:
            await self.message_manager.delete_step_messages(update, context)
            await self.show_error(update, context, "Не удалось сохранить аварию. Попробуйте позже.")
            return ConversationHandler.END

        who = get_user_display_name(update.effective_user)
        details = (
            f"Срочность: {accident_urgency}. "
            f"Ответственные: {accident_responsible}. "
            f"Кто добавил: {accident_who}"
        )
        await write_log(
            who,
            "Добавлена авария администратором",
            accident_short,
            ACCIDENTS_SHEET,
            details,
        )

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data["flow_mode"] = "accidents_menu"
        await self.send_text(update, context, "✅ Авария добавлена в таблицу.")
        return await self._send_accidents_menu(update, context)

    async def start_edit_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Запускает редактирование названия, комментария, срока и ответственных."""

        query = update.callback_query
        await query.answer()
        _, sheet_key, row_index_raw = query.data.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить задачу для редактирования.")
            return ConversationHandler.END

        if task is None:
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self.send_text(
                update,
                context,
                "Авария не найдена" if sheet_key == "accidents" else "Задача не найдена",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return ConversationHandler.END

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
        if sheet_key == "accidents":
            context.user_data["flow_mode"] = "edit_accident"
            context.user_data["flow_data"] = {
                "sheet_key": sheet_key,
                "row_index": row_index,
                "current_description": task.comments,
                "current_urgency": task.deadline,
            }
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self._ask_edit_accident_description(update, context)
            return AdminStates.EDIT_ACCIDENT_C

        flow_data = {
            "sheet_key": sheet_key,
            "row_index": row_index,
            "current_task_name": task.task_name,
            "current_comments": task.comments,
            "current_deadline": task.deadline,
        }
        if sheet_key == "progress":
            flow_data["current_responsible"] = task.responsible
        context.user_data["flow_mode"] = "edit"
        context.user_data["flow_data"] = flow_data

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self._ask_edit_task_name(update, context)
        return AdminStates.EDIT_TASK_NAME

    async def receive_edit_task_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Принимает новое название задачи и спрашивает комментарий."""

        context.user_data.setdefault("flow_data", {})["new_task_name"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_edit_comments(update, context)
        return AdminStates.EDIT_COMMENTS

    async def receive_edit_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Принимает новый комментарий и спрашивает новый срок."""

        context.user_data.setdefault("flow_data", {})["new_comments"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_edit_deadline(update, context)
        return AdminStates.EDIT_DEADLINE

    async def receive_edit_deadline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Принимает новый срок. Для листа «В работе» — запрашивает ответственных, иначе завершает редактирование."""

        flow_data = context.user_data.setdefault("flow_data", {})
        flow_data["new_deadline"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)

        if flow_data.get("sheet_key") == "progress":
            context.user_data["current_state"] = AdminStates.EDIT_DEADLINE
            await self._ask_edit_responsible(update, context)
            return AdminStates.EDIT_RESPONSIBLE

        return await self._apply_edit_and_finish(update, context)

    async def receive_edit_responsible(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Принимает новых ответственных и применяет все правки к задаче из листа «В работе»."""

        flow_data = context.user_data.setdefault("flow_data", {})
        flow_data["new_responsible"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        return await self._apply_edit_and_finish(update, context)

    async def receive_edit_accident_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Принимает новое подробное описание аварии и спрашивает срочность."""

        context.user_data.setdefault("flow_data", {})["new_description"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_edit_accident_urgency(update, context)
        return AdminStates.EDIT_ACCIDENT_E

    async def receive_edit_accident_urgency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Принимает новую срочность аварии и завершает редактирование."""

        context.user_data.setdefault("flow_data", {})["new_urgency"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        return await self._apply_accident_edit_and_finish(update, context)

    async def _apply_accident_edit_and_finish(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Применяет изменения подробного описания и срочности аварии."""

        flow_data = context.user_data.setdefault("flow_data", {})
        new_description = (
            flow_data["current_description"]
            if flow_data.get("new_description") == "-"
            else flow_data.get("new_description", flow_data["current_description"])
        )
        new_urgency = (
            flow_data["current_urgency"]
            if flow_data.get("new_urgency") == "-"
            else flow_data.get("new_urgency", flow_data["current_urgency"])
        )
        row_index = int(flow_data["row_index"])
        current_task = context.user_data.get("current_task") or {}

        changes: list[str] = []
        try:
            if new_description != flow_data["current_description"]:
                await update_cell(ACCIDENTS_SHEET, row_index, 3, new_description)
                current_task["C"] = new_description
                changes.append(f'Подробное описание: "{flow_data["current_description"]}" → "{new_description}"')
            if new_urgency != flow_data["current_urgency"]:
                await update_cell(ACCIDENTS_SHEET, row_index, 5, new_urgency)
                current_task["E"] = new_urgency
                changes.append(f'Срочность: "{flow_data["current_urgency"]}" → "{new_urgency}"')
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось обновить аварию.")
            return ConversationHandler.END

        details = "; ".join(changes) if changes else "Изменений не внесено"
        who = get_user_display_name(update.effective_user)
        await write_log(
            who,
            "Редактирование аварии",
            current_task.get("B") or "",
            ACCIDENTS_SHEET,
            details,
        )

        context.user_data["current_task"] = current_task
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        await self.send_text(update, context, "Авария успешно обновлена")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def _apply_edit_and_finish(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Применяет изменения названия, комментария, срока и ответственных и завершает редактирование."""

        flow_data = context.user_data.setdefault("flow_data", {})
        new_task_name = (
            flow_data["current_task_name"]
            if flow_data.get("new_task_name") == "-"
            else flow_data.get("new_task_name", flow_data["current_task_name"])
        )
        new_comments = flow_data["current_comments"] if flow_data.get("new_comments") == "-" else flow_data.get("new_comments", flow_data["current_comments"])
        new_deadline = flow_data["current_deadline"] if flow_data.get("new_deadline") == "-" else flow_data.get("new_deadline", flow_data["current_deadline"])
        sheet_name = self._sheet_name(flow_data["sheet_key"])
        row_index = int(flow_data["row_index"])

        current_task = context.user_data.get("current_task") or {}

        changes: list[str] = []
        try:
            if new_task_name != flow_data["current_task_name"]:
                await update_cell(sheet_name, row_index, 2, new_task_name)
                current_task["B"] = new_task_name
                changes.append(f'Название: "{flow_data["current_task_name"]}" → "{new_task_name}"')
            if new_comments != flow_data["current_comments"]:
                await update_cell(sheet_name, row_index, 3, new_comments)
                current_task["C"] = new_comments
                changes.append(f'Комментарий: "{flow_data["current_comments"]}" → "{new_comments}"')
            if new_deadline != flow_data["current_deadline"]:
                await update_cell(sheet_name, row_index, 5, new_deadline)
                current_task["E"] = new_deadline
                changes.append(f'Срок: "{flow_data["current_deadline"]}" → "{new_deadline}"')
            if flow_data.get("sheet_key") == "progress" and "new_responsible" in flow_data:
                new_responsible = flow_data["current_responsible"] if flow_data["new_responsible"] == "-" else flow_data["new_responsible"]
                if new_responsible != flow_data.get("current_responsible"):
                    await update_cell(sheet_name, row_index, 4, new_responsible)
                    current_task["D"] = new_responsible
                    changes.append(f'Ответственные: "{flow_data.get("current_responsible", "")}" → "{new_responsible}"')
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось обновить задачу.")
            return ConversationHandler.END

        details = "; ".join(changes) if changes else "Изменений не внесено"
        who = get_user_display_name(update.effective_user)
        await write_log(
            who,
            "Редактирование задачи",
            current_task.get("B") or flow_data["current_task_name"],
            sheet_name,
            details,
        )

        context.user_data["current_task"] = current_task
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        await self.send_text(update, context, "Задача успешно обновлена")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def mark_done_from_todo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Переносит задачу из «Не начатые» напрямую в «Выполненные» (минуя «В работе»)."""

        query = update.callback_query
        await query.answer()
        row_index = int(query.data.split("_")[-1])

        try:
            task = await self.fetch_task("todo", row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить задачу.")
            return ConversationHandler.END

        if task is None:
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self.send_text(update, context, "Задача не найдена", reply_markup=KeyboardFactory.home_only_menu())
            return ConversationHandler.END

        row_data = [
            self.now_date(),
            task.task_name,
            task.comments,
            task.responsible,
            task.deadline,
            task.added_by,
        ]

        try:
            await move_task(
                NOT_STARTED_SHEET,
                COMPLETED_SHEET,
                row_index,
                {"row_data": row_data},
            )
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось перенести задачу в архив.")
            return ConversationHandler.END

        who = get_user_display_name(update.effective_user)
        await write_log(
            who,
            "Задача выполнена (без взятия в работу)",
            task.task_name,
            "Не начатые → Выполненные",
            f"Ответственные: {task.responsible}",
        )

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.send_text(
            update,
            context,
            "✅ Задача отмечена как выполненная и перенесена в архив.",
            reply_markup=KeyboardFactory.inline_home_menu(),
        )
        return ConversationHandler.END

    async def show_delete_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает подтверждение удаления задачи (только для администраторов)."""

        query = update.callback_query
        await query.answer()

        if not self.is_admin(update):
            return

        parts = query.data.split("_", maxsplit=3)
        if len(parts) < 4:
            return
        sheet_key = parts[2]
        try:
            row_index = int(parts[3])
        except ValueError:
            return

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить задачу.")
            return

        if task is None:
            await self.send_text(
                update,
                context,
                "Авария не найдена." if sheet_key == "accidents" else "Задача не найдена.",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return

        try:
            await self.safe_delete_message(update, context, query.message.message_id)
        except Exception:
            pass

        entity_name = "аварию" if sheet_key == "accidents" else "задачу"
        marker = "🚨" if sheet_key == "accidents" else "📌"
        confirmation_text = (
            f"⚠️ Вы уверены, что хотите удалить {entity_name}\\?\n\n"
            f"{marker} " + TextFormatter.escape(task.task_name) + "\n\n"
            "Это действие необратимо\\."
        )
        msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=confirmation_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),
        )
        self.message_manager.remember_message(context, msg.message_id)

    async def confirm_delete_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Удаляет задачу из листа и показывает обновлённый список."""

        query = update.callback_query
        await query.answer()

        if not self.is_admin(update):
            return

        parts = query.data.split("_", maxsplit=3)
        if len(parts) < 4:
            return
        sheet_key = parts[2]
        try:
            row_index = int(parts[3])
        except ValueError:
            return

        sheet_name = self._sheet_name(sheet_key)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить задачу.")
            return
        if task is None:
            await self.send_text(
                update,
                context,
                "Авария не найдена." if sheet_key == "accidents" else "Задача не найдена.",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return
        task_name = task.task_name

        try:
            await delete_row(sheet_name, row_index)
        except SheetsServiceError:
            try:
                await self.safe_delete_message(update, context, query.message.message_id)
            except Exception:
                pass
            await self.send_text(
                update,
                context,
                "❌ Ошибка при удалении задачи. Попробуйте снова.",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return

        who = get_user_display_name(update.effective_user)
        if sheet_key == "accidents":
            await write_log(who, "Авария удалена", task_name, ACCIDENTS_SHEET, "Удалено администратором")
        else:
            await write_log(who, "Задача удалена", task_name, sheet_name, "Удалено администратором")

        try:
            await self.safe_delete_message(update, context, query.message.message_id)
        except Exception:
            pass

        await self.send_text(
            update,
            context,
            "🗑 Авария удалена." if sheet_key == "accidents" else "🗑 Задача удалена.",
        )

        await asyncio.sleep(1.5)
        await self._show_task_list_for_sheet(update, context, sheet_key)

    async def cancel_delete_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Отменяет удаление и снова показывает карточку задачи."""

        query = update.callback_query
        await query.answer()

        if not self.is_admin(update):
            return

        parts = query.data.split("_", maxsplit=3)
        if len(parts) < 4:
            return
        sheet_key = parts[2]
        try:
            row_index = int(parts[3])
        except ValueError:
            return

        try:
            await self.safe_delete_message(update, context, query.message.message_id)
        except Exception:
            pass

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.send_text(
                update,
                context,
                "Не удалось загрузить задачу.",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return

        if task is None:
            await self.send_text(
                update,
                context,
                "Авария не найдена." if sheet_key == "accidents" else "Задача не найдена.",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return

        await self.send_preformatted_text(
            update,
            context,
            TextFormatter.task_details(task),
            reply_markup=KeyboardFactory.task_detail_keyboard(
                sheet_key, row_index, is_admin=True
            ),
        )
        await self.send_text(
            update,
            context,
            "Используйте кнопки ниже для навигации",
            reply_markup=KeyboardFactory.navigation_menu(),
        )

    async def _show_task_list_for_sheet(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        sheet_key: str,
    ) -> None:
        """Показывает список задач указанного листа (для администратора после удаления)."""

        sheet_name = SHEET_KEY_TO_NAME[sheet_key]
        try:
            tasks = await get_all_tasks(sheet_name)
        except SheetsServiceError:
            await self.send_text(
                update,
                context,
                "Не удалось загрузить список задач.",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
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
        payload = [
            {"task_name": t.task_name or "Без названия", "row_index": t.row_index}
            for t in latest_tasks
        ]
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

    async def start_take_in_work(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Запускает перевод задачи в лист 'В работе'."""

        query = update.callback_query
        await query.answer()
        _, sheet_key, row_index_raw = query.data.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить задачу для перевода в работу.")
            return ConversationHandler.END

        if task is None:
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self.send_text(
                update,
                context,
                "Авария не найдена" if sheet_key == "accidents" else "Задача не найдена",
                reply_markup=KeyboardFactory.home_only_menu(),
            )
            return ConversationHandler.END

        if sheet_key == "accidents":
            context.user_data["flow_mode"] = "take_accident_in_work"
            context.user_data["flow_data"] = {
                "sheet_key": sheet_key,
                "row_index": row_index,
                "task_name": task.task_name,
                "current_comments": task.comments,
                "deadline": task.deadline,
                "added_by": task.added_by,
            }
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self._ask_take_responsible(update, context)
            return AdminStates.TAKE_IN_WORK_RESPONSIBLE

        context.user_data["flow_mode"] = "take_in_work"
        context.user_data["flow_data"] = {
            "sheet_key": sheet_key,
            "row_index": row_index,
            "task_name": task.task_name,
            "current_comments": task.comments,
            "deadline": task.deadline,
            "added_by": task.added_by,
        }

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self._ask_take_comment(update, context)
        return AdminStates.TAKE_IN_WORK_COMMENTS

    async def receive_take_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Принимает комментарий для перевода в работу."""

        context.user_data.setdefault("flow_data", {})["take_comments"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_take_responsible(update, context)
        return AdminStates.TAKE_IN_WORK_RESPONSIBLE

    async def finish_take_in_work(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Переносит задачу в лист 'В работе'."""

        flow_data = context.user_data.setdefault("flow_data", {})
        responsible = update.message.text.strip()

        if context.user_data.get("flow_mode") == "take_accident_in_work":
            row_data = [
                self.now_date(),
                flow_data["task_name"],
                flow_data["current_comments"],
                responsible,
                flow_data["deadline"],
                flow_data["added_by"],
            ]

            try:
                await move_task(
                    ACCIDENTS_SHEET,
                    IN_PROGRESS_SHEET,
                    int(flow_data["row_index"]),
                    {"row_data": row_data},
                )
            except SheetsServiceError:
                await self.message_manager.delete_step_messages(update, context)
                await self.show_error(update, context, "Не удалось перевести аварию в работу.")
                return ConversationHandler.END

            who = get_user_display_name(update.effective_user)
            await write_log(
                who,
                "Авария взята в работу",
                flow_data["task_name"],
                "Аварии → В работе",
                f"Ответственные: {responsible}",
            )

            await self.message_manager.delete_step_messages(update, context)
            context.user_data.pop("flow_data", None)
            context.user_data.pop("flow_mode", None)
            await self.send_text(update, context, "Авария переведена в статус «В работе»")
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        comments = (
            flow_data["current_comments"]
            if flow_data["take_comments"] == "-"
            else flow_data["take_comments"]
        )

        row_data = [
            self.now_date(),
            flow_data["task_name"],
            comments,
            responsible,
            flow_data["deadline"],
            flow_data["added_by"],
        ]

        try:
            await move_task(
                NOT_STARTED_SHEET,
                IN_PROGRESS_SHEET,
                int(flow_data["row_index"]),
                {"row_data": row_data},
            )
        except SheetsServiceError:
            await self.message_manager.delete_step_messages(update, context)
            await self.show_error(update, context, "Не удалось перевести задачу в работу.")
            return ConversationHandler.END

        who = get_user_display_name(update.effective_user)
        await write_log(
            who,
            "Задача взята в работу",
            flow_data["task_name"],
            "Не начатые → В работе",
            f"Ответственные: {responsible}",
        )

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        await self.send_text(update, context, "Задача переведена в статус «В работе»")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def complete_accident(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Переводит аварию из листа 'Аварии' в лист 'Выполненные'."""

        query = update.callback_query
        await query.answer()
        row_index = int(query.data.split("_")[-1])

        try:
            task = await self.fetch_task("accidents", row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить аварию для завершения.")
            return ConversationHandler.END

        if task is None:
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self.send_text(update, context, "Авария не найдена", reply_markup=KeyboardFactory.home_only_menu())
            return ConversationHandler.END

        row_data = [
            self.now_date(),
            task.task_name,
            task.comments,
            task.responsible,
            task.deadline,
            task.added_by,
        ]

        try:
            await move_task(
                ACCIDENTS_SHEET,
                COMPLETED_SHEET,
                row_index,
                {"row_data": row_data},
            )
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось отметить аварию как выполненную.")
            return ConversationHandler.END

        who = get_user_display_name(update.effective_user)
        await write_log(
            who,
            "Авария выполнена",
            task.task_name,
            "Аварии → Выполненные",
            f"Ответственные: {task.responsible}",
        )

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.send_text(update, context, "Авария отмечена как выполненная")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def complete_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Переводит задачу из листа 'В работе' в лист 'Выполненные'."""

        query = update.callback_query
        await query.answer()
        _, sheet_key, row_index_raw = query.data.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось загрузить задачу для завершения.")
            return ConversationHandler.END

        if task is None:
            await self.message_manager.cleanup_session(update.effective_chat.id, context)
            await self.send_text(update, context, "Задача не найдена", reply_markup=KeyboardFactory.home_only_menu())
            return ConversationHandler.END

        row_data = [
            self.now_date(),
            task.task_name,
            task.comments,
            task.responsible,
            task.deadline,
            task.added_by,
        ]

        try:
            await move_task(
                IN_PROGRESS_SHEET,
                COMPLETED_SHEET,
                row_index,
                {"row_data": row_data},
            )
        except SheetsServiceError:
            await self.show_error(update, context, "Не удалось отметить задачу как выполненную.")
            return ConversationHandler.END

        who = get_user_display_name(update.effective_user)
        await write_log(
            who,
            "Задача выполнена",
            task.task_name,
            "В работе → Выполненные",
            f"Ответственные: {task.responsible}",
        )

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.send_text(update, context, "Задача отмечена как выполненная")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def go_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Возвращает администратора на предыдущий шаг текущего сценария."""

        mode = context.user_data.get("flow_mode")
        current_state = AdminStates(context.user_data.get("current_state", AdminStates.ADD_TASK_NAME))
        flow_data = context.user_data.setdefault("flow_data", {})

        await self.message_manager.delete_step_messages(update, context)

        if mode == "admin_add":
            if current_state == AdminStates.ADD_TASK_NAME:
                context.user_data.pop("flow_data", None)
                context.user_data.pop("flow_mode", None)
                await self.show_main_menu(update, context)
                return ConversationHandler.END
            if current_state == AdminStates.ADD_COMMENTS:
                flow_data.pop("task_name", None)
                await self._ask_add_task_name(update, context)
                return AdminStates.ADD_TASK_NAME
            if current_state == AdminStates.ADD_RESPONSIBLE:
                flow_data.pop("comments", None)
                await self._ask_add_comments(update, context)
                return AdminStates.ADD_COMMENTS
            if current_state == AdminStates.ADD_FULL_NAME:
                flow_data.pop("responsible", None)
                await self._ask_add_responsible(update, context)
                return AdminStates.ADD_RESPONSIBLE

            flow_data.pop("full_name", None)
            await self._ask_add_full_name(update, context)
            return AdminStates.ADD_FULL_NAME

        if mode == "accidents_menu":
            context.user_data.pop("flow_data", None)
            context.user_data.pop("flow_mode", None)
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        if mode == "accidents_list":
            context.user_data["flow_mode"] = "accidents_menu"
            return await self.show_accidents_menu(update, context)

        if mode == "admin_add_accident":
            if current_state == AdminStates.ADMIN_ACCIDENT_SHORT:
                context.user_data.pop("flow_data", None)
                context.user_data["flow_mode"] = "accidents_menu"
                return await self.show_accidents_menu(update, context)
            if current_state == AdminStates.ADMIN_ACCIDENT_DETAIL:
                flow_data.pop("accident_short", None)
                await self._ask_admin_accident_short(update, context)
                return AdminStates.ADMIN_ACCIDENT_SHORT
            if current_state == AdminStates.ADMIN_ACCIDENT_RESPONSIBLE:
                flow_data.pop("accident_detail", None)
                await self._ask_admin_accident_detail(update, context)
                return AdminStates.ADMIN_ACCIDENT_DETAIL
            if current_state == AdminStates.ADMIN_ACCIDENT_URGENCY:
                flow_data.pop("accident_responsible", None)
                await self._ask_admin_accident_responsible(update, context)
                return AdminStates.ADMIN_ACCIDENT_RESPONSIBLE

            flow_data.pop("accident_urgency", None)
            await self._ask_admin_accident_urgency(update, context)
            return AdminStates.ADMIN_ACCIDENT_URGENCY

        if mode == "edit":
            if current_state == AdminStates.EDIT_TASK_NAME:
                context.user_data.pop("flow_data", None)
                context.user_data.pop("flow_mode", None)
                await self.show_main_menu(update, context)
                return ConversationHandler.END
            if current_state == AdminStates.EDIT_COMMENTS:
                flow_data.pop("new_task_name", None)
                await self._ask_edit_task_name(update, context)
                return AdminStates.EDIT_TASK_NAME
            if current_state == AdminStates.EDIT_DEADLINE:
                flow_data.pop("new_comments", None)
                await self._ask_edit_comments(update, context)
                return AdminStates.EDIT_COMMENTS
            if current_state == AdminStates.EDIT_RESPONSIBLE:
                flow_data.pop("new_deadline", None)
                await self._ask_edit_deadline(update, context)
                return AdminStates.EDIT_DEADLINE

        if mode == "edit_accident":
            if current_state == AdminStates.EDIT_ACCIDENT_C:
                context.user_data.pop("flow_data", None)
                context.user_data.pop("flow_mode", None)
                await self.show_main_menu(update, context)
                return ConversationHandler.END
            if current_state == AdminStates.EDIT_ACCIDENT_E:
                flow_data.pop("new_description", None)
                await self._ask_edit_accident_description(update, context)
                return AdminStates.EDIT_ACCIDENT_C

        if mode == "take_in_work":
            if current_state == AdminStates.TAKE_IN_WORK_COMMENTS:
                context.user_data.pop("flow_data", None)
                context.user_data.pop("flow_mode", None)
                await self.show_main_menu(update, context)
                return ConversationHandler.END

            flow_data.pop("take_comments", None)
            await self._ask_take_comment(update, context)
            return AdminStates.TAKE_IN_WORK_COMMENTS

        if mode == "take_accident_in_work":
            context.user_data.pop("flow_data", None)
            context.user_data.pop("flow_mode", None)
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def go_home(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Завершает активный административный сценарий и возвращает в меню."""

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        context.user_data.pop("current_task", None)
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отменяет текущий административный сценарий."""

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        context.user_data.pop("current_task", None)
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.send_text(update, context, "Действие отменено")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def _ask_add_task_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADD_TASK_NAME
        await self.send_text(
            update,
            context,
            "Введите наименование задачи",
            reply_markup=KeyboardFactory.navigation_menu(include_back=False),
            remember_as_last=True,
        )

    async def _ask_add_comments(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADD_COMMENTS
        await self.send_text(
            update,
            context,
            "Введите комментарии к задаче (или «-» чтобы пропустить)",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_add_responsible(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADD_RESPONSIBLE
        await self.send_text(
            update,
            context,
            "Введите ответственных (или «-» чтобы пропустить)",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_add_full_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADD_FULL_NAME
        await self.send_text(
            update,
            context,
            "Введите ваши ФИО",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_add_deadline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADD_DEADLINE
        await self.send_text(
            update,
            context,
            "Введите срок выполнения (или «-» чтобы пропустить)",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_short(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_SHORT
        await self.send_text(
            update,
            context,
            "Введите краткое описание аварии и укажите на каком участке она произошла:",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_DETAIL
        await self.send_text(
            update,
            context,
            "Введите подробное описание произошедшего (или «-» чтобы пропустить):",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_responsible(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_RESPONSIBLE
        await self.send_text(
            update,
            context,
            "Введите ответственных (или «-» чтобы пропустить):",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_urgency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_URGENCY
        await self.send_text(
            update,
            context,
            "Как срочно требуется ремонт? (или «-» чтобы пропустить):",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_who(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_WHO
        await self.send_text(
            update,
            context,
            "Введите ваши ФИО:",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    @staticmethod
    def _format_edit_current_value(value: str | None) -> str:
        """Возвращает строку текущего значения для вставки в сообщение (MarkdownV2). Пустое — «(не заполнено)»."""

        if value is None or str(value).strip() == "":
            return "\\(не заполнено\\)"
        return TextFormatter.escape(str(value).strip())

    async def _ask_edit_task_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.EDIT_TASK_NAME
        current_task = context.user_data.get("current_task") or {}
        current_b = current_task.get("B") or ""
        display = self._format_edit_current_value(current_b)
        text = (
            "✏️ Редактирование названия задачи\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое название или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(
            update,
            context,
            text,
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_comments(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.EDIT_COMMENTS
        current_task = context.user_data.get("current_task") or {}
        current_c = current_task.get("C") or ""
        display = self._format_edit_current_value(current_c)
        text = (
            "✏️ Редактирование комментария\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новый комментарий или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(
            update,
            context,
            text,
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_deadline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.EDIT_DEADLINE
        current_task = context.user_data.get("current_task") or {}
        current_e = current_task.get("E") or ""
        display = self._format_edit_current_value(current_e)
        text = (
            "✏️ Редактирование срока выполнения\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новый срок или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(
            update,
            context,
            text,
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_responsible(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.EDIT_RESPONSIBLE
        current_task = context.user_data.get("current_task") or {}
        current_d = current_task.get("D") or ""
        display = self._format_edit_current_value(current_d)
        text = (
            "✏️ Редактирование ответственных\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новых ответственных или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(
            update,
            context,
            text,
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_accident_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.EDIT_ACCIDENT_C
        current_task = context.user_data.get("current_task") or {}
        current_c = current_task.get("C") or ""
        display = self._format_edit_current_value(current_c)
        text = (
            "✏️ *Редактирование подробного описания*\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое описание или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(
            update,
            context,
            text,
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_accident_urgency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.EDIT_ACCIDENT_E
        current_task = context.user_data.get("current_task") or {}
        current_e = current_task.get("E") or ""
        display = self._format_edit_current_value(current_e)
        text = (
            "✏️ *Редактирование срочности*\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое значение или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(
            update,
            context,
            text,
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_take_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.TAKE_IN_WORK_COMMENTS
        await self.send_text(
            update,
            context,
            "Комментарии (оставьте текущие / введите новые / или «-»)",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_take_responsible(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.TAKE_IN_WORK_RESPONSIBLE
        await self.send_text(
            update,
            context,
            "Введите ответственных",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    @staticmethod
    def _sheet_name(sheet_key: str) -> str:
        if sheet_key == "todo":
            return NOT_STARTED_SHEET
        if sheet_key == "progress":
            return IN_PROGRESS_SHEET
        if sheet_key == "accidents":
            return ACCIDENTS_SHEET
        return COMPLETED_SHEET

    async def _fetch_row(self, sheet_name: str, row_index: int) -> dict | None:
        """Возвращает сырую строку листа по номеру строки."""

        rows = await get_all_tasks(sheet_name)
        for row in rows:
            if int(row["row_index"]) == row_index:
                return row
        return None

