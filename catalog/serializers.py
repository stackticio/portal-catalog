from rest_framework import serializers

from catalog.models import Bid, Listing
from catalog.storage import presigned_image_url


class ListingSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    highest_bid = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Listing
        fields = [
            "id", "title", "description", "price", "seller",
            "image_key", "image_url", "highest_bid", "created_at",
        ]

    def get_image_url(self, obj):
        return presigned_image_url(obj.image_key)


class BidSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bid
        fields = ["id", "listing", "bidder", "amount", "created_at"]
        read_only_fields = ["id", "created_at"]
