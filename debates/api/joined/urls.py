from django.urls import path

from .views import JoinedDebateListView


urlpatterns = [
    path("me/debates", JoinedDebateListView.as_view(), name="joined-debate-list"),
]
