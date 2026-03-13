from __future__ import annotations

"""
Упрощённая точка входа для локального запуска.

Позволяет запускать бота командой:

    py -3 run.py        (Windows)
    python3 run.py      (Linux / macOS)
"""

from bot.main import main


if __name__ == "__main__":
    main()

