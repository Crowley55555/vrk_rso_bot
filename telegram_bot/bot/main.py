from __future__ import annotations

import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    _here = Path(__file__).resolve().parent
    sys.path.append(str(_here.parent))
    sys.path.append(str(_here.parent.parent))

from telegram import Update
from telegram.error import Conflict, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from bot.config import (
    BACK_BUTTON,
    HOME_BUTTON,
    LOGS_BUTTON,
    TASKS_DONE_BUTTON,
    TASKS_IN_PROGRESS_BUTTON,
    TASKS_TODO_BUTTON,
    load_settings,
)
from bot.handlers.admin import AdminTaskHandler
from bot.handlers.common import CommonHandlers, MessageManager
from bot.handlers.user import UserTaskHandler
from shared.api_client import configure_api_client


def configure_logging() -> None:
    """Настраивает стандартный логгер приложения."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует необработанные ошибки и сообщает пользователю о сбое."""

    log = logging.getLogger(__name__)

    if isinstance(context.error, Conflict):
        log.warning(
            "Conflict: к боту одновременно подключается больше одного экземпляра (getUpdates). "
            "Запускайте бота только в одном месте: либо на сервере, либо локально."
        )
        return

    if isinstance(context.error, TimedOut):
        log.warning("TimedOut: запрос к Telegram API не успел выполниться. Проверьте сеть/доступность Telegram.")
        return

    log.exception("Необработанная ошибка бота", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Произошла внутренняя ошибка. Попробуйте повторить действие позже.",
            )
        except TimedOut:
            log.warning("Не удалось отправить сообщение об ошибке пользователю: Telegram API timed out.")
        except Exception:
            log.exception("Не удалось отправить сообщение об ошибке пользователю")


def build_application() -> Application:
    """Создаёт и настраивает экземпляр Telegram-приложения."""

    settings = load_settings()
    configure_api_client(settings.api_base_url, settings.api_key)

    message_manager = MessageManager()
    common_handlers = CommonHandlers(settings, message_manager)
    admin_handlers = AdminTaskHandler(settings, message_manager)
    user_handlers = UserTaskHandler(settings, message_manager)

    admin_filter = filters.User(user_id=list(settings.admin_ids))

    # Стандартный способ инициализации приложения в python-telegram-bot v20+.
    # Для Python 3.13 требуется свежая версия библиотеки (см. requirements.txt).
    application = (
        Application.builder()
        .token(settings.bot_token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .build()
    )

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
        MessageHandler(filters.Regex(rf"^{LOGS_BUTTON}$") & admin_filter, admin_handlers.show_logs)
    )

    application.add_handler(
        CallbackQueryHandler(common_handlers.show_task_card, pattern=r"^task_(todo|progress|done|accidents)_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(admin_handlers.show_log_detail, pattern=r"^log_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(admin_handlers.back_to_logs, pattern=r"^back_to_logs$")
    )
    application.add_handler(
        CallbackQueryHandler(common_handlers.go_home_inline_callback, pattern=r"^home_menu$")
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_handlers.show_delete_confirmation,
            pattern=r"^delete_task_(todo|progress|done|accidents)_\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_handlers.confirm_delete_task,
            pattern=r"^confirm_delete_(todo|progress|done|accidents)_\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_handlers.cancel_delete_task,
            pattern=r"^cancel_delete_(todo|progress|done|accidents)_\d+$",
        )
    )

    application.add_handler(MessageHandler(filters.Regex(rf"^{HOME_BUTTON}$"), common_handlers.go_home))
    application.add_handler(MessageHandler(filters.Regex(rf"^{BACK_BUTTON}$"), common_handlers.go_home))

    application.add_error_handler(error_handler)
    return application


def main() -> None:
    """Точка входа в приложение."""

    configure_logging()
    log = logging.getLogger(__name__)
    log.info("Запуск бота...")
    try:
        application = build_application()
        log.info("Бот запущен, ожидание обновлений.")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        log.exception("Ошибка при запуске: %s", e)
        raise


if __name__ == "__main__":
    main()
