from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


YANDEX_DISK_API_BASE = "https://cloud-api.yandex.net"

logger = logging.getLogger(__name__)


class YandexDiskService:
    """Клиент для безопасной работы с Яндекс Диском."""

    def __init__(self, token: str | None, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._client = client or httpx.AsyncClient(base_url=YANDEX_DISK_API_BASE, timeout=30.0)
        self._owns_client = client is None

    async def close(self) -> None:
        """Закрывает внутренний HTTP-клиент, если он создан сервисом."""
        if self._owns_client:
            await self._client.aclose()

    async def upload_file(self, local_path: Path, remote_path: str) -> bool:
        """
        Загружает локальный файл на Яндекс Диск.

        Возвращает True при успехе, иначе False.
        Ошибки наружу не выбрасываются.
        """
        if not self._is_enabled():
            return False
        if not local_path.exists() or not local_path.is_file():
            logger.error("Файл для загрузки не найден: %s", local_path)
            return False

        headers = self._auth_headers()
        if headers is None:
            return False

        try:
            response = await self._client.get(
                "/v1/disk/resources/upload",
                params={"path": remote_path, "overwrite": "true"},
                headers=headers,
            )
            response.raise_for_status()
            upload_url = response.json().get("href")
            if not upload_url:
                logger.error("Не удалось получить upload URL для %s", remote_path)
                return False

            # Передаём файл потоком в бинарном режиме без чтения целиком в память.
            with local_path.open("rb") as file_obj:
                async with httpx.AsyncClient(timeout=30.0) as upload_client:
                    put_response = await upload_client.put(
                        upload_url,
                        content=file_obj,
                        headers={"Content-Type": "application/octet-stream"},
                        follow_redirects=True,
                    )
                    put_response.raise_for_status()
            return True
        except Exception as error:
            logger.error("Ошибка загрузки файла на Яндекс Диск: %s", error, exc_info=True)
            return False

    async def get_file_modified(self, remote_path: str) -> float | None:
        """
        Возвращает modified файла в формате unix timestamp.

        Если файл не найден (404) или произошла ошибка, возвращает None.
        """
        if not self._is_enabled():
            return None

        headers = self._auth_headers()
        if headers is None:
            return None

        try:
            response = await self._client.get(
                "/v1/disk/resources",
                params={"path": remote_path},
                headers=headers,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()

            modified_raw = response.json().get("modified")
            if not modified_raw:
                return None

            # Преобразуем RFC3339 строку в unix timestamp.
            return self._parse_modified(modified_raw)
        except Exception as error:
            logger.error("Ошибка чтения metadata Яндекс Диска: %s", error, exc_info=True)
            return None

    async def download_file(self, remote_path: str, local_path: Path) -> bool:
        """
        Скачивает файл с Яндекс Диска в локальный путь.

        Возвращает True при успехе, иначе False.
        Ошибки наружу не выбрасываются.
        """
        if not self._is_enabled():
            return False

        headers = self._auth_headers()
        if headers is None:
            return False

        try:
            response = await self._client.get(
                "/v1/disk/resources/download",
                params={"path": remote_path},
                headers=headers,
            )
            if response.status_code == 404:
                return False
            response.raise_for_status()

            href = response.json().get("href")
            if not href:
                logger.error("Не удалось получить URL скачивания для %s", remote_path)
                return False

            download_response = await self._client.get(
                href,
                follow_redirects=True,
            )
            download_response.raise_for_status()

            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(download_response.content)
            return True
        except Exception as error:
            logger.error("Ошибка скачивания файла с Яндекс Диска: %s", error, exc_info=True)
            return False

    def _is_enabled(self) -> bool:
        """Проверяет, что синхронизация с Яндекс Диском включена."""
        if not self._token:
            logger.warning("YANDEX_DISK_TOKEN не задан, операции с Яндекс Диском отключены.")
            return False
        return True

    def _auth_headers(self) -> dict[str, str] | None:
        """Возвращает заголовки авторизации для OAuth-токена."""
        if not self._token:
            return None
        return {"Authorization": f"OAuth {self._token}"}

    @staticmethod
    def _parse_modified(value: Any) -> float | None:
        """Преобразует значение modified из API в unix timestamp."""
        if not isinstance(value, str):
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return None
