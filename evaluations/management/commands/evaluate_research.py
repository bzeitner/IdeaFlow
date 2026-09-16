import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError, SuspiciousFileOperation
from django.core.management.base import BaseCommand, CommandError

from evaluations.models import EvaluatorVersion
from evaluations.services import evaluate_run
from executions.models import LLMRun


def read_bounded(path):
    with Path(path).open("rb") as source:
        content = source.read(settings.IDEAFLOW_EXECUTION_PAYLOAD_MAX_BYTES + 1)
    if len(content) > settings.IDEAFLOW_EXECUTION_PAYLOAD_MAX_BYTES:
        raise ValidationError("Input file exceeds the execution payload limit.")
    return content


class Command(BaseCommand):
    help = "Evaluate exact research output bytes with deterministic checks; no provider calls or production projections."

    def add_arguments(self, parser):
        parser.add_argument("run_id")
        parser.add_argument("--actor", required=True, help="Operator identity recorded in the audit.")
        parser.add_argument("--idempotency-key", required=True)
        parser.add_argument("--evaluator-version", type=int, default=1)
        parser.add_argument("--output-file", help="Optional exact raw output; its hash must match the run.")
        parser.add_argument("--contract-file", help="Frozen flat output contract JSON; defaults to free text.")
        parser.add_argument("--references-file", help="JSON list of internal model/id references.")

    def handle(self, *args, **options):
        try:
            run = LLMRun.objects.get(pk=options["run_id"])
            version = EvaluatorVersion.objects.select_related("evaluator", "metric").get(
                evaluator__key="research.structure", version=options["evaluator_version"])
            result, created = evaluate_run(
                run, version, actor_label=options["actor"], idempotency_key=options["idempotency_key"],
                output=read_bounded(options["output_file"]) if options["output_file"] else None,
                contract=json.loads(read_bounded(options["contract_file"])) if options["contract_file"] else None,
                references=json.loads(read_bounded(options["references_file"])) if options["references_file"] else None,
            )
        except (ObjectDoesNotExist, ValidationError, SuspiciousFileOperation, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps({"result_id": result.pk, "created": created,
                                     "output_hash": result.output_hash,
                                     "summary": result.summary, "criteria": result.criterion_results}, indent=2))
