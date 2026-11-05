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
