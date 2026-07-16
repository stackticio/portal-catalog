"""Keep the OpenSearch `listings` index in sync with the database.

Every Listing write mirrors into OpenSearch (create/update) and every delete removes the
document. Both hops are best-effort inside the client, so search indexing can never
break a listing write. Registered from CatalogConfig.ready().
"""
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalog import opensearch_client
from catalog.models import Listing


@receiver(post_save, sender=Listing)
def _index_listing(sender, instance, **kwargs):
    # Defer until COMMIT: checkout saves the listing inside a select_for_update
    # transaction, and an OpenSearch call in there would hold the row lock (blocking
    # every bidder/buyer of that listing) for up to the HTTP timeout. on_commit also
    # guarantees we never index state that ends up rolled back. Outside a transaction
    # it runs immediately.
    transaction.on_commit(lambda: opensearch_client.index_listing(instance))


@receiver(post_delete, sender=Listing)
def _deindex_listing(sender, instance, **kwargs):
    listing_id = instance.id
    transaction.on_commit(lambda: opensearch_client.delete_listing(listing_id))
