from __future__ import annotations

import asyncio
import logging
from typing import Any

import gspread

from bot.config import Settings


logger = logging.getLogger(__name__)


class SheetsServiceError(Exception):
    """Ошибка доступа к Google Sheets."""


class GoogleSheetsService:
    """Сервис для чтения и записи задач в Google Sheets."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = gspread.service_account(filename=str(settings.google_credentials_file))
        self._spreadsheet = self._client.open_by_key(settings.spreadsheet_id)
        self._write_lock = asyncio.Lock()

    async def get_all_tasks(self, sheet_name: str) -> list[dict[str, Any]]:
        """Возвращает все непустые строки листа в виде словарей."""

        try:
            return await asyncio.to_thread(self._get_all_tasks_sync, sheet_name)
        except Exception as error:  # pragma: no cover - внешнее API
            logger.exception("Не удалось получить задачи из листа %s", sheet_name)
            raise SheetsServiceError("Не удалось получить данные из Google Sheets.") from error

    async def append_task(self, sheet_name: str, row_data: list[Any]) -> None:
        """Добавляет новую строку в указанный лист."""

        try:
            async with self._write_lock:
                await asyncio.to_thread(self._append_task_sync, sheet_name, row_data)
        except Exception as error:  # pragma: no cover - внешнее API
            logger.exception("Не удалось добавить задачу в лист %s", sheet_name)
            raise SheetsServiceError("Не удалось сохранить задачу в Google Sheets.") from error

    async def update_cell(self, sheet_name: str, row_index: int, col_index: int, value: Any) -> None:
        """Обновляет одну ячейку."""

        try:
            async with self._write_lock:
                await asyncio.to_thread(self._update_cell_sync, sheet_name, row_index, col_index, value)
        except Exception as error:  # pragma: no cover - внешнее API
            logger.exception(
                "Не удалось обновить ячейку листа %s: строка=%s столбец=%s",
                sheet_name,
                row_index,
                col_index,
            )
            raise SheetsServiceError("Не удалось обновить данные в Google Sheets.") from error

    async def delete_row(self, sheet_name: str, row_index: int) -> None:
        """Удаляет строку из листа."""

        try:
            async with self._write_lock:
                await asyncio.to_thread(self._delete_row_sync, sheet_name, row_index)
        except Exception as error:  # pragma: no cover - внешнее API
            logger.exception("Не удалось удалить строку %s из листа %s", row_index, sheet_name)
            raise SheetsServiceError("Не удалось удалить строку в Google Sheets.") from error

    async def move_task(
        self,
        from_sheet: str,
        to_sheet: str,
        row_index: int,
        extra_data: dict[str, Any],
    ) -> None:
        """Атомарно переносит задачу между листами: сначала добавляет, затем удаляет."""

        row_data = extra_data.get("row_data")
        if not isinstance(row_data, list):
            raise SheetsServiceError("Для переноса задачи не переданы данные строки.")

        try:
            async with self._write_lock:
                await asyncio.to_thread(
                    self._move_task_sync,
                    from_sheet,
                    to_sheet,
                    row_index,
                    row_data,
                )
        except Exception as error:  # pragma: no cover - внешнее API
            logger.exception(
                "Не удалось перенести задачу из листа %s в %s, строка=%s",
                from_sheet,
                to_sheet,
                row_index,
            )
            raise SheetsServiceError("Не удалось перенести задачу между листами.") from error

    def _worksheet(self, sheet_name: str) -> gspread.Worksheet:
        """Возвращает объект листа по имени."""

        return self._spreadsheet.worksheet(sheet_name)

    def _get_all_tasks_sync(self, sheet_name: str) -> list[dict[str, Any]]:
        worksheet = self._worksheet(sheet_name)
        rows = worksheet.get_all_values()
        if not rows:
            return []

        raw_headers = rows[0]
        headers = [
            header.strip() if header.strip() else f"column_{index + 1}"
            for index, header in enumerate(raw_headers)
        ]

        tasks: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows[1:], start=2):
            if not any(cell.strip() for cell in row):
                continue
            padded_row = row + [""] * (len(headers) - len(row))
            task = {headers[index]: padded_row[index] for index in range(len(headers))}
            task["row_index"] = row_index
            tasks.append(task)
        return tasks

    def _append_task_sync(self, sheet_name: str, row_data: list[Any]) -> None:
        worksheet = self._worksheet(sheet_name)
        worksheet.append_row(row_data, value_input_option="USER_ENTERED")

    def _update_cell_sync(self, sheet_name: str, row_index: int, col_index: int, value: Any) -> None:
        worksheet = self._worksheet(sheet_name)
        worksheet.update_cell(row_index, col_index, value)

    def _delete_row_sync(self, sheet_name: str, row_index: int) -> None:
        worksheet = self._worksheet(sheet_name)
        worksheet.delete_rows(row_index)

    def _move_task_sync(
        self,
        from_sheet: str,
        to_sheet: str,
        row_index: int,
        row_data: list[Any],
    ) -> None:
        target = self._worksheet(to_sheet)
        source = self._worksheet(from_sheet)
        target.append_row(row_data, value_input_option="USER_ENTERED")
        source.delete_rows(row_index)


_service: GoogleSheetsService | None = None


def setup_sheets(settings: Settings) -> None:
    """Инициализирует singleton-сервис работы с Google Sheets."""

    global _service
    _service = GoogleSheetsService(settings)


def _get_service() -> GoogleSheetsService:
    if _service is None:
        raise RuntimeError("Сервис Google Sheets не инициализирован.")
    return _service


async def get_all_tasks(sheet_name: str) -> list[dict[str, Any]]:
    """Возвращает все задачи указанного листа."""

    return await _get_service().get_all_tasks(sheet_name)


async def append_task(sheet_name: str, row_data: list[Any]) -> None:
    """Добавляет задачу в указанный лист."""

    await _get_service().append_task(sheet_name, row_data)


async def update_cell(sheet_name: str, row_index: int, col_index: int, value: Any) -> None:
    """Обновляет одну ячейку в листе."""

    await _get_service().update_cell(sheet_name, row_index, col_index, value)


async def delete_row(sheet_name: str, row_index: int) -> None:
    """Удаляет строку из указанного листа."""

    await _get_service().delete_row(sheet_name, row_index)


async def move_task(
    from_sheet: str,
    to_sheet: str,
    row_index: int,
    extra_data: dict[str, Any],
) -> None:
    """Перемещает задачу между листами с блокировкой на запись."""

    await _get_service().move_task(from_sheet, to_sheet, row_index, extra_data)
