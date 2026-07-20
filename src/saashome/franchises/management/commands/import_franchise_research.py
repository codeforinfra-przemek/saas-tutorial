from django.core.management.base import BaseCommand, CommandError

from franchises.research_import import (
    FranchiseResearchImportError,
    import_franchise_research,
)


class Command(BaseCommand):
    help = "Import one immutable, approved Human Review artifact idempotently."

    def add_arguments(self, parser):
        parser.add_argument("--review", required=True, help="Approved review JSON path.")
        parser.add_argument(
            "--franchise-slug",
            help="Existing franchise slug or explicit slug for a newly created profile.",
        )
        parser.add_argument(
            "--category-slug",
            default="pozostale",
            help="Category for a newly created franchise (default: pozostale).",
        )
        parser.add_argument(
            "--allow-approved-with-gaps",
            action="store_true",
            help="Explicitly allow a human-approved incomplete research artifact.",
        )

    def handle(self, *args, **options):
        try:
            research_import, created = import_franchise_research(
                options["review"],
                franchise_slug=options.get("franchise_slug"),
                category_slug=options["category_slug"],
                allow_approved_with_gaps=options["allow_approved_with_gaps"],
            )
        except (FranchiseResearchImportError, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        state = "created" if created else "already imported"
        self.stdout.write(
            self.style.SUCCESS(
                f"Research import {state}: id={research_import.pk}, "
                f"franchise={research_import.franchise.slug}, "
                f"decision={research_import.decision}, "
                f"detail={research_import.franchise.get_absolute_url()}research/"
            )
        )
