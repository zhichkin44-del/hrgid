# HRgid threat model

| Threat | Where | Control | Test |
|---|---|---|---|
| Injection in a question ("ignore the instructions…") | chat, bot | the question is data, not an instruction; the answer comes only from what was found; citation check | 5 injections in the check set (stage 2) |
| Injection in an uploaded document | stage 5 | the document text is passed as data; suspicious fragments are marked | document test with an injection (stage 5) |
| Leak of personal data into the model, logs, cache | stage 6 | masking; the label table lives only in the memory of the request; the cache key is built from the masked text | masking test, "extra label" test (stage 6) |
| Access to another company's data | search, API, bot, MCP, cache | tenant_id in every table and every query | search isolation test (stage 1), API/bot/MCP/cache (stages 2–7) |
| Abuse of MCP | stage 7 | company token, rate limiting, read-only | foreign token test (stage 7) |
| Substitution or staleness of the law text | loading | source and edition in the metadata; a content hash of each fragment | hash test (stage 1) |
| Key leak | all | environment only; `.env.example` is empty | `tests/test_ops_files.py::test_no_secrets_in_history` (stage 8) |
| Image or Compose file with secrets, process running as root | build, run | unprivileged user; secrets only from the environment or a volume; database and Redis not published | `tests/test_ops_files.py` (stage 8) |
| A weakened prompt gets into main unnoticed | CI | recording key = hash(prompt + model + set version); without recordings the gate is red | `tests/test_quality_gate.py` (stage 8) |
