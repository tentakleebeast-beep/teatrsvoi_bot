# tool-api: единственная точка доступа к Postgres и Google.
# Контекст сборки — корень репозитория (см. docker-compose.yml).
# Структура каталогов внутри образа повторяет репозиторий: код читает файлы
# относительно корня (bot/manifests), поэтому плоская раскладка ломается.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY services/tool-api/app ./services/tool-api/app
# Накат схемы: конфигурация Alembic, ревизии и сам SQL (единственный источник схемы).
COPY alembic.ini ./alembic.ini
COPY services/tool-api/migrations ./services/tool-api/migrations
COPY db/init ./db/init
# Общие помощники обоих сервисов (services/shared/format.py, R-6): формат сумм.
COPY services/shared ./services/shared
COPY bot ./bot
COPY config ./config

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "services/tool-api", "--host", "0.0.0.0", "--port", "8000"]
