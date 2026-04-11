FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PYTHONPATH=/app

WORKDIR /app

COPY max_bot/requirements.txt ./max_bot-requirements.txt
RUN pip install --upgrade pip && pip install -r max_bot-requirements.txt

COPY max_bot ./max_bot
COPY shared ./shared

CMD ["python", "-m", "max_bot.main"]
