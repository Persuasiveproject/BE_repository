from django.urls import path

from .views import MeBadgesView, MeCreditHistoryView, MeCreditsView, MeInterestsView, MeView


urlpatterns = [
    path("me", MeView.as_view(), name="me"),
    path("me/credits", MeCreditsView.as_view(), name="me-credits"),
    path("me/credits/history", MeCreditHistoryView.as_view(), name="me-credit-history"),
    path("me/badges", MeBadgesView.as_view(), name="me-badges"),
    path("me/interests", MeInterestsView.as_view(), name="me-interests"),
]
