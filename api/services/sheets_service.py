from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from api.config import (
    ACCIDENTS_SHEET,
    COMPLETED_SHEET,
    IN_PROGRESS_SHEET,
    LOG_SHEET,
    NOT_STARTED_SHEET,
    Settings,
)
from api.services.excel_service import (
    ACCIDENTS_HEADERS,
    LOG_HEADERS,
    SUPPORTED_SHEETS,
    TASK_HEADERS,
    ExcelService,
)
from api.services.sqlite_service import SQLiteService, SQLiteServiceError
from api.services.yadisk_service import YandexDiskService


logger = logging.getLogger(__name__)


class SheetsServiceError(Exception):
    """Ошибка доступа к данным задач."""


class GoogleSheetsService:
    """Фасад сервиса данных: SQLite + локальный xlsx + Яндекс Диск."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sqlite_service = SQLiteService(settings.sqlite_path)
        self._excel_service = ExcelService(settings.excel_path)
        self._yadisk_service = YandexDiskService(settings.yandex_disk_token)
        # Общий lock для всех операций записи, включая merge из планировщика.
        self._write_lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def get_all_tasks(self, sheet_name: str) -> list[dict[str, Any]]:
        await self._ensure_initialized()
        try:
            rows = await self._sqlite_service.get_all(sheet_name)
            return [self._row_to_api_dict(sheet_name, row) for row in rows]
        except Exception as error:
            logger.exception("Не удалось получить задачи из листа %s", sheet_name)
            raise SheetsServiceError("Не удалось получить данные из хранилища.") from error

    async def append_task(self, sheet_name: str, row_data: list[Any]) -> None:
        await self._ensure_initialized()
        try:
            async with self._write_lock:
                await self._sqlite_service.insert(sheet_name, row_data)
                await self._export_sheet_locked(sheet_name)
        except Exception as error:
            logger.exception("Не удалось добавить задачу в лист %s", sheet_name)
            raise SheetsServiceError("Не удалось сохранить задачу в хранилище.") from error

        asyncio.create_task(self.upload_to_yadisk())

    async def update_cell(self, sheet_name: str, row_index: int, col_index: int, value: Any) -> None:
        await self._ensure_initialized()
        try:
            async with self._write_lock:
                await self._sqlite_service.update_cell(sheet_name, row_index, col_index, value)
                await self._export_sheet_locked(sheet_name)
        except Exception as error:
            logger.exception(
                "Не удалось обновить ячейку листа %s: строка=%s столбец=%s",
                sheet_name,
                row_index,
                col_index,
            )
            raise SheetsServiceError("Не удалось обновить данные в хранилище.") from error

        asyncio.create_task(self.upload_to_yadisk())

    async def delete_row(self, sheet_name: str, row_index: int) -> None:
        await self._ensure_initialized()
        try:
            async with self._write_lock:
                await self._sqlite_service.delete_by_id(sheet_name, row_index)
                await self._export_sheet_locked(sheet_name)
        except Exception as error:
            logger.exception("Не удалось удалить строку %s из листа %s", row_index, sheet_name)
            raise SheetsServiceError("Не удалось удалить строку в хранилище.") from error

        asyncio.create_task(self.upload_to_yadisk())

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

        await self._ensure_initialized()
        try:
            async with self._write_lock:
                target_row_id = await self._sqlite_service.move_row(from_sheet, to_sheet, row_index)
                normalized_row = self._normalize_row_data(row_data)
                for col_index, value in enumerate(normalized_row, start=1):
                    await self._sqlite_service.update_cell(to_sheet, target_row_id, col_index, value)

                await self._export_sheet_locked(from_sheet)
                await self._export_sheet_locked(to_sheet)
        except Exception as error:
            logger.exception(
                "Не удалось перенести задачу из листа %s в %s, строка=%s",
                from_sheet,
                to_sheet,
                row_index,
            )
            raise SheetsServiceError("Не удалось перенести задачу между листами.") from error

        asyncio.create_task(self.upload_to_yadisk())

    async def write_log(
        self,
        who: str,
        action: str,
        task_name: str,
        sheet_name: str,
        details: str,
    ) -> None:
        await self._ensure_initialized()
        try:
            async with self._write_lock:
                await self._sqlite_service.write_log(who, action, task_name, sheet_name, details)
                await self._export_sheet_locked(LOG_SHEET)
            asyncio.create_task(self.upload_to_yadisk())
        except Exception as err:
            logger.error(
                "Не удалось записать в лист «Лог»: %s",
                err,
                exc_info=True,
            )

    async def check_disk_changes(self) -> None:
        """Проверяет изменение файла на Яндекс Диске и выполняет merge."""
        await self._ensure_initialized()
        if not self._settings.yandex_disk_token:
            return

        try:
            modified = await self._yadisk_service.get_file_modified(self._settings.yandex_disk_remote_path)
            if modified is None:
                return

            last_known_raw = await self._sqlite_service.get_sync_meta("last_disk_modified")
            last_known = self._safe_float(last_known_raw, default=0.0)
            if modified <= last_known:
                return

            downloaded = await self._yadisk_service.download_file(
                self._settings.yandex_disk_remote_path,
                self._settings.excel_path,
            )
            if not downloaded:
                return

            async with self._write_lock:
                for sheet_name in SUPPORTED_SHEETS:
                    await self._excel_service.import_sheet(
                        sheet_name,
                        self._sqlite_service,
                        disk_mtime=modified,
                    )
                await self._sqlite_service.set_sync_meta("last_disk_modified", str(modified))
        except Exception as error:
            logger.error("Ошибка синхронизации изменений с Яндекс Диска: %s", error, exc_info=True)

    async def upload_to_yadisk(self) -> bool:
        """Загружает локальный xlsx на Яндекс Диск и обновляет sync_meta."""
        await self._ensure_initialized()
        if not self._settings.yandex_disk_token:
            return False

        try:
            uploaded = await self._yadisk_service.upload_file(
                self._settings.excel_path,
                self._settings.yandex_disk_remote_path,
            )
            if not uploaded:
                return False

            now_ts = time.time()
            await self._sqlite_service.set_sync_meta("last_upload_at", str(now_ts))

            modified = await self._yadisk_service.get_file_modified(self._settings.yandex_disk_remote_path)
            if modified is not None:
                await self._sqlite_service.set_sync_meta("last_disk_modified", str(modified))
            return True
        except Exception as error:
            logger.error("Ошибка загрузки файла на Яндекс Диск: %s", error, exc_info=True)
            return False

    async def close(self) -> None:
        """Закрывает внутренние ресурсы сервиса."""
        await self._yadisk_service.close()
        await self._sqlite_service.close()

    async def _ensure_initialized(self) -> None:
        """Ленивая инициализация SQLite при первом обращении."""
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return
            await self._sqlite_service.initialize()
            self._initialized = True

    async def _export_sheet_locked(self, sheet_name: str) -> None:
        """Экспортирует один лист в xlsx (вызывать только внутри _write_lock)."""
        rows = await self._sqlite_service.get_all(sheet_name)
        await asyncio.to_thread(self._excel_service.export_sheet, sheet_name, rows)

    def _row_to_api_dict(self, sheet_name: str, db_row: dict[str, Any]) -> dict[str, Any]:
        """Преобразует строку SQLite в формат ответа API."""
        headers = self._headers_for_sheet(sheet_name)
        values = [
            db_row.get("col_a", ""),
            db_row.get("col_b", ""),
            db_row.get("col_c", ""),
            db_row.get("col_d", ""),
            db_row.get("col_e", ""),
            db_row.get("col_f", ""),
        ]
        payload = {headers[index]: values[index] for index in range(6)}
        payload["row_index"] = int(db_row["id"])
        return payload

    @staticmethod
    def _normalize_row_data(row_data: list[Any]) -> list[str]:
        """Нормализует значения до шести колонок A-F."""
        values = ["" if value is None else str(value) for value in row_data[:6]]
        if len(values) < 6:
            values.extend([""] * (6 - len(values)))
        return values

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _headers_for_sheet(sheet_name: str) -> list[str]:
        if sheet_name == ACCIDENTS_SHEET:
            return ACCIDENTS_HEADERS.copy()
        if sheet_name == LOG_SHEET:
            return LOG_HEADERS.copy()
        if sheet_name in {NOT_STARTED_SHEET, IN_PROGRESS_SHEET, COMPLETED_SHEET}:
            return TASK_HEADERS.copy()
        raise SheetsServiceError(f"Неизвестный лист: «{sheet_name}».")
