from datetime import timedelta

from django.utils import timezone

from debates.api_contract import ContractError
from discussions.models import Opinion


RATE_WINDOW = timedelta(seconds=60)
RATE_LIMIT = 10
DUPLICATE_WINDOW = timedelta(minutes=10)


def _normalize(content):
    return " ".join(content.split())


def _rate_limited(reason):
    return ContractError(429, "POST_RATE_LIMITED", "Too many posts. Try again shortly.", details={"reason": reason})


def require_not_spamming(user, debate, content):
    # Callers hold the user row lock, so these counts cannot race with the same user's writes.
    now = timezone.now()
    recent = Opinion.objects.filter(participant__user=user, created_at__gt=now - RATE_WINDOW)
    if recent.count() >= RATE_LIMIT:
        raise _rate_limited("RATE")
    text = _normalize(content)
    previous = Opinion.objects.filter(
        participant__user=user, debate=debate, created_at__gt=now - DUPLICATE_WINDOW,
    ).values_list("content", flat=True)
    if any(_normalize(item) == text for item in previous):
        raise _rate_limited("DUPLICATE")
