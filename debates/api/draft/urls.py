from django.urls import path

from .views import DebateDraftCreateView


urlpatterns = [
    path("debates", DebateDraftCreateView.as_view(), name="debate-draft-create"),
]
