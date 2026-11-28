import re

from django.core import signing
from django.db.models import Q
from django.utils import timezone
from django.utils.functional import cached_property
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param

from debates.api.draft.serializers import DraftInput
from debates.api.draft.views import active_category, validated_draft
from debates.api.joined.views import DebateCursorPagination
from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin, IsActiveMember
from debates.listing import POPULAR_ORDERING, filter_visible
from debates.models import Category, Debate
from debates.serializers import utc
from .serializers import DebateListSerializer


ID_PATTERN = re.compile(r"[1-9][0-9]{0,18}")


class PublicDebateCursorPagination(DebateCursorPagination):
    invalid_cursor_code = "CURSOR_INVALID"


class PopularDebateCursorPagination(PublicDebateCursorPagination):
    cursor_salt = "debates.public-list.popular"

    def paginate_queryset(self, queryset, request, view=None):
        self.base_url = request.build_absolute_uri()
        cursor = request.query_params.get(self.cursor_query_param)
        if cursor is not None:
            try:
                participant_count, debate_id = signing.loads(cursor, salt=self.cursor_salt)
            except (signing.BadSignature, TypeError, ValueError) as exc:
                raise ContractError(400, self.invalid_cursor_code, "Use a valid cursor.") from exc
            queryset = queryset.filter(
                Q(participant_count__lt=participant_count)
                | Q(participant_count=participant_count, pk__lt=debate_id),
            )
        page = list(queryset.order_by(*POPULAR_ORDERING)[:self.page_size + 1])
        self.has_next = len(page) > self.page_size
        self.page = page[:self.page_size]
        return self.page

    def get_next_link(self):
        if not self.has_next:
            return None
        last = self.page[-1]
        cursor = signing.dumps((last.participant_count, last.pk), salt=self.cursor_salt)
        return replace_query_param(self.base_url, self.cursor_query_param, cursor)


def filter_invalid(message):
    return ContractError(400, "FILTER_INVALID", message)


class DebateListView(ContractResponseMixin, ListAPIView):
    serializer_class = DebateListSerializer

    @property
    def pagination_class(self):
        if self.request.query_params.get("sort") == "popular":
            return PopularDebateCursorPagination
        return PublicDebateCursorPagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsActiveMember()]
        return [AllowAny()]

    def post(self, request):
        data = validated_draft(DraftInput(data=request.data))
        category = active_category(data["categoryId"])
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

    @cached_property
    def as_of(self):
        return timezone.now()

    def get_queryset(self):
        params = self.request.query_params
        if params.get("sort", "latest") not in ("latest", "popular"):
            raise filter_invalid("Use latest or popular.")
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
            debates = debates.filter(
                Q(title__icontains=query) | Q(content__icontains=query),
            )
        return debates

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "as_of": self.as_of}
