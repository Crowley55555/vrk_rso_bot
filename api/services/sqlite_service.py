from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from api.config import (
    ACCIDENTS_SHEET,
    APP_TIMEZONE,
    COMPLETED_SHEET,
    IN_PROGRESS_SHEET,
    LOG_SHEET,
    NOT_STARTED_SHEET,
)


# Маппинг имён листов на таблицы SQLite.
SHEET_TO_TABLE: dict[str, str] = {
    NOT_STARTED_SHEET: "tasks_todo",
    IN_PROGRESS_SHEET: "tasks_in_progress",
    COMPLETED_SHEET: "tasks_done",
    ACCIDENTS_SHEET: "tasks_accidents",
    LOG_SHEET: "tasks_log",
}


class SQLiteServiceError(Exception):
    """Ошибка при работе с SQLite."""


class SQLiteService:
    """Сервис для операций с SQLite как источником правды."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        # Единый lock на все операции записи.
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Открывает соединение и создаёт необходимые таблицы."""
        if self._conn is not None:
            return

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self._create_tables()

    async def close(self) -> None:
        """Закрывает соединение с базой."""
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def get_all(self, sheet_name: str) -> list[dict[str, Any]]:
        """Возвращает все строки листа в порядке id по возрастанию."""
        table = self._table_for_sheet(sheet_name)
        conn = self._require_connection()
        query = (
            f"SELECT id, row_uuid, col_a, col_b, col_c, col_d, col_e, col_f, row_order, created_at, updated_at "
            f"FROM {table} ORDER BY row_order ASC, id ASC"
        )
        async with conn.execute(query) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def insert(
        self,
        sheet_name: str,
        row_data: list[Any],
        *,
        row_uuid: str | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
        row_order: int = 0,
    ) -> int:
        """Добавляет строку в таблицу и возвращает её id."""
        table = self._table_for_sheet(sheet_name)
        conn = self._require_connection()
        values = self._normalize_row_data(row_data)
        now_ts = time.time()
        created = created_at if created_at is not None else now_ts
        updated = updated_at if updated_at is not None else now_ts
        row_uuid_value = row_uuid or str(uuid.uuid4())

        async with self._write_lock:
            cursor = await conn.execute(
                f"""
                INSERT INTO {table} (
                    row_uuid, col_a, col_b, col_c, col_d, col_e, col_f, row_order, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_uuid_value,
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    values[4],
                    values[5],
                    row_order,
                    created,
                    updated,
                ),
            )
            await conn.commit()
            return int(cursor.lastrowid)

    async def update_row_order(self, sheet_name: str, row_id: int, row_order: int) -> None:
        """Обновляет порядок строки (row_order) по row_id."""
        table = self._table_for_sheet(sheet_name)
        conn = self._require_connection()

        async with self._write_lock:
            cursor = await conn.execute(
                f"UPDATE {table} SET row_order = ?, updated_at = ? WHERE id = ?",
                (row_order, time.time(), row_id),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise SQLiteServiceError(f"Строка id={row_id} не найдена в листе «{sheet_name}».")

    async def update_cell(self, sheet_name: str, row_id: int, col_index: int, value: Any) -> None:
        """Обновляет одну ячейку по row_id (row_index=id)."""
        table = self._table_for_sheet(sheet_name)
        conn = self._require_connection()
        column = self._column_name_from_index(col_index)

        async with self._write_lock:
            cursor = await conn.execute(
                f"UPDATE {table} SET {column} = ?, updated_at = ? WHERE id = ?",
                (self._to_text(value), time.time(), row_id),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise SQLiteServiceError(f"Строка id={row_id} не найдена в листе «{sheet_name}».")

    async def update_row(self, sheet_name: str, row_id: int, row_data: list[Any]) -> None:
        """Обновляет строку целиком по колонкам A-F одной SQL-операцией."""
        table = self._table_for_sheet(sheet_name)
        conn = self._require_connection()
        values = self._normalize_row_data(row_data)

        async with self._write_lock:
            cursor = await conn.execute(
                f"""
                UPDATE {table}
                SET col_a = ?, col_b = ?, col_c = ?, col_d = ?, col_e = ?, col_f = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    values[4],
                    values[5],
                    time.time(),
                    row_id,
                ),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                raise SQLiteServiceError(f"Строка id={row_id} не найдена в листе «{sheet_name}».")

    async def delete_by_id(self, sheet_name: str, row_id: int) -> None:
        """Удаляет строку по row_id (id не пересчитывается)."""
        table = self._table_for_sheet(sheet_name)
        conn = self._require_connection()

        async with self._write_lock:
            cursor = await conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
            await conn.commit()
            if cursor.rowcount == 0:
                raise SQLiteServiceError(f"Строка id={row_id} не найдена в листе «{sheet_name}».")

    async def move_row(self, from_sheet: str, to_sheet: str, row_id: int) -> int:
        """Переносит строку между таблицами и возвращает новый id в целевой таблице."""
        source_table = self._table_for_sheet(from_sheet)
        target_table = self._table_for_sheet(to_sheet)
        conn = self._require_connection()

        async with self._write_lock:
            await conn.execute("BEGIN")
            try:
                async with conn.execute(
                    f"""
                    SELECT row_uuid, col_a, col_b, col_c, col_d, col_e, col_f, row_order, created_at
                    FROM {source_table}
                    WHERE id = ?
                    """,
                    (row_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if row is None:
                    raise SQLiteServiceError(f"Строка id={row_id} не найдена в листе «{from_sheet}».")

                new_updated_at = time.time()
                insert_cursor = await conn.execute(
                    f"""
                    INSERT INTO {target_table} (
                        row_uuid, col_a, col_b, col_c, col_d, col_e, col_f, row_order, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["row_uuid"],
                        row["col_a"],
                        row["col_b"],
                        row["col_c"],
                        row["col_d"],
                        row["col_e"],
                        row["col_f"],
                        row["row_order"],
                        row["created_at"],
                        new_updated_at,
                    ),
                )
                await conn.execute(f"DELETE FROM {source_table} WHERE id = ?", (row_id,))
                await conn.commit()
                return int(insert_cursor.lastrowid)
            except Exception:
                await conn.rollback()
                raise

    async def get_uuids(self, sheet_name: str) -> set[str]:
        """Возвращает набор uuid для листа."""
        table = self._table_for_sheet(sheet_name)
        conn = self._require_connection()
        async with conn.execute(f"SELECT row_uuid FROM {table}") as cursor:
            rows = await cursor.fetchall()
        return {str(row["row_uuid"]) for row in rows}

    async def get_rows_created_after(self, sheet_name: str, ts: float) -> list[dict[str, Any]]:
        """Возвращает строки, созданные позже указанного времени."""
        table = self._table_for_sheet(sheet_name)
        conn = self._require_connection()
        query = (
            f"SELECT id, row_uuid, col_a, col_b, col_c, col_d, col_e, col_f, row_order, created_at, updated_at "
            f"FROM {table} WHERE created_at > ? ORDER BY row_order ASC, id ASC"
        )
        async with conn.execute(query, (ts,)) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def write_log(
        self,
        who: str,
        action: str,
        task_name: str,
        sheet_name: str,
        details: str,
    ) -> int:
        """Пишет запись в таблицу лога и возвращает id новой строки."""
        date_time = datetime.now(APP_TIMEZONE).strftime("%d.%m.%Y %H:%M:%S")
        return await self.insert(
            LOG_SHEET,
            [date_time, who, action, task_name, sheet_name, details],
        )

    async def get_sync_meta(self, key: str) -> str | None:
        """Читает значение ключа из sync_meta."""
        conn = self._require_connection()
        async with conn.execute("SELECT value FROM sync_meta WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return str(row["value"])

    async def set_sync_meta(self, key: str, value: str) -> None:
        """Создаёт или обновляет значение ключа в sync_meta."""
        conn = self._require_connection()
        async with self._write_lock:
            await conn.execute(
                """
                INSERT INTO sync_meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            await conn.commit()

    async def _create_tables(self) -> None:
        """Создаёт таблицы данных и метаданных синхронизации."""
        conn = self._require_connection()
        async with self._write_lock:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks_todo (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    row_uuid    TEXT NOT NULL UNIQUE,
                    col_a       TEXT DEFAULT '',
                    col_b       TEXT DEFAULT '',
                    col_c       TEXT DEFAULT '',
                    col_d       TEXT DEFAULT '',
                    col_e       TEXT DEFAULT '',
                    col_f       TEXT DEFAULT '',
                    row_order   INTEGER DEFAULT 0,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks_in_progress (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    row_uuid    TEXT NOT NULL UNIQUE,
                    col_a       TEXT DEFAULT '',
                    col_b       TEXT DEFAULT '',
                    col_c       TEXT DEFAULT '',
                    col_d       TEXT DEFAULT '',
                    col_e       TEXT DEFAULT '',
                    col_f       TEXT DEFAULT '',
                    row_order   INTEGER DEFAULT 0,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks_done (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    row_uuid    TEXT NOT NULL UNIQUE,
                    col_a       TEXT DEFAULT '',
                    col_b       TEXT DEFAULT '',
                    col_c       TEXT DEFAULT '',
                    col_d       TEXT DEFAULT '',
                    col_e       TEXT DEFAULT '',
                    col_f       TEXT DEFAULT '',
                    row_order   INTEGER DEFAULT 0,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks_accidents (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    row_uuid    TEXT NOT NULL UNIQUE,
                    col_a       TEXT DEFAULT '',
                    col_b       TEXT DEFAULT '',
                    col_c       TEXT DEFAULT '',
                    col_d       TEXT DEFAULT '',
                    col_e       TEXT DEFAULT '',
                    col_f       TEXT DEFAULT '',
                    row_order   INTEGER DEFAULT 0,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    row_uuid    TEXT NOT NULL UNIQUE,
                    col_a       TEXT DEFAULT '',
                    col_b       TEXT DEFAULT '',
                    col_c       TEXT DEFAULT '',
                    col_d       TEXT DEFAULT '',
                    col_e       TEXT DEFAULT '',
                    col_f       TEXT DEFAULT '',
                    row_order   INTEGER DEFAULT 0,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_meta (
                    key     TEXT PRIMARY KEY,
                    value   TEXT NOT NULL
                );
                """
            )

            # Добавляем колонку row_order в уже существующие таблицы, если её ещё нет.
            for table_name in (
                "tasks_todo",
                "tasks_in_progress",
                "tasks_done",
                "tasks_accidents",
                "tasks_log",
            ):
                try:
                    await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN row_order INTEGER DEFAULT 0;")
                except aiosqlite.OperationalError as error:
                    if "duplicate column name" in str(error).lower():
                        # Колонка уже существует — это нормальный сценарий повторной миграции.
                        pass
                    else:
                        raise
            await conn.commit()

    def _table_for_sheet(self, sheet_name: str) -> str:
        table = SHEET_TO_TABLE.get(sheet_name)
        if table is None:
            raise SQLiteServiceError(f"Неизвестный лист: «{sheet_name}».")
        return table

    def _require_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise SQLiteServiceError("SQLiteService не инициализирован. Вызовите initialize().")
        return self._conn

    @staticmethod
    def _normalize_row_data(row_data: list[Any]) -> list[str]:
        # Приводим данные к колонкам A-F без пересчёта индексов строк.
        values = [SQLiteService._to_text(value) for value in row_data[:6]]
        if len(values) < 6:
            values.extend([""] * (6 - len(values)))
        return values

    @staticmethod
    def _column_name_from_index(col_index: int) -> str:
        mapping = {
            1: "col_a",
            2: "col_b",
            3: "col_c",
            4: "col_d",
            5: "col_e",
            6: "col_f",
        }
        column = mapping.get(col_index)
        if column is None:
            raise SQLiteServiceError("col_index должен быть в диапазоне 1..6.")
        return column

    @staticmethod
    def _to_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value)
