from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from bot.config import COMPLETED_SHEET, IN_PROGRESS_SHEET, NOT_STARTED_SHEET, Settings
from bot.keyboards import KeyboardFactory
from bot.sheets import SheetsServiceError, append_task, move_task, update_cell
from bot.states import AdminStates

from .common import ADD_TASK_PATTERN, BACK_PATTERN, HOME_PATTERN, BaseHandler, TextFormatter


logger = logging.getLogger(__name__)


class AdminTaskHandler(BaseHandler):
    """Административные диалоги управления задачами."""

    def build(self):
        """Создаёт `ConversationHandler` для административных сценариев."""

        admin_filter = filters.User(user_id=list(self.settings.admin_ids))
        user_text_filter = filters.TEXT & ~filters.COMMAND & ~filters.Regex(BACK_PATTERN) & ~filters.Regex(HOME_PATTERN)

        return ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(ADD_TASK_PATTERN) & admin_filter, self.start_add_task),
                CallbackQueryHandler(self.start_edit_task, pattern=r"^edit_(todo|progress|done)_\d+$"),
                CallbackQueryHandler(self.start_take_in_work, pattern=r"^take_(todo)_\d+$"),
                CallbackQueryHandler(self.complete_task, pattern=r"^complete_(progress)_\d+$"),
            ],
            states={
                AdminStates.ADD_TASK_NAME: [MessageHandler(user_text_filter, self.receive_task_name)],
                AdminStates.ADD_COMMENTS: [MessageHandler(user_text_filter, self.receive_comments)],
                AdminStates.ADD_RESPONSIBLE: [MessageHandler(user_text_filter, self.receive_responsible)],
                AdminStates.ADD_FULL_NAME: [MessageHandler(user_text_filter, self.receive_full_name)],
                AdminStates.ADD_DEADLINE: [MessageHandler(user_text_filter, self.finish_add_task)],
                AdminStates.EDIT_COMMENTS: [MessageHandler(user_text_filter, self.receive_edit_comment)],
                AdminStates.EDIT_DEADLINE: [MessageHandler(user_text_filter, self.finish_edit_task)],
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

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        await self.send_text(update, context, "Задача успешно добавлена")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def start_edit_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Запускает редактирование комментария и срока."""

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
            await self.send_text(update, context, "Задача не найдена", reply_markup=KeyboardFactory.home_only_menu())
            return ConversationHandler.END

        context.user_data["flow_mode"] = "edit"
        context.user_data["flow_data"] = {
            "sheet_key": sheet_key,
            "row_index": row_index,
            "current_comments": task.comments,
            "current_deadline": task.deadline,
        }

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self._ask_edit_comments(update, context)
        return AdminStates.EDIT_COMMENTS

    async def receive_edit_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Принимает новый комментарий и спрашивает новый срок."""

        context.user_data.setdefault("flow_data", {})["new_comments"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_edit_deadline(update, context)
        return AdminStates.EDIT_DEADLINE

    async def finish_edit_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обновляет комментарий и/или срок в исходной строке."""

        flow_data = context.user_data.setdefault("flow_data", {})
        flow_data["new_deadline"] = update.message.text.strip()

        new_comments = flow_data["current_comments"] if flow_data["new_comments"] == "-" else flow_data["new_comments"]
        new_deadline = flow_data["current_deadline"] if flow_data["new_deadline"] == "-" else flow_data["new_deadline"]
        sheet_name = self._sheet_name(flow_data["sheet_key"])
        row_index = int(flow_data["row_index"])

        try:
            if new_comments != flow_data["current_comments"]:
                await update_cell(sheet_name, row_index, 3, new_comments)
            if new_deadline != flow_data["current_deadline"]:
                await update_cell(sheet_name, row_index, 5, new_deadline)
        except SheetsServiceError:
            await self.message_manager.delete_step_messages(update, context)
            await self.show_error(update, context, "Не удалось обновить задачу.")
            return ConversationHandler.END

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        await self.send_text(update, context, "Задача успешно обновлена")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

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
            await self.send_text(update, context, "Задача не найдена", reply_markup=KeyboardFactory.home_only_menu())
            return ConversationHandler.END

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

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        await self.send_text(update, context, "Задача переведена в статус «В работе»")
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

        if mode == "edit":
            if current_state == AdminStates.EDIT_COMMENTS:
                context.user_data.pop("flow_data", None)
                context.user_data.pop("flow_mode", None)
                await self.show_main_menu(update, context)
                return ConversationHandler.END

            flow_data.pop("new_comments", None)
            await self._ask_edit_comments(update, context)
            return AdminStates.EDIT_COMMENTS

        if mode == "take_in_work":
            if current_state == AdminStates.TAKE_IN_WORK_COMMENTS:
                context.user_data.pop("flow_data", None)
                context.user_data.pop("flow_mode", None)
                await self.show_main_menu(update, context)
                return ConversationHandler.END

            flow_data.pop("take_comments", None)
            await self._ask_take_comment(update, context)
            return AdminStates.TAKE_IN_WORK_COMMENTS

        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def go_home(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Завершает активный административный сценарий и возвращает в меню."""

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отменяет текущий административный сценарий."""

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        context.user_data.pop("flow_mode", None)
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

    async def _ask_edit_comments(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.EDIT_COMMENTS
        await self.send_text(
            update,
            context,
            "Введите новый комментарий (или «-» чтобы оставить без изменений)",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_deadline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = AdminStates.EDIT_DEADLINE
        await self.send_text(
            update,
            context,
            "Введите новый срок выполнения (или «-» чтобы оставить без изменений)",
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
        return COMPLETED_SHEET
