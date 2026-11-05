from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin
from debates.models import Debate, DebateStatistics, Stance
from debates.results import MIN_TRANSITION_PARTICIPANTS
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
        result = None
        if debate.status == Debate.Status.CLOSED:
            result = DebateStatistics.objects.filter(debate=debate).first()
        if result is None:
            failed = debate.result_retry_at is not None
            raise ContractError(
                409, "RESULT_NOT_READY",
                "Check again shortly." if failed else "The result is being computed.",
                details={
                    "status": "FAILED" if failed else "PENDING",
                    "title": header["title"],
                    "closedAt": header["closedAt"],
                },
            )

        total = result.total_participants
        show_transitions = total >= MIN_TRANSITION_PARTICIPANTS
        final_counts = {
            Stance.PRO: result.final_pro,
            Stance.CON: result.final_con,
            Stance.NEUTRAL: result.final_neutral,
        }
        return Response({"data": {
            **header,
            "finalStancePercentages": {
                stance: round(count * 100 / total, 1) if total else 0.0
                for stance, count in final_counts.items()
            },
            "counts": {
                "totalParticipants": total,
                "opinionCount": result.opinion_count,
                "rebuttalCount": result.rebuttal_count,
                "finalPro": result.final_pro,
                "finalCon": result.final_con,
                "finalNeutral": result.final_neutral,
                "stanceChangedCount": result.stance_changed_count if show_transitions else None,
                "stanceKeptCount": result.stance_kept_count if show_transitions else None,
            },
            "sankeyData": result.sankey_data if show_transitions else [],
            # Keep demographics private until aggregation and withdrawal rules are settled.
            "genderData": None,
            "ageData": None,
            "regionData": None,
            "genderHidden": not show_transitions or result.gender_hidden,
            "ageHidden": not show_transitions or result.age_hidden,
            "computedAt": utc(result.computed_at),
        }})
