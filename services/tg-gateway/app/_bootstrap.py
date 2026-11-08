"""Корень репозитория в sys.path — до импорта `services.shared`.

При запуске файлом (`python services/tg-gateway/app/main.py`) sys.path[0] —
каталог app, и пакет `services.shared` (общие форматы, R-6) без этого не
находится. В контейнере путь уже задан PYTHONPATH=/app, поэтому вставка
срабатывает только если корня там ещё нет. Импорт идемпотентен.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
