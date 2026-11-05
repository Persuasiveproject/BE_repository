from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin
from debates.models import Debate
from debates.serializers import utc


class DebateResultView(ContractResponseMixin, APIView):
    permission_classes = [AllowAny]

    def get(self, request, debate_id):
        debate = get_object_or_404(
            Debate.objects.with_deadline().exclude(status=Debate.Status.DRAFT),
            pk=debate_id,
        )
        now = timezone.now()
        if debate.effective_status(now) != Debate.Status.CLOSED:
            raise ContractError(409, "RESULT_NOT_READY", "The result is unavailable for this debate.")
        header = {
            "title": debate.title,
            "status": Debate.Status.CLOSED,
            "closedAt": utc(debate.closed_at if debate.status == Debate.Status.CLOSED else debate.deadline),
        }
        return Response({"data": header})
