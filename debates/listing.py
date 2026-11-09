from django.db.models import Q

from .api_contract import ContractError
from .models import Debate
from .serializers import utc


VISIBLE_STATUSES = (Debate.Status.OPEN, Debate.Status.CLOSED)


def filter_visible(debates, status, as_of):
    # debates must come from Debate.objects.with_deadline().
    if status is not None and status not in VISIBLE_STATUSES:
        raise ContractError(400, "FILTER_INVALID", "Use OPEN or CLOSED.")
    visible = debates.filter(status__in=VISIBLE_STATUSES)
    if status == Debate.Status.OPEN:
        visible = visible.filter(status=Debate.Status.OPEN).filter(
            Q(deadline__isnull=True) | Q(deadline__gt=as_of),
        )
    if status == Debate.Status.CLOSED:
        visible = visible.filter(Q(status=Debate.Status.CLOSED) | Q(deadline__lte=as_of))
    return visible


def debate_list_item(debate, as_of):
    status = debate.effective_status(as_of)
    closed_at = debate.deadline if debate.status != status else debate.closed_at
    return {
        "id": str(debate.pk),
        "kind": debate.type,
        "title": debate.title,
        "status": status,
        "createdAt": utc(debate.created_at),
        "categoryId": str(debate.category_id),
        "participantCount": debate.participant_count,
        "opinionCount": debate.opinion_count,
        "viewCount": debate.view_count,
        "startedAt": utc(debate.started_at),
        "endsAt": utc(debate.deadline),
        "closedAt": utc(closed_at),
        "canceledAt": utc(debate.canceled_at),
    }
