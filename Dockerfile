# syntax=docker/dockerfile:1.7
# HRgid: один образ на все роли (api, worker, bot, mcp, init); роль выбирается командой запуска.

ARG PYTHON=python:3.12-slim

# --- 1. зависимости: слой зависит только от pyproject.toml -------------------------------------
FROM ${PYTHON} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# CPU-сборка torch: без гигабайтов CUDA
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml /tmp/pyproject.toml
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('/tmp/pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

# --- 2. веса моделей: отдельный слой, не пересобирается при правке кода ------------------------
FROM deps AS models
ENV HF_HOME=/models
RUN python -c "from huggingface_hub import snapshot_download as d; \
d('intfloat/multilingual-e5-small'); d('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"

# --- 3. рабочий образ: непривилегированный пользователь, без сети к HF ------------------------
FROM ${PYTHON} AS runtime
RUN useradd --system --uid 10001 --create-home --home-dir /home/hrgid hrgid \
    && mkdir -p /var/lib/hrgid /app && chown hrgid:hrgid /var/lib/hrgid /app
COPY --from=deps /opt/venv /opt/venv
COPY --from=models --chown=hrgid:hrgid /models /models
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    HF_HUB_OFFLINE=1 \
    HRGID_STATE_DIR=/var/lib/hrgid \
    HRGID_LAW_DIR=/var/lib/hrgid/law
WORKDIR /app
COPY --chown=hrgid:hrgid src ./src
COPY --chown=hrgid:hrgid scripts/fetch_tk.py ./scripts/fetch_tk.py
COPY --chown=hrgid:hrgid rules ./rules
COPY --chown=hrgid:hrgid data/calendar ./data/calendar
COPY --chown=hrgid:hrgid data/raw/SOURCE.json ./data/raw/SOURCE.json
USER hrgid
EXPOSE 8000
CMD ["python", "-m", "hrgid.ops.launch", "api"]
