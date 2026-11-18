PY ?= python

.PHONY: test lint record eval

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
