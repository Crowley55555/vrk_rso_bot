FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PYTHONPATH=/app

WORKDIR /app

COPY telegram_bot/requirements.txt ./telegram_bot-requirements.txt
RUN pip install --upgrade pip && pip install -r telegram_bot-requirements.txt

COPY telegram_bot/bot ./bot
COPY shared ./shared

CMD ["python", "-m", "bot.main"]
