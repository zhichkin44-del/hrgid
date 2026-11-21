# ADR-0001. Hybrid search instead of vector-only

Status: accepted (measurement figures are in reports/retrieval-*.md; the decision is reviewed against them).

Context. Legal text is rich in exact terms and article numbers: vectors capture meaning well, full-text search captures exact words.

Decision. The vector search (pgvector, e5-small) and PostgreSQL full-text search (the russian dictionary) are combined by weighted rank fusion (RRF), followed by reranking.

Rejected.
- Vector only: worse on queries with numbers and rare terms.
- Full-text only: does not find paraphrases.
