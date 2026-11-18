import re

from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin
from debates.models import Debate
from discussions.models import Opinion, OpinionReference

from .models import Report


ID_PATTERN = re.compile(r"[1-9][0-9]{0,18}")
PUBLIC_DEBATE_STATES = (Debate.Status.OPEN, Debate.Status.CLOSED)
TARGETS = {
    Report.TargetType.OPINION: (
        "opinion",
        lambda: Opinion.objects.filter(debate__status__in=PUBLIC_DEBATE_STATES)
        .exclude(status=Opinion.Status.DELETED),
    ),
    Report.TargetType.DEBATE: (
        "debate",
        lambda: Debate.objects.filter(status__in=PUBLIC_DEBATE_STATES),
    ),
    Report.TargetType.REFERENCE: (
        "reference",
        lambda: OpinionReference.objects.filter(opinion__debate__status__in=PUBLIC_DEBATE_STATES)
        .exclude(opinion__status=Opinion.Status.DELETED),
    ),
}


def report_input(data):
    invalid = ContractError(400, "REPORT_INVALID", "Check the report fields.")
    if not isinstance(data, dict):
        raise invalid
    target_type, target_id, reason = data.get("targetType"), data.get("targetId"), data.get("reason")
    if (
        not isinstance(target_type, str) or target_type not in TARGETS
        or not isinstance(target_id, str) or not ID_PATTERN.fullmatch(target_id) or int(target_id) > MAX_ID
        or not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255
    ):
        raise invalid
    return target_type, int(target_id), reason.strip()


class ReportCreateView(ContractResponseMixin, APIView):
    # Suspended members can still report.
    permission_classes = [IsAuthenticated]

    def post(self, request):
        target_type, target_id, reason = report_input(request.data)
        field, targets = TARGETS[target_type]
        target = targets().filter(pk=target_id).first()
        if target is None:
            raise ContractError(404, "REPORT_TARGET_NOT_FOUND", "Report target not found.")

        with transaction.atomic():
            # Serialize the same member's reports; there is no unique constraint to rely on.
            User.objects.select_for_update().filter(pk=request.user.pk).exists()
            if Report.objects.filter(reporter=request.user, target_type=target_type, **{field: target}).exists():
                raise ContractError(409, "ALREADY_REPORTED", "You already reported this.")
            report = Report.objects.create(
                reporter=request.user, target_type=target_type, reason=reason, **{field: target},
            )
        return Response({"data": {"reportId": str(report.pk), "status": report.status}}, status=201)
