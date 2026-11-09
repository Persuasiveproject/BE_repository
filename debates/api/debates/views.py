import re

from django.utils import timezone
from django.utils.functional import cached_property
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny

from debates.api.joined.views import DebateCursorPagination
from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin
from debates.listing import filter_visible
from debates.models import Category, Debate
from .serializers import DebateListSerializer


ID_PATTERN = re.compile(r"[1-9][0-9]{0,18}")


class PublicDebateCursorPagination(DebateCursorPagination):
    invalid_cursor_code = "CURSOR_INVALID"


def filter_invalid(message):
    return ContractError(400, "FILTER_INVALID", message)


class DebateListView(ContractResponseMixin, ListAPIView):
    serializer_class = DebateListSerializer
    permission_classes = [AllowAny]
    pagination_class = PublicDebateCursorPagination

    @cached_property
    def as_of(self):
        return timezone.now()

    def get_queryset(self):
        params = self.request.query_params
                # Latest only until the popularity score is defined.
        if params.get("sort", "latest") != "latest":
            raise filter_invalid("Use latest.")
        debates = filter_visible(Debate.objects.with_deadline(), params.get("status"), self.as_of)

        kind = params.get("kind")
        if kind is not None:
            if kind not in Debate.Type.values:
                raise filter_invalid("Use ENDLESS or QUICK.")
            debates = debates.filter(type=kind)

        category_id = params.get("categoryId")
        if category_id is not None:
            if (
                not ID_PATTERN.fullmatch(category_id) or int(category_id) > MAX_ID
                or not Category.objects.filter(pk=category_id).exists()
            ):
                raise filter_invalid("Use an existing category ID.")
            debates = debates.filter(category_id=category_id)

        query = params.get("query", "").strip()
        if len(query) > 100:
            raise filter_invalid("Use a query of up to 100 characters.")
        if query:
            debates = debates.filter(title__icontains=query)
        return debates

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "as_of": self.as_of}
