from django.urls import path

from .views import JoinDebateView


urlpatterns = [
    path("debates/<int:debate_id>/participants", JoinDebateView.as_view(), name="debate-join"),
]
