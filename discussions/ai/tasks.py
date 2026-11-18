"""요약 주기 작업 (FC-DB-220-003·006).

- 진행 중 토론: 공개 글 5개 이상이고 마지막 생성 후 5분(간단토론 2분)이 지나면 다시 만든다.
  선정 의견이 그대로면 건너뛴다(service에서 판단).
- 종료된 토론: 종료 직후 최종 요약을 한 번 만든다.
- 근거 글 삭제·숨김: 매 주기 확인해 요약을 내리고 다시 만든다. 삭제 API에서 즉시 반영하려면
  refresh_debate_summary.delay(debate_id)를 호출한다.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from debates.models import Debate
from discussions.models import AISummary

from . import state
from .service import generate_summary

logger = logging.getLogger(__name__)
INTERVAL = {Debate.Type.QUICK: timedelta(minutes=2), Debate.Type.ENDLESS: timedelta(minutes=5)}
FINAL_WINDOW = timedelta(hours=1)
BATCH = 20


def _last_created(debate_ids):
    return dict(
        AISummary.objects.filter(debate_id__in=debate_ids)
        .values("debate_id").annotate(last=Max("created_at")).values_list("debate_id", "last")
    )


@shared_task(name="discussions.tasks.refresh_ai_summaries")
def refresh_ai_summaries():
    if not settings.AI_SUMMARY_ENABLED:
        return 0
    now = timezone.now()
    done = 0

    # 1) 종료 직후 최종 요약 1회
    closed = list(
        Debate.objects.filter(status=Debate.Status.CLOSED, closed_at__gte=now - FINAL_WINDOW)
        .order_by("closed_at", "id").values_list("id", "closed_at")[:BATCH]
    )
    last = _last_created([i for i, _ in closed])
    for debate_id, closed_at in closed:
        if state.final_done(debate_id) or (last.get(debate_id) and last[debate_id] >= closed_at):
            continue
        result = generate_summary(debate_id, force=True)
        if result not in ("failed", "locked", "budget", "backoff"):
            state.mark_final_done(debate_id)
        done += result == "created"

    # 2) 진행 중 토론 주기 갱신
    open_debates = list(Debate.objects.filter(status=Debate.Status.OPEN).values_list("id", "type"))
    last = _last_created([i for i, _ in open_debates])
    epoch = now - timedelta(days=3650)
    due = [
        (last.get(i) or epoch, i) for i, t in open_debates
        if last.get(i) is None or now - last[i] >= INTERVAL.get(t, INTERVAL[Debate.Type.ENDLESS])
    ]
    for _, debate_id in sorted(due)[:BATCH]:
        done += generate_summary(debate_id) == "created"
    return done


@shared_task(name="discussions.tasks.refresh_debate_summary")
def refresh_debate_summary(debate_id):
    """글 삭제·숨김 직후 등 한 토론만 즉시 다시 확인할 때 쓴다."""
    if not settings.AI_SUMMARY_ENABLED:
        return "disabled"
    return generate_summary(debate_id)
