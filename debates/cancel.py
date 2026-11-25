from django.db import transaction
from django.utils import timezone

from accounts.models import User
from economy.models import CoinTransaction
from engagement.notifications import notify_debate_canceled

from .api_contract import ContractError
from .models import Debate


@transaction.atomic
def cancel_debate(*, debate_id, operator, refund, reason):
    # Match the publish lock order so cancellation and balance changes serialize.
    debate = Debate.objects.with_deadline().select_for_update().filter(pk=debate_id).first()
    if debate is None:
        raise ContractError(404, "NOT_FOUND", "Debate not found.")

    charges = CoinTransaction.objects.filter(
        user_id=debate.creator_id,
        type=CoinTransaction.Type.DEBATE_CREATE,
        ref_type=CoinTransaction.RefType.DEBATE,
        ref_id=debate.pk,
    )
    if refund and charges.filter(refund__isnull=False).exists():
        raise ContractError(409, "REFUND_ALREADY_PROCESSED", "The publishing charge was already refunded.")

    now = timezone.now()
    if debate.started_at is None or debate.effective_status(now) != Debate.Status.OPEN:
        raise ContractError(409, "INVALID_DEBATE_STATE", "Only an active debate can be canceled.")

    reversal = None
    if refund:
        try:
            charge = charges.get()
        except (CoinTransaction.DoesNotExist, CoinTransaction.MultipleObjectsReturned) as exc:
            raise ContractError(409, "INVALID_DEBATE_STATE", "A single publishing charge is required.") from exc
        if debate.coin_cost <= 0 or charge.amount != -debate.coin_cost:
            raise ContractError(409, "INVALID_DEBATE_STATE", "The publishing charge does not match the debate.")

        account = User.objects.select_for_update().get(pk=debate.creator_id)
        refund_amount = -charge.amount
        account.coin_balance += refund_amount
        account.save(update_fields=["coin_balance"])
        reversal = CoinTransaction.objects.create(
            user=account,
            amount=refund_amount,
            type=CoinTransaction.Type.REFUND,
            balance_after=account.coin_balance,
            ref_type=CoinTransaction.RefType.DEBATE,
            ref_id=debate.pk,
            reverses_transaction=charge,
            created_by=operator,
        )

    debate.status = Debate.Status.CANCELED
    debate.canceled_at = now
    debate.canceled_by = operator
    debate.cancel_reason = reason
    debate.save(update_fields=["status", "canceled_at", "canceled_by", "cancel_reason"])
    notify_debate_canceled(debate)
    return {
        "data": {
            "debateId": str(debate.pk),
            "status": debate.status,
            "refundedCoin": reversal.amount if reversal is not None else 0,
            "refundTransactionId": str(reversal.pk) if reversal is not None else None,
        },
    }
