# LLM Call-Site Register

Registry version: 1.0.0
Canonical machine-readable definition: `ideas/instrumentation.py`

| Key | Entrypoint | Purpose | Providers | Primary outcome | Migration order |
| --- | --- | --- | --- | --- | ---: |
| feed-score | `score_items.sh` | Classification | Claude | Evidence precision@5 | 1 |
| agent-research | `research_idea.sh research` | Generation | Claude, Codex | Research accepted within 7 days | 2 |
| agent-review | `research_idea.sh review` | Generation | Claude, Codex | Disposition accepted | 2 |
| relationship-classifier | `process_semantic_graph` | Classification | OpenAI-compatible | Accepted-edge precision | 3 |
| relationship-council-vote | `tools/review_relationships.py` | Evaluation | Claude, Codex | Council/human agreement | 3 |
| agent-summary | `research_idea.sh summary` | Generation | Claude, Codex | Accepted without edit | 4 |
| agent-repeat | `research_idea.sh repeat` | Generation | Claude, Codex | Result actioned within 30 days | 4 |
| open-question-single | `extract_open_questions --use-ai` | Extraction | OpenAI-compatible | Accepted-question precision | 4 |
| open-question-batch | `tools/extract_open_questions_remote.py` | Extraction | OpenAI-compatible | Accepted-question precision | 4 |
| persona-council | `research_idea.sh persona` | Evaluation | Claude, Codex | Proposal retained within 30 days | 5 |
| weekly-summary | `weekly_summary.sh` | Generation | Claude, Codex | Accepted without refresh | 5 |
| portfolio-reflection | `research_all.sh --reflect` | Generation | Claude, Codex | Action adopted within 7 days | 6 |
| podcast-script | podcast branch of `research_idea.sh repeat` | Generation | Claude, Codex | Episode published within 30 days | 6 |
| agent-execute | `research_idea.sh execute` | Generation/tool use | Claude, Codex | Change merged | 7 |
| agent-critique | `research_idea.sh critique` | Evaluation/tool use | Claude, Codex | Finding actioned | 7 |

Deterministic RSS fetching, repository commands, and audio rendering are not LLM calls. Later phases will record them as jobs or tool invocations under the relevant trace.

## Provider measurement capability audit

Before a call site moves from compatibility instrumentation to the gateway, its adapter must document and test:

- provider request identifier availability;
- input, output, cached, and reasoning token availability;
- first-token and completion timing availability;
- billed cost availability or the pricing-table fallback;
- structured-output and finish-reason behavior;
- retry identifiers and rate-limit/error classification;
- raw response preservation and redaction behavior.

Unavailable measurements must be stored with an explicit reason. They must not be represented as zero.
