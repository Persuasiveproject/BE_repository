from celery import shared_task
from django.utils import timezone

from .models import WithdrawnIdentity


@shared_task
def purge_expired_withdrawn_identities():
    deleted, _ = WithdrawnIdentity.objects.filter(expires_at__lte=timezone.now()).delete()
    return deleted
