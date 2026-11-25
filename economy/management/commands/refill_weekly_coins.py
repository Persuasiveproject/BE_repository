import json

from django.core.management.base import BaseCommand

from economy.weekly_refill import refill_weekly_coins


class Command(BaseCommand):
    help = "Refill active members' coins for the current Korean calendar week."

    def handle(self, *args, **options):
        return json.dumps({"refilled": refill_weekly_coins()})
