from math import ceil

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.models import Debate


class DebateTimingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, debate_id):
        debate = get_object_or_404(
            Debate.objects.with_deadline().exclude(status=Debate.Status.DRAFT),
            pk=debate_id,
        )
        now = timezone.now()
        status = debate.effective_status(now)
        remaining = 0
        if status == Debate.Status.OPEN:
            remaining = ceil((debate.deadline - now).total_seconds()) if debate.deadline else None
        date_time = serializers.DateTimeField()
        return Response({
            "debateId": debate.pk,
            "status": status,
            "serverTime": date_time.to_representation(now),
            "endsAt": date_time.to_representation(debate.deadline),
            "remainingSeconds": remaining,
            "isReadOnly": status != Debate.Status.OPEN,
        })
