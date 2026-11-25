from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from economy.models import CoinTransaction


SEOUL = ZoneInfo("Asia/Seoul")
WEEKLY_BALANCE = 3


@transaction.atomic
def refill_account(user_id, week):
    account = User.objects.select_for_update().filter(pk=user_id, status=User.Status.ACTIVE).first()
    if account is None or (
        account.last_weekly_coin_refill_on is not None and account.last_weekly_coin_refill_on >= week
    ):
        return False

    # Record an evaluation even at full balance so a retry cannot refill after a later spend.
    account.last_weekly_coin_refill_on = week
    if account.coin_balance >= WEEKLY_BALANCE:
        account.save(update_fields=["last_weekly_coin_refill_on"])
        return False

    amount = WEEKLY_BALANCE - account.coin_balance
    account.coin_balance = WEEKLY_BALANCE
    account.save(update_fields=["coin_balance", "last_weekly_coin_refill_on"])
    CoinTransaction.objects.create(
        user=account,
        amount=amount,
        type=CoinTransaction.Type.WEEKLY_REFILL,
        balance_after=WEEKLY_BALANCE,
        ref_type=CoinTransaction.RefType.USER,
        ref_id=account.pk,
    )
    return True


def refill_weekly_coins():
    now = timezone.localtime(timezone.now(), SEOUL)
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    week = week_start.date()
    users = User.objects.filter(status=User.Status.ACTIVE, created_at__lte=week_start).filter(
        Q(last_weekly_coin_refill_on__isnull=True) | Q(last_weekly_coin_refill_on__lt=week),
    )
    user_ids = users.order_by("pk").values_list("pk", flat=True)
    return sum(refill_account(user_id, week) for user_id in user_ids.iterator(chunk_size=500))
