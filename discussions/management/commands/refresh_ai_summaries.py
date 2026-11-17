from django.core.management.base import BaseCommand

from discussions.ai.tasks import refresh_ai_summaries


class Command(BaseCommand):
    help = "Run one scheduled AI summary refresh pass."

    def handle(self, *args, **options):
        return str(refresh_ai_summaries())
