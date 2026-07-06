import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_bid"),
    ]

    operations = [
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True)),
                ("slug", models.SlugField(blank=True, max_length=90, unique=True)),
                ("emoji", models.CharField(blank=True, max_length=8)),
                ("order", models.PositiveIntegerField(default=100)),
            ],
            options={
                "verbose_name_plural": "categories",
                "ordering": ["order", "name"],
            },
        ),
        migrations.AddField(
            model_name="listing",
            name="condition",
            field=models.CharField(
                choices=[
                    ("new", "New"),
                    ("like_new", "Like new"),
                    ("used", "Used"),
                    ("refurbished", "Refurbished"),
                ],
                default="used",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="listing",
            name="location",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="listing",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="listings",
                to="catalog.category",
            ),
        ),
    ]
