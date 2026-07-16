from django.db import models
from django.utils.text import slugify


class Category(models.Model):
    """A top-level marketplace category (Electronics, Fashion, ...)."""

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=90, unique=True, blank=True)
    emoji = models.CharField(max_length=8, blank=True)
    order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Listing(models.Model):
    """A product listing in the eBay-like portal."""

    CONDITION_CHOICES = [
        ("new", "New"),
        ("like_new", "Like new"),
        ("used", "Used"),
        ("refurbished", "Refurbished"),
    ]
    SELL_TYPE_CHOICES = [
        ("auction", "Auction"),
        ("buy_now", "Buy now"),
    ]
    STATUS_CHOICES = [
        ("active", "Active"),
        ("sold", "Sold"),
        ("ended", "Ended"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    seller = models.CharField(max_length=120, blank=True)
    category = models.ForeignKey(
        Category, related_name="listings", null=True, blank=True, on_delete=models.SET_NULL
    )
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, default="used")
    location = models.CharField(max_length=120, blank=True)
    # auction (bid to win) vs buy_now (direct checkout); status hides sold items.
    sell_type = models.CharField(max_length=10, choices=SELL_TYPE_CHOICES, default="auction")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active")
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


class Order(models.Model):
    """A purchase created at buy-now checkout — the start of the money path.

    Payment is SIMULATED in this increment: `paid` is set with a `payment_ref` of
    `SIMULATED-<uuid>`; no card data touches the app. A real PSP (Stripe/Adyen) later
    slots behind this same status machine as a separate, PCI-isolated service.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("shipped", "Shipped"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    buyer = models.CharField(max_length=120)   # token subject (preferred_username)
    seller = models.CharField(max_length=120)  # copied from the listing at purchase
    total = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="pending")
    payment_ref = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["buyer", "-created_at"], name="order_buyer_created_idx"),
            models.Index(fields=["seller", "-created_at"], name="order_seller_created_idx"),
        ]

    def __str__(self):
        return f"Order #{self.id} ({self.status}) {self.buyer} -> {self.seller} ${self.total}"


class OrderItem(models.Model):
    """A line in an Order. Title + price are SNAPSHOTTED at purchase time so the order
    stays immutable even if the listing later changes or is deleted."""

    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    listing = models.ForeignKey(
        Listing, related_name="order_items", null=True, on_delete=models.SET_NULL
    )
    title = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.title} @ ${self.price}"


class Profile(models.Model):
    """A user's account record — one per Keycloak subject. Identity (sub, username) is
    token-derived and read-only; the rest is editable in Settings. Auto-created on the
    first authenticated /me/ access."""

    sub = models.CharField(max_length=255, unique=True)  # Keycloak subject (stable id)
    username = models.CharField(max_length=120)          # preferred_username (from token)
    display_name = models.CharField(max_length=120, blank=True)
    contact_email = models.EmailField(blank=True)
    default_location = models.CharField(max_length=120, blank=True)
    notify_outbid = models.BooleanField(default=True)
    notify_sold = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile<{self.username}>"


class Watchlist(models.Model):
    """A user following a listing they want to track (eBay 'Watching'). Keyed by the
    token subject so it survives username changes; unique per (user, listing)."""

    user_sub = models.CharField(max_length=255)
    listing = models.ForeignKey(Listing, related_name="watchers", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user_sub", "listing"], name="uniq_watch_user_listing"),
        ]
        indexes = [models.Index(fields=["user_sub", "-created_at"])]

    def __str__(self):
        return f"{self.user_sub} watches listing {self.listing_id}"
