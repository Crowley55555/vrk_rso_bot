from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx


logger = logging.getLogger(__name__)


class SheetsServiceError(Exception):
    """Ошибка доступа к Google Sheets через API."""


def _error_detail_from_response(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict) and "detail" in data:
            detail = data["detail"]
            if isinstance(detail, str):
                return detail
            if isinstance(detail, list):
                return "; ".join(str(x) for x in detail)
    except Exception:
        pass
    return response.text or f"HTTP {response.status_code}"


class APIClient:
    """HTTP-клиент к FastAPI-сервису Google Sheets (интерфейс как у bot.sheets)."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={"X-API-Key": api_key},
            timeout=timeout,
            # Это внутренний сервис проекта (localhost / docker service name),
            # ему не нужны системные HTTP(S)_PROXY. Иначе локальные запросы
            # могут уходить в прокси и зависать по ReadTimeout.
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_all_tasks(self, sheet_name: str) -> list[dict[str, Any]]:
        path = f"/api/v1/sheets/tasks/{quote(sheet_name, safe='')}"
        try:
            response = await self._client.get(path)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = _error_detail_from_response(error.response)
            logger.exception(
                "Ошибка API get_all_tasks (%s): %s",
                sheet_name,
                detail,
            )
            raise SheetsServiceError(detail) from error
        except httpx.HTTPError as error:
            logger.exception("Сетевая ошибка API get_all_tasks (%s): %s", sheet_name, error)
            raise SheetsServiceError("Не удалось получить данные из сервиса.") from error

        data = response.json()
        return list(data.get("tasks", []))

    async def append_task(self, sheet_name: str, row_data: list[Any]) -> None:
        try:
            response = await self._client.post(
                "/api/v1/sheets/tasks/append",
                json={"sheet_name": sheet_name, "row_data": row_data},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = _error_detail_from_response(error.response)
            logger.exception("Ошибка API append_task: %s", detail)
            raise SheetsServiceError(detail) from error
        except httpx.HTTPError as error:
            logger.exception("Сетевая ошибка API append_task: %s", error)
            raise SheetsServiceError("Не удалось сохранить задачу в сервисе.") from error

    async def update_cell(self, sheet_name: str, row_index: int, col_index: int, value: Any) -> None:
        try:
            response = await self._client.patch(
                "/api/v1/sheets/cell",
                json={
                    "sheet_name": sheet_name,
                    "row_index": row_index,
                    "col_index": col_index,
                    "value": value,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = _error_detail_from_response(error.response)
            logger.exception("Ошибка API update_cell: %s", detail)
            raise SheetsServiceError(detail) from error
        except httpx.HTTPError as error:
            logger.exception("Сетевая ошибка API update_cell: %s", error)
            raise SheetsServiceError("Не удалось обновить данные в сервисе.") from error

    async def delete_row(self, sheet_name: str, row_index: int) -> None:
        try:
            response = await self._client.delete(
                "/api/v1/sheets/rows",
                params={"sheet_name": sheet_name, "row_index": row_index},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = _error_detail_from_response(error.response)
            logger.exception("Ошибка API delete_row: %s", detail)
            raise SheetsServiceError(detail) from error
        except httpx.HTTPError as error:
            logger.exception("Сетевая ошибка API delete_row: %s", error)
            raise SheetsServiceError("Не удалось удалить строку в сервисе.") from error

    async def move_task(
        self,
        from_sheet: str,
        to_sheet: str,
        row_index: int,
        extra_data: dict[str, Any],
    ) -> None:
        try:
            response = await self._client.post(
                "/api/v1/sheets/move",
                json={
                    "from_sheet": from_sheet,
                    "to_sheet": to_sheet,
                    "row_index": row_index,
                    "extra_data": extra_data,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = _error_detail_from_response(error.response)
            logger.exception("Ошибка API move_task: %s", detail)
            raise SheetsServiceError(detail) from error
        except httpx.HTTPError as error:
            logger.exception("Сетевая ошибка API move_task: %s", error)
            raise SheetsServiceError("Не удалось перенести задачу в сервисе.") from error

    async def write_log(
        self,
        who: str,
        action: str,
        task_name: str,
        sheet_name: str,
        details: str,
    ) -> None:
        """Как в sheets.write_log: при ошибке только логируем, сценарий не прерываем."""

        try:
            response = await self._client.post(
                "/api/v1/sheets/log",
                json={
                    "who": who,
                    "action": action,
                    "task_name": task_name,
                    "sheet_name": sheet_name,
                    "details": details,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.error(
                "Не удалось записать лог через API: %s",
                error,
                exc_info=True,
            )


_default_client: APIClient | None = None


def configure_api_client(base_url: str, api_key: str) -> APIClient:
    """Создаёт singleton-клиент для вызовов как у модульных функций sheets."""

    global _default_client
    _default_client = APIClient(base_url, api_key)
    return _default_client


def get_api_client() -> APIClient:
    if _default_client is None:
        raise RuntimeError("APIClient не инициализирован. Вызовите configure_api_client().")
    return _default_client


async def get_all_tasks(sheet_name: str) -> list[dict[str, Any]]:
    return await get_api_client().get_all_tasks(sheet_name)


async def append_task(sheet_name: str, row_data: list[Any]) -> None:
    return await get_api_client().append_task(sheet_name, row_data)


async def update_cell(sheet_name: str, row_index: int, col_index: int, value: Any) -> None:
    return await get_api_client().update_cell(sheet_name, row_index, col_index, value)


async def delete_row(sheet_name: str, row_index: int) -> None:
    return await get_api_client().delete_row(sheet_name, row_index)


async def move_task(
    from_sheet: str,
    to_sheet: str,
    row_index: int,
    extra_data: dict[str, Any],
) -> None:
    return await get_api_client().move_task(from_sheet, to_sheet, row_index, extra_data)


async def write_log(
    who: str,
    action: str,
    task_name: str,
    sheet_name: str,
    details: str,
) -> None:
    return await get_api_client().write_log(who, action, task_name, sheet_name, details)
