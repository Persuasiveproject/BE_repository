from django.db import transaction
from accounts.models import User
from discussions.models import Persuasion

from .api_contract import ContractError
from .idempotency import request_hash, require_valid_key, save_response, saved_response
from .models import Debate, DebateParticipant, StanceHistory
from .stance_checks import (  # StanceConflict is re-exported for callers.
    StanceConflict, eligible_opinion, require_before_deadline, require_current_version,
    require_open_debate,
)


@transaction.atomic
def change_stance(
    *, debate_id, user, stance, idempotency_key, request_path, opinion_id=None, version=None,
):
    require_valid_key(idempotency_key)

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
    body_hash = request_hash([stance, opinion_id, version])
    saved = saved_response(scope, body_hash)
    if saved is not None:
        return saved

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
    save_response(scope, body_hash, response, 200)
    return response, 200
