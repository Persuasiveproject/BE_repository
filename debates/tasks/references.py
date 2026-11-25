import logging

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from debates.api.references import safety
from discussions.models import OpinionReference

logger = logging.getLogger(__name__)


@shared_task(name="debates.tasks.check_references")
def check_references():
    if not settings.SAFE_BROWSING_ENABLED or not settings.SAFE_BROWSING_API_KEY:
        return 0
    stale = Q(checked_at__isnull=True) | Q(checked_at__lt=timezone.now() - safety.REFERENCE_CHECK_TTL)
    targets = list(
        OpinionReference.objects.filter(stale).order_by("id").values_list("id", "url")[:safety.LOOKUP_LIMIT]
    )
    if not targets:
        return 0
    try:
        unsafe = safety.lookup_unsafe(sorted({url for _, url in targets}))
    except Exception:
        # Leave every row as it was; the next run retries the same links.
        logger.warning("Safe Browsing lookup failed for %d links", len(targets), exc_info=True)
        return 0

    now = timezone.now()
    updated = 0
    for reference_id, url in targets:
        status = OpinionReference.SafetyStatus.UNSAFE if url in unsafe else OpinionReference.SafetyStatus.SAFE
        # Matching the url skips links replaced through #39 while the lookup ran.
        updated += OpinionReference.objects.filter(pk=reference_id, url=url).update(
            safe_browsing_status=status, checked_at=now,
        )
    return updated
