from django.urls import path

from .views import DebateTimingView


urlpatterns = [
    path("debates/<int:debate_id>/timing", DebateTimingView.as_view(), name="debate-timing"),
]
