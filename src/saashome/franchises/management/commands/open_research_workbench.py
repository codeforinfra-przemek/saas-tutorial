from django.core.management.base import BaseCommand, CommandError

from franchises.research_workbench import (
    ResearchWorkbenchError,
    create_research_workspace,
)


class Command(BaseCommand):
    help = "Open an idempotent Human Research Workbench from a Normalizer artifact."

    def add_arguments(self, parser):
        parser.add_argument("--normalized", required=True, help="Normalizer JSON path.")
        parser.add_argument(
            "--franchise-slug",
            required=True,
            help="Existing franchise directory slug.",
        )

    def handle(self, *args, **options):
        try:
            workspace, created = create_research_workspace(
                options["normalized"],
                franchise_slug=options["franchise_slug"],
            )
        except (ResearchWorkbenchError, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        state = "created" if created else "already exists"
        self.stdout.write(
            self.style.SUCCESS(
                f"Research Workbench {state}: id={workspace.workspace_id}, "
                f"franchise={workspace.franchise.slug}, "
                f"detail=/internal/research/{workspace.workspace_id}/"
            )
        )
