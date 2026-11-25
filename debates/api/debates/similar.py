import re
from functools import reduce
from operator import add, or_

from django.db.models import Case, Q, Value, When
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import UserIdentityVerification
from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.listing import debate_list_item, filter_visible
from debates.models import Debate


class SimilarDebateListView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def get(self, request):
        if not UserIdentityVerification.objects.filter(
            user=request.user, status=UserIdentityVerification.Status.VERIFIED,
        ).exists():
            raise ContractError(403, "FORBIDDEN", "Identity verification is required.")

        title = request.query_params.get("title", "").strip()
        if len(title) > 100:
            raise ContractError(400, "TITLE_INVALID", "Enter a title of up to 100 characters.")
        keywords = {word.casefold(): word for word in re.findall(r"[^\W_]+", title)}
        if not keywords:
            raise ContractError(400, "TITLE_INVALID", "Enter a title containing a keyword.")

        matches = [Q(title__icontains=word) for word in keywords.values()]
        keyword_matches = reduce(add, (Case(When(match, then=Value(1)), default=Value(0)) for match in matches))
        as_of = timezone.now()
        debates = filter_visible(Debate.objects.with_deadline(), None, as_of)
        similar = (
            debates.filter(reduce(or_, matches))
            .annotate(
                exact_match=Case(When(title__iexact=title, then=Value(1)), default=Value(0)),
                keyword_matches=keyword_matches,
            )
            .order_by("-exact_match", "-keyword_matches", "-id")[:5]
        )
        return Response({"data": {"debates": [debate_list_item(debate, as_of) for debate in similar]}})
