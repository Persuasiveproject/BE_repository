from collections.abc import Mapping

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin
from debates.cancel import cancel_debate


class DebateCancelView(ContractResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, debate_id):
        if not request.user.is_staff:
            raise ContractError(403, "ADMIN_REQUIRED", "Only an active administrator can cancel a debate.")

        data = request.data
        if not isinstance(data, Mapping) or set(data) - {"refund", "cancelReason"}:
            raise ContractError(400, "VALIDATION_ERROR", "Check the request fields.")
        refund = data.get("refund")
        reason = data.get("cancelReason")
        if not isinstance(refund, bool):
            raise ContractError(400, "VALIDATION_ERROR", "Refund must be a boolean.")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 255 or "\x00" in reason):
            raise ContractError(400, "VALIDATION_ERROR", "Use a cancellation reason of up to 255 characters.")

        return Response(cancel_debate(debate_id=debate_id, operator=request.user, refund=refund, reason=reason))
