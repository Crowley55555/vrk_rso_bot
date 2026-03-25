from __future__ import annotations

import logging

from max_bot.config import ACCIDENTS_SHEET
from max_bot.handlers.common_max import BaseMaxHandler, MaxCtx, TextFormatter, get_user_display_name
from max_bot.keyboards import KeyboardFactory
from max_bot.max_api import MaxApi
from max_bot.states import CONV_END, UserStates
from shared.api_client import SheetsServiceError, append_task, write_log


logger = logging.getLogger(__name__)


class UserTaskHandlerMax(BaseMaxHandler):
    """Диалог сообщения об аварии для обычного пользователя (Max)."""

    async def start(self, ctx: MaxCtx) -> int:
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        self._clear_accident_data(ctx.user_data)

        if ctx.incoming_message_mid:
            self.message_manager.remember_user_message(ctx)
            await self.message_manager.delete_message(ctx.user_id, ctx.user_data, ctx.incoming_message_mid)

        await self._ask_accident_short(ctx)
        return int(UserStates.ACCIDENT_SHORT)

    async def receive_accident_short(self, ctx: MaxCtx) -> int:
        ctx.user_data["accident_short"] = (ctx.text or "").strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_accident_detail(ctx)
        return int(UserStates.ACCIDENT_DETAIL)

    async def receive_accident_detail(self, ctx: MaxCtx) -> int:
        raw_value = (ctx.text or "").strip()
        ctx.user_data["accident_detail"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_accident_who(ctx)
        return int(UserStates.ACCIDENT_WHO)

    async def receive_accident_who(self, ctx: MaxCtx) -> int:
        ctx.user_data["accident_who"] = (ctx.text or "").strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_accident_urgency(ctx)
        return int(UserStates.ACCIDENT_URGENCY)

    async def finish_creation(self, ctx: MaxCtx) -> int:
        raw_urgency = (ctx.text or "").strip()
        short_text = ctx.user_data.get("accident_short", "")
        detail_text = ctx.user_data.get("accident_detail", "")
        who_text = ctx.user_data.get("accident_who", "")
        urgency_text = "" if raw_urgency == "-" else raw_urgency
        ctx.user_data["accident_urgency"] = urgency_text
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
            await self.message_manager.delete_step_messages(ctx)
            await self.show_error(ctx, "Не удалось сохранить сообщение об аварии. Попробуйте позже.")
            return CONV_END

        display_name = get_user_display_name(ctx.user_proxy)
        details = f"Срочность: {urgency_text}. Кто сообщил: {who_text}"
        await write_log(display_name, "Сообщение об аварии", short_text, ACCIDENTS_SHEET, details)
        await self._notify_admins_about_accident(
            ctx,
            short_text=short_text,
            detail_text=detail_text,
            urgency_text=urgency_text,
            who_text=who_text,
            event_time=event_time,
        )

        await self.message_manager.delete_step_messages(ctx)
        self._clear_accident_data(ctx.user_data)
        await self.send_text(ctx, "✅ Сообщение об аварии принято. Администраторы уведомлены.")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def go_back(self, ctx: MaxCtx) -> int:
        current_state = self._current_state(ctx.user_data)
        await self.message_manager.delete_step_messages(ctx)

        if current_state == UserStates.ACCIDENT_SHORT:
            await self.show_main_menu(ctx.user_id, ctx.user_data)
            return CONV_END
        if current_state == UserStates.ACCIDENT_DETAIL:
            ctx.user_data.pop("accident_short", None)
            await self._ask_accident_short(ctx)
            return int(UserStates.ACCIDENT_SHORT)
        if current_state == UserStates.ACCIDENT_WHO:
            ctx.user_data.pop("accident_detail", None)
            await self._ask_accident_detail(ctx)
            return int(UserStates.ACCIDENT_DETAIL)

        ctx.user_data.pop("accident_who", None)
        await self._ask_accident_who(ctx)
        return int(UserStates.ACCIDENT_WHO)

    async def go_home(self, ctx: MaxCtx) -> int:
        await self.message_manager.delete_step_messages(ctx)
        self._clear_accident_data(ctx.user_data)
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def cancel(self, ctx: MaxCtx) -> int:
        await self.message_manager.delete_step_messages(ctx)
        self._clear_accident_data(ctx.user_data)
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.send_text(ctx, "Действие отменено")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def _ask_accident_short(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = int(UserStates.ACCIDENT_SHORT)
        await self.send_text(
            ctx,
            "Введите краткое описание аварии и укажите на каком участке она произошла:",
            attachments=KeyboardFactory.navigation_menu(include_back=False),
            remember_as_last=True,
        )

    async def _ask_accident_detail(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = int(UserStates.ACCIDENT_DETAIL)
        await self.send_text(
            ctx,
            "Введите подробное описание произошедшего (или «-» чтобы пропустить):",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_accident_who(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = int(UserStates.ACCIDENT_WHO)
        await self.send_text(
            ctx,
            "Введите ваши ФИО:",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_accident_urgency(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = int(UserStates.ACCIDENT_URGENCY)
        await self.send_text(
            ctx,
            "Как срочно требуется ремонт? (или «-» чтобы пропустить):",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _notify_admins_about_accident(
        self,
        ctx: MaxCtx,
        *,
        short_text: str,
        detail_text: str,
        urgency_text: str,
        who_text: str,
        event_time: str,
    ) -> None:
        message = (
            "🚨 **Новая авария!**\n\n"
            f"📍 **Участок:** {TextFormatter.escape(short_text or '—')}\n"
            f"📝 **Подробности:** {TextFormatter.escape(detail_text or '—')}\n"
            f"⚡ **Срочность:** {TextFormatter.escape(urgency_text or '—')}\n"
            f"👤 **Сообщил:** {TextFormatter.escape(who_text or '—')}\n"
            f"📅 **Время:** {TextFormatter.escape(event_time)}"
        )
        for admin_id in self.settings.admin_ids:
            try:
                await self.max_api.send_message(admin_id, text=message, format_="markdown")
            except Exception as error:
                logger.warning("Не удалось отправить уведомление об аварии администратору %s: %s", admin_id, error)

    @staticmethod
    def _clear_accident_data(user_data: dict) -> None:
        for key in ("accident_short", "accident_detail", "accident_who", "accident_urgency"):
            user_data.pop(key, None)

    @staticmethod
    def _current_state(user_data: dict) -> UserStates:
        value = user_data.get("current_state", int(UserStates.ACCIDENT_SHORT))
        return UserStates(value)
