"""Преобразует скопированный telegram admin.py в вариант для Max."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "max_bot" / "handlers" / "admin.py"

HEADER = '''from __future__ import annotations

import asyncio
import logging

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


logger = logging.getLogger(__name__)

ACCIDENTS_LIST_BUTTON = "📋 Список аварий"
ADD_ACCIDENT_BUTTON = "➕ Добавить аварию"

ConversationHandler = type("ConversationHandler", (), {"END": CONV_END})


'''

def main() -> None:
    text = P.read_text(encoding="utf-8")

    text = re.sub(
        r"\n    def build\(self\):.*?\n            persistent=False,\n        \)\n",
        "\n",
        text,
        count=1,
        flags=re.DOTALL,
    )

    text = re.sub(r"^.*?^class AdminTaskHandler", "class AdminTaskHandler", text, count=1, flags=re.MULTILINE | re.DOTALL)
    text = HEADER + text.replace("class AdminTaskHandler(BaseHandler):", "class AdminTaskHandler(BaseMaxHandler):")

    text = text.replace(
        "(self, update: Update, context: ContextTypes.DEFAULT_TYPE)",
        "(self, ctx: MaxCtx)",
    )
    text = text.replace("ConversationHandler.END", "CONV_END")
    text = text.replace("await query.answer()", "await ctx.answer_callback()")
    text = text.replace("query.data", "ctx.callback_payload")
    text = text.replace("query.message.message_id", "ctx.callback_message_mid")
    text = text.replace("update.effective_chat.id", "ctx.user_id")
    text = text.replace("update.effective_user", "ctx.user_proxy")
    text = text.replace("self.is_admin(update)", "self.is_admin_ctx(ctx)")
    text = text.replace("context.user_data", "ctx.user_data")
    text = text.replace(
        "await self.message_manager.delete_step_messages(update, context)",
        "await self.message_manager.delete_step_messages(ctx)",
    )
    text = text.replace("update.message.text.strip()", "(ctx.text or '').strip()")

    text = text.replace(
        "if update.effective_chat is None:\n            return\n        await self.message_manager.delete_message(ctx.user_id, ctx.user_data, message_id)",
        "await self.message_manager.delete_message(ctx.user_id, ctx.user_data, message_id)",
    )

    text = text.replace(
        "await self.message_manager.delete_message(\n                ctx.user_id,\n                ctx.user_data,\n                update.message.message_id,\n            )",
        "await self.message_manager.delete_message(ctx.user_id, ctx.user_data, ctx.incoming_message_mid)",
    )
    text = text.replace(
        "if update.message:\n            self.message_manager.remember_user_message(update, context)\n            await self.message_manager.delete_message(ctx.user_id, ctx.user_data, ctx.incoming_message_mid)",
        "if ctx.incoming_message_mid:\n            self.message_manager.remember_user_message(ctx)\n            await self.message_manager.delete_message(ctx.user_id, ctx.user_data, ctx.incoming_message_mid)",
    )

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
        r"await self\.show_main_menu\(\s*update,\s*context\)",
        "await self.show_main_menu(ctx.user_id, ctx.user_data)",
        text,
    )

    text = text.replace("reply_markup=", "attachments=")

    # show_delete_confirmation: context.bot.send_message
    text = text.replace(
        "msg = await context.bot.send_message(\n            chat_id=ctx.user_id,\n            text=confirmation_text,\n            parse_mode=ParseMode.MARKDOWN_V2,\n            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),\n        )\n        self.message_manager.remember_message(ctx.user_data, msg.message_id)",
        "mid_del = await self.max_api.send_message(\n            ctx.user_id,\n            text=confirmation_text,\n            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),\n            format_=\"markdown\",\n        )\n        if mid_del:\n            self.message_manager.remember_message(ctx.user_data, mid_del)",
    )

    # ParseMode / telegram imports cleanup
    for line in (
        "from telegram import Update",
        "from telegram.constants import ParseMode",
        "from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters",
    ):
        text = text.replace(line + "\n", "")

    text = text.replace("from bot.config import", "# removed\n# from bot.config import")
    text = text.replace("from bot.keyboards import KeyboardFactory", "")
    text = text.replace("from bot.states import AdminStates", "")
    text = text.replace("from bot.sheets import", "from shared.api_client import")
    text = text.replace(
        "from .common import (\n    ACCIDENTS_PATTERN,\n    ADD_TASK_PATTERN,\n    BACK_PATTERN,\n    HOME_PATTERN,\n    BaseHandler,\n    TaskMapper,\n    TextFormatter,\n    get_user_display_name,\n)\n",
        "",
    )

    # Remove duplicate broken lines
    text = text.replace("# removed\n# from bot.config import ACCIDENTS_SHEET, COMPLETED_SHEET, IN_PROGRESS_SHEET, LOG_SHEET, NOT_STARTED_SHEET, Settings, SHEET_KEY_TO_NAME\n", "")

    P.write_text(text, encoding="utf-8")
    print("OK", P)


if __name__ == "__main__":
    main()
