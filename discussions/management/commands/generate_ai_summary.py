from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from discussions.ai.service import generate_summary


class Command(BaseCommand):
    help = "토론 하나의 AI 요약을 바로 만든다(시연·점검용). 선정 의견이 같아도 다시 만든다."

    def add_arguments(self, parser):
        parser.add_argument("debate_id", type=int)

    def handle(self, *args, debate_id, **options):
        if not settings.GEMINI_API_KEY:
            raise CommandError("GEMINI_API_KEY가 비어 있다. .env에 넣고 다시 실행한다.")
        result = generate_summary(debate_id, force=True)
        self.stdout.write(f"debate {debate_id}: {result}")
