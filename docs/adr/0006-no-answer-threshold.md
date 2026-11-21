# ADR-0006. "No answer" threshold (DRAFT, not accepted)

Status. Draft. The value is not chosen: it is picked after a live recording, before the run on the main part.

Decision (plan). If the score of the top fragment after reranking is below the threshold, the flow answers "В законе и правилах компании ответа нет" ("There is no answer in the law and the company rules") and does not call the answer model. The threshold is the parameter `FlowConfig.no_answer_threshold` (disabled by default).

How it is picked. Only on the tuning part `data/evals/tuning.jsonl` (25 questions: 15 law, 10 "no answer"): the top-fragment scores for both groups are passed to `hrgid.flow.threshold.pick_threshold`; the threshold with the largest number of correct outcomes is taken, on a tie the smaller one. The main part is not used for the choice.

Recording. The value, the date and the number of correct outcomes on the tuning part are entered here in a separate commit BEFORE the first run on the main part.

Value: not chosen. Date: —. Tuning part: — out of 25.
