from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin
from debates.models import Debate, Stance
from debates.serializers import utc
from discussions.ai.service import summary_status
from discussions.models import AISummary, Opinion


class DebateSummaryView(ContractResponseMixin, APIView):
    """API 43: GET /v1/debates/:debateId/summary (비회원).

    PRO/CON summaries remain separate from neutralSummary. A summary with
    missing, hidden, or unrelated sources is withheld until it is refreshed.
    """

    permission_classes = [AllowAny]

    def get(self, request, debate_id):
        debate = Debate.objects.filter(pk=debate_id).only("id", "status").first()
        if debate is None or debate.status in (Debate.Status.DRAFT, Debate.Status.CANCELED):
            raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")

        latest = []
        for stance in Stance.values:
            row = AISummary.objects.filter(debate=debate, stance=stance).order_by("-created_at", "-id").first()
            if row is not None:
                latest.append(row)
        source_ids = {int(i) for row in latest for i in (row.source_opinion_ids or []) if str(i).isdigit()}
        visible = set(Opinion.objects.filter(
            debate=debate, pk__in=source_ids, status=Opinion.Status.VISIBLE,
        ).values_list("id", flat=True))
        summaries = []
        neutral = None
        for row in latest:
            ids = row.source_opinion_ids or []
            if not ids or any(not str(i).isdigit() or int(i) not in visible for i in ids):
                continue
            item = {
                "stance": row.stance,
                "summaryText": row.summary_text,
                "sourcePostIds": [str(i) for i in ids],
                "createdAt": utc(row.created_at),
            }
            if row.stance == Stance.NEUTRAL:
                neutral = item
            else:
                summaries.append(item)
        return Response({"data": {
            "summaries": summaries,
            "neutralSummary": neutral,
            "status": summary_status(debate.pk, bool(summaries or neutral)),
        }})
