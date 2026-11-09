from django.urls import path

from .views import DebateDetailView


urlpatterns = [
    path("debates/<int:debate_id>", DebateDetailView.as_view(), name="debate-detail"),
]
