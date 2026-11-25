from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api.posts.views import PUBLIC_DEBATE_STATES, opinion_cards
from debates.api_contract import ContractResponseMixin
from discussions.models import Opinion

from .bookmarks import bookmark_data, created_page


class MyPostListView(ContractResponseMixin, APIView):
    # Restricted members can still list their own posts.
    permission_classes = [IsAuthenticated]

    def get(self, request):
        posts = Opinion.objects.filter(
            participant__user=request.user, debate__status__in=PUBLIC_DEBATE_STATES,
        ).exclude(status=Opinion.Status.DELETED)
        page, next_cursor = created_page(request, opinion_cards(posts), f"engagement.my_posts:{request.user.pk}")
        return Response({
            "data": {"posts": [bookmark_data(post) for post in page]},
            "meta": {"nextCursor": next_cursor},
        })
