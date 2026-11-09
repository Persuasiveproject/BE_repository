from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api.draft.serializers import DraftInput, draft_data
from debates.api.draft.views import active_category, validated_draft
from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Debate


class DebateDetailView(ContractResponseMixin, APIView):
    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsActiveMember()]
        return super().get_permissions()

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
