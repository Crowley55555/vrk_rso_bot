from __future__ import annotations

import logging
from typing import Any

import httpx


logger = logging.getLogger(__name__)


def _extract_mid(message: dict[str, Any] | None) -> str | None:
    if not message:
        return None
    mid = message.get("mid")
    if mid is not None:
        return str(mid)
    body = message.get("body")
    if isinstance(body, dict):
        bmid = body.get("mid")
        if bmid is not None:
            return str(bmid)
    return None


def message_body_text(message: dict[str, Any] | None) -> str:
    if not message:
        return ""
    body = message.get("body")
    if not isinstance(body, dict):
        return ""
    text = body.get("text")
    return (text or "").strip() if isinstance(text, str) else ""


def sender_user_id(message: dict[str, Any] | None) -> int | None:
    if not message:
        return None
    sender = message.get("sender")
    if isinstance(sender, dict):
        uid = sender.get("user_id")
        if uid is not None:
            return int(uid)
    return None


class MaxApi:
    """Обёртка над HTTP API платформы Max."""

    def __init__(self, access_token: str, base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": access_token},
            timeout=httpx.Timeout(120.0, connect=30.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_updates(
        self,
        *,
        marker: int | None = None,
        limit: int = 100,
        timeout: int = 30,
        types: tuple[str, ...] = ("message_created", "message_callback"),
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: list[tuple[str, str | int]] = [
            ("limit", limit),
            ("timeout", timeout),
        ]
        if marker is not None:
            params.append(("marker", marker))
        for t in types:
            params.append(("types", t))

        try:
            response = await self._client.get("/updates", params=params)
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.exception("Ошибка GET /updates: %s", e)
            raise

        data = response.json()
        updates = data.get("updates") or []
        if not isinstance(updates, list):
            updates = []
        next_marker = data.get("marker")
        if next_marker is not None:
            next_marker = int(next_marker)
        return updates, next_marker

    async def send_message(
        self,
        user_id: int,
        *,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        format_: str | None = "markdown",
    ) -> str | None:
        """Отправляет сообщение; возвращает mid при успехе."""

        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if format_:
            body["format"] = format_

        try:
            response = await self._client.post(
                "/messages",
                params={"user_id": user_id},
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.exception("Ошибка POST /messages: %s", e)
            raise

        data = response.json()
        msg = data.get("message") if isinstance(data, dict) else None
        if isinstance(msg, dict):
            mid = _extract_mid(msg)
            if mid:
                return mid
        if isinstance(data, dict):
            mid = _extract_mid(data)
            if mid:
                return mid
        logger.warning("В ответе POST /messages не найден mid: %s", data)
        return None

    async def edit_message(
        self,
        message_id: str,
        *,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        format_: str | None = "markdown",
    ) -> None:
        body: dict[str, Any] = {"text": text}
        if attachments is not None:
            body["attachments"] = attachments
        if format_:
            body["format"] = format_

        try:
            response = await self._client.put(
                "/messages",
                params={"message_id": message_id},
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.exception("Ошибка PUT /messages: %s", e)
            raise

    async def delete_message(self, message_id: str) -> None:
        try:
            response = await self._client.delete(
                "/messages",
                params={"message_id": message_id},
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.debug("Не удалось удалить сообщение %s: %s", message_id, e)

    async def answer_callback(self, callback_id: str, user_id: int) -> None:
        try:
            response = await self._client.post(
                f"/answers/callbacks/{callback_id}",
                json={"user_id": user_id},
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.debug("answer_callback %s: %s", callback_id, e)
