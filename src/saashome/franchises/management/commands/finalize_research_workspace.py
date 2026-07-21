from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from franchises.models import FranchiseResearchWorkspace
from franchises.research_finalizer import (
    ResearchFinalizationError,
    finalize_research_workspace,
)


class Command(BaseCommand):
    help = "Freeze one approved Workbench and idempotently attach it to an import."

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace",
            required=True,
            help="Workbench UUID shown in the internal review URL.",
        )

    def handle(self, *args, **options):
        try:
            workspace = FranchiseResearchWorkspace.objects.get(
                workspace_id=options["workspace"]
            )
            finalization, created = finalize_research_workspace(workspace)
        except FranchiseResearchWorkspace.DoesNotExist as exc:
            raise CommandError("Workbench does not exist.") from exc
        except (ResearchFinalizationError, DatabaseError, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        state = "created" if created else "already finalized"
        self.stdout.write(
            self.style.SUCCESS(
                f"Workbench finalization {state}: "
                f"id={finalization.finalization_id}, "
                f"release={finalization.release_number}, "
                f"import={finalization.research_import_id}, "
                f"sha256={finalization.artifact_sha256}"
            )
        )
