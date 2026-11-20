PY ?= python

.PHONY: test lint record eval gate up down

test:
	$(PY) -m pytest -m "not slow"

lint:
	$(PY) -m ruff check src tests scripts
	$(PY) -m mypy

# Живая запись ответов модели ключом владельца (ANTHROPIC_API_KEY в окружении).
# После первой записи удалить tests/recordings/PENDING: CI начнёт требовать записи.
record:
	$(PY) scripts/eval_flow.py --provider $${PROVIDER:-claude} --record

# Воспроизведение записей (то же, что CI): без ключа, без сети.
eval:
	$(PY) scripts/eval_flow.py --provider $${PROVIDER:-claude}

# Ворота качества: набор на записанных ответах (то же, что CI); нет записей -> предупреждение.
gate:
	$(PY) scripts/quality_gate.py

# Всё в Docker: панель и веб-чат на http://localhost:8000
up:
	docker compose up -d --build

down:
	docker compose down
