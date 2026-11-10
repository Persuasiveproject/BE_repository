from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin
from debates.models import Debate, Stance
from debates.serializers import utc
from discussions.models import AISummary, Opinion


class DebateSummaryView(ContractResponseMixin, APIView):
    """API 43: GET /v1/debates/:debateId/summary (비회원).

    PRO/CON별 최신 요약을 반환한다. 근거 ID는 같은 토론의 공개 의견만 남긴다.
    요약이 없으면 summaries를 빈 배열로 반환한다. NEUTRAL 요약은 추가하지 않는다.
    """

    permission_classes = [AllowAny]

    def get(self, request, debate_id):
        debate = Debate.objects.filter(pk=debate_id).only("id", "status").first()
        if debate is None or debate.status in (Debate.Status.DRAFT, Debate.Status.CANCELED):
            raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")

        latest = []
        for stance in (Stance.PRO, Stance.CON):
            row = AISummary.objects.filter(debate=debate, stance=stance).order_by("-created_at", "-id").first()
            if row is not None:
                latest.append(row)
        source_ids = {int(i) for row in latest for i in (row.source_opinion_ids or []) if str(i).isdigit()}
        visible = set(Opinion.objects.filter(
            debate=debate, pk__in=source_ids, status=Opinion.Status.VISIBLE,
        ).values_list("id", flat=True))
        return Response({"data": {"summaries": [{
            "stance": row.stance,
            "summaryText": row.summary_text,
            "sourcePostIds": [str(i) for i in row.source_opinion_ids or [] if str(i).isdigit() and int(i) in visible],
            "createdAt": utc(row.created_at),
        } for row in latest]}})
