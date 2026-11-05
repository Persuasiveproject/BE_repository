from urllib.parse import parse_qs, urlsplit

from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.functional import cached_property
from rest_framework.generics import ListAPIView
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Debate, DebateParticipant
from .serializers import JoinedDebateSerializer


class DebateCursorPagination(CursorPagination):
    page_size = 20
    ordering = "-id"

    def get_paginated_response(self, data):
        next_link = self.get_next_link()
        cursor = parse_qs(urlsplit(next_link).query).get(self.cursor_query_param, [None])[0] if next_link else None
        return Response({"data": {"debates": data}, "meta": {"nextCursor": cursor}})


class JoinedDebateListView(ContractResponseMixin, ListAPIView):
    visible_statuses = (Debate.Status.OPEN, Debate.Status.CLOSED)
    serializer_class = JoinedDebateSerializer
    permission_classes = [IsActiveMember]
    pagination_class = DebateCursorPagination

    @cached_property
    def as_of(self):
        return timezone.now()

    def get_queryset(self):
        status = self.request.query_params.get("status")
        if status is not None and status not in self.visible_statuses:
            raise ContractError(400, "FILTER_INVALID", "Use OPEN or CLOSED.")
        debates = Debate.objects.with_deadline()
        visible = debates.filter(status__in=self.visible_statuses)
        if status == Debate.Status.OPEN:
            visible = visible.filter(status=Debate.Status.OPEN).filter(
                Q(deadline__isnull=True) | Q(deadline__gt=self.as_of),
            )
        if status == Debate.Status.CLOSED:
            visible = visible.filter(Q(status=Debate.Status.CLOSED) | Q(deadline__lte=self.as_of))
        return DebateParticipant.objects.filter(
            user=self.request.user, debate__in=visible,
        ).prefetch_related(Prefetch("debate", queryset=debates))

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "as_of": self.as_of}
