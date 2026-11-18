from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin
from debates.models import Debate, Stance
from debates.serializers import utc
from discussions.ai.service import flat_source_ids
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
        source_ids = {
            int(i) for row in latest for i in flat_source_ids(row.source_opinion_ids) if str(i).isdigit()
        }
        roots = dict(Opinion.objects.filter(
            debate=debate, pk__in=source_ids, status=Opinion.Status.VISIBLE,
        ).values_list("id", "root_id"))

        def public(ids):
            return [int(i) for i in dict.fromkeys(str(i) for i in ids) if i.isdigit() and int(i) in roots]

        def items(row):
            lines = row.summary_text.splitlines()
            groups = row.source_opinion_ids or []
            if len(groups) != len(lines) or not all(isinstance(g, list) for g in groups):
                groups = [flat_source_ids(groups)] * len(lines)
            return [{
                "text": line.removeprefix("- "),
                "sources": [
                    {"postId": str(i), "rootPostId": str(roots[i]) if roots[i] else None} for i in public(ids)
                ],
            } for line, ids in zip(lines, groups)]

        return Response({"data": {"summaries": [{
            "stance": row.stance,
            "summaryText": row.summary_text,
            "sourcePostIds": [str(i) for i in public(flat_source_ids(row.source_opinion_ids))],
            "items": items(row),
            "createdAt": utc(row.created_at),
        } for row in latest]}})
