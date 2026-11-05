from rest_framework import serializers

from debates.models import DebateParticipant


class StanceStateSerializer(serializers.ModelSerializer):
    initialStance = serializers.CharField(source="initial_stance")
    currentStance = serializers.CharField(source="current_stance")

    class Meta:
        model = DebateParticipant
        fields = ("initialStance", "currentStance", "version")
