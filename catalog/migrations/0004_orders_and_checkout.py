import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0003_category_listing_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="sell_type",
            field=models.CharField(
                choices=[("auction", "Auction"), ("buy_now", "Buy now")],
                default="auction",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="listing",
            name="status",
            field=models.CharField(
                choices=[("active", "Active"), ("sold", "Sold"), ("ended", "Ended")],
                default="active",
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name="Order",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("buyer", models.CharField(max_length=120)),
                ("seller", models.CharField(max_length=120)),
                ("total", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("paid", "Paid"),
                            ("shipped", "Shipped"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="pending",
                        max_length=12,
                    ),
                ),
                ("payment_ref", models.CharField(blank=True, max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("price", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "listing",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="order_items",
                        to="catalog.listing",
                    ),
                ),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="catalog.order",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["buyer", "-created_at"], name="order_buyer_created_idx"),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["seller", "-created_at"], name="order_seller_created_idx"),
        ),
    ]
