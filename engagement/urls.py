from django.urls import path

from .bookmarks import BookmarkListView, PostBookmarkView
from .views import NotificationListView, NotificationReadAllView, NotificationReadView


urlpatterns = [
    path("posts/<int:post_id>/bookmark", PostBookmarkView.as_view(), name="post-bookmark"),
    path("me/bookmarks", BookmarkListView.as_view(), name="bookmark-list"),
    path("me/notifications", NotificationListView.as_view(), name="notification-list"),
    path("me/notifications/read-all", NotificationReadAllView.as_view(), name="notification-read-all"),
    path("me/notifications/<int:notification_id>", NotificationReadView.as_view(), name="notification-read"),
]
