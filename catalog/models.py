from django.db import models


class Listing(models.Model):
    """A product listing in the eBay-like portal."""

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    seller = models.CharField(max_length=120, blank=True)
    # Object key in the SeaweedFS S3 bucket (image bytes live in object storage, not the DB).
    image_key = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} (${self.price})"

    @property
    def highest_bid(self):
        top = self.bids.order_by("-amount").first()
        return top.amount if top else self.price


class Bid(models.Model):
    """A bid placed on a listing. Every accepted bid is persisted (durable record)
    AND published to the Kafka `bids` topic so all live viewers update in real time."""

    listing = models.ForeignKey(Listing, related_name="bids", on_delete=models.CASCADE)
    bidder = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["listing", "-amount"])]

    def __str__(self):
        return f"{self.bidder} bid ${self.amount} on listing {self.listing_id}"
