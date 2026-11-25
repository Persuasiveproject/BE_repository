from django.urls import path

from .views import CoinTransactionListView, WalletViews

urlpatterns = [
    path("me/wallet", WalletViews.as_view(), name="wallet"),
    path("me/coin-transactions", CoinTransactionListView.as_view(), name="coin-transactions"),
]