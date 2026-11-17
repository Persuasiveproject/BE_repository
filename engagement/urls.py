from django.urls import path

from .views import NotificationListView, NotificationReadAllView, NotificationReadView


urlpatterns = [
    path("me/notifications", NotificationListView.as_view(), name="notification-list"),
    path("me/notifications/read-all", NotificationReadAllView.as_view(), name="notification-read-all"),
    path("me/notifications/<int:notification_id>", NotificationReadView.as_view(), name="notification-read"),
]
