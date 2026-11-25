from django.urls import path

from .trend import DebateTrendView
from .views import DebateTimingView


urlpatterns = [
    path("debates/<int:debate_id>/trend", DebateTrendView.as_view(), name="debate-trend"),
    path("debates/<int:debate_id>/timing", DebateTimingView.as_view(), name="debate-timing"),
]
