from django.core.management.base import BaseCommand

from catalog.models import Category, Listing

CATEGORIES = [
    {"name": "Electronics", "emoji": "\U0001F4F1", "order": 10},
    {"name": "Fashion", "emoji": "\U0001F455", "order": 20},
    {"name": "Home & Garden", "emoji": "\U0001F3E1", "order": 30},
    {"name": "Sports & Outdoors", "emoji": "\U0001F6B4", "order": 40},
    {"name": "Collectibles", "emoji": "\U0001F3B4", "order": 50},
    {"name": "Vehicles", "emoji": "\U0001F697", "order": 60},
    {"name": "Books & Media", "emoji": "\U0001F4DA", "order": 70},
    {"name": "Toys & Games", "emoji": "\U0001F9F8", "order": 80},
]

# (category slug, title, price, seller, condition, location, description)
LISTINGS = [
    ("electronics", "iPhone 13 Pro, 256GB", "560.00", "mobile_hub", "like_new", "Berlin, DE", "Unlocked, 89% battery, boxed."),
    ("electronics", "Sony WH-1000XM5 headphones", "230.00", "audio_den", "used", "Munich, DE", "ANC over-ear, minor wear."),
    ("electronics", "Mechanical keyboard, TKL", "120.00", "clack_co", "new", "Amsterdam, NL", "Tactile brown switches, hot-swap."),
    ("electronics", "iPad Air 5th gen", "410.00", "tab_world", "like_new", "Vienna, AT", "M1, Wi-Fi, space grey."),
    ("electronics", "Vintage film camera, 35mm", "89.00", "retro_optics", "used", "Cologne, DE", "Fully working, light meter OK."),
    ("fashion", "Leather jacket, size M", "140.00", "wardrobe", "used", "Paris, FR", "Genuine leather, black."),
    ("fashion", "Running shoes, US 10", "70.00", "sole_mate", "like_new", "Zurich, CH", "Worn twice, breathable mesh."),
    ("fashion", "Wool overcoat, navy", "95.00", "tailor_made", "used", "Milan, IT", "Classic cut, warm."),
    ("fashion", "Designer sunglasses", "60.00", "shade_co", "new", "Barcelona, ES", "Polarized, with case."),
    ("home-garden", "Espresso machine, 15 bar", "180.00", "brew_lab", "refurbished", "Hamburg, DE", "Descaled + tested."),
    ("home-garden", "Standing desk, electric", "260.00", "deskify", "used", "Rotterdam, NL", "Dual motor, 70-120cm."),
    ("home-garden", "Cast iron dutch oven", "55.00", "kitchen_kit", "like_new", "Lyon, FR", "5.5qt, enameled."),
    ("sports-outdoors", "Road bike, 54cm carbon", "640.00", "two_wheels", "used", "Innsbruck, AT", "Lightweight, recently serviced."),
    ("sports-outdoors", "Trekking backpack 45L", "85.00", "trailhead", "like_new", "Bern, CH", "Rain cover included."),
    ("sports-outdoors", "Tennis racket, pro", "95.00", "court_side", "used", "Madrid, ES", "Strung, great control."),
    ("collectibles", "Vinyl record bundle (10)", "75.00", "groove_vault", "used", "Manchester, UK", "70s-80s rock classics."),
    ("collectibles", "Rare trading card, graded", "220.00", "card_keep", "new", "Brussels, BE", "Sleeved, mint."),
    ("vehicles", "Vespa scooter 125cc", "1850.00", "moto_market", "used", "Rome, IT", "Runs great, new tyres."),
    ("vehicles", "Roof rack, universal", "110.00", "auto_parts", "like_new", "Stuttgart, DE", "Aero bars, locks."),
    ("books-media", "Sci-fi paperback set (8)", "40.00", "page_turner", "used", "Dublin, IE", "Classic anthology."),
    ("books-media", "Boxed strategy board game", "35.00", "meeple_co", "new", "Copenhagen, DK", "Sealed."),
    ("toys-games", "Retro game console", "130.00", "pixel_past", "refurbished", "Prague, CZ", "Tested, 2 controllers."),
    ("toys-games", "Building brick set, 1200pc", "60.00", "brick_town", "new", "Warsaw, PL", "Boxed, complete."),
]


class Command(BaseCommand):
    help = "Insert demo categories + listings (idempotent by name/title)."

    def handle(self, *args, **options):
        cats = {}
        for row in CATEGORIES:
            obj, created = Category.objects.get_or_create(name=row["name"], defaults=row)
            cats[obj.slug] = obj
            self.stdout.write(("created cat " if created else "exists  cat ") + obj.name)

        for cat_slug, title, price, seller, condition, location, desc in LISTINGS:
            category = cats.get(cat_slug) or Category.objects.filter(slug=cat_slug).first()
            _, created = Listing.objects.get_or_create(
                title=title,
                defaults={
                    "price": price,
                    "seller": seller,
                    "condition": condition,
                    "location": location,
                    "description": desc,
                    "category": category,
                },
            )
            self.stdout.write(("created " if created else "exists  ") + title)
