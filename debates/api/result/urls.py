from django.urls import path

from .views import DebateResultView


urlpatterns = [
    path("debates/<int:debate_id>/result", DebateResultView.as_view(), name="debate-result"),
]
