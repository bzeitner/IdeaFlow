import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ideas.instrumentation import (
    production_baseline, registry_payload, validate_registries,
)


class Command(BaseCommand):
    help = "Emit a read-only Phase 0 LLM usage baseline or the stable registry."

    def add_arguments(self, parser):
        parser.add_argument(
            "--registry", action="store_true",
            help="Emit the call-site, metric, and outcome registry instead of DB counts.",
        )
        parser.add_argument(
            "--output", help="Optional file path; existing files are not overwritten."
        )

    def handle(self, *args, **options):
        errors = validate_registries()
        if errors:
            raise CommandError("Invalid instrumentation registry: " + " ".join(errors))
        payload = registry_payload() if options["registry"] else production_baseline()
        rendered = json.dumps(payload, default=str, indent=2, sort_keys=True) + "\n"
        if options["output"]:
            path = Path(options["output"])
            try:
                with path.open("x", encoding="utf-8") as output:
                    output.write(rendered)
            except FileExistsError as exc:
                raise CommandError(f"Refusing to overwrite existing file: {path}") from exc
            self.stdout.write(str(path))
            return
        self.stdout.write(rendered, ending="")
