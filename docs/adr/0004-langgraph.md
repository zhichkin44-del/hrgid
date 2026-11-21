# ADR-0004. LangGraph instead of a custom loop

Decision. The answer flow is a LangGraph graph: nodes for routing, search, answer, citation check, refusal; conditional edges for rebuilding the answer and handing off to HR. The state is explicit and is tested node by node.

Rejected. A custom if/else loop: faster at the start, but a flow with branches and retries is harder to verify and to show.
