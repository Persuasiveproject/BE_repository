from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import UserInterestCategory
from debates.api_contract import ContractResponseMixin
from debates.listing import debate_list_item, filter_visible
from debates.models import Debate


HOME_SIZE = 10


class HomeView(ContractResponseMixin, APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        as_of = timezone.now()
        open_debates = filter_visible(Debate.objects.with_deadline(), Debate.Status.OPEN, as_of)

        popular = open_debates.annotate(
            recent_joins=Count(
                "debateparticipant",
                filter=Q(debateparticipant__joined_at__gt=as_of - timedelta(hours=24)),
            ),
        ).order_by("-recent_joins", "-id")[:HOME_SIZE]

        recommended = []
        if request.user.is_authenticated:
            interests = UserInterestCategory.objects.filter(user=request.user).values("category_id")
            recommended = list(open_debates.filter(category_id__in=interests).order_by("-id")[:HOME_SIZE])
            open_debates = open_debates.exclude(category_id__in=interests)
        recommended += open_debates.order_by("-id")[:HOME_SIZE - len(recommended)]

        return Response({"data": {
            "popularDebates": [debate_list_item(debate, as_of) for debate in popular],
            "recommendedDebates": [debate_list_item(debate, as_of) for debate in recommended],
        }})
