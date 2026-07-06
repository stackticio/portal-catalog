from rest_framework import serializers

from catalog.models import Bid, Category, Listing
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
    highest_bid = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    category = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default="")
    condition_label = serializers.CharField(source="get_condition_display", read_only=True)

    class Meta:
        model = Listing
        fields = [
            "id", "title", "description", "price", "seller",
            "category", "category_name", "condition", "condition_label", "location",
            "image_key", "image_url", "highest_bid", "created_at",
        ]

    def get_image_url(self, obj):
        return presigned_image_url(obj.image_key)


class BidSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bid
        fields = ["id", "listing", "bidder", "amount", "created_at"]
        read_only_fields = ["id", "created_at"]
