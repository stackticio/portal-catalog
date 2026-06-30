from django.core.management.base import BaseCommand

from catalog.models import Listing

SAMPLE = [
    {"title": "Vintage film camera", "price": "89.00", "seller": "retro_optics", "description": "35mm, fully working."},
    {"title": "Mechanical keyboard", "price": "120.00", "seller": "clack_co", "description": "Tactile brown switches."},
    {"title": "Road bike, 54cm", "price": "640.00", "seller": "two_wheels", "description": "Carbon frame, lightly used."},
]


class Command(BaseCommand):
    help = "Insert sample listings (idempotent by title)."

    def handle(self, *args, **options):
        for row in SAMPLE:
            obj, created = Listing.objects.get_or_create(title=row["title"], defaults=row)
            self.stdout.write(("created " if created else "exists  ") + obj.title)
