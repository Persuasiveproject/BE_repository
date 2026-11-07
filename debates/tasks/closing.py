from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from debates.models import Debate


@shared_task(name="debates.tasks.close_due_debates")
@transaction.atomic
def close_due_debates():
    now = timezone.now()
    due_ids = list(
        Debate.objects.with_deadline().select_for_update(skip_locked=True)
        .filter(status=Debate.Status.OPEN, deadline__lte=now)
        .order_by("deadline", "id")
        .values_list("id", flat=True)[:100]
    )
    return Debate.objects.with_deadline().filter(pk__in=due_ids).update(
        status=Debate.Status.CLOSED, closed_at=F("deadline"), ends_at=F("deadline"),
    )
