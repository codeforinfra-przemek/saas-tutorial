from django.core.management.base import BaseCommand

from .seed_demo_data import Command as DemoSeedCommand


class Command(BaseCommand):
    help = "Seed franchise profiles and demo map coverage without creating leads, visits, or subscriptions."

    def handle(self, *args, **options):
        seed = DemoSeedCommand()
        categories = seed.seed_categories()
        organizations = seed.seed_organizations()
        franchises = seed.seed_franchises(categories, organizations)
        seed.seed_locations(franchises)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(franchises)} catalogue profiles with 10 demonstrative map points each."
            )
        )
