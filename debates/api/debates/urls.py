from django.urls import path

from .views import DebateListView


urlpatterns = [
    path("debates", DebateListView.as_view(), name="debate-list"),
]
