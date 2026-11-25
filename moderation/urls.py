from django.urls import path

from .views import ReferenceChallengeView, ReportCreateView


urlpatterns = [
    path("reports", ReportCreateView.as_view(), name="report-create"),
    path("references/<int:reference_id>/challenges", ReferenceChallengeView.as_view(), name="reference-challenge"),
]
