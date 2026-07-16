from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"

    def ready(self):
        # Register the post_save/post_delete hooks that mirror listings into OpenSearch.
        from catalog import signals  # noqa: F401
