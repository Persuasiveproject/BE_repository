from django.urls import path

from .views import DebateIssuesView, DebateSummaryView


urlpatterns = [
    path("debates/<int:debate_id>/summary", DebateSummaryView.as_view(), name="debate-summary"),
    path("debates/<int:debate_id>/issues", DebateIssuesView.as_view(), name="debate-issues"),
]
