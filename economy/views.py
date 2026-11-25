from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import ContractResponseMixin
# Create your views here.
class WalletViews(ContractResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "data":{
                "coinBalance" : request.user.coin_balance,
            }
        })