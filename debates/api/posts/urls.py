from django.urls import path

from .views import DebatePostListView, PostCoachView, PostReactionView, PostThreadView


urlpatterns = [
    path("debates/<int:debate_id>/posts", DebatePostListView.as_view(), name="debate-posts"),
    path("debates/<int:debate_id>/posts/coach", PostCoachView.as_view(), name="debate-post-coach"),
    path("posts/<int:post_id>/thread", PostThreadView.as_view(), name="post-thread"),
    path("posts/<int:post_id>/reaction", PostReactionView.as_view(), name="post-reaction"),
]
