"""Подсказки при локальном запуске рядом с настройками под Docker Compose."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

_log = logging.getLogger(__name__)


def warn_if_api_base_url_uses_docker_hostname(api_base_url: str) -> None:
    """Подсказки по типичным ошибкам API_BASE_URL при запуске не в compose."""

    _warn_docker_service_hostname(api_base_url)
    _warn_typo_port_800_instead_of_8000(api_base_url)


def _warn_docker_service_hostname(api_base_url: str) -> None:
    if urlparse(api_base_url).hostname != "api":
        return
    if Path("/.dockerenv").is_file():
        return
    _log.warning(
        "API_BASE_URL=%s: хост «api» доступен только внутри Docker Compose. "
        "Для запуска бота на этой машине задайте API_BASE_URL=http://127.0.0.1:8000 "
        "и поднимите API (uvicorn) на порту 8000.",
        api_base_url,
    )


def _warn_typo_port_800_instead_of_8000(api_base_url: str) -> None:
    if urlparse(api_base_url).port != 800:
        return
    _log.warning(
        "API_BASE_URL=%s: указан порт 800 — в этом проекте API обычно слушает 8000 "
        "(uvicorn --port 8000). Исправьте на http://127.0.0.1:8000",
        api_base_url,
    )
