from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from api.config import (
    ACCIDENTS_SHEET,
    COMPLETED_SHEET,
    IN_PROGRESS_SHEET,
    LOG_SHEET,
    NOT_STARTED_SHEET,
)
from api.services.sqlite_service import SQLiteService


TASK_HEADERS = ["Дата", "Наименование", "Комментарий", "Ответственные", "Срок", "Кто добавил", "_uuid"]
ACCIDENTS_HEADERS = [
    "Дата и время",
    "Краткое описание",
    "Подробное описание",
    "Ответственные",
    "Срочность",
    "Кто сообщил",
    "_uuid",
]
LOG_HEADERS = ["Дата и время", "Кто", "Действие", "Задача", "Лист", "Подробности", "_uuid"]
UUID_COLUMN_LETTER = "G"

SUPPORTED_SHEETS = [
    NOT_STARTED_SHEET,
    IN_PROGRESS_SHEET,
    COMPLETED_SHEET,
    ACCIDENTS_SHEET,
    LOG_SHEET,
]


class ExcelServiceError(Exception):
    """Ошибка при работе с xlsx-файлом."""


class ExcelService:
    """Сервис синхронизации данных между SQLite и xlsx."""

    def __init__(self, excel_path: Path) -> None:
        self._excel_path = excel_path

    def export_sheet(self, sheet_name: str, db_rows: list[dict[str, Any]]) -> None:
        """Полностью перезаписывает лист данными из БД."""
        self._ensure_parent_dir()
        workbook = self._load_or_create_workbook()
        worksheet = self._ensure_sheet(workbook, sheet_name)

        # Полностью очищаем лист и записываем актуальный срез из SQLite.
        if worksheet.max_row > 0:
            worksheet.delete_rows(1, worksheet.max_row)

        worksheet.append(self._headers_for_sheet(sheet_name))
        for row in db_rows:
            worksheet.append(
                [
                    row.get("col_a", ""),
                    row.get("col_b", ""),
                    row.get("col_c", ""),
                    row.get("col_d", ""),
                    row.get("col_e", ""),
                    row.get("col_f", ""),
                    row.get("row_uuid", ""),
                ]
            )

        # Служебный uuid всегда в колонке G и всегда скрыт.
        worksheet.column_dimensions[UUID_COLUMN_LETTER].hidden = True
        workbook.save(self._excel_path)
        workbook.close()

    async def import_sheet(self, sheet_name: str, sqlite_service: SQLiteService, disk_mtime: float | None = None) -> None:
        """Импортирует лист из xlsx в SQLite с merge по uuid."""
        effective_mtime = disk_mtime if disk_mtime is not None else self._local_file_mtime()
        disk_rows = await asyncio.to_thread(self._read_sheet_rows, sheet_name)
        db_rows = await sqlite_service.get_all(sheet_name)

        db_by_uuid = {str(row["row_uuid"]): row for row in db_rows}
        disk_by_uuid = {str(row["row_uuid"]): row for row in disk_rows}

        db_uuids = set(db_by_uuid.keys())
        disk_uuids = set(disk_by_uuid.keys())

        last_upload_raw = await sqlite_service.get_sync_meta("last_upload_at")
        last_upload_at = self._safe_float(last_upload_raw, default=0.0)

        # 1. Только на диске -> INSERT в SQLite.
        for row_uuid in sorted(disk_uuids - db_uuids):
            disk_row = disk_by_uuid[row_uuid]
            await sqlite_service.insert(
                sheet_name,
                disk_row["row_data"],
                row_uuid=row_uuid,
                created_at=effective_mtime,
                updated_at=effective_mtime,
            )

        # 2. Только в БД -> удаляем только то, что уже точно было выгружено на диск.
        for row_uuid in sorted(db_uuids - disk_uuids):
            db_row = db_by_uuid[row_uuid]
            created_at = self._safe_float(db_row.get("created_at"), default=0.0)
            if created_at > last_upload_at:
                continue
            await sqlite_service.delete_by_id(sheet_name, int(db_row["id"]))

        # 3. Есть и в БД и на диске -> если диск свежее, обновляем SQLite.
        if effective_mtime > 0:
            for row_uuid in sorted(db_uuids & disk_uuids):
                db_row = db_by_uuid[row_uuid]
                db_updated_at = self._safe_float(db_row.get("updated_at"), default=0.0)
                if effective_mtime <= db_updated_at:
                    continue

                row_id = int(db_row["id"])
                disk_values = disk_by_uuid[row_uuid]["row_data"]
                for col_index, value in enumerate(disk_values, start=1):
                    await sqlite_service.update_cell(sheet_name, row_id, col_index, value)

    async def export_all_sheets(self, sqlite_service: SQLiteService) -> None:
        """Выгружает все поддерживаемые листы из SQLite в xlsx."""
        rows_by_sheet: dict[str, list[dict[str, Any]]] = {}
        for sheet_name in SUPPORTED_SHEETS:
            rows_by_sheet[sheet_name] = await sqlite_service.get_all(sheet_name)

        await asyncio.to_thread(self._export_all_sheets_sync, rows_by_sheet)

    def _export_all_sheets_sync(self, rows_by_sheet: dict[str, list[dict[str, Any]]]) -> None:
        """Синхронная часть полной выгрузки всех листов."""
        self._ensure_parent_dir()
        workbook = self._load_or_create_workbook()

        for sheet_name in SUPPORTED_SHEETS:
            worksheet = self._ensure_sheet(workbook, sheet_name)
            if worksheet.max_row > 0:
                worksheet.delete_rows(1, worksheet.max_row)

            worksheet.append(self._headers_for_sheet(sheet_name))
            for row in rows_by_sheet.get(sheet_name, []):
                worksheet.append(
                    [
                        row.get("col_a", ""),
                        row.get("col_b", ""),
                        row.get("col_c", ""),
                        row.get("col_d", ""),
                        row.get("col_e", ""),
                        row.get("col_f", ""),
                        row.get("row_uuid", ""),
                    ]
                )
            worksheet.column_dimensions[UUID_COLUMN_LETTER].hidden = True

        workbook.save(self._excel_path)
        workbook.close()

    def _read_sheet_rows(self, sheet_name: str) -> list[dict[str, Any]]:
        """Читает строки листа и возвращает данные в формате merge."""
        workbook = self._load_or_create_workbook()
        worksheet = self._ensure_sheet(workbook, sheet_name)

        rows: list[dict[str, Any]] = []
        max_row = worksheet.max_row
        for row_idx in range(2, max_row + 1):
            values = [self._to_text(worksheet.cell(row=row_idx, column=col).value) for col in range(1, 8)]
            row_uuid = values[6].strip()
            if not any(values[:6]) and not row_uuid:
                continue
            if not row_uuid:
                row_uuid = str(uuid.uuid4())
            rows.append(
                {
                    "row_uuid": row_uuid,
                    "row_data": values[:6],
                }
            )

        workbook.close()
        return rows

    def _load_or_create_workbook(self) -> Workbook:
        if self._excel_path.exists():
            return load_workbook(self._excel_path)

        workbook = Workbook()
        # Удаляем дефолтный лист, чтобы создать строго нужную структуру.
        default_sheet = workbook.active
        workbook.remove(default_sheet)
        for sheet_name in SUPPORTED_SHEETS:
            worksheet = workbook.create_sheet(sheet_name)
            worksheet.append(self._headers_for_sheet(sheet_name))
            worksheet.column_dimensions[UUID_COLUMN_LETTER].hidden = True
        workbook.save(self._excel_path)
        return workbook

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    def _local_file_mtime(self) -> float:
        """Возвращает mtime локального xlsx для сравнения свежести."""
        if not self._excel_path.exists():
            return 0.0
        return float(self._excel_path.stat().st_mtime)

    def _ensure_parent_dir(self) -> None:
        self._excel_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _headers_for_sheet(sheet_name: str) -> list[str]:
        if sheet_name == ACCIDENTS_SHEET:
            return ACCIDENTS_HEADERS.copy()
        if sheet_name == LOG_SHEET:
            return LOG_HEADERS.copy()
        return TASK_HEADERS.copy()

    @staticmethod
    def _ensure_sheet(workbook: Workbook, sheet_name: str):
        if sheet_name in workbook.sheetnames:
            return workbook[sheet_name]
        return workbook.create_sheet(sheet_name)
