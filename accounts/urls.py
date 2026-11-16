from django.urls import path

from .views import MeInterestsView, MeView


urlpatterns = [
    path("me", MeView.as_view(), name="me"),
    path("me/interests", MeInterestsView.as_view(), name="me-interests"),
]
