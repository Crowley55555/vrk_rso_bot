from __future__ import annotations

import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from bot.config import (
    BACK_BUTTON,
    HOME_BUTTON,
    TASKS_DONE_BUTTON,
    TASKS_IN_PROGRESS_BUTTON,
    TASKS_TODO_BUTTON,
    load_settings,
)
from bot.handlers.admin import AdminTaskHandler
from bot.handlers.common import CommonHandlers, MessageManager
from bot.handlers.user import UserTaskHandler
from bot.sheets import setup_sheets


def configure_logging() -> None:
    """Настраивает стандартный логгер приложения."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует необработанные ошибки и сообщает пользователю о сбое."""

    logging.getLogger(__name__).exception("Необработанная ошибка бота", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Произошла внутренняя ошибка. Попробуйте повторить действие позже.",
            )
        except Exception:
            logging.getLogger(__name__).exception("Не удалось отправить сообщение об ошибке пользователю")


def build_application() -> Application:
    """Создаёт и настраивает экземпляр Telegram-приложения."""

    settings = load_settings()
    setup_sheets(settings)

    message_manager = MessageManager()
    common_handlers = CommonHandlers(settings, message_manager)
    admin_handlers = AdminTaskHandler(settings, message_manager)
    user_handlers = UserTaskHandler(settings, message_manager)

    admin_filter = filters.User(user_id=list(settings.admin_ids))

    # Стандартный способ инициализации приложения в python-telegram-bot v20+.
    # Для Python 3.13 требуется свежая версия библиотеки (см. requirements.txt).
    application = Application.builder().token(settings.bot_token).build()

    application.add_handler(admin_handlers.build())
    application.add_handler(user_handlers.build())

    application.add_handler(CommandHandler("start", common_handlers.start_admin, filters=admin_filter))
    application.add_handler(CommandHandler("cancel", common_handlers.cancel))

    application.add_handler(
        MessageHandler(filters.Regex(rf"^{TASKS_TODO_BUTTON}$") & admin_filter, common_handlers.show_todo_tasks)
    )
    application.add_handler(
        MessageHandler(
            filters.Regex(rf"^{TASKS_IN_PROGRESS_BUTTON}$") & admin_filter,
            common_handlers.show_in_progress_tasks,
        )
    )
    application.add_handler(
        MessageHandler(filters.Regex(rf"^{TASKS_DONE_BUTTON}$") & admin_filter, common_handlers.show_done_tasks)
    )

    application.add_handler(
        CallbackQueryHandler(common_handlers.show_task_card, pattern=r"^task_(todo|progress|done)_\d+$")
    )

    application.add_handler(MessageHandler(filters.Regex(rf"^{HOME_BUTTON}$"), common_handlers.go_home))
    application.add_handler(MessageHandler(filters.Regex(rf"^{BACK_BUTTON}$"), common_handlers.go_home))

    application.add_error_handler(error_handler)
    return application


def main() -> None:
    """Точка входа в приложение."""

    configure_logging()
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
