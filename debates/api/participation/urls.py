from django.urls import path

from .views import DebateDetailView, JoinDebateView


urlpatterns = [
    path("debates/<int:debate_id>", DebateDetailView.as_view(), name="debate-detail"),
    path("debates/<int:debate_id>/participants", JoinDebateView.as_view(), name="debate-join"),
]
