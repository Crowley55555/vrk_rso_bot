from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from bot.config import ADD_TASK_BUTTON, BACK_BUTTON, HOME_BUTTON, NOT_STARTED_SHEET, Settings
from bot.keyboards import KeyboardFactory
from bot.sheets import SheetsServiceError, append_task, write_log
from bot.states import UserStates

from .common import ADD_TASK_PATTERN, BACK_PATTERN, HOME_PATTERN, BaseHandler, MessageManager, get_user_display_name


logger = logging.getLogger(__name__)


class UserTaskHandler(BaseHandler):
    """Диалог создания задачи для обычного пользователя."""

    def build(self):
        """Создаёт `ConversationHandler` пользовательского сценария."""

        admin_filter = filters.User(user_id=list(self.settings.admin_ids))
        user_text_filter = filters.TEXT & ~filters.COMMAND & ~filters.Regex(BACK_PATTERN) & ~filters.Regex(HOME_PATTERN)

        return ConversationHandler(
            entry_points=[
                CommandHandler("start", self.start, filters=~admin_filter),
                MessageHandler(filters.Regex(ADD_TASK_PATTERN) & ~admin_filter, self.start),
            ],
            states={
                UserStates.TASK_NAME: [MessageHandler(user_text_filter, self.receive_task_name)],
                UserStates.COMMENTS: [MessageHandler(user_text_filter, self.receive_comments)],
                UserStates.FULL_NAME: [MessageHandler(user_text_filter, self.receive_full_name)],
                UserStates.DEADLINE: [MessageHandler(user_text_filter, self.finish_creation)],
            },
            fallbacks=[
                MessageHandler(filters.Regex(BACK_PATTERN), self.go_back),
                MessageHandler(filters.Regex(HOME_PATTERN), self.go_home),
                CommandHandler("cancel", self.cancel),
            ],
            name="user_task_conversation",
            persistent=False,
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Запускает диалог добавления задачи."""

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        context.user_data["flow_data"] = {}

        if update.message:
            self.message_manager.remember_user_message(update, context)
            await self.message_manager.delete_message(
                update.effective_chat.id,
                context,
                update.message.message_id,
            )

        await self._ask_task_name(update, context)
        return UserStates.TASK_NAME

    async def receive_task_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет наименование задачи и спрашивает комментарий."""

        context.user_data.setdefault("flow_data", {})["task_name"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_comments(update, context)
        return UserStates.COMMENTS

    async def receive_comments(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет комментарий и спрашивает ФИО автора."""

        raw_value = update.message.text.strip()
        context.user_data.setdefault("flow_data", {})["comments"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_full_name(update, context)
        return UserStates.FULL_NAME

    async def receive_full_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет ФИО автора и спрашивает срок выполнения."""

        context.user_data.setdefault("flow_data", {})["full_name"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_deadline(update, context)
        return UserStates.DEADLINE

    async def finish_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет задачу в листе 'Не начатые'."""

        raw_deadline = update.message.text.strip()
        flow_data = context.user_data.setdefault("flow_data", {})
        flow_data["deadline"] = "" if raw_deadline == "-" else raw_deadline

        row_data = [
            self.now_date(),
            flow_data["task_name"],
            flow_data.get("comments", ""),
            "",
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
        details = f"Срок: {flow_data.get('deadline') or ''}. Ответственные: . Кто добавил: {flow_data['full_name']}"
        await write_log(who, "Добавлена задача", flow_data["task_name"], NOT_STARTED_SHEET, details)

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        await self.send_text(update, context, "Задача успешно добавлена")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def go_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Возвращает пользователя на предыдущий шаг диалога."""

        current_state = self._current_state(context)
        flow_data = context.user_data.setdefault("flow_data", {})
        await self.message_manager.delete_step_messages(update, context)

        if current_state == UserStates.TASK_NAME:
            await self.show_main_menu(update, context)
            return ConversationHandler.END
        if current_state == UserStates.COMMENTS:
            flow_data.pop("task_name", None)
            await self._ask_task_name(update, context)
            return UserStates.TASK_NAME
        if current_state == UserStates.FULL_NAME:
            flow_data.pop("comments", None)
            await self._ask_comments(update, context)
            return UserStates.COMMENTS

        flow_data.pop("full_name", None)
        await self._ask_full_name(update, context)
        return UserStates.FULL_NAME

    async def go_home(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Завершает сценарий и возвращает пользователя в меню."""

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отменяет создание задачи."""

        await self.message_manager.delete_step_messages(update, context)
        context.user_data.pop("flow_data", None)
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.send_text(update, context, "Действие отменено")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def _ask_task_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = UserStates.TASK_NAME
        await self.send_text(
            update,
            context,
            "Введите наименование задачи",
            reply_markup=KeyboardFactory.navigation_menu(include_back=False),
            remember_as_last=True,
        )

    async def _ask_comments(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = UserStates.COMMENTS
        await self.send_text(
            update,
            context,
            "Введите комментарии к задаче (или «-» чтобы пропустить)",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_full_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = UserStates.FULL_NAME
        await self.send_text(
            update,
            context,
            "Введите ваши ФИО",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_deadline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = UserStates.DEADLINE
        await self.send_text(
            update,
            context,
            "Введите срок выполнения (или «-» чтобы пропустить)",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    @staticmethod
    def _current_state(context: ContextTypes.DEFAULT_TYPE) -> UserStates:
        value = context.user_data.get("current_state", UserStates.TASK_NAME)
        return UserStates(value)
