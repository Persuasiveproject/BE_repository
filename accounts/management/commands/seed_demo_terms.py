"""시연용 필수 약관 두 개(서비스 이용, 개인정보 처리)를 넣는다. 이미 있으면 건너뛴다.

실제 서비스 전에는 법무 검토를 거친 약관으로 새 버전을 넣어야 한다.
사용: python manage.py seed_demo_terms
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Term

DEMO = [
    (Term.Type.SERVICE, True, ("영업중 서비스 이용약관(시연용)\n\n이 약관은 산학 프로젝트 시연을 위한 임시 문서입니다. "
     "토론 글은 익명으로 공개되며, 다른 참가자를 비방하거나 개인을 알아볼 수 있는 정보를 올리면 글이 숨겨질 수 있습니다.")),
    (Term.Type.PRIVACY, True, ("개인정보 처리 안내(시연용)\n\n수집 항목: 휴대폰 번호 또는 카카오 회원번호, 닉네임, 관심 주제.\n"
     "이용 목적: 회원 식별, 중복 가입 방지, 토론 추천.\n보관 기간: 탈퇴 시까지. 시연이 끝나면 시연 데이터는 삭제합니다.")),
    (Term.Type.MARKETING, False, "이벤트·소식 알림 수신 동의(선택)\n\n새 토론과 이벤트 소식을 알림으로 받습니다. 동의하지 않아도 가입할 수 있습니다."),
]


class Command(BaseCommand):
    help = "시연용 약관을 넣는다(이미 있으면 건너뜀)."

    def handle(self, *args, **options):
        for kind, required, content in DEMO:
            _, created = Term.objects.get_or_create(
                type=kind, version="demo-1",
                defaults={"content": content, "is_required": required, "effective_at": timezone.now()},
            )
            self.stdout.write(f"{kind} demo-1: {'추가' if created else '이미 있음'}")
