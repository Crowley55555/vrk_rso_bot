from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.helpers import escape_markdown

from bot.config import ACCIDENTS_SHEET, Settings
from bot.keyboards import KeyboardFactory
from bot.sheets import SheetsServiceError, append_task, write_log
from bot.states import UserStates

from .common import (
    BACK_PATTERN,
    HOME_PATTERN,
    REPORT_ACCIDENT_PATTERN,
    BaseHandler,
    MessageManager,
    get_user_display_name,
)


logger = logging.getLogger(__name__)


class UserTaskHandler(BaseHandler):
    """Диалог сообщения об аварии для обычного пользователя."""

    def build(self):
        """Создаёт `ConversationHandler` пользовательского сценария."""

        admin_filter = filters.User(user_id=list(self.settings.admin_ids))
        user_text_filter = filters.TEXT & ~filters.COMMAND & ~filters.Regex(BACK_PATTERN) & ~filters.Regex(HOME_PATTERN)

        return ConversationHandler(
            entry_points=[
                CommandHandler("start", self.start, filters=~admin_filter),
                MessageHandler(filters.Regex(REPORT_ACCIDENT_PATTERN) & ~admin_filter, self.start),
            ],
            states={
                UserStates.ACCIDENT_SHORT: [MessageHandler(user_text_filter, self.receive_accident_short)],
                UserStates.ACCIDENT_DETAIL: [MessageHandler(user_text_filter, self.receive_accident_detail)],
                UserStates.ACCIDENT_WHO: [MessageHandler(user_text_filter, self.receive_accident_who)],
                UserStates.ACCIDENT_URGENCY: [MessageHandler(user_text_filter, self.finish_creation)],
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
        """Запускает диалог сообщения об аварии."""

        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        self._clear_accident_data(context)

        if update.message:
            self.message_manager.remember_user_message(update, context)
            await self.message_manager.delete_message(
                update.effective_chat.id,
                context,
                update.message.message_id,
            )

        await self._ask_accident_short(update, context)
        return UserStates.ACCIDENT_SHORT

    async def receive_accident_short(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет краткое описание аварии и участок."""

        context.user_data["accident_short"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_accident_detail(update, context)
        return UserStates.ACCIDENT_DETAIL

    async def receive_accident_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет подробное описание аварии."""

        raw_value = update.message.text.strip()
        context.user_data["accident_detail"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_accident_who(update, context)
        return UserStates.ACCIDENT_WHO

    async def receive_accident_who(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет ФИО автора сообщения."""

        context.user_data["accident_who"] = update.message.text.strip()
        await self.message_manager.delete_step_messages(update, context)
        await self._ask_accident_urgency(update, context)
        return UserStates.ACCIDENT_URGENCY

    async def finish_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Сохраняет сообщение об аварии в листе 'Аварии'."""

        raw_urgency = update.message.text.strip()
        short_text = context.user_data.get("accident_short", "")
        detail_text = context.user_data.get("accident_detail", "")
        who_text = context.user_data.get("accident_who", "")
        urgency_text = "" if raw_urgency == "-" else raw_urgency
        context.user_data["accident_urgency"] = urgency_text
        event_time = self.now_datetime_minutes()

        row_data = [
            event_time,
            short_text,
            detail_text,
            "",
            urgency_text,
            who_text,
        ]

        try:
            await append_task(ACCIDENTS_SHEET, row_data)
        except SheetsServiceError:
            await self.message_manager.delete_step_messages(update, context)
            await self.show_error(update, context, "Не удалось сохранить сообщение об аварии. Попробуйте позже.")
            return ConversationHandler.END

        display_name = get_user_display_name(update.effective_user)
        details = f"Срочность: {urgency_text}. Кто сообщил: {who_text}"
        await write_log(display_name, "Сообщение об аварии", short_text, ACCIDENTS_SHEET, details)
        await self._notify_admins_about_accident(
            context,
            short_text=short_text,
            detail_text=detail_text,
            urgency_text=urgency_text,
            who_text=who_text,
            event_time=event_time,
        )

        await self.message_manager.delete_step_messages(update, context)
        self._clear_accident_data(context)
        await self.send_text(update, context, "✅ Сообщение об аварии принято. Администраторы уведомлены.")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def go_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Возвращает пользователя на предыдущий шаг диалога."""

        current_state = self._current_state(context)
        await self.message_manager.delete_step_messages(update, context)

        if current_state == UserStates.ACCIDENT_SHORT:
            await self.show_main_menu(update, context)
            return ConversationHandler.END
        if current_state == UserStates.ACCIDENT_DETAIL:
            context.user_data.pop("accident_short", None)
            await self._ask_accident_short(update, context)
            return UserStates.ACCIDENT_SHORT
        if current_state == UserStates.ACCIDENT_WHO:
            context.user_data.pop("accident_detail", None)
            await self._ask_accident_detail(update, context)
            return UserStates.ACCIDENT_DETAIL

        context.user_data.pop("accident_who", None)
        await self._ask_accident_who(update, context)
        return UserStates.ACCIDENT_WHO

    async def go_home(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Завершает сценарий и возвращает пользователя в меню."""

        await self.message_manager.delete_step_messages(update, context)
        self._clear_accident_data(context)
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отменяет сообщение об аварии."""

        await self.message_manager.delete_step_messages(update, context)
        self._clear_accident_data(context)
        await self.message_manager.cleanup_session(update.effective_chat.id, context)
        await self.send_text(update, context, "Действие отменено")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def _ask_accident_short(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = UserStates.ACCIDENT_SHORT
        await self.send_text(
            update,
            context,
            "Введите краткое описание аварии и укажите на каком участке она произошла:",
            reply_markup=KeyboardFactory.navigation_menu(include_back=False),
            remember_as_last=True,
        )

    async def _ask_accident_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = UserStates.ACCIDENT_DETAIL
        await self.send_text(
            update,
            context,
            "Введите подробное описание произошедшего (или «-» чтобы пропустить):",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_accident_who(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = UserStates.ACCIDENT_WHO
        await self.send_text(
            update,
            context,
            "Введите ваши ФИО:",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_accident_urgency(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["current_state"] = UserStates.ACCIDENT_URGENCY
        await self.send_text(
            update,
            context,
            "Как срочно требуется ремонт? (или «-» чтобы пропустить):",
            reply_markup=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _notify_admins_about_accident(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        short_text: str,
        detail_text: str,
        urgency_text: str,
        who_text: str,
        event_time: str,
    ) -> None:
        """Отправляет уведомление об аварии всем администраторам."""

        message = (
            "🚨 *Новая авария\\!*\n\n"
            f"📍 *Участок:* {escape_markdown(short_text or '—', version=2)}\n"
            f"📝 *Подробности:* {escape_markdown(detail_text or '—', version=2)}\n"
            f"⚡ *Срочность:* {escape_markdown(urgency_text or '—', version=2)}\n"
            f"👤 *Сообщил:* {escape_markdown(who_text or '—', version=2)}\n"
            f"📅 *Время:* {escape_markdown(event_time, version=2)}"
        )
        for admin_id in self.settings.admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except Exception as error:  # pragma: no cover - зависит от Telegram API
                logger.warning("Не удалось отправить уведомление об аварии администратору %s: %s", admin_id, error)

    @staticmethod
    def _clear_accident_data(context: ContextTypes.DEFAULT_TYPE) -> None:
        for key in ("accident_short", "accident_detail", "accident_who", "accident_urgency"):
            context.user_data.pop(key, None)

    @staticmethod
    def _current_state(context: ContextTypes.DEFAULT_TYPE) -> UserStates:
        value = context.user_data.get("current_state", UserStates.ACCIDENT_SHORT)
        return UserStates(value)
