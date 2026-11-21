PY ?= python

.PHONY: test lint record eval gate up down

test:
	$(PY) -m pytest -m "not slow"

lint:
	$(PY) -m ruff check src tests scripts
	$(PY) -m mypy

# Live recording of the model's answers with the owner's key (ANTHROPIC_API_KEY in the environment).
# After the first recording, delete tests/recordings/PENDING: CI will start requiring recordings.
record:
	$(PY) scripts/eval_flow.py --provider $${PROVIDER:-claude} --record

# Replay of recordings (same as CI): no key, no network.
eval:
	$(PY) scripts/eval_flow.py --provider $${PROVIDER:-claude}

# Quality gate: the set on recorded answers (same as CI); no recordings -> warning.
gate:
	$(PY) scripts/quality_gate.py

# Everything in Docker: the panel and web chat at http://localhost:8000
up:
	docker compose up -d --build

down:
	docker compose down
