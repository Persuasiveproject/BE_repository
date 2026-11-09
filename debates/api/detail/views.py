from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from debates.api.draft.serializers import DraftInput, draft_data
from debates.api.draft.views import active_category, validated_draft
from debates.api.participation.serializers import debate_detail_data, image_data, participation_data
from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Debate, DebateImage, DebateParticipant


class DebateDetailView(ContractResponseMixin, APIView):
    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsActiveMember()]
        return [AllowAny()]

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

    @transaction.atomic
    def patch(self, request, debate_id):
        debate = get_object_or_404(Debate.objects.select_for_update(), pk=debate_id)
        if debate.creator_id != request.user.pk:
            raise ContractError(403, "NOT_DEBATE_CREATOR", "Only the creator can edit this draft.")
        if debate.status != Debate.Status.DRAFT:
            raise ContractError(409, "NOT_DRAFT", "Only drafts can be edited.")
        data = validated_draft(DraftInput(debate, data=request.data, partial=True))
        if "categoryId" in data:
            debate.category = active_category(data["categoryId"])
        fields = {"kind": "type", "title": "title", "content": "content", "durationMinutes": "duration_minutes"}
        for key, field in fields.items():
            if key in data:
                setattr(debate, field, data[key])
        if data:
            debate.save()
        return Response({"data": {"draft": draft_data(debate)}})
