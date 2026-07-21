import json

from django.core.management.base import BaseCommand, CommandError

from franchises.models import Franchise
from franchises.research_publication import (
    ResearchPublicationError,
    project_approved_research,
)


class Command(BaseCommand):
    help = "Preview or materialize the approved field projection for one franchise."

    def add_arguments(self, parser):
        parser.add_argument("--franchise", required=True, help="Franchise slug.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist the projection. Without this flag the command is read-only.",
        )

    def handle(self, *args, **options):
        franchise = Franchise.objects.filter(slug=options["franchise"]).first()
        if franchise is None:
            raise CommandError("Franchise not found.")
        research_import = franchise.research_imports.filter(is_current=True).first()
        if research_import is None:
            raise CommandError("Franchise has no current research import.")
        finalization = research_import.workbench_finalizations.order_by(
            "-finalized_at"
        ).first()
        if finalization is None:
            raise CommandError("Current research import has no editorial finalization.")
        try:
            actions = project_approved_research(
                finalization,
                dry_run=not options["apply"],
            )
        except ResearchPublicationError as exc:
            raise CommandError(str(exc)) from exc
        summary = {
            "franchise": franchise.slug,
            "finalization_id": str(finalization.finalization_id),
            "release_number": finalization.release_number,
            "mode": "apply" if options["apply"] else "dry_run",
            "accepted_decisions": finalization.accepted_count
            + finalization.edited_count,
            "pending_decisions": finalization.pending_count,
            "profile_actions": actions,
        }
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
        if options["apply"]:
            self.stdout.write(self.style.SUCCESS("Approved publication projection saved."))
        else:
            self.stdout.write(
                self.style.WARNING("Dry run only. Repeat with --apply to persist.")
            )
