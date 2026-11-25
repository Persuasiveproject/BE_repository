from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin
from debates.models import Debate, Stance
from debates.serializers import utc
from discussions.ai.service import flat_source_ids, summary_status
from discussions.models import AIIssueMap, AISummary, Opinion


STATUS_MESSAGES = {
    "INSUFFICIENT": "AI 요약을 만들려면 공개 글이 5개 이상 필요해요.",
    "PENDING": "AI 요약을 준비하고 있어요.",
    "GENERATING": "AI가 토론 내용을 요약하고 있어요.",
    "UPDATING": "최신 토론 내용을 반영해 AI 요약을 갱신하고 있어요.",
    "READY": "AI 요약이 준비됐어요.",
    "FAILED": "AI 요약을 만들지 못했어요. 잠시 후 다시 확인해 주세요.",
}


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

        summaries = []
        neutral = None
        for row in latest:
            ids = flat_source_ids(row.source_opinion_ids)
            if not ids or any(not str(i).isdigit() or int(i) not in roots for i in ids):
                continue
            item = {
                "stance": row.stance,
                "summaryText": row.summary_text,
                "sourcePostIds": [str(i) for i in public(ids)],
                "items": items(row),
                "createdAt": utc(row.created_at),
            }
            if row.stance == Stance.NEUTRAL:
                neutral = item
            else:
                summaries.append(item)
        status = summary_status(debate.pk, bool(summaries or neutral))
        return Response({"data": {
            "summaries": summaries,
            "neutralSummary": neutral,
            "status": status,
            "statusMessage": STATUS_MESSAGES[status],
        }})


class DebateIssuesView(ContractResponseMixin, APIView):
    """GET /v1/debates/:debateId/issues (비회원). 끝장토론의 최신 쟁점 정리.

    근거 글이 숨김·삭제되면 그 근거를 빼고, 근거가 하나도 남지 않은 쪽은 null로 내린다.
    awaiting: 한쪽만 이야기한 쟁점에서 아직 답하지 않은 쪽(PRO/CON). 양쪽 다 있으면 null.
    정리가 없으면 issues는 빈 배열이다.
    """

    permission_classes = [AllowAny]

    def get(self, request, debate_id):
        debate = Debate.objects.filter(pk=debate_id).only("id", "status").first()
        if debate is None or debate.status in (Debate.Status.DRAFT, Debate.Status.CANCELED):
            raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")
        row = AIIssueMap.objects.filter(debate=debate).order_by("-created_at", "-id").first()
        if row is None:
            return Response({"data": {"issues": [], "createdAt": None}})

        ids = {
            int(i) for issue in row.issues for key in ("pro", "con")
            for i in ((issue.get(key) or {}).get("sourceIds") or []) if str(i).isdigit()
        }
        roots = dict(Opinion.objects.filter(
            debate=debate, pk__in=ids, status=Opinion.Status.VISIBLE,
        ).values_list("id", "root_id"))

        def side(entry):
            if not entry:
                return None
            sources = [
                {"postId": str(i), "rootPostId": str(roots[i]) if roots[i] else None}
                for i in dict.fromkeys(int(x) for x in entry.get("sourceIds") or [] if str(x).isdigit())
                if i in roots
            ]
            return {"text": entry["text"], "sources": sources} if sources else None

        issues = []
        for issue in row.issues:
            pro, con = side(issue.get("pro")), side(issue.get("con"))
            if not pro and not con:
                continue
            awaiting = None if pro and con else (Stance.CON if pro else Stance.PRO)
            issues.append({"title": issue["title"], "pro": pro, "con": con, "awaiting": awaiting})
        return Response({"data": {"issues": issues, "createdAt": utc(row.created_at)}})
