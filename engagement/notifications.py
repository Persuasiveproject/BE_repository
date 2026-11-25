from datetime import timedelta

from django.utils import timezone

from accounts.models import User
from debates.models import DebateParticipant
from discussions.models import Opinion

from .models import Notification

REPLY_BUNDLE_WINDOW = timedelta(seconds=60)


def notify_debate_canceled(debate):
    recipients = DebateParticipant.objects.filter(debate=debate).exclude(
        user__status=User.Status.DELETED,
    ).values_list("user_id", flat=True)
    Notification.objects.bulk_create([
        Notification(user_id=user_id, type=Notification.Type.DEBATE_CANCELED,
                     title="참여한 토론이 취소됐어요", body=debate.title,
                     target_type="DEBATE", target_id=debate.pk)
        for user_id in recipients
    ], batch_size=500)


def notify_reply(parent, reply):
    user_id = parent.participant.user_id
    if user_id == reply.participant.user_id:
        return
    target = {"user_id": user_id, "type": Notification.Type.REPLY, "target_type": "OPINION", "target_id": parent.pk}
    bundle = Notification.objects.filter(
        **target, is_read=False, created_at__gte=timezone.now() - REPLY_BUNDLE_WINDOW,
    ).order_by("-created_at").first()
    if bundle is None:
        Notification.objects.create(**target, title="내 글에 답글이 달렸어요", body="답글 1개")
        return
    # The reply that opened the bundle was saved just before it, so count it separately.
    count = 1 + Opinion.objects.filter(parent=parent, created_at__gt=bundle.created_at).exclude(
        participant__user_id=user_id,
    ).count()
    Notification.objects.filter(pk=bundle.pk).update(body=f"답글 {count}개")
