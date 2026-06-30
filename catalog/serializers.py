from rest_framework import serializers

from catalog.models import Listing
from catalog.storage import presigned_image_url


class ListingSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = ["id", "title", "description", "price", "seller", "image_key", "image_url", "created_at"]

    def get_image_url(self, obj):
        return presigned_image_url(obj.image_key)
