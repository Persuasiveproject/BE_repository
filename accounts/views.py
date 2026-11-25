from collections.abc import Mapping
import re

from django.db import transaction
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api.joined.views import DebateCursorPagination
from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Category
from debates.serializers import utc
from economy.models import CreditHistory, UserBadge

from .models import User, UserIdentityVerification, UserInterestCategory


ID_PATTERN = re.compile(r"\A[1-9][0-9]{0,18}\Z")


def serialize_interests(user):
    interests = (
        UserInterestCategory.objects.filter(user=user, category__is_active=True)
        .select_related("category").order_by("category__sort_order", "category_id")
    )
    return [
        {"id": str(i.category_id), "name": i.category.name, "sortOrder": i.category.sort_order}
        for i in interests
    ]


def serialize_badges(user):
    badges = UserBadge.objects.filter(user=user).select_related("badge").order_by("acquired_at", "badge_id")
    return [
        {
            "id": str(b.badge_id),
            "name": b.badge.name,
            "iconUrl": b.badge.icon_url,
            "acquiredAt": utc(b.acquired_at),
            "isRepresentative": b.is_representative,
        }
        for b in badges
    ]


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
        return Response({"data": {
            "id": str(user.pk),
            "status": user.status,
            "identityVerified": UserIdentityVerification.objects.filter(
                user=user, status=UserIdentityVerification.Status.VERIFIED,
            ).exists(),
            "nickname": user.nickname,
            "interests": serialize_interests(user),
            "credit": format(user.credit, ".6f"),
            "badges": serialize_badges(user),
        }})


class MeCreditsView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def get(self, request):
        return Response({"data": {
            "credit": format(request.user.credit, ".6f"),
            "badges": serialize_badges(request.user),
        }})


class MeBadgesView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def get(self, request):
        return Response({"data": {
            "badges": serialize_badges(request.user),
        }})


class CreditHistoryCursorPagination(DebateCursorPagination):
    invalid_cursor_code = "CURSOR_INVALID"

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data = {
            "data": {"histories": data},
            "meta": response.data["meta"],
        }
        return response


class CreditHistorySerializer(serializers.BaseSerializer):
    def to_representation(self, history):
        return {
            "id": str(history.pk),
            "amount": format(history.amount, ".6f"),
            "reason": history.reason,
            "refId": str(history.ref_id) if history.ref_id is not None else None,
            "createdAt": utc(history.created_at),
        }


class MeCreditHistoryView(ContractResponseMixin, ListAPIView):
    permission_classes = [IsActiveMember]
    serializer_class = CreditHistorySerializer
    pagination_class = CreditHistoryCursorPagination

    def get_queryset(self):
        return CreditHistory.objects.filter(user=self.request.user)


class MeInterestsView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def put(self, request):
        if (
            not isinstance(request.data, Mapping)
            or set(request.data.keys()) != {"categoryIds"}
            or not isinstance(request.data.get("categoryIds"), list)
        ):
            raise ContractError(
                400,
                "VALIDATION_ERROR",
                "Check the request fields.",
                details={"categoryIds": ["Provide a list of category IDs."]},
            )

        category_ids = request.data["categoryIds"]
        for item in category_ids:
            if not isinstance(item, str) or not ID_PATTERN.fullmatch(item) or int(item) > MAX_ID:
                raise ContractError(
                    400,
                    "VALIDATION_ERROR",
                    "Check the request fields.",
                    details={"categoryIds": ["Use positive 64-bit ID strings."]},
                )

        unique_ids = list(dict.fromkeys(int(item) for item in category_ids))
        if len(unique_ids) > 5:
            raise ContractError(
                400,
                "INTEREST_LIMIT_EXCEEDED",
                "You can select up to 5 interest categories.",
            )

        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            found_ids = set(
                Category.objects.filter(pk__in=unique_ids, is_active=True).values_list("pk", flat=True)
            )
            if len(found_ids) != len(unique_ids):
                raise ContractError(404, "CATEGORY_NOT_FOUND", "The category was not found.")

            UserInterestCategory.objects.filter(user=request.user).delete()
            UserInterestCategory.objects.bulk_create([
                UserInterestCategory(user=request.user, category_id=cid)
                for cid in unique_ids
            ])
            interests = serialize_interests(request.user)

        return Response({"data": {"interests": interests}})
