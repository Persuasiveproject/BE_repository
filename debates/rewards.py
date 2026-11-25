from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from accounts.models import User
from discussions.models import Opinion, Persuasion
from economy.models import CoinTransaction, CreditHistory
from engagement.models import Notification

from .models import Stance, StanceHistory


MAX_CREDIT = Decimal("100")
CREDIT_UNIT = Decimal("0.000001")
DAILY_LIMITS = (Decimal("1"), Decimal("0.5"), Decimal("0.25"))
PERSUASION_REWARD = "PERSUASION_REWARD"
SEOUL = ZoneInfo("Asia/Seoul")


def successful_persuasions(debate):
    # The correlated subquery includes direct changes. Filtering them out here
    # would incorrectly reward an earlier opinion after a later direct change.
    latest = StanceHistory.objects.filter(
        participant_id=OuterRef("participant_id"),
    ).order_by("-changed_at", "-pk")
    return StanceHistory.objects.filter(
        participant__debate=debate,
        pk=Subquery(latest.values("pk")[:1]),
        to_stance=F("participant__current_stance"),
        persuasion__persuaded_user_id=F("participant__user_id"),
        persuasion__from_stance=F("from_stance"),
        persuasion__to_stance=F("to_stance"),
        persuasion__opinion__debate=debate,
        persuasion__opinion__participant__debate=debate,
        persuasion__opinion__status=Opinion.Status.VISIBLE,
        persuasion__opinion__participant__user__deleted_at__isnull=True,
    ).exclude(
        participant__initial_stance=F("participant__current_stance"),
    ).exclude(
        persuasion__opinion__participant__user_id=F("participant__user_id"),
    ).select_related("persuasion__opinion__participant")


def credit_amount(account, successes):
    today = timezone.localdate(timezone=SEOUL)
    start = datetime.combine(today, time.min, tzinfo=SEOUL)
    paid = CreditHistory.objects.filter(
        user=account, reason=PERSUASION_REWARD, amount__gt=0,
        created_at__gte=start, created_at__lt=start + timedelta(days=1),
    ).values("ref_id").distinct().count()
    if paid >= len(DAILY_LIMITS) or account.credit >= MAX_CREDIT:
        return Decimal("0")
    remaining = MAX_CREDIT - account.credit
    weight = sum(
        Decimal("0.5") if row.from_stance != Stance.NEUTRAL and row.to_stance == Stance.NEUTRAL
        else Decimal("1")
        for row in successes
    )
    points = Decimal("0.25") * remaining * weight / Decimal("70")
    return min(points, DAILY_LIMITS[paid], remaining).quantize(CREDIT_UNIT, rounding=ROUND_HALF_UP)


def award_debate_rewards(debate, total_participants):
    """Called once under the debate lock in the result transaction."""
    successes = defaultdict(list)
    counts = Counter()
    for history in successful_persuasions(debate):
        opinion = history.persuasion.opinion
        successes[opinion.participant.user_id].append(history)
        counts[opinion.pk] += 1

    recipients = set(successes)
    if total_participants >= 20:
        recipients.add(debate.creator_id)
    # The same user can earn rewards in multiple debates settled concurrently.
    # Serialize their credit caps and coin balance in a stable lock order.
    accounts = User.objects.select_for_update().filter(pk__in=recipients).exclude(status=User.Status.DELETED).order_by("pk")
    for account in accounts:
        wins = successes.get(account.pk)
        if wins:
            amount = credit_amount(account, wins)
            if amount > 0:
                account.credit += amount
                account.save(update_fields=["credit"])
                CreditHistory.objects.create(
                    user=account, amount=amount, reason=PERSUASION_REWARD, ref_id=debate.pk,
                )
                Persuasion.objects.filter(pk__in=[row.persuasion_id for row in wins]).update(reward_given=True)
                Notification.objects.create(
                    user=account, type=Notification.Type.PERSUASION_REWARD, title="영업 성공 보상",
                    body=f"영업 {len(wins)}건 성공으로 크레딧 {amount:.6f}점을 받았어요.",
                    target_type="DEBATE", target_id=debate.pk,
                )
        if account.pk == debate.creator_id and total_participants >= 20:
            account.coin_balance += 1
            account.save(update_fields=["coin_balance"])
            CoinTransaction.objects.create(
                user=account, amount=1, type=CoinTransaction.Type.REWARD, balance_after=account.coin_balance,
                ref_type=CoinTransaction.RefType.DEBATE, ref_id=debate.pk,
            )

    for opinion_id, count in counts.items():
        Opinion.objects.filter(pk=opinion_id).update(persuasion_count=count)
