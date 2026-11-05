from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractResponseMixin, IsActiveMember
from debates.models import Debate, DebateParticipant, Stance
from .serializers import StanceStateSerializer


class MyStanceView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def get(self, request, debate_id):
        participant = get_object_or_404(
            DebateParticipant.objects.exclude(debate__status=Debate.Status.DRAFT).prefetch_related(
                Prefetch("debate", queryset=Debate.objects.with_deadline()),
            ),
            debate_id=debate_id, user=request.user,
        )
        can_change = participant.debate.effective_status(timezone.now()) == Debate.Status.OPEN
        return Response({
            **StanceStateSerializer(participant).data,
            "selectableStances": [
                stance for stance in Stance.values if stance != participant.current_stance
            ] if can_change else [],
        })
