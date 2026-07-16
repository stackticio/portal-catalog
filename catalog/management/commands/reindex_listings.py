"""Backfill the OpenSearch `listings` index from the database.

Run once after wiring OpenSearch (or any time the index drifts):

    python manage.py reindex_listings

Ongoing sync is automatic via the post_save/post_delete signals; this command only
seeds existing rows and (re)creates the index/mapping if needed.
"""
from django.core.management.base import BaseCommand

from catalog import opensearch_client
from catalog.models import Listing


class Command(BaseCommand):
    help = "(Re)index all listings into OpenSearch (creates the index if missing)."

    def handle(self, *args, **options):
        if not opensearch_client.is_enabled():
            self.stderr.write(
                "OpenSearch is not configured (OPENSEARCH_URL unset) — nothing to do."
            )
            return
        count = opensearch_client.reindex_all(
            Listing.objects.select_related("category").all()
        )
        self.stdout.write(
            self.style.SUCCESS(f"Indexed {count} listings into '{opensearch_client.INDEX}'.")
        )
