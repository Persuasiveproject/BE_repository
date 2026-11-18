from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Debate, DebateParticipant, StanceHistory
from .serializers import StanceChangeInput, StanceEventSerializer
from debates.stance import change_stance


class MyStanceView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def patch(self, request, debate_id):
        payload = StanceChangeInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        body, status = change_stance(
            debate_id=debate_id, user=request.user, stance=data["stance"],
            opinion_id=data.get("opinionId"), version=data.get("version"),
            idempotency_key=request.headers.get("Idempotency-Key"),
            request_path=request.path_info,
        )
        return Response(body, status=status)


class MyStanceEventListView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def get(self, request, debate_id):
        debate = get_object_or_404(
            Debate.objects.exclude(status=Debate.Status.DRAFT), pk=debate_id,
        )
        participant = DebateParticipant.objects.filter(
            debate=debate, user=request.user,
        ).first()
        if participant is None:
            raise ContractError(403, "NOT_PARTICIPANT", "Only participants can read their stance history.")
        events = StanceHistory.objects.filter(participant=participant).order_by("-changed_at", "-id")
        return Response({"data": {"events": StanceEventSerializer(events, many=True).data}})
