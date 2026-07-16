from rest_framework import serializers

from catalog.models import Bid, Category, Listing, Order, OrderItem, Profile, Watchlist
from catalog.storage import presigned_image_url


class CategorySerializer(serializers.ModelSerializer):
    count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "emoji", "count"]

    def get_count(self, obj):
        return obj.listings.count()


class ListingSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    # Served from the queryset's current_high annotation when present (one query per
    # page); the model property is only a fallback for non-annotated paths (it costs
    # one bids query per row).
    highest_bid = serializers.SerializerMethodField()
    category = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default="")
    condition_label = serializers.CharField(source="get_condition_display", read_only=True)

    class Meta:
        model = Listing
        fields = [
            "id", "title", "description", "price", "seller",
            "category", "category_name", "condition", "condition_label", "location",
            "sell_type", "status",
            "image_key", "image_url", "highest_bid", "created_at",
        ]
        # seller comes from the token; status is server-controlled (set at checkout).
        read_only_fields = ["seller", "status"]

    def get_image_url(self, obj):
        return presigned_image_url(obj.image_key)

    def get_highest_bid(self, obj):
        val = getattr(obj, "current_high", None)
        if val is None:
            val = obj.highest_bid
        return "{:.2f}".format(val)


class BidSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bid
        fields = ["id", "listing", "bidder", "amount", "created_at"]
        read_only_fields = ["id", "created_at"]


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "listing", "title", "price"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        # Orders are created only via the checkout action / lifecycle transitions —
        # never by a direct write, so the whole payload is read-only.
        fields = ["id", "buyer", "seller", "total", "status", "payment_ref", "created_at", "items"]
        read_only_fields = fields


class ProfileSerializer(serializers.ModelSerializer):
    """Account settings. Identity (sub/username) is token-derived and read-only; only the
    display/contact/notification fields are editable via PATCH."""

    class Meta:
        model = Profile
        fields = [
            "sub", "username", "display_name", "contact_email",
            "default_location", "notify_outbid", "notify_sold",
            "created_at", "updated_at",
        ]
        read_only_fields = ["sub", "username", "created_at", "updated_at"]


class MyListingSerializer(ListingSerializer):
    """A seller's own listing with bid_count for the dashboard (highest_bid already on
    the base serializer)."""

    bid_count = serializers.IntegerField(read_only=True)

    class Meta(ListingSerializer.Meta):
        fields = ListingSerializer.Meta.fields + ["bid_count"]


class MyBidSerializer(serializers.Serializer):
    """One row per auction the caller has bid on: my highest bid, the current high, and
    the derived state (winning / outbid / won / lost)."""

    listing = ListingSerializer(read_only=True)
    my_max = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    current_high = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    state = serializers.CharField(read_only=True)


class WatchlistSerializer(serializers.ModelSerializer):
    listing = ListingSerializer(read_only=True)

    class Meta:
        model = Watchlist
        fields = ["id", "listing", "created_at"]
        read_only_fields = fields
