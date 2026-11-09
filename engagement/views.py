from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from debates.api.joined.views import DebateCursorPagination
from debates.api_contract import ContractError, ContractResponseMixin
from debates.serializers import utc

from .models import Notification


class NotificationCursorPagination(DebateCursorPagination):
    def decode_cursor(self, request):
        # Base class reports FILTER_INVALID; this API uses CURSOR_INVALID.
        try:
            return super().decode_cursor(request)
        except ContractError as exc:
            raise ContractError(400, "CURSOR_INVALID", "Use a valid cursor.") from exc

    def paginate_queryset(self, queryset, request, view=None):
        self.unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data = {
            "data": {"notifications": data, "unreadCount": self.unread_count},
            "meta": response.data["meta"],
        }
        return response


class NotificationSerializer(serializers.BaseSerializer):
    def to_representation(self, notification):
        return {
            "notificationId": str(notification.pk),
            "type": notification.type,
            "title": notification.title,
            "body": notification.body,
            "targetType": notification.target_type,
            "targetId": str(notification.target_id) if notification.target_id is not None else None,
            "isRead": notification.is_read,
            "createdAt": utc(notification.created_at),
        }


class NotificationListView(ContractResponseMixin, ListAPIView):
    # Suspended members can still read their own notifications.
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer
    pagination_class = NotificationCursorPagination

    def get_queryset(self):
        unread_only = self.request.query_params.get("unreadOnly", "false")
        if unread_only not in ("true", "false"):
            raise ContractError(400, "FILTER_INVALID", "Use true or false.")
        notifications = Notification.objects.filter(user=self.request.user)
        if unread_only == "true":
            notifications = notifications.filter(is_read=False)
        return notifications
