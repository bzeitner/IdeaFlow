# Laya relationship pre-filter — Phase 0 evaluation

Status: evaluated 2026-09-29. **Decision: do not proceed** with the remote Laya batch pipeline as designed. No IdeaFlow code, schema, or production behavior was changed. This document records the result so the question is not re-run without new information.

## Question

Can [Laya](https://github.com/NandhaKishorM/laya) (Convai Innovations, Apache 2.0, ModernBERT-based non-autoregressive "System 1" decision model) act as a cheap pre-filter in front of the `semantic-relationship-classifier` LLM call, so fewer candidate pairs reach the LLM and token usage drops?

Success required a threshold on Laya's "related" probability that filters a large share of candidate pairs while keeping most true edges. The gate was: at least match the current classifier's accepted-edge precision. A filter that cannot separate related from unrelated pairs fails before precision matters.

## Method

- **Labels.** Read-only export from `tools/ideaflow graph` (graph revision 3341): 75 ideas and 173 edges. `parent_of` (13, derived) was excluded, leaving 160 typed edges over **124 distinct pairs** (69 `depends_on`, 46 `related_to`, 19 `enables`, 11 `supports`, 5 `contradicts`, 4 `alternative_to`, 4 `duplicates`, 2 `inspired_by`). Provenance: 158 agent, 2 human. Some accepted edges were auto-accepted above the 90% threshold, so the labels include classifier output rather than only human-reviewed edges.
- **Negatives.** All 2,669 unlinked pairs. Rejected suggestions are not exposed by the API, so no human-rejected labels were available.
- **Candidate set.** Production uses pgvector (`IDEAFLOW_SEMANTIC_CANDIDATES=12`, min similarity 0.35). This evaluation approximated it with TF-IDF top-12 neighbors per idea, This is where the token-saving question actually applies. Two slightly different candidate sets were used: the two 10-way runs scored the neighbor pairs only (**596 pairs, 74 positive**), while the alternative-framing runs unioned in every linked pair (**628 pairs, 106 positive**).
- **Input.** Idea title plus summary (first 900 characters) for each side; no pair hit Laya's context limit.
- **Model.** `laya` 0.3.22, zero-shot (no fine-tuning), Apple M4 Mac mini (16 GB), CPU. Checkpoints `english` and `typed-decisions`.
- **Framings.** A 10-way choice (`none` plus the 9 `RelationType` values) on both checkpoints, then three alternatives on `english` to rule out poor question wording: binary related/unrelated with text input, the same with structured input, and a 4-level strength score.
- **Metric.** AUROC of `1 - P(none)` (or the framing's equivalent) against accepted-edge labels. 0.5 is chance.

## Results

Candidate pairs (10-way rows: 596 pairs, 74 positive; other rows: 628 pairs, 106 positive):

| Framing | Checkpoint | AUROC |
|---|---|---|
| 10-way relation type | `english` | 0.511 |
| 10-way relation type | `typed-decisions` | 0.486 |
| Binary related/unrelated, text input | `english` | 0.530 |
| Binary related/unrelated, structured input | `english` | 0.555 |
| 4-level strength score | `english` | 0.556 |

All candidate-pair AUROCs are within 0.05 of chance. Across all 2,793 pairs (positives plus every unlinked pair) the 10-way AUROCs were 0.550 (`english`) and 0.521 (`typed-decisions`).

Token-saving view of the 10-way `english` run, where pairs scoring below the threshold skip the LLM (precision stays about 12% at every point, equal to the base rate of 74 positives in 596 candidate pairs, i.e. no lift):

| Threshold | Pairs filtered | True edges kept |
|---|---|---|
| 0.30 | 0.3% | 100% |
| 0.50 | 10.9% | 89.2% |
| 0.70 | 50.7% | 48.6% |
| 0.90 | 97.8% | 2.7% |

Filtering half the pairs loses half the real edges, which is what random dropping would do. `typed-decisions` filtered nothing until threshold 0.9 (22.7% filtered, 70.3% of edges kept).

Relation typing on the 124 linked pairs (top non-`none` type is one of the accepted types): **14%** (`english`), **35%** (`typed-decisions`). For the most common accepted type, `depends_on` (69 edges), `english` predicted `enables` 36 times, `contradicts` 15, and `depends_on` only 5; `typed-decisions` predicted `depends_on` 39 times (57%), `supports` 16, and `enables` 14. Typing was therefore better on `typed-decisions`, but that does not help the filtering question above.

Latency: median 171 ms (`english`) and 212 ms (`typed-decisions`) per pair on CPU with this input size. Speed was not the limiting factor.

Warnings: none were raised in the full runs. A separate first-time-setup query on the `laya` router warned that one checkpoint ships temperatures outside its valid range, so its `confidence` field is uncalibrated. This evaluation used class probabilities only.

## Interpretation

Zero-shot Laya does not distinguish related from unrelated IdeaFlow idea pairs. Four independent framings agree, so this is unlikely to be a wording problem. The likely cause is domain mismatch: Laya's typed decisions target triage-style inputs such as tickets and messages, while IdeaFlow relationships depend on nuanced, research-heavy idea text.

## Caveats

- **Negative labels are noisy.** Unlinked pairs may include real relationships that were never suggested or accepted. That would depress every metric, but results at chance level, not merely low, make it unlikely to explain the whole gap.
- **Positive labels include classifier output.** Agent-provenance edges accepted automatically reflect the LLM's own judgments, not only human review.
- **Sample size.** 124 positive pairs is too few for a reliable fine-tuned-model evaluation.
- **Candidate stage approximated.** TF-IDF stood in for pgvector, so the candidate mix differs from production.
- **No LLM baseline.** The current classifier's precision was not measured here, so the original gate (match its precision) was never reached.
- **Local-only tooling.** The evaluation scripts and raw data are in `~/laya/eval/` on the evaluator's machine and are not in this repository. The raw data contains idea text and must not be committed.

## Decision and follow-ups

1. **Do not build** the remote Laya job API, worker, shadow mode, or cutover (originally Phases 1–4).
2. **Cheaper token levers to measure on the same data:** lower `IDEAFLOW_SEMANTIC_CANDIDATES` (12), raise `IDEAFLOW_SEMANTIC_MIN_SIMILARITY` (0.35), and shorten per-candidate text (`[:5000]` per candidate, `[:10000]` for the source in `SemanticAPI.classify`). Each can be evaluated by its recall cost against the accepted edges.
3. **Revisit Laya only if** a fine-tuning set with enough reviewed positives and rejected negatives exists, or a newer checkpoint targets this kind of input. Re-run the same protocol before any build work.
