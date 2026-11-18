from django.utils import timezone
from discussions.models import Opinion

from .api_contract import ContractError
from .models import Debate


class StanceConflict(ContractError):
    def __init__(self, code, message, *, details=None):
        super().__init__(409, code, message, details=details)


def eligible_opinion(debate, participant, opinion_id):
    opinion = None
    if opinion_id is not None:
        opinion = Opinion.objects.select_for_update(of=("self",)).filter(
            pk=opinion_id, debate=debate, participant__debate=debate,
            status=Opinion.Status.VISIBLE,
        ).exclude(participant=participant).first()
        if opinion is None:
            raise ContractError(
                400, "OPINION_DEBATE_MISMATCH", "Choose an eligible opinion in this debate.",
            )
    return opinion


def require_current_version(participant, version):
    if version is not None and participant.version != version:
        raise StanceConflict(
            "STANCE_CONFLICT", "Refresh the current stance before retrying.",
            details={"currentStance": participant.current_stance, "version": participant.version},
        )


def require_open_debate(debate):
    if debate.effective_status(timezone.now()) != Debate.Status.OPEN:
        raise StanceConflict("DEBATE_NOT_OPEN", "This debate is no longer open.")


def require_before_deadline(debate):
    if debate.deadline is not None and debate.deadline <= timezone.now():
        raise StanceConflict("DEBATE_NOT_OPEN", "This debate is no longer open.")
