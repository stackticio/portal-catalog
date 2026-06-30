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
