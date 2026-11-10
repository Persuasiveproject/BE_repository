"""요약 생성·갱신·삭제 반영 (FC-DB-220-002·003·006)."""
import logging

from django.conf import settings
from django.db import transaction

from debates.models import Debate, Stance
from discussions.models import AISummary

from . import client, state
from .prompt import build_user_text, to_summary_text
from .selection import MIN_PUBLIC_POSTS, fingerprint, input_ids, public_post_count, select_inputs, visible_posts
from .validate import SummaryRejected, clean

logger = logging.getLogger(__name__)


def drop_stale_summaries(debate_id):
    """근거 글이 삭제·숨김되면 그 요약을 내린다(FC-DB-220-006). 내린 것이 있으면 True."""
    rows = list(AISummary.objects.filter(debate_id=debate_id).only("id", "source_opinion_ids"))
    if not rows:
        return False
    visible = {str(i) for i in visible_posts(debate_id).values_list("id", flat=True)}
    stale = [r.pk for r in rows if any(str(i) not in visible for i in (r.source_opinion_ids or []))]
    if stale:
        AISummary.objects.filter(pk__in=stale).delete()
        logger.info("Dropped %d stale summaries for debate %s", len(stale), debate_id)
    return bool(stale)


def estimate_cost(usage):
    tokens_in = usage.get("promptTokenCount") or 0
    tokens_out = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
    return (tokens_in * settings.AI_SUMMARY_PRICE_IN + tokens_out * settings.AI_SUMMARY_PRICE_OUT) / 1e6


@transaction.atomic
def save_summaries(debate, items_by_stance):
    created = 0
    for stance in (Stance.PRO, Stance.CON):
        items = items_by_stance.get(stance) or []
        if not items:
            continue  # 새 항목이 없으면 이전 요약을 그대로 둔다
        ids = sorted({int(i) for item in items for i in item["sourceIds"]})
        AISummary.objects.create(
            debate=debate, stance=stance, summary_text=to_summary_text(items),
            source_opinion_ids=ids, model=settings.AI_SUMMARY_MODEL[:50],
        )
        created += 1
    return created


def generate_summary(debate_id, *, force=False):
    """한 토론의 요약을 필요하면 만든다. 반환값은 결과 상태 문자열(로그·테스트용)."""
    debate = Debate.objects.filter(
        pk=debate_id, status__in=[Debate.Status.OPEN, Debate.Status.CLOSED],
    ).only("id", "title", "status").first()
    if debate is None:
        return "skipped"
    token = state.acquire_lock(debate_id)
    if token is None:
        return "locked"
    try:
        stale = drop_stale_summaries(debate_id)
        if state.in_backoff(debate_id) and not stale:
            return "backoff"
        if public_post_count(debate_id) < MIN_PUBLIC_POSTS:
            return "insufficient"  # 화면은 '의견이 더 모이면 요약해 드려요'
        groups = select_inputs(debate_id)
        fp = fingerprint(groups)
        if (not force and not stale and state.get_fingerprint(debate_id) == fp
                and AISummary.objects.filter(debate_id=debate_id).exists()):
            return "unchanged"
        if state.month_cost() >= settings.AI_SUMMARY_MONTHLY_BUDGET_USD:
            logger.warning("Monthly AI summary budget reached; skipping debate %s", debate_id)
            return "budget"

        raw, usage = client.generate(build_user_text(debate.title, groups))
        state.add_cost(estimate_cost(usage))
        items = clean(raw, input_ids(groups))
        created = save_summaries(debate, items)
        state.set_fingerprint(debate_id, fp)
        logger.info(
            "AI summary debate=%s created=%d model=%s usage=%s",
            debate_id, created, settings.AI_SUMMARY_MODEL, usage,
        )
        return "created" if created else "empty"
    except (client.SummaryLLMError, SummaryRejected) as e:
        state.set_backoff(debate_id)
        logger.warning("AI summary failed for debate %s: %s", debate_id, e)
        return "failed"
    finally:
        state.release_lock(debate_id, token)
