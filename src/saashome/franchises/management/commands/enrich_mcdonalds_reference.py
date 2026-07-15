from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from franchises.models import Franchise, FranchiseLocation
from franchises.reference_data import MCDONALDS_REFERENCE_SOURCE_URL, mcdonalds_reference_fields


class Command(BaseCommand):
    help = "Replace McDonald's demo profile fields with public reference data."

    def handle(self, *args, **options):
        franchise = Franchise.objects.filter(slug="mcdonalds").first()
        if not franchise:
            raise CommandError("McDonald's profile was not found. Run seed_demo_data first or create it in management.")

        fields = mcdonalds_reference_fields()
        fields["updated_at"] = timezone.now()
        Franchise.objects.filter(pk=franchise.pk).update(**fields)

        deleted_count, _ = FranchiseLocation.objects.filter(
            franchise=franchise,
            address__startswith="Demo street",
        ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                "McDonald's profile enriched with public reference data; "
                f"removed {deleted_count} generated demo location(s)."
            )
        )
        self.stdout.write(f"Primary source: {MCDONALDS_REFERENCE_SOURCE_URL}")
