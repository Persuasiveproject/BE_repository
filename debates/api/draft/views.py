from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Category, Debate
from debates.serializers import utc
from .serializers import DraftInput


class DebateDraftCreateView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def post(self, request):
        payload = DraftInput(data=request.data)
        if not payload.is_valid():
            raise ContractError(400, "DRAFT_INVALID", "Check the draft fields.", details=payload.errors)
        data = payload.validated_data
        category = Category.objects.filter(pk=data["categoryId"], is_active=True).first()
        if category is None:
            raise ContractError(404, "CATEGORY_NOT_FOUND", "The category was not found.")
        debate = Debate.objects.create(
            creator=request.user, category=category, type=data["kind"],
            title=data["title"], content=data["content"],
            duration_minutes=data.get("durationMinutes"),
        )
        return Response({"data": {
            "debateId": str(debate.pk),
            "status": debate.status,
            "createdAt": utc(debate.created_at),
        }}, status=201)
