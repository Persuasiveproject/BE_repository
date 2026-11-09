from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from economy.models import CoinTransaction

from .api_contract import ContractError
from .idempotency import request_hash, require_valid_key, save_response, saved_response
from .models import Debate, DebateImage
from .serializers import utc


PUBLISH_COST = 1


@transaction.atomic
def publish_debate(*, debate_id, user, idempotency_key, request_path):
    require_valid_key(idempotency_key)

    # Lock order matches change_stance: debate -> user.
    debate = Debate.objects.select_for_update().filter(pk=debate_id).first()
    if debate is None:
        raise ContractError(404, "NOT_FOUND", "Debate not found.")
    if debate.creator_id != user.pk:
        raise ContractError(403, "NOT_DEBATE_CREATOR", "Only the creator can publish this debate.")
    account = User.objects.select_for_update().filter(pk=user.pk).first()
    if account is None or account.status != User.Status.ACTIVE:
        raise ContractError(403, "FORBIDDEN", "This account cannot publish debates.")

    scope = {
        "user": user,
        "http_method": "POST",
        "request_path": request_path,
        "idempotency_key": idempotency_key,
    }
    body_hash = request_hash([])
    saved = saved_response(scope, body_hash)
    if saved is not None:
        return saved

    if debate.status != Debate.Status.DRAFT:
        raise ContractError(409, "NOT_DRAFT", "Only drafts can be published.")
    if DebateImage.objects.filter(debate=debate).exclude(status=DebateImage.Status.READY).exists():
        raise ContractError(409, "ASSET_NOT_READY", "Images are still being checked.")
    if account.coin_balance < PUBLISH_COST:
        raise ContractError(409, "INSUFFICIENT_COINS", "Not enough coins to publish.")

    account.coin_balance -= PUBLISH_COST
    account.save(update_fields=["coin_balance"])
    CoinTransaction.objects.create(
        user=account, amount=-PUBLISH_COST, type=CoinTransaction.Type.DEBATE_CREATE,
        balance_after=account.coin_balance,
        ref_type=CoinTransaction.RefType.DEBATE, ref_id=debate.pk,
    )

    now = timezone.now()
    if debate.type == Debate.Type.ENDLESS:
        duration = timedelta(hours=72)
    else:
        duration = timedelta(minutes=debate.duration_minutes)
    debate.status = Debate.Status.OPEN
    debate.coin_cost = PUBLISH_COST
    debate.started_at = now
    debate.ends_at = now + duration
    debate.save(update_fields=["status", "coin_cost", "started_at", "ends_at"])

    response = {"data": {
        "debateId": str(debate.pk),
        "status": debate.status,
        "startedAt": utc(debate.started_at),
        "endsAt": utc(debate.ends_at),
        "coinBalance": account.coin_balance,
    }}
    save_response(scope, body_hash, response, 200)
    return response, 200
