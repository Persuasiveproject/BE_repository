from django.urls import path

from .views import DebateSummaryView


urlpatterns = [
    path("debates/<int:debate_id>/summary", DebateSummaryView.as_view(), name="debate-summary"),
]
