"""Одноразовый порт telegram_bot/handlers/admin.py -> max_bot/handlers/admin.py (через MaxCtx)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "telegram_bot" / "bot" / "handlers" / "admin.py"
DST = ROOT / "max_bot" / "handlers" / "admin.py"


def main() -> None:
    text = SRC.read_text(encoding="utf-8")

    # Удалить ConversationHandler.build
    text = re.sub(
        r"\n    def build\(self\):.*?\n            persistent=False,\n        \)\n",
        "\n",
        text,
        count=1,
        flags=re.DOTALL,
    )

    header = '''from __future__ import annotations

import asyncio
import logging
import re as re_module
from typing import TYPE_CHECKING, Any

from max_bot.config import (
    ACCIDENTS_SHEET,
    COMPLETED_SHEET,
    IN_PROGRESS_SHEET,
    LOG_SHEET,
    NOT_STARTED_SHEET,
    Settings,
    SHEET_KEY_TO_NAME,
)
from max_bot.handlers.common_max import (
    BaseMaxHandler,
    MaxCtx,
    TaskMapper,
    TextFormatter,
    get_user_display_name,
)
from max_bot.keyboards import KeyboardFactory
from max_bot.states import AdminStates, CONV_END
from shared.api_client import (
    SheetsServiceError,
    append_task,
    delete_row,
    get_all_tasks,
    move_task,
    update_cell,
    write_log,
)

if TYPE_CHECKING:
    from max_bot.max_api import MaxApi


logger = logging.getLogger(__name__)

ACCIDENTS_LIST_BUTTON = "📋 Список аварий"
ADD_ACCIDENT_BUTTON = "➕ Добавить аварию"

ConversationHandler = type("ConversationHandler", (), {"END": CONV_END})


'''

    # Удалить старый header до class AdminTaskHandler
    text = re.sub(
        r"^.*?^class AdminTaskHandler",
        "class AdminTaskHandler",
        text,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )

    text = header + text.replace("class AdminTaskHandler(BaseHandler):", "class AdminTaskHandler(BaseMaxHandler):")

    # Сигнатуры методов: (self, update: Update, context: ...) -> (self, ctx: MaxCtx)
    text = re.sub(
        r"\(self,\s*update:\s*Update,\s*context:\s*ContextTypes\.DEFAULT_TYPE\)",
        "(self, ctx: MaxCtx)",
        text,
    )
    text = text.replace("ConversationHandler.END", "CONV_END")

    # Замены тел методов — паттерны update/context
    repls = [
        ("update.effective_chat.id", "ctx.user_id"),
        ("update.effective_chat is None", "False"),
        ("update.effective_user", "ctx.user_proxy"),
        ("update.effective_user.id if update.effective_user else None", "ctx.user_id"),
        ("update.message", "ctx.message_proxy"),
        ("update.message.text", "(ctx.text or '')"),
        ("update.callback_query", "ctx.callback_proxy"),
        ("await query.answer()", "await ctx.answer_callback()"),
        ("query.data", "ctx.callback_payload"),
        ("query.message.message_id", "ctx.callback_message_mid"),
        ("self.message_manager.cleanup_session(update.effective_chat.id, context)", "await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)"),
        ("self.message_manager.cleanup_session(update.effective_chat.id, context)", "await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)"),
    ]
    for old, new in repls:
        text = text.replace(old, new)

    text = text.replace(
        "await self.message_manager.delete_step_messages(update, context)",
        "await self.message_manager.delete_step_messages(ctx)",
    )
    text = text.replace(
        "await self.message_manager.delete_message(\n                update.effective_chat.id,\n                context,\n                ",
        "await self.message_manager.delete_message(ctx.user_id, ctx.user_data, ",
    )
    text = text.replace(
        "await self.message_manager.delete_message(chat_id, context, message_id)",
        "await self.message_manager.delete_message(ctx.user_id, ctx.user_data, message_id)",
    )
    text = text.replace("self.message_manager.remember_user_message(update, context)", "self.message_manager.remember_user_message(ctx)")
    text = text.replace("self.message_manager.remember_message(context, msg.message_id)", "self.message_manager.remember_message(ctx.user_data, mid)")
    text = text.replace(
        "self.message_manager.remember_message(context,",
        "self.message_manager.remember_message(ctx.user_data,",
    )

    # send_text / show_error / send_preformatted — (update, context, -> (ctx,
    text = re.sub(
        r"await self\.send_text\(\s*update,\s*context,",
        "await self.send_text(ctx,",
        text,
    )
    text = re.sub(
        r"await self\.show_error\(\s*update,\s*context,",
        "await self.show_error(ctx,",
        text,
    )
    text = re.sub(
        r"await self\.send_preformatted_text\(\s*update,\s*context,",
        "await self.send_preformatted_text(ctx,",
        text,
    )
    text = re.sub(
        r"await self\.show_main_menu\(\s*update,\s*context",
        "await self.show_main_menu(ctx.user_id, ctx.user_data",
        text,
    )

    # show_delete_confirmation uses context.bot.send_message — special case
    text = re.sub(
        r"msg = await context\.bot\.send_message\(\s*chat_id=update\.effective_chat\.id,",
        "mid_conf = await self._send_raw(ctx.user_id,",
        text,
    )
    # This block needs manual fix — we'll patch file after script

    text = text.replace("from telegram import Update", "")
    text = text.replace("from telegram.constants import ParseMode", "")
    text = text.replace(
        "from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters",
        "",
    )
    text = text.replace("reply_markup=", "attachments=",)

    # ParseMode / escape for delete confirmation - replace block manually later
    text = text.replace("parse_mode=ParseMode.MARKDOWN_V2,", 'format_="markdown",')

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(text, encoding="utf-8")
    print("Written", DST)


if __name__ == "__main__":
    main()
