import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from executions.models import (
    ArtifactVersion, CutoverMode, DeterministicJob, ExecutionTrace, LLMRun,
    OutcomeEvent, TraceStatus, WorkflowCutover,
)
from executions.storage import ExecutionPayloadStore
from ideas.models import (
    Artifact, Episode, EpisodeRun, FeedItem, FeedItemAssessment,
    IdeaRelationSuggestion, PersonaReview, PersonaVote, RepeatResult,
    ResearchEntry, RelationshipCouncilReview, RelationshipCouncilVote,
    WeeklySummary,
)


PHASE4_WORKFLOWS = (
    "persona_council", "relationship_council", "weekly_summary", "execute",
    "critique", "podcast_script",
)
TERMINAL = (TraceStatus.SUCCEEDED, TraceStatus.FAILED, TraceStatus.CANCELLED)
LAUNCH_THRESHOLD = 99.5

# Durable AI-created projections and the timestamp (or parent timestamp) used
# to exclude known legacy rows in a bounded audit.
# The optional final value scopes models that also have a legitimate manual
# creation path. An artifact written through the execution API has an immutable
# version; summaries are also generated outputs. Research created by an agent
# carries execution identity even when a legacy caller omitted its run ID.
PROJECTIONS = (
    ("research_entries", ResearchEntry, "produced_by_run", "created_at",
     Q(produced_by_run__isnull=False) | ~Q(execution_provider="") | ~Q(execution_model="")),
    ("feed_item_summaries", FeedItem, "summarized_by_run", "summarized_at", ~Q(summary="")),
    ("feed_item_assessments", FeedItemAssessment, "produced_by_run", "created_at", None),
    ("relationship_suggestions", IdeaRelationSuggestion, "produced_by_run", "created_at", None),
    ("persona_reviews", PersonaReview, "produced_by_run", "created_at", None),
    ("persona_votes", PersonaVote, "produced_by_run", "review__created_at", None),
    ("relationship_reviews", RelationshipCouncilReview, "produced_by_run", "reviewed_at", None),
    ("relationship_votes", RelationshipCouncilVote, "produced_by_run", "review__reviewed_at", None),
    ("repeat_results", RepeatResult, "produced_by_run", "found_at", None),
    ("weekly_summaries", WeeklySummary, "produced_by_run", "created_at", None),
    ("artifacts", Artifact, "produced_by_run", "created_at",
     Q(produced_by_run__isnull=False) | Q(kind=Artifact.Kind.SUMMARY) | Q(versions__isnull=False)),
    ("podcast_episodes", Episode, "produced_by_run", "created_at", None),
    ("artifact_versions", ArtifactVersion, "producing_run", "created_at", None),
)


def _percentage(numerator, denominator):
    return None if not denominator else round(100 * numerator / denominator, 2)


def _coverage(queryset, fact_filter, reason_filter=None):
    total = queryset.count()
    facts = queryset.filter(fact_filter).count()
    explained = queryset.filter(reason_filter).count() if reason_filter else 0
    covered = (
        queryset.filter(fact_filter | reason_filter).distinct().count()
        if reason_filter else facts
    )
    return {
        "total": total, "facts": facts, "explicit_unavailable": explained,
        "covered": covered, "missing": total - covered,
        "percent": _percentage(covered, total),
    }


def _reason_query(*terms):
    query = Q(pk__in=[])
    for term in terms:
        query |= Q(measurement_unavailable_reasons__icontains=term)
    return query


class Command(BaseCommand):
    help = "Generate the authoritative R4.1 production reconciliation report."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since", help="Audit records at or after this ISO-8601 timestamp."
        )
        parser.add_argument(
            "--rollback-evidence",
            help=(
                "JSON file keyed by workflow with owner, tested_at, result, and "
                "optional notes. Evidence is read but never persisted."
            ),
        )
        parser.add_argument(
            "--fail-on-issues", action="store_true",
            help="Exit non-zero after printing a report that is not ready.",
        )

    def handle(self, *args, **options):
        since = self._parse_since(options.get("since"))
        generated_at = timezone.now()
        rollback_evidence = self._load_rollback_evidence(
            options.get("rollback_evidence"), since=since, through=generated_at
        )
        traces = ExecutionTrace.objects.select_related("workflow_version__workflow")
        runs = LLMRun.objects.select_related(
            "trace__workflow_version__workflow", "model_configuration"
        )
        traces = traces.filter(created_at__lte=generated_at)
        runs = runs.filter(created_at__lte=generated_at)
        if since:
            traces = traces.filter(created_at__gte=since)
            runs = runs.filter(created_at__gte=since)

        projections, projection_run_ids = self._projection_report(since, generated_at)
        workflows = self._workflow_report(
            traces, rollback_evidence, projection_run_ids
        )
        measurements = self._measurement_report(runs)
        payloads = self._payload_report(runs)
        checks = self._checks(workflows, projections, measurements, payloads)
        failed = [row["key"] for row in checks if row["status"] == "fail"]
        warnings = [row["key"] for row in checks if row["status"] == "warning"]

        unexplained = runs.filter(
            status__in=TERMINAL,
            measurement_status__in=["partial", "unavailable"],
        ).filter(
            Q(measurement_unavailable_reasons=[])
            | Q(measurement_unavailable_reasons__isnull=True)
        ).count()
        report = {
            "schema_version": "r4.1-reconciliation-v1",
            "generated_at": generated_at.isoformat(),
            "scope": {
                "since": since.isoformat() if since else None,
                "through": generated_at.isoformat(),
                "launch_threshold_percent": LAUNCH_THRESHOLD,
            },
            "readiness": {
                "status": "fail" if failed else ("warning" if warnings else "pass"),
                "failed_checks": failed,
                "warnings": warnings,
            },
            "checks": checks,
            "workflows": workflows,
            "projection_attribution": projections,
            "measurements": measurements,
            "payload_storage": payloads,
            # Backward-compatible keys consumed by the original Phase 4 runbook.
            "cutovers": dict(WorkflowCutover.objects.values_list("workflow_key", "mode")),
            "provenance": self._legacy_provenance(),
            "audit": {
                "scope": "audit_window",
                "deterministic_jobs": self._windowed_count(
                    DeterministicJob.objects.all(), "queued_at", since, generated_at
                ),
                "outcome_events": self._windowed_count(
                    OutcomeEvent.objects.all(), "occurred_at", since, generated_at
                ),
                "terminal_runs_without_measurement_reason": unexplained,
            },
            "scope_notes": {
                "cutovers": "current configuration snapshot",
                "provenance": "all-time backward-compatible counters",
            },
        }
        self.stdout.write(json.dumps(report, sort_keys=True, indent=2))
        if options.get("fail_on_issues") and report["readiness"]["status"] != "pass":
            raise CommandError(
                "R4.1 reconciliation did not pass: "
                + ", ".join(failed + warnings)
            )

    @staticmethod
    def _parse_since(value):
        if not value:
            return None
        parsed = parse_datetime(value)
        if parsed is None:
            raise CommandError("--since must be an ISO-8601 date-time.")
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    @staticmethod
    def _load_rollback_evidence(filename, *, since, through):
        if not filename:
            return {}
        try:
            value = json.loads(Path(filename).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Cannot read rollback evidence: {exc}") from exc
        if not isinstance(value, dict):
            raise CommandError("Rollback evidence must be a JSON object keyed by workflow.")
        unknown = sorted(set(value) - set(PHASE4_WORKFLOWS))
        if unknown:
            raise CommandError(
                "Rollback evidence contains unknown workflows: " + ", ".join(unknown)
            )
        normalized = {}
        for workflow_key, evidence in value.items():
            if not isinstance(evidence, dict):
                raise CommandError(f"Rollback evidence for {workflow_key} must be an object.")
            owner = evidence.get("owner")
            if not isinstance(owner, str) or not owner.strip():
                raise CommandError(f"Rollback evidence for {workflow_key} requires an owner.")
            tested_at = parse_datetime(str(evidence.get("tested_at") or ""))
            if tested_at is None:
                raise CommandError(
                    f"Rollback evidence for {workflow_key} has an invalid tested_at."
                )
            if timezone.is_naive(tested_at):
                tested_at = timezone.make_aware(
                    tested_at, timezone.get_current_timezone()
                )
            if tested_at > through:
                raise CommandError(
                    f"Rollback evidence for {workflow_key} is dated in the future."
                )
            if since and tested_at < since:
                raise CommandError(
                    f"Rollback evidence for {workflow_key} predates the audit window."
                )
            if evidence.get("result") not in {"pass", "fail"}:
                raise CommandError(
                    f"Rollback evidence for {workflow_key} result must be pass or fail."
                )
            normalized[workflow_key] = {
                **evidence,
                "owner": owner.strip(),
                "tested_at": tested_at.isoformat(),
            }
        return normalized

    @staticmethod
    def _windowed_count(queryset, timestamp_field, since, through):
        filters = {f"{timestamp_field}__lte": through}
        if since:
            filters[f"{timestamp_field}__gte"] = since
        return queryset.filter(**filters).count()

    @staticmethod
    def _workflow_report(traces, rollback_evidence, projection_run_ids):
        result = {}
        cutovers = {
            row.workflow_key: row
            for row in WorkflowCutover.objects.select_related("changed_by")
        }
        workflow_keys = set(
            traces.values_list("workflow_version__workflow__key", flat=True).distinct()
        ) | set(cutovers) | set(PHASE4_WORKFLOWS)
        for key in sorted(workflow_keys):
            scoped = traces.filter(workflow_version__workflow__key=key)
            successful = scoped.filter(status=TraceStatus.SUCCEEDED)
            successful_with_run = successful.filter(
                runs__status=TraceStatus.SUCCEEDED
            ).distinct().count()
            successful_runs = LLMRun.objects.filter(
                trace__in=scoped, status=TraceStatus.SUCCEEDED
            )
            successful_run_count = successful_runs.count()
            successful_runs_with_projection = successful_runs.filter(
                pk__in=projection_run_ids
            ).count()
            cutover = cutovers.get(key)
            evidence = rollback_evidence.get(key) or {}
            if not isinstance(evidence, dict):
                evidence = {}
            owner = evidence.get("owner") or (
                cutover.changed_by.get_username()
                if cutover and cutover.changed_by_id else None
            )
            rollback = {
                "owner": owner,
                "tested_at": evidence.get("tested_at"),
                "result": evidence.get("result"),
                "notes": evidence.get("notes"),
                "evidence_complete": bool(
                    owner and evidence.get("tested_at")
                    and evidence.get("result") == "pass"
                ),
            }
            result[key] = {
                "trace_counts": {
                    "total": scoped.count(),
                    "terminal": scoped.filter(status__in=TERMINAL).count(),
                    "successful": successful.count(),
                    "successful_with_successful_run": successful_with_run,
                },
                "trace_completeness_percent": _percentage(
                    successful_with_run, successful.count()
                ),
                "projection_attribution": {
                    "successful_runs": successful_run_count,
                    "with_projection": successful_runs_with_projection,
                    "without_projection": (
                        successful_run_count - successful_runs_with_projection
                    ),
                    # Diagnostic only. Coordinator, evaluator, and other auxiliary
                    # runs can legitimately complete without creating a durable
                    # product projection. Readiness is therefore gated in the
                    # opposite direction: every projection in scope must identify
                    # its producing run (see the global projection check).
                    "readiness_gating": False,
                },
                "cutover": {
                    "mode": cutover.mode if cutover else CutoverMode.LEGACY,
                    "reason": cutover.reason if cutover else "No cutover record; legacy default applies.",
                    "changed_at": cutover.changed_at.isoformat() if cutover else None,
                },
                "rollback": rollback,
            }
        return result

    @staticmethod
    def _projection_report(since, through):
        types = {}
        total = attributed = 0
        by_workflow = {}
        projection_run_ids = set()
        for name, model, run_field, timestamp_field, candidate_filter in PROJECTIONS:
            queryset = (
                model.all_objects.all() if model is RepeatResult else model.objects.all()
            )
            if candidate_filter is not None:
                queryset = queryset.filter(candidate_filter)
            queryset = queryset.filter(**{f"{timestamp_field}__lte": through})
            if since:
                queryset = queryset.filter(**{f"{timestamp_field}__gte": since})
            queryset = queryset.distinct()
            count = queryset.count()
            valid_producer_filter = {
                f"{run_field}__status": TraceStatus.SUCCEEDED,
                f"{run_field}__trace__status": TraceStatus.SUCCEEDED,
            }
            attributed_count = queryset.filter(**valid_producer_filter).count()
            missing_producer_count = queryset.filter(**{run_field: None}).count()
            invalid_producer_count = queryset.exclude(**{run_field: None}).exclude(
                **valid_producer_filter
            ).count()
            run_id_field = f"{run_field}_id"
            projection_run_ids.update(
                queryset.filter(**valid_producer_filter).values_list(
                    run_id_field, flat=True
                )
            )
            workflow_field = f"{run_field}__trace__workflow_version__workflow__key"
            for workflow_key in queryset.filter(**valid_producer_filter).values_list(
                workflow_field, flat=True
            ):
                row = by_workflow.setdefault(
                    workflow_key, {"attributed_projections": 0, "by_type": {}}
                )
                row["attributed_projections"] += 1
                row["by_type"][name] = row["by_type"].get(name, 0) + 1
            types[name] = {
                "total": count,
                "attributed": attributed_count,
                "unattributed": count - attributed_count,
                "missing_producer": missing_producer_count,
                "invalid_producer": invalid_producer_count,
                "percent": _percentage(attributed_count, count),
                "window_limited": True,
            }
            total += count
            attributed += attributed_count
        report = {
            "total": total,
            "attributed": attributed,
            "unattributed_legacy_writes": total - attributed,
            "percent": _percentage(attributed, total),
            "by_projection_type": types,
            "by_workflow": by_workflow,
        }
        return report, projection_run_ids

    @staticmethod
    def _measurement_report(runs):
        terminal = runs.filter(status__in=TERMINAL)
        reason_present = ~Q(measurement_unavailable_reasons=[]) & ~Q(
            measurement_unavailable_reasons__isnull=True
        )
        token_fact = Q(total_tokens__isnull=False) | (
            Q(input_tokens__isnull=False) & Q(output_tokens__isnull=False)
        )
        timing_fact = Q(started_at__isnull=False) & Q(completed_at__isnull=False)
        cost_fact = Q(cost_micros__isnull=False) & ~Q(cost_source="")
        provider_fact = ~Q(model_configuration__provider="") & ~Q(
            model_configuration__model_identifier=""
        )
        request_failed_before_measurement = _reason_query(
            "provider_request_failed", "run_failed_before_usage"
        ) & Q(status=TraceStatus.FAILED)
        return {
            "terminal_runs": terminal.count(),
            "measurement_status": {
                status: terminal.filter(measurement_status=status).count()
                for status in ("complete", "partial", "unavailable")
            },
            "provider_model": _coverage(terminal, provider_fact),
            "tokens": _coverage(
                terminal, token_fact,
                _reason_query("usage", "token") | request_failed_before_measurement,
            ),
            "cost": _coverage(
                terminal, cost_fact,
                _reason_query("cost") | request_failed_before_measurement,
            ),
            "timing": _coverage(
                terminal, timing_fact,
                _reason_query("timing", "latency"),
            ),
            "unavailable_reason": _coverage(
                terminal.filter(measurement_status__in=["partial", "unavailable"]),
                reason_present,
            ),
        }

    @staticmethod
    def _payload_report(runs):
        store = ExecutionPayloadStore()
        report = {
            "capture_enabled": bool(settings.IDEAFLOW_EXECUTION_CAPTURE_PAYLOADS),
            "references": 0, "verified": 0, "missing": 0,
            "hash_mismatch": 0, "invalid_reference": 0, "not_captured": 0,
        }
        for run in runs.iterator():
            pairs = [(run.rendered_input_ref, run.rendered_input_hash)]
            if run.status == TraceStatus.SUCCEEDED:
                pairs.append((run.output_ref, run.output_hash))
            for reference, digest in pairs:
                if not reference:
                    report["not_captured"] += 1
                    continue
                report["references"] += 1
                if not reference.startswith(store.scheme):
                    report["invalid_reference"] += 1
                    continue
                try:
                    verified = store.verify(reference, digest)
                except FileNotFoundError:
                    report["missing"] += 1
                except Exception:
                    report["invalid_reference"] += 1
                else:
                    report["verified" if verified else "hash_mismatch"] += 1
        report["healthy"] = not any(
            report[key]
            for key in ("missing", "hash_mismatch", "invalid_reference")
        ) and not (report["capture_enabled"] and report["not_captured"])
        return report

    @staticmethod
    def _checks(workflows, projections, measurements, payloads):
        checks = []

        def add(key, status, detail):
            checks.append({"key": key, "status": status, "detail": detail})

        percent = projections["percent"]
        add(
            "projection_attribution",
            "warning" if percent is None else (
                "pass" if percent >= LAUNCH_THRESHOLD else "fail"
            ),
            f"{percent}% attributed" if percent is not None else "No projections in scope.",
        )
        for key in ("provider_model", "tokens", "cost", "timing", "unavailable_reason"):
            percent = measurements[key]["percent"]
            add(
                f"measurement_{key}",
                "warning" if percent is None and key != "unavailable_reason" else (
                    "pass" if percent is None or percent >= LAUNCH_THRESHOLD else "fail"
                ),
                f"{percent}% covered" if percent is not None else "No applicable terminal runs.",
            )
        incomplete_traces = [
            key for key, row in workflows.items()
            if row["trace_completeness_percent"] is not None
            and row["trace_completeness_percent"] < LAUNCH_THRESHOLD
        ]
        add(
            "trace_completeness", "pass" if not incomplete_traces else "fail",
            "Every successful trace has a successful run." if not incomplete_traces
            else "Below threshold: " + ", ".join(incomplete_traces),
        )
        missing_cutovers = [
            key for key in PHASE4_WORKFLOWS
            if workflows[key]["cutover"]["changed_at"] is None
        ]
        add(
            "cutover_records", "pass" if not missing_cutovers else "fail",
            "Recorded for every Phase 4 workflow." if not missing_cutovers
            else "Missing for: " + ", ".join(missing_cutovers),
        )
        add(
            "payload_storage", "pass" if payloads["healthy"] else "fail",
            f"{payloads['verified']} verified; {payloads['missing']} missing; "
            f"{payloads['hash_mismatch']} hash mismatches; "
            f"{payloads['invalid_reference']} invalid.",
        )
        missing = [
            key for key in PHASE4_WORKFLOWS
            if not workflows[key]["rollback"]["evidence_complete"]
        ]
        add(
            "rollback_evidence", "pass" if not missing else "fail",
            "Complete for every Phase 4 workflow." if not missing
            else "Missing or failing for: " + ", ".join(missing),
        )
        return checks

    @staticmethod
    def _legacy_provenance():
        return {
            "persona_reviews": PersonaReview.objects.count(),
            "persona_reviews_attributed": PersonaReview.objects.exclude(produced_by_run=None).count(),
            "persona_votes": PersonaVote.objects.count(),
            "persona_votes_attributed": PersonaVote.objects.exclude(produced_by_run=None).count(),
            "relationship_reviews": RelationshipCouncilReview.objects.count(),
            "relationship_reviews_attributed": RelationshipCouncilReview.objects.exclude(produced_by_run=None).count(),
            "relationship_votes": RelationshipCouncilVote.objects.count(),
            "relationship_votes_attributed": RelationshipCouncilVote.objects.exclude(produced_by_run=None).count(),
            "weekly_summaries": WeeklySummary.objects.count(),
            "weekly_summaries_attributed": WeeklySummary.objects.exclude(produced_by_run=None).count(),
            "artifacts": Artifact.objects.count(),
            "artifact_versions": ArtifactVersion.objects.filter(artifact__isnull=False).count(),
            "episodes": Episode.objects.count(),
            "episode_runs": EpisodeRun.objects.count(),
            "episode_runs_traced": EpisodeRun.objects.exclude(execution_trace=None).count(),
            "media_versions": ArtifactVersion.objects.filter(episode__isnull=False).count(),
        }
