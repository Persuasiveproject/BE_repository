from django.urls import path

from .views import WalletViews

urlpatterns = [
    path("me/wallet", WalletViews.as_view(), name="wallet"),
]