from datetime import datetime

from django.core import signing
from django.db.models import Q
from rest_framework.response import Response

from debates.api_contract import ContractError
from debates.serializers import utc

from .serializers import opinion_data


PAGE_SIZE = 20


def post_page(request, queryset, response_key, *, stance=None):
    salt = f"debates.post-page:{request.path_info}:{stance or ''}"
    cursor = request.query_params.get("cursor")
    if cursor is not None:
        try:
            created_at, post_id = signing.loads(cursor, salt=salt)
            timestamp = datetime.fromisoformat(created_at)
        except (signing.BadSignature, TypeError, ValueError) as exc:
            raise ContractError(400, "CURSOR_INVALID", "Use a valid cursor.") from exc
        queryset = queryset.filter(Q(created_at__lt=timestamp) | Q(created_at=timestamp, pk__lt=post_id))

    posts = list(queryset.order_by("-created_at", "-id")[:PAGE_SIZE + 1])
    next_cursor = None
    if len(posts) > PAGE_SIZE:
        posts.pop()
        last = posts[-1]
        next_cursor = signing.dumps([utc(last.created_at), last.pk], salt=salt)
    return Response({
        "data": {response_key: [opinion_data(post) for post in posts]},
        "meta": {"nextCursor": next_cursor},
    })
