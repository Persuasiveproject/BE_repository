from django.urls import path

from .similar import SimilarDebateListView
from .views import DebateListView


urlpatterns = [
    path("debates", DebateListView.as_view(), name="debate-list"),
    path("debates/similar", SimilarDebateListView.as_view(), name="debate-similar"),
]
