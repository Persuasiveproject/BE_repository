from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from debates.models import Debate, Stance
from debates.rewards import CREDIT_UNIT, PERSUASION_REWARD
from discussions.models import Opinion, Persuasion
from economy.models import CreditHistory


PENALTIES = {
    "mild": Decimal("0.5"), "moderate": Decimal("2"),
    "severe": Decimal("5"), "fraud": Decimal("15"),
}


def _record_debit(user, opinion, reason, amount):
    if CreditHistory.objects.filter(user=user, reason=reason, ref_id=opinion.pk).exists():
        return
    debit = max(Decimal("0"), min(user.credit, amount))
    user.credit -= debit
    user.save(update_fields=["credit"])
    CreditHistory.objects.create(user=user, reason=reason, ref_id=opinion.pk, amount=-debit)


def _paid_share(user, opinion):
    paid = CreditHistory.objects.filter(
        user=user, reason=PERSUASION_REWARD, ref_id=opinion.debate_id, amount__gt=0,
    ).values_list("amount", flat=True).first()
    if paid is None:
        return Decimal("0")
    wins = Persuasion.objects.filter(
        opinion__debate_id=opinion.debate_id, opinion__participant__user=user, reward_given=True,
    ).values_list("opinion_id", "from_stance", "to_stance")
    # P2 stores the capped total per author and debate. Allocate it by the same
    # weights used to calculate payment, with cumulative rounding to preserve the total.
    weights = Counter()
    for opinion_id, previous, current in wins:
        weights[opinion_id] += 1 if previous != Stance.NEUTRAL and current == Stance.NEUTRAL else 2
    if opinion.pk not in weights:
        return Decimal("0")
    denominator = sum(weights.values())
    before = sum(weight for pk, weight in weights.items() if pk < opinion.pk)
    start = (paid * before / denominator).quantize(CREDIT_UNIT, rounding=ROUND_HALF_UP)
    end = (paid * (before + weights[opinion.pk]) / denominator).quantize(CREDIT_UNIT, rounding=ROUND_HALF_UP)
    return end - start


@transaction.atomic
def moderate_opinion(opinion_id, *, severity=None):
    penalty = PENALTIES[severity] if severity is not None else None
    target = Opinion.objects.values("debate_id", "participant__user_id").get(pk=opinion_id)
    Debate.objects.select_for_update().get(pk=target["debate_id"])
    user = User.objects.select_for_update().get(pk=target["participant__user_id"])
    opinion = Opinion.objects.select_for_update().get(pk=opinion_id)
    if opinion.status == Opinion.Status.VISIBLE:
        opinion.status = Opinion.Status.FILTERED
        opinion.updated_at = timezone.now()
        opinion.save(update_fields=["status", "updated_at"])
    if user.status == User.Status.DELETED:
        return
    share = _paid_share(user, opinion)
    if share:
        _record_debit(user, opinion, "PERSUASION_RECLAIM", share)
    if penalty is not None:
        _record_debit(user, opinion, "MODERATION_PENALTY", penalty)
