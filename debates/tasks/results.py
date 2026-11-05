from celery import shared_task
from django.db.models import F, Q
from django.utils import timezone

from debates.models import Debate
from debates.results import compute_debate_result


@shared_task(name="debates.tasks.compute_pending_results")
def compute_pending_results():
    debate_ids = list(
        Debate.objects.filter(
            status=Debate.Status.CLOSED, debatestatistics__isnull=True,
        ).filter(
            Q(result_retry_at__isnull=True) | Q(result_retry_at__lte=timezone.now()),
        ).order_by(
            F("result_retry_at").asc(nulls_first=True), "closed_at", "id",
        ).values_list("id", flat=True)[:100]
    )
    return sum(compute_debate_result(debate_id) for debate_id in debate_ids)
