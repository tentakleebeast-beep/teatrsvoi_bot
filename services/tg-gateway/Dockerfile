# tg-gateway: транспорт Telegram (aiogram, polling).
# Контекст сборки — корень репозитория (см. docker-compose.yml).
# Структура каталогов внутри образа повторяет репозиторий: код читает манифесты
# и правила календаря относительно корня.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY services/tg-gateway/app ./services/tg-gateway/app
# Общие помощники обоих сервисов (services/shared/format.py, R-6): формат сумм.
COPY services/shared ./services/shared
COPY bot ./bot
COPY config ./config

CMD ["python", "services/tg-gateway/app/main.py"]
