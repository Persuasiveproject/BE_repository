from django.urls import path

from .api.views import KakaoLoginView, LogoutView, PhoneCodeView, PhoneVerifyView, RefreshView, SignupView, TermsView
from .views import (MeBadgesView, MeCreditHistoryView, MeCreditsView, MeDemographicConsentView,
                    MeDemographicView, MeInterestsView, MeView)


urlpatterns = [
    path("auth/phone/code", PhoneCodeView.as_view(), name="auth-phone-code"),
    path("auth/phone/verify", PhoneVerifyView.as_view(), name="auth-phone-verify"),
    path("auth/kakao", KakaoLoginView.as_view(), name="auth-kakao"),
    path("auth/signup", SignupView.as_view(), name="auth-signup"),
    path("auth/refresh", RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", LogoutView.as_view(), name="auth-logout"),
    path("terms", TermsView.as_view(), name="terms"),
    path("me", MeView.as_view(), name="me"),
    path("me/demographic", MeDemographicView.as_view(), name="me-demographic"),
    path("me/demographic/consent", MeDemographicConsentView.as_view(), name="me-demographic-consent"),
    path("me/credits", MeCreditsView.as_view(), name="me-credits"),
    path("me/credits/history", MeCreditHistoryView.as_view(), name="me-credit-history"),
    path("me/badges", MeBadgesView.as_view(), name="me-badges"),
    path("me/interests", MeInterestsView.as_view(), name="me-interests"),
]
