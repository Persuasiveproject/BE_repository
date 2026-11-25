from django.db.models.signals import post_save
from django.dispatch import receiver

from engagement.notifications import notify_debate_canceled

from .models import Debate


@receiver(post_save, sender=Debate)
def notify_after_debate_cancellation(sender, instance, created, raw, update_fields, **kwargs):
    if not raw and not created and instance.status == Debate.Status.CANCELED and (
        update_fields is None or {"status", "canceled_at"} <= update_fields
    ) and instance.canceled_at is not None:
        # The cancellation service holds the debate lock and commits this with the refund.
        notify_debate_canceled(instance)
