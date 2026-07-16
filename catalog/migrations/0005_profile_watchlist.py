import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_orders_and_checkout"),
    ]

    operations = [
        migrations.CreateModel(
            name="Profile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sub", models.CharField(max_length=255, unique=True)),
                ("username", models.CharField(max_length=120)),
                ("display_name", models.CharField(blank=True, max_length=120)),
                ("contact_email", models.EmailField(blank=True, max_length=254)),
                ("default_location", models.CharField(blank=True, max_length=120)),
                ("notify_outbid", models.BooleanField(default=True)),
                ("notify_sold", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="Watchlist",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_sub", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "listing",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="watchers",
                        to="catalog.listing",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="watchlist",
            index=models.Index(fields=["user_sub", "-created_at"], name="catalog_wat_user_su_1d8e7a_idx"),
        ),
        migrations.AddConstraint(
            model_name="watchlist",
            constraint=models.UniqueConstraint(fields=["user_sub", "listing"], name="uniq_watch_user_listing"),
        ),
    ]
