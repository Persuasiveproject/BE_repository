from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractResponseMixin
from debates.models import Category


class CategoryListView(ContractResponseMixin, APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        categories = Category.objects.filter(is_active=True).order_by("sort_order", "id")
        return Response({"data": {"categories": [
            {"id": str(c.pk), "name": c.name, "sortOrder": c.sort_order}
            for c in categories
        ]}})
