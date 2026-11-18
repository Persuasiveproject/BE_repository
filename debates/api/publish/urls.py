from django.urls import path

from .views import DebatePublishView


urlpatterns = [
    path("debates/<int:debate_id>/publish", DebatePublishView.as_view(), name="debate-publish"),
]
