from django.urls import path

from .views import NotificationListView


urlpatterns = [
    path("me/notifications", NotificationListView.as_view(), name="notification-list"),
]
