from datetime import datetime

from django.core import signing
from django.db.models import F, Q, Subquery
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api.posts.pagination import PAGE_SIZE
from debates.api.posts.serializers import opinion_data
from debates.api.posts.views import PUBLIC_DEBATE_STATES, opinion_cards, visible_opinions
from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin, IsActiveMember
from debates.serializers import utc
from discussions.models import Opinion

from .models import Scrap


def bookmark_page(request, scraps):
    salt = f"engagement.bookmarks:{request.user.pk}"
    cursor = request.query_params.get("cursor")
    if cursor is not None:
        try:
            created_at, scrap_id = signing.loads(cursor, salt=salt)
            timestamp = datetime.fromisoformat(created_at)
        except (signing.BadSignature, TypeError, ValueError) as error:
            raise ContractError(400, "CURSOR_INVALID", "Use a valid cursor.") from error
        scraps = scraps.filter(Q(created_at__lt=timestamp) | Q(created_at=timestamp, pk__lt=scrap_id))

    page = list(scraps.order_by("-created_at", "-id")[:PAGE_SIZE + 1])
    next_cursor = None
    if len(page) > PAGE_SIZE:
        page.pop()
        last = page[-1]
        next_cursor = signing.dumps([utc(last.created_at), last.pk], salt=salt)
    return page, next_cursor


def opinion_scraps(user):
    return Scrap.objects.filter(user=user, target_type=Scrap.TargetType.OPINION)


class PostBookmarkView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not 1 <= kwargs["post_id"] <= MAX_ID:
            raise ContractError(400, "VALIDATION_ERROR", "Use a valid post ID.")

    def put(self, request, post_id):
        if not visible_opinions().filter(pk=post_id, debate__status__in=PUBLIC_DEBATE_STATES).exists():
            raise ContractError(404, "POST_NOT_FOUND", "Post not found.")
        Scrap.objects.get_or_create(
            user=request.user, target_type=Scrap.TargetType.OPINION, target_id=post_id,
        )
        return Response({"data": {"bookmarked": True}})

    def delete(self, request, post_id):
        opinion_scraps(request.user).filter(target_id=post_id).delete()
        return Response(status=204)


def bookmark_data(post):
    data = opinion_data(post)
    # OpinionItem in API #37 does not include the public feed's badge extension.
    data.pop("badge")
    if post.status != Opinion.Status.VISIBLE:
        data["content"] = "삭제된 글입니다"
        data["references"] = []
    return data


class BookmarkListView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def get(self, request):
        accessible = Opinion.objects.filter(
            debate__status__in=PUBLIC_DEBATE_STATES, participant__debate_id=F("debate_id"),
        )
        scraps = opinion_scraps(request.user).filter(target_id__in=Subquery(accessible.values("pk")))
        page, next_cursor = bookmark_page(request, scraps)
        posts = opinion_cards(accessible.filter(pk__in=[scrap.target_id for scrap in page])).in_bulk()
        return Response({
            "data": {"posts": [
                bookmark_data(posts[scrap.target_id]) for scrap in page if scrap.target_id in posts
            ]},
            "meta": {"nextCursor": next_cursor},
        })
