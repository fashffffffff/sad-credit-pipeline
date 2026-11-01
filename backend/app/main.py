"""Точка входа. Запуск из корня репо: uv run uvicorn backend.app.main:app --port 8000"""

from backend.app.factory import create_app

app = create_app()
