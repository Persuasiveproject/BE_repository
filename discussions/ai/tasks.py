"""요약 주기 작업 (FC-DB-220-003·006).

- 진행 중 토론: 공개 글 5개 이상이고 마지막 생성 후 5분(간단토론 2분)이 지나면 다시 만든다.
  선정 의견이 그대로면 건너뛴다(service에서 판단).
- 종료된 토론: 종료 직후 최종 요약을 한 번 만든다.
- 근거 글 삭제·숨김: 매 주기 확인해 요약을 내리고 다시 만든다. 삭제 API에서 즉시 반영하려면
  refresh_debate_summary.delay(debate_id)를 호출한다.
- 쟁점 정리(끝장토론만): 30분마다 확인하고, 입력 글 구성이 바뀌었을 때만 다시 만든다.
  종료된 끝장토론은 종료 직후 한 번 더 확인한다. 화면은 읽을 때 숨김·삭제된 근거를 뺀다.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from debates.models import Debate
from discussions.models import AIIssueMap, AISummary

from . import state
from .issues import generate_issue_map
from .service import generate_summary

logger = logging.getLogger(__name__)
INTERVAL = {Debate.Type.QUICK: timedelta(minutes=2), Debate.Type.ENDLESS: timedelta(minutes=5)}
FINAL_WINDOW = timedelta(hours=1)
ISSUE_INTERVAL = timedelta(minutes=30)
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

    # 3) 끝장토론 쟁점 정리: 진행 중은 30분마다, 종료된 것은 종료 직후 한 번
    endless_open = [i for i, t in open_debates if t == Debate.Type.ENDLESS]
    endless_closed = list(
        Debate.objects.filter(pk__in=[i for i, _ in closed], type=Debate.Type.ENDLESS).values_list("id", flat=True)
    )
    last_issue = dict(
        AIIssueMap.objects.filter(debate_id__in=endless_open + endless_closed)
        .values("debate_id").annotate(last=Max("created_at")).values_list("debate_id", "last")
    )
    issue_due = [(last_issue.get(i) or epoch, i) for i in endless_open
                 if last_issue.get(i) is None or now - last_issue[i] >= ISSUE_INTERVAL]
    issue_due += [(epoch, i) for i in endless_closed]
    for _, debate_id in sorted(issue_due)[:BATCH]:
        generate_issue_map(debate_id)
    return done


@shared_task(name="discussions.tasks.refresh_debate_summary")
def refresh_debate_summary(debate_id):
    """글 삭제·숨김 직후 등 한 토론만 즉시 다시 확인할 때 쓴다."""
    if not settings.AI_SUMMARY_ENABLED:
        return "disabled"
    return generate_summary(debate_id)
