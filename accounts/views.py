from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractResponseMixin
from debates.serializers import utc
from economy.models import UserBadge

from .models import UserIdentityVerification, UserInterestCategory


class MeView(ContractResponseMixin, APIView):
    # Suspended members can still read their own profile.
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if response.status_code == 401:
            # API #6 defines AUTH_REQUIRED for profile authentication failures.
            response.data["error"]["code"] = "AUTH_REQUIRED"
        return response

    def get(self, request):
        user = request.user
        interests = (
            UserInterestCategory.objects.filter(user=user, category__is_active=True)
            .select_related("category").order_by("category__sort_order", "category_id")
        )
        badges = UserBadge.objects.filter(user=user).select_related("badge").order_by("acquired_at", "badge_id")
        return Response({"data": {
            "id": str(user.pk),
            "status": user.status,
            "identityVerified": UserIdentityVerification.objects.filter(
                user=user, status=UserIdentityVerification.Status.VERIFIED,
            ).exists(),
            "nickname": user.nickname,
            "interests": [
                {"id": str(i.category_id), "name": i.category.name, "sortOrder": i.category.sort_order}
                for i in interests
            ],
            "credit": format(user.credit, ".6f"),
            "badges": [
                {
                    "id": str(b.badge_id),
                    "name": b.badge.name,
                    "iconUrl": b.badge.icon_url,
                    "acquiredAt": utc(b.acquired_at),
                    "isRepresentative": b.is_representative,
                }
                for b in badges
            ],
        }})
