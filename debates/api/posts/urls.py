from django.urls import path

from .views import DebatePostListView, PostThreadView


urlpatterns = [
    path("debates/<int:debate_id>/posts", DebatePostListView.as_view(), name="debate-posts"),
    path("posts/<int:post_id>/thread", PostThreadView.as_view(), name="post-thread"),
]
