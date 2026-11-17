from collections.abc import Mapping

from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api.joined.views import DebateCursorPagination
from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin, IsActiveMember
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


class NotificationReadView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        notification_id = kwargs.get("notification_id")
        if notification_id is not None and (
            not 1 <= notification_id <= MAX_ID or len(request.path_info) > 255
        ):
            raise ContractError(400, "VALIDATION_ERROR", "Use a valid notification ID and request path.")

    def patch(self, request, notification_id):
        if (
            not isinstance(request.data, Mapping)
            or set(request.data.keys()) != {"isRead"}
            or request.data.get("isRead") is not True
        ):
            raise ContractError(
                400,
                "VALIDATION_ERROR",
                "Check the request fields.",
                details={"isRead": ["Must be boolean true."]},
            )

        notification = Notification.objects.filter(pk=notification_id).first()
        if notification is None:
            raise ContractError(404, "NOT_FOUND", "The notification was not found.")
        if notification.user_id != request.user.pk:
            raise ContractError(403, "NOT_NOTIFICATION_OWNER", "You do not own this notification.")

        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read"])

        return Response({"data": {
            "notificationId": str(notification.pk),
            "isRead": True,
        }})


class NotificationReadAllView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def post(self, request):
        updated_count = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"data": {"updatedCount": updated_count}})
