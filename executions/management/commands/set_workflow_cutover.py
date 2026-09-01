from django.core.management.base import BaseCommand, CommandError

from executions.models import CutoverMode, WorkflowCutover, WorkflowDefinition


class Command(BaseCommand):
    help = "Change one workflow's reversible Phase 4 cutover mode."

    def add_arguments(self, parser):
        parser.add_argument("workflow")
        parser.add_argument("mode", choices=CutoverMode.values)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        key = options["workflow"]
        if not WorkflowDefinition.objects.filter(key=key).exists():
            raise CommandError(f"Unknown workflow: {key}")
        if options["mode"] in {CutoverMode.AUTHORITATIVE, CutoverMode.FROZEN} and not options["confirm"]:
            raise CommandError("Authoritative/frozen cutovers require --confirm.")
        cutover, _created = WorkflowCutover.objects.update_or_create(
            workflow_key=key,
            defaults={"mode": options["mode"], "reason": options["reason"]},
        )
        self.stdout.write(self.style.SUCCESS(f"{cutover.workflow_key} -> {cutover.mode}"))
