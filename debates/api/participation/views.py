from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User, UserIdentityVerification
from debates.api.writes import IdempotentWrite, lock_debate_for_write
from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Debate, DebateImage, DebateParticipant
from debates.stance_checks import require_before_deadline, require_open_debate

from .serializers import JoinDebateInput, debate_detail_data, image_data, participation_data


class DebateDetailView(ContractResponseMixin, APIView):
    permission_classes = [AllowAny]

    def get(self, request, debate_id):
        debate = Debate.objects.with_deadline().select_related("category").filter(pk=debate_id).first()
        if debate is None or debate.status == Debate.Status.CANCELED:
            raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")

        active_member = request.user.is_authenticated and request.user.status == User.Status.ACTIVE
        draft = debate.status == Debate.Status.DRAFT
        if draft and (not active_member or debate.creator_id != request.user.pk):
            raise ContractError(403, "DRAFT_NOT_ACCESSIBLE", "Only the creator can view this draft.")

        images = DebateImage.objects.filter(debate=debate).order_by("sort_order", "id")
        if not draft:
            images = images.filter(status=DebateImage.Status.READY)
        participation = None
        if active_member and not draft:
            participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()
        return Response({"data": {
            "debate": debate_detail_data(debate, debate.effective_status(timezone.now())),
            "images": [image_data(image, draft) for image in images],
            "participantCount": debate.participant_count,
            "myParticipation": participation_data(participation) if participation is not None else None,
        }})


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
