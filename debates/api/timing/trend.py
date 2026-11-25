from datetime import timezone as utc_timezone

from django.db.models import Count
from django.db.models.functions import TruncHour, TruncMinute
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from debates.api_contract import ContractError, ContractResponseMixin
from debates.demographics import participants_for_result
from debates.listing import VISIBLE_STATUSES
from debates.models import Debate, DebateParticipant
from debates.serializers import utc


class DebateTrendView(ContractResponseMixin, APIView):
    permission_classes = [AllowAny]

    def get(self, request, debate_id):
        debate = Debate.objects.with_deadline().filter(pk=debate_id, status__in=VISIBLE_STATUSES).first()
        if debate is None:
            raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")

        now = timezone.now()
        end = min(at for at in (now, debate.closed_at, debate.deadline) if at is not None)
        if debate.effective_status(now) == Debate.Status.CLOSED:
            participants = participants_for_result(debate, closed_at=end)
        else:
            participants = DebateParticipant.objects.filter(debate=debate).exclude(user__status=User.Status.DELETED)
        truncate = TruncMinute if debate.type == Debate.Type.QUICK else TruncHour
        rows = participants.filter(joined_at__lte=end).values(
            bucket=truncate("joined_at", tzinfo=utc_timezone.utc),
        ).annotate(total=Count("id")).order_by("bucket")
        return Response({"data": {"points": [
            {"recordedAt": utc(row["bucket"]), "total": row["total"], "pro": None, "con": None, "neutral": None}
            for row in rows
        ]}})
