from rest_framework.routers import DefaultRouter

from catalog.views import ListingViewSet

router = DefaultRouter()
router.register("listings", ListingViewSet, basename="listing")

urlpatterns = router.urls
