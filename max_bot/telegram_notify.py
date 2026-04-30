from __future__ import annotations

import logging
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


def _escape_markdown_v2(text: str) -> str:
    """Экранирование для Telegram MarkdownV2 (как escape_markdown(..., version=2) в PTB)."""

    if not text:
        return ""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    out: list[str] = []
    for ch in str(text):
        if ch in escape_chars or ch == "\\":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _build_accident_message_markdown_v2(
    *,
    short_text: str,
    detail_text: str,
    urgency_text: str,
    who_text: str,
    event_time: str,
    source_label: str,
) -> str:
    src = _escape_markdown_v2(source_label)
    return (
        "🚨 *Новая авария\\!*\n\n"
        f"*Источник:* {src}\n\n"
        f"📍 *Участок:* {_escape_markdown_v2(short_text or '—')}\n"
        f"📝 *Подробности:* {_escape_markdown_v2(detail_text or '—')}\n"
        f"⚡ *Срочность:* {_escape_markdown_v2(urgency_text or '—')}\n"
        f"👤 *Сообщил:* {_escape_markdown_v2(who_text or '—')}\n"
        f"📅 *Время:* {_escape_markdown_v2(event_time)}"
    )


async def notify_telegram_admins_about_accident(
    *,
    bot_token: str,
    admin_chat_ids: Iterable[int],
    short_text: str,
    detail_text: str,
    urgency_text: str,
    who_text: str,
    event_time: str,
    source_label: str = "MAX",
) -> None:
    """Дублирует оповещение об аварии в Telegram (если заданы BOT_TOKEN и TELEGRAM_ADMIN_IDS)."""

    token = (bot_token or "").strip()
    chat_ids = tuple(admin_chat_ids)
    if not token or not chat_ids:
        return

    text = _build_accident_message_markdown_v2(
        short_text=short_text,
        detail_text=detail_text,
        urgency_text=urgency_text,
        who_text=who_text,
        event_time=event_time,
        source_label=source_label,
    )
    url = f"{_TELEGRAM_API}/bot{token}/sendMessage"

    async with httpx.AsyncClient(timeout=30.0, trust_env=True) as client:
        for chat_id in chat_ids:
            try:
                response = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "MarkdownV2",
                    },
                )
                response.raise_for_status()
            except Exception as error:
                logger.warning(
                    "Не удалось отправить уведомление об аварии в Telegram администратору %s: %s",
                    chat_id,
                    error,
                )
