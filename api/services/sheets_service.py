from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import gspread

from api.config import APP_TIMEZONE, LOG_SHEET, Settings


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
        try:
            return await asyncio.to_thread(self._get_all_tasks_sync, sheet_name)
        except Exception as error:
            logger.exception("Не удалось получить задачи из листа %s", sheet_name)
            raise SheetsServiceError("Не удалось получить данные из Google Sheets.") from error

    async def append_task(self, sheet_name: str, row_data: list[Any]) -> None:
        try:
            async with self._write_lock:
                await asyncio.to_thread(self._append_task_sync, sheet_name, row_data)
        except Exception as error:
            logger.exception("Не удалось добавить задачу в лист %s", sheet_name)
            raise SheetsServiceError("Не удалось сохранить задачу в Google Sheets.") from error

    async def update_cell(self, sheet_name: str, row_index: int, col_index: int, value: Any) -> None:
        try:
            async with self._write_lock:
                await asyncio.to_thread(self._update_cell_sync, sheet_name, row_index, col_index, value)
        except Exception as error:
            logger.exception(
                "Не удалось обновить ячейку листа %s: строка=%s столбец=%s",
                sheet_name,
                row_index,
                col_index,
            )
            raise SheetsServiceError("Не удалось обновить данные в Google Sheets.") from error

    async def delete_row(self, sheet_name: str, row_index: int) -> None:
        try:
            async with self._write_lock:
                await asyncio.to_thread(self._delete_row_sync, sheet_name, row_index)
        except Exception as error:
            logger.exception("Не удалось удалить строку %s из листа %s", row_index, sheet_name)
            raise SheetsServiceError("Не удалось удалить строку в Google Sheets.") from error

    async def move_task(
        self,
        from_sheet: str,
        to_sheet: str,
        row_index: int,
        extra_data: dict[str, Any],
    ) -> None:
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
        except Exception as error:
            logger.exception(
                "Не удалось перенести задачу из листа %s в %s, строка=%s",
                from_sheet,
                to_sheet,
                row_index,
            )
            raise SheetsServiceError("Не удалось перенести задачу между листами.") from error

    def _worksheet(self, sheet_name: str) -> gspread.Worksheet:
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
        worksheet.append_row(
            row_data,
            value_input_option="USER_ENTERED",
            table_range=self._table_range_for_row(row_data),
        )

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
        target.append_row(
            row_data,
            value_input_option="USER_ENTERED",
            table_range=self._table_range_for_row(row_data),
        )
        source.delete_rows(row_index)

    async def write_log(
        self,
        who: str,
        action: str,
        task_name: str,
        sheet_name: str,
        details: str,
    ) -> None:
        try:
            async with self._write_lock:
                row = [
                    datetime.now(APP_TIMEZONE).strftime("%d.%m.%Y %H:%M:%S"),
                    who,
                    action,
                    task_name,
                    sheet_name,
                    details,
                ]
                await asyncio.to_thread(
                    self._append_task_sync,
                    LOG_SHEET,
                    row,
                )
        except Exception as err:
            logger.error(
                "Не удалось записать в лист «Лог»: %s",
                err,
                exc_info=True,
            )

    @staticmethod
    def _table_range_for_row(row_data: list[Any]) -> str:
        return f"A:{GoogleSheetsService._column_letter(len(row_data))}"

    @staticmethod
    def _column_letter(column_index: int) -> str:
        if column_index < 1:
            return "A"

        result = ""
        current = column_index
        while current > 0:
            current, remainder = divmod(current - 1, 26)
            result = chr(65 + remainder) + result
        return result
