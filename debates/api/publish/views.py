from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractResponseMixin, IsActiveMember
from debates.publish import publish_debate


class DebatePublishView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def post(self, request, debate_id):
        body, status = publish_debate(
            debate_id=debate_id, user=request.user,
            idempotency_key=request.headers.get("Idempotency-Key"),
            request_path=request.path,
        )
        return Response(body, status=status)
