from base64 import b64decode
from urllib.parse import parse_qs, urlsplit

from django.db.models import Prefetch
from django.utils import timezone
from django.utils.functional import cached_property
from rest_framework.generics import ListAPIView
from rest_framework.pagination import Cursor, CursorPagination
from rest_framework.response import Response

from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin, IsActiveMember
from debates.listing import filter_visible
from debates.models import Debate, DebateParticipant
from .serializers import JoinedDebateSerializer


class DebateCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-id"
    invalid_cursor_code = "FILTER_INVALID"

    def decode_cursor(self, request):
        encoded = request.query_params.get(self.cursor_query_param)
        if encoded is None:
            return None
        try:
            decoded = b64decode(encoded, validate=True).decode("ascii")
            tokens = parse_qs(decoded, strict_parsing=True, keep_blank_values=True)
            if tokens.keys() != {"p"} or len(tokens["p"]) != 1:
                raise ValueError("Invalid cursor parameters.")
            position = tokens["p"][0]
            if (
                not position.isascii() or not position.isdecimal() or len(position) > 19
                or not 1 <= int(position) <= MAX_ID
            ):
                raise ValueError("Invalid cursor position.")
            return Cursor(offset=0, reverse=False, position=position)
        except ValueError as exc:
            raise ContractError(400, self.invalid_cursor_code, "Use a valid cursor.") from exc

    def get_paginated_response(self, data):
        next_link = self.get_next_link()
        cursor = parse_qs(urlsplit(next_link).query).get(self.cursor_query_param, [None])[0] if next_link else None
        return Response({"data": {"debates": data}, "meta": {"nextCursor": cursor}})


class JoinedDebateListView(ContractResponseMixin, ListAPIView):
    serializer_class = JoinedDebateSerializer
    permission_classes = [IsActiveMember]
    pagination_class = DebateCursorPagination

    @cached_property
    def as_of(self):
        return timezone.now()

    def get_queryset(self):
        debates = Debate.objects.with_deadline()
        visible = filter_visible(debates, self.request.query_params.get("status"), self.as_of)
        return DebateParticipant.objects.filter(
            user=self.request.user, debate__in=visible,
        ).prefetch_related(Prefetch("debate", queryset=debates))

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "as_of": self.as_of}
