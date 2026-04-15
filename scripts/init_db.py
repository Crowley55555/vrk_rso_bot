from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Добавляем корень проекта в путь импорта для standalone-запуска скрипта.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.config import Settings, load_settings  # noqa: E402
from api.services.excel_service import SUPPORTED_SHEETS, ExcelService  # noqa: E402
from api.services.sqlite_service import SQLiteService  # noqa: E402
from api.services.yadisk_service import YandexDiskService  # noqa: E402


logger = logging.getLogger("init_db")


def configure_logging() -> None:
    """Настраивает логирование скрипта инициализации."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _load_settings_for_init() -> Settings:
    """
    Загружает настройки через load_settings().

    Если файл EXCEL_PATH ещё не создан, подготавливает временные настройки для bootstrap.
    """
    try:
        return load_settings()
    except ValueError as error:
        message = str(error)
        if "Файл EXCEL_PATH не найден" not in message:
            raise

        logger.warning("EXCEL_PATH ещё не существует, продолжаем bootstrap: %s", message)
        load_dotenv(PROJECT_ROOT / ".env")

        api_key = os.getenv("API_KEY", "").strip()
        sqlite_raw = os.getenv("SQLITE_PATH", "").strip()
        excel_raw = os.getenv("EXCEL_PATH", "").strip()
        yandex_disk_token = os.getenv("YANDEX_DISK_TOKEN", "").strip() or None
        yandex_disk_remote_path = os.getenv("YANDEX_DISK_REMOTE_PATH", "").strip()
        disk_check_interval_raw = os.getenv("DISK_CHECK_INTERVAL", "30").strip()
        yadisk_upload_interval_raw = os.getenv("YADISK_UPLOAD_INTERVAL", "300").strip()

        if not api_key:
            raise ValueError("Не задана переменная окружения API_KEY.")
        if not sqlite_raw:
            raise ValueError("Не задана переменная окружения SQLITE_PATH.")
        if not excel_raw:
            raise ValueError("Не задана переменная окружения EXCEL_PATH.")
        if not yandex_disk_remote_path:
            raise ValueError("Не задана переменная окружения YANDEX_DISK_REMOTE_PATH.")

        sqlite_path = _resolve_path(PROJECT_ROOT, sqlite_raw)
        excel_path = _resolve_path(PROJECT_ROOT, excel_raw)

        try:
            disk_check_interval = int(disk_check_interval_raw)
            yadisk_upload_interval = int(yadisk_upload_interval_raw)
        except ValueError as parse_error:
            raise ValueError("DISK_CHECK_INTERVAL и YADISK_UPLOAD_INTERVAL должны быть целыми.") from parse_error

        if disk_check_interval <= 0 or yadisk_upload_interval <= 0:
            raise ValueError("DISK_CHECK_INTERVAL и YADISK_UPLOAD_INTERVAL должны быть больше 0.")

        return Settings(
            api_key=api_key,
            sqlite_path=sqlite_path,
            excel_path=excel_path,
            yandex_disk_token=yandex_disk_token,
            yandex_disk_remote_path=yandex_disk_remote_path,
            disk_check_interval=disk_check_interval,
            yadisk_upload_interval=yadisk_upload_interval,
        )


async def _is_database_empty(sqlite_service: SQLiteService) -> bool:
    """Проверяет, что все таблицы задач и лога пока пустые."""
    for sheet_name in SUPPORTED_SHEETS:
        rows = await sqlite_service.get_all(sheet_name)
        if rows:
            return False
    return True


async def main() -> None:
    configure_logging()
    settings = _load_settings_for_init()

    sqlite_service = SQLiteService(settings.sqlite_path)
    excel_service = ExcelService(settings.excel_path)
    yadisk_service = YandexDiskService(settings.yandex_disk_token)

    disk_modified: float | None = None
    downloaded_from_disk = False

    try:
        # Шаг 1. Создаём все таблицы SQLite.
        logger.info("Шаг 1/6: создаём таблицы SQLite, если они отсутствуют.")
        await sqlite_service.initialize()
        logger.info("Таблицы SQLite готовы.")

        # Шаг 2. Проверяем файл на Яндекс Диске и при наличии скачиваем.
        logger.info("Шаг 2/6: проверяем наличие файла на Яндекс Диске.")
        if settings.yandex_disk_token:
            disk_modified = await yadisk_service.get_file_modified(settings.yandex_disk_remote_path)
            if disk_modified is not None:
                downloaded_from_disk = await yadisk_service.download_file(
                    settings.yandex_disk_remote_path,
                    settings.excel_path,
                )
                if downloaded_from_disk:
                    logger.info("Файл успешно скачан с Яндекс Диска в %s.", settings.excel_path)
                else:
                    logger.warning("Файл найден на диске, но скачать его не удалось.")
            else:
                logger.info("Файл на Яндекс Диске не найден.")
        else:
            logger.info("YANDEX_DISK_TOKEN не задан, проверка Яндекс Диска пропущена.")

        # Шаг 3. Если файла с диска нет, проверяем локальный EXCEL_PATH.
        logger.info("Шаг 3/6: проверяем локальный файл xlsx.")
        local_exists = settings.excel_path.exists() and settings.excel_path.is_file()
        if local_exists:
            logger.info("Локальный файл найден: %s", settings.excel_path)
        else:
            logger.info("Локальный файл отсутствует: %s", settings.excel_path)

        # Шаг 4. Если файла нет нигде, создаём пустой xlsx с корректными заголовками.
        logger.info("Шаг 4/6: при необходимости создаём пустой xlsx.")
        if not downloaded_from_disk and not local_exists:
            await excel_service.export_all_sheets(sqlite_service)
            logger.info("Создан новый пустой xlsx с нужными листами и заголовками: %s", settings.excel_path)
            local_exists = True
        else:
            logger.info("Создание пустого xlsx не требуется.")

        # Шаг 5. Если БД пуста и xlsx доступен, импортируем все листы в SQLite.
        logger.info("Шаг 5/6: при необходимости импортируем данные из xlsx в SQLite.")
        db_empty = await _is_database_empty(sqlite_service)
        if db_empty and local_exists:
            for sheet_name in SUPPORTED_SHEETS:
                report = await excel_service.import_sheet(sheet_name, sqlite_service, disk_mtime=disk_modified)
                logger.info(
                    "Bootstrap импорт листа %s: прочитано=%s импортировано=%s пропущено=%s причины=%s",
                    sheet_name,
                    report["read_count"],
                    report["imported_count"],
                    report["skipped_count"],
                    report["skipped_reasons"],
                )
            logger.info("Импорт из xlsx в пустую SQLite завершён.")
        else:
            logger.info("Импорт пропущен (БД уже содержит данные или xlsx недоступен).")

        # Шаг 6. Записываем начальные значения sync_meta.
        logger.info("Шаг 6/6: записываем начальные значения sync_meta.")
        await sqlite_service.set_sync_meta("last_upload_at", "0.0")
        await sqlite_service.set_sync_meta("last_disk_modified", "")
        logger.info("sync_meta инициализировано: last_upload_at=0.0, last_disk_modified=''.")

        logger.info("Инициализация завершена успешно.")
    finally:
        await yadisk_service.close()
        await sqlite_service.close()


if __name__ == "__main__":
    asyncio.run(main())
