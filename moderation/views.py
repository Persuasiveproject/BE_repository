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


def create_report(user, target_type, target, reason):
    field = TARGETS[target_type][0]
    with transaction.atomic():
        # Serialize the same member's reports; there is no unique constraint to rely on.
        User.objects.select_for_update().filter(pk=user.pk).exists()
        if Report.objects.filter(reporter=user, target_type=target_type, **{field: target}).exists():
            raise ContractError(409, "ALREADY_REPORTED", "You already reported this.")
        return Report.objects.create(reporter=user, target_type=target_type, reason=reason, **{field: target})


def report_created(report):
    return Response({"data": {"reportId": str(report.pk), "status": report.status}}, status=201)


class ReportCreateView(ContractResponseMixin, APIView):
    # Suspended members can still report.
    permission_classes = [IsAuthenticated]

    def post(self, request):
        target_type, target_id, reason = report_input(request.data)
        target = TARGETS[target_type][1]().filter(pk=target_id).first()
        if target is None:
            raise ContractError(404, "REPORT_TARGET_NOT_FOUND", "Report target not found.")
        return report_created(create_report(request.user, target_type, target, reason))


class ReferenceChallengeView(ContractResponseMixin, APIView):
    # Suspended members can still report.
    permission_classes = [IsAuthenticated]

    def post(self, request, reference_id):
        if not 1 <= reference_id <= MAX_ID:
            raise ContractError(400, "VALIDATION_ERROR", "Use a valid reference ID.")
        reference = TARGETS[Report.TargetType.REFERENCE][1]().filter(pk=reference_id).first()
        if reference is None:
            raise ContractError(404, "REFERENCE_NOT_FOUND", "Reference not found.")
        reason = request.data.get("reason") if isinstance(request.data, dict) else None
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
            raise ContractError(400, "REASON_INVALID", "Enter a reason of 1 to 255 characters.")
        return report_created(create_report(request.user, Report.TargetType.REFERENCE, reference, reason.strip()))
