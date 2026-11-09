from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User, UserIdentityVerification
from debates.api.writes import IdempotentWrite, lock_debate_for_write
from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Debate, DebateParticipant
from debates.stance_checks import require_before_deadline, require_open_debate

from .serializers import JoinDebateInput, participation_data


class JoinDebateView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    @transaction.atomic
    def post(self, request, debate_id):
        payload = JoinDebateInput(data=request.data)
        payload.is_valid(raise_exception=True)
        write = IdempotentWrite(request, payload.validated_data)
        debate = lock_debate_for_write(debate_id, request.user.pk)
        if (saved := write.replay()) is not None:
            return Response(saved[0], status=saved[1])

        verified = UserIdentityVerification.objects.select_for_update().filter(
            user=request.user, status=UserIdentityVerification.Status.VERIFIED,
        ).exists()
        if not verified:
            raise ContractError(403, "IDENTITY_VERIFICATION_REQUIRED", "Verify your identity before joining.")
        require_open_debate(debate)
        participants = DebateParticipant.objects.filter(debate=debate)
        if participants.filter(user=request.user).exists():
            raise ContractError(409, "ALREADY_PARTICIPATING", "You already joined this debate.")

        last_number = participants.order_by("-anonymous_no").values_list("anonymous_no", flat=True).first()
        require_before_deadline(debate)
        stance = payload.validated_data["stance"]
        participant = participants.create(
            debate=debate, user=request.user, anonymous_no=(last_number or 0) + 1,
            initial_stance=stance, current_stance=stance,
        )
        Debate.objects.filter(pk=debate.pk).update(participant_count=F("participant_count") + 1)
        response = {"data": participation_data(participant)}
        write.save(response, 201)
        return Response(response, status=201)
