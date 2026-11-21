# ADR-0007. One image for all roles and a start without keys

Status: accepted (stage 8).

## Decision
- One `Dockerfile` (multi-stage: dependencies → model weights → runtime image) and one entry point
  `python -m hrgid.ops.launch <role>`: init, api, worker, bot, mcp. The e5 and reranker weights live in a
  separate layer: a code change does not re-download gigabytes; in the runtime image `HF_HUB_OFFLINE=1`.
- The runtime image runs as an unprivileged user (uid 10001); secrets do not get into the image. The hash
  peppers (`HRGID_PANEL_PEPPER`, `HRGID_CODE_PEPPER`), if not set, are generated at random on the first start
  and live in the `state` volume.
- `docker compose up` from a clean clone must be green without a single key: the model defaults to
  `fake` (all answers are "no answer", search, the panel and the web chat work), the `bot` without a token and
  `mcp` without a module wait and write the reason to the log instead of crashing into an endless restart.
- One-time initialization is a separate `init` service (schema, loading the law and indexing on the first
  start, the first company); the others wait for it with `service_completed_successfully`. The law is not stored in the
  repository: it is downloaded on the first start (network required) and kept in a volume.
- Sign-in codes for the panel without SMTP are written as files to `/var/lib/hrgid/outbox` (volume). This is the
  self-hosted install mode; real email is a separate decision of the owner.
- `worker`: deletion of traces older than the retention period (§9, 1 year) and a liveness signal to Redis. The
  Telegram outbox delivery is done by the bot: it already has a background task and access to the Telegram API.

## Rejected
- A separate image per role: five builds of torch just to run on one machine.
- Keys in `docker-compose.yml` or in the image: violates §9 and the threat model.
- A mandatory `.env`: a clean clone would stop starting with one command.
- Downloading the weights at the first start: a race between services for disk and network, no reproducibility.

## Consequences
The first start is long (the law ≈ 600 articles, indexing on CPU). The Compose check in CI is the
`compose-smoke` job; on a development machine without Docker the files are checked statically (`tests/test_ops_files.py`).
