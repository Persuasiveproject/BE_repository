from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .ai.tasks import refresh_summary_on_commit
from .models import Opinion


@receiver(post_save, sender=Opinion)
def refresh_for_hidden_opinion(sender, instance, created, raw, update_fields, **kwargs):
    if not raw and not created and instance.status != Opinion.Status.VISIBLE and (
        update_fields is None or "status" in update_fields
    ):
        refresh_summary_on_commit(instance.debate_id)


@receiver(post_delete, sender=Opinion)
def refresh_for_deleted_opinion(sender, instance, **kwargs):
    refresh_summary_on_commit(instance.debate_id)
