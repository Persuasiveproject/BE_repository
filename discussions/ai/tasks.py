"""Periodic and post-commit summary refreshes."""
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from debates.models import Debate
from discussions.models import AISummary

from .service import generate_summary

INTERVAL = {Debate.Type.QUICK: timedelta(minutes=2), Debate.Type.ENDLESS: timedelta(minutes=5)}
BATCH = 20
ATTEMPTED = ("created", "failed", "invalidated")


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

    # Fetch unfinished final summaries from the database so a scheduler outage
    # does not permanently miss debates that closed during it.
    closed_ids = Debate.objects.filter(
        status=Debate.Status.CLOSED, closed_at__isnull=False, ai_summary_finalized_at__isnull=True,
    ).order_by("closed_at", "id").values_list("id", flat=True)
    attempts = 0
    for debate_id in closed_ids.iterator(chunk_size=BATCH):
        result = generate_summary(debate_id, finalize=True)
        done += result == "created"
        attempts += result in ATTEMPTED
        if attempts >= BATCH:
            break

    # 2) 진행 중 토론 주기 갱신
    open_debates = list(Debate.objects.filter(status=Debate.Status.OPEN).values_list("id", "type"))
    last = _last_created([i for i, _ in open_debates])
    epoch = now - timedelta(days=3650)
    due = [
        (last.get(i) or epoch, i) for i, t in open_debates
        if last.get(i) is None or now - last[i] >= INTERVAL.get(t, INTERVAL[Debate.Type.ENDLESS])
    ]
    attempts = 0
    for _, debate_id in sorted(due):
        result = generate_summary(debate_id)
        done += result == "created"
        attempts += result in ATTEMPTED
        if attempts >= BATCH:
            break
    return done


@shared_task(name="discussions.tasks.refresh_debate_summary")
def refresh_debate_summary(debate_id):
    """글 삭제·숨김 직후 등 한 토론만 즉시 다시 확인할 때 쓴다."""
    if not settings.AI_SUMMARY_ENABLED:
        return "disabled"
    finalize = Debate.objects.filter(pk=debate_id, status=Debate.Status.CLOSED, closed_at__isnull=False).exists()
    return generate_summary(debate_id, finalize=finalize)


def refresh_summary_on_commit(debate_id):
    """Call after changing opinion visibility, including QuerySet.update()."""
    def refresh():
        if not AISummary.objects.filter(debate_id=debate_id).exists():
            return
        Debate.objects.filter(pk=debate_id, status=Debate.Status.CLOSED).update(ai_summary_finalized_at=None)
        if settings.AI_SUMMARY_ENABLED:
            refresh_debate_summary.delay(debate_id)

    transaction.on_commit(refresh, robust=True)
