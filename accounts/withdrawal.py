from calendar import monthrange

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from discussions.ai.tasks import refresh_summary_on_commit
from discussions.models import Opinion, OpinionReference
from engagement.models import Notification, Scrap

from .models import APIIdempotencyRecord, User, UserIdentityVerification, UserInterestCategory, WithdrawnIdentity


def rejoin_fingerprint(provider, provider_id):
    return salted_hmac("accounts.withdrawal.v1", f"{provider}:{provider_id}", algorithm="sha256").hexdigest()


def remember_withdrawal(user, now):
    if not user.provider or not user.provider_id:
        return
    anniversary = timezone.localtime(now)
    year = anniversary.year + 1
    day = min(anniversary.day, monthrange(year, anniversary.month)[1])
    WithdrawnIdentity.objects.update_or_create(
        fingerprint=rejoin_fingerprint(user.provider, user.provider_id),
        defaults={"credit": user.credit, "was_suspended": user.status == User.Status.SUSPENDED,
                  "expires_at": anniversary.replace(year=year, day=day)},
    )


@transaction.atomic
def withdraw_account(user_id):
    user = User.objects.select_for_update().get(pk=user_id)
    if user.status == User.Status.DELETED:
        return
    now = timezone.now()
    remember_withdrawal(user, now)
    user.status = User.Status.DELETED
    user.deleted_at = now
    user.email = ""
    user.nickname = "탈퇴한 회원"
    user.password = None
    user.provider = user.provider_id = user.profile_image_url = None
    user.gender = user.birth_year = user.region = None
    user.statistics_consent = False
    user.statistics_consent_updated_at = None
    user.notification_enabled = False
    user.credit = 0
    user.coin_balance = 0
    user.save(update_fields=[
        "status", "deleted_at", "email", "nickname", "password", "provider", "provider_id",
        "profile_image_url", "gender", "birth_year", "region", "statistics_consent",
        "statistics_consent_updated_at", "notification_enabled", "credit", "coin_balance", "updated_at",
    ])

    posts = Opinion.objects.filter(participant__user=user)
    debate_ids = set(posts.values_list("debate_id", flat=True))
    OpinionReference.objects.filter(opinion__in=posts).update(url="")
    posts.update(status=Opinion.Status.DELETED, content="", content_plain="", badge=None, filter_reason=None, updated_at=now)
    UserIdentityVerification.objects.filter(user=user).delete()
    UserInterestCategory.objects.filter(user=user).delete()
    APIIdempotencyRecord.objects.filter(user=user).delete()
    Scrap.objects.filter(user=user).delete()
    Notification.objects.filter(user=user).delete()
    for debate_id in debate_ids:
        refresh_summary_on_commit(debate_id)
