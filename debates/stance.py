import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from accounts.models import APIIdempotencyRecord, User
from discussions.models import Persuasion

from .api_contract import ContractError
from .models import Debate, DebateParticipant, StanceHistory
from .stance_checks import (
    StanceConflict, eligible_opinion, require_before_deadline, require_current_version,
    require_open_debate,
)


@transaction.atomic
def change_stance(
    *, debate_id, user, stance, idempotency_key, request_path, opinion_id=None, version=None,
):
    if (
        not idempotency_key
        or len(idempotency_key) > 128
        or not idempotency_key.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in idempotency_key)
    ):
        raise ContractError(400, "IDEMPOTENCY_KEY_INVALID", "Use a nonblank key of up to 128 characters.")

    # Lock order: debate -> user -> participant -> opinion. Account changes must
    # not acquire the same rows in reverse order.
    debate = (
        Debate.objects.with_deadline().select_for_update()
        .exclude(status=Debate.Status.DRAFT).filter(pk=debate_id).first()
    )
    if debate is None:
        raise ContractError(404, "NOT_FOUND", "Debate not found.")
    # Authentication may have loaded this user before waiting for the debate
    # lock. Serialize against account suspension/deletion and recheck DB state.
    account = User.objects.select_for_update().only("id", "status").filter(pk=user.pk).first()
    if account is None or account.status != User.Status.ACTIVE:
        raise ContractError(403, "FORBIDDEN", "This account cannot change stance.")

    participant = DebateParticipant.objects.select_for_update().filter(
        debate=debate, user=user,
    ).first()
    if participant is None:
        raise ContractError(403, "NOT_PARTICIPANT", "Only participants can change stance.")

    scope = {
        "user": user,
        "http_method": "PATCH",
        "request_path": request_path,
        "idempotency_key": idempotency_key,
    }
    request_hash = hashlib.sha256(
        json.dumps([stance, opinion_id, version]).encode(),
    ).hexdigest()
    saved = APIIdempotencyRecord.objects.filter(
        **scope, expires_at__gt=timezone.now(),
    ).first()
    if saved is not None:
        if saved.request_hash != request_hash:
            raise StanceConflict("IDEMPOTENCY_KEY_REUSED", "This key was used with a different request.")
        if saved.status != APIIdempotencyRecord.Status.COMPLETED:
            raise StanceConflict("REQUEST_PROCESSING", "The original request is still processing.")
        if not isinstance(saved.response_body, dict) or "data" not in saved.response_body:
            # Legacy responses have no event ID. Keep the key occupied without replaying or mutating.
            raise StanceConflict("IDEMPOTENCY_KEY_REUSED", "This key belongs to an earlier API response format.")
        return saved.response_body, saved.response_status

    require_open_debate(debate)
    require_current_version(participant, version)
    if participant.current_stance == stance:
        raise ContractError(400, "VALIDATION_ERROR", "Choose a different stance.")

    opinion = eligible_opinion(debate, participant, opinion_id)
    require_before_deadline(debate)

    previous = participant.current_stance
    persuasion = None
    if opinion is not None:
        persuasion = Persuasion.objects.create(
            opinion=opinion, persuaded_user=user, from_stance=previous, to_stance=stance,
        )
    event = StanceHistory.objects.create(
        participant=participant, from_stance=previous, to_stance=stance,
        persuasion=persuasion,
    )
    participant.current_stance = stance
    participant.version += 1
    participant.save(update_fields=["current_stance", "version"])
    response = {"data": {
        "currentStance": participant.current_stance,
        "eventId": str(event.pk),
        "persuasionId": str(persuasion.pk) if persuasion is not None else None,
        "reward": None,
    }}
    APIIdempotencyRecord.objects.update_or_create(
        **scope,
        defaults={
            "request_hash": request_hash,
            "status": APIIdempotencyRecord.Status.COMPLETED,
            "response_status": 200,
            "response_body": response,
            "expires_at": timezone.now() + timedelta(hours=24),
        },
    )
    return response, 200
