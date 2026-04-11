from __future__ import annotations

"""
Локальный запуск Telegram-бота (код в telegram_bot/, общий клиент в shared/).

Пример:
    py -3 run.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "telegram_bot"))

from bot.main import main

if __name__ == "__main__":
    main()
