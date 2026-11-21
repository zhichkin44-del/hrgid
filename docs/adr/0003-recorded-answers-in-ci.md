# ADR-0003. Recorded model answers in CI instead of a live run

Decision. In CI the check set runs on recordings; the recording key = hash(prompt + model + set version). Changing a prompt without updating the recordings fails the test. A live run updates the recordings manually, with the owner's key.

Rejected. A live run in every CI: cost, non-determinism, a secret in CI.
