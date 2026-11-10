from datetime import datetime

from django.core import signing
from django.db.models import Q
from rest_framework.response import Response

from debates.api_contract import ContractError
from debates.serializers import utc

from .serializers import opinion_data


PAGE_SIZE = 20


def post_page(request, queryset, response_key, *, stance=None, score_field=None):
    salt = f"debates.post-page:{request.path_info}:{stance or ''}"
    ordering = ("-created_at", "-id")
    if score_field is not None:
        salt = f"{salt}:{score_field}"
        ordering = (f"-{score_field}", *ordering)

    cursor = request.query_params.get("cursor")
    if cursor is not None:
        try:
            position = signing.loads(cursor, salt=salt)
            if score_field is None:
                created_at, post_id = position
            else:
                created_at, post_id, score = position
            timestamp = datetime.fromisoformat(created_at)
        except (signing.BadSignature, TypeError, ValueError) as exc:
            raise ContractError(400, "CURSOR_INVALID", "Use a valid cursor.") from exc
        after = Q(created_at__lt=timestamp) | Q(created_at=timestamp, pk__lt=post_id)
        if score_field is not None:
            after = Q(**{f"{score_field}__lt": score}) | (Q(**{score_field: score}) & after)
        queryset = queryset.filter(after)

    posts = list(queryset.order_by(*ordering)[:PAGE_SIZE + 1])
    next_cursor = None
    if len(posts) > PAGE_SIZE:
        posts.pop()
        last = posts[-1]
        position = [utc(last.created_at), last.pk]
        if score_field is not None:
            position.append(getattr(last, score_field))
        next_cursor = signing.dumps(position, salt=salt)
    return Response({
        "data": {response_key: [opinion_data(post) for post in posts]},
        "meta": {"nextCursor": next_cursor},
    })
