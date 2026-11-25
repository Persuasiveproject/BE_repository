import logging

from celery import shared_task

from debates.models import Debate
from debates.results import compute_debate_result

logger = logging.getLogger(__name__)


@shared_task(name="debates.tasks.compute_pending_results")
def compute_pending_results():
    # This sweep is for the Celery-beat deployment option. The management-command
    # runtime uses its own timer instead: enable exactly one retry scheduler.
    # Never combine a periodic sweep with task.retry().
    # Scan past cooling-down/locked rows, so 100 failures cannot starve later IDs.
    debate_ids = (
        Debate.objects.filter(
            status=Debate.Status.CLOSED, debatestatistics__isnull=True,
        ).order_by(
            "closed_at", "id",
        ).values_list("id", flat=True).iterator(chunk_size=100)
    )
    completed = 0
    for debate_id in debate_ids:
        try:
            completed += compute_debate_result(debate_id)
        except Exception:
            # Includes transaction/commit failures outside the aggregation savepoint.
            # No durable hint is needed: the next sweep rediscovers the missing row.
            logger.exception("Result attempt aborted for debate %s", debate_id)
    return completed
