# ADR-0002. Local reranker instead of an API

Status: model choice is based on the stage 1 measurement (reports/retrieval-*.md).

Decision. A small multilingual cross-model on CPU: no external requests, no per-request cost, and the score is usable for the "no answer" threshold.

Rejected. Reranking through an API: latency and cost on every request, and the data leaves the system.

## Measurement in CI, 21.11.2026 (GitHub Actions, 4 vCPU Xeon 8573C, 40 questions on the law)

Report: eval-retrieval run 44864399153, files retrieval-report-2026-11-21.json and retrieval-select-2026-11-21.json.

| Mode | hit@5 | p50 | p95 |
|---|---|---|---|
| vector only | 40/40 (1.0) | 18 ms | 20 ms |
| hybrid (vector + full-text, RRF) | 38/40 (0.95) | 23 ms | 33 ms |
| hybrid + mmarco-minilm | 40/40 (1.0) | 1.26 s | 1.56 s |
| hybrid + bge-m3 (15 selection questions) | 15/15 | 13.5 s | 15.9 s |

Conclusion. Acceptance of stage 1 under SPEC-09 §16 is not honestly met: (1) "hybrid + reranking is better than vector only" is a tie (1.0 = 1.0), the set is saturated and the modes do not differ on it; (2) search p95 <= 400 ms (§12) is 1.56 s with the reranker.
Decision until the set is extended: the bot keeps hybrid + mmarco-minilm (best quality, fits the overall answer budget p95 <= 8 s); bge-m3 is rejected because of latency. The next step is a harder set (paraphrases without the law's words, article numbers, questions "close to the topic") where the modes will diverge; then the final choice and the "no answer" threshold (ADR-0006).
