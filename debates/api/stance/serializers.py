from rest_framework import serializers

from debates.api_contract import MAX_ID
from debates.models import DebateParticipant, Stance
from debates.serializers import utc


class StanceChangeInput(serializers.Serializer):
    stance = serializers.ChoiceField(choices=Stance.choices)
    opinionId = serializers.RegexField(r"\A[1-9][0-9]{0,18}\Z", required=False, trim_whitespace=False)
    version = serializers.IntegerField(min_value=0, max_value=2**31 - 1, required=False)

    def validate_opinionId(self, value):
        if not isinstance(self.initial_data["opinionId"], str) or int(value) > MAX_ID:
            raise serializers.ValidationError("Use a positive 64-bit ID as a decimal string.")
        return int(value)


class StanceStateSerializer(serializers.ModelSerializer):
    initialStance = serializers.CharField(source="initial_stance")
    currentStance = serializers.CharField(source="current_stance")

    class Meta:
        model = DebateParticipant
        fields = ("initialStance", "currentStance", "version")


class StanceEventSerializer(serializers.BaseSerializer):
    def to_representation(self, event):
        return {
            "id": str(event.pk),
            "fromStance": event.from_stance,
            "toStance": event.to_stance,
            "changedAt": utc(event.changed_at),
            "persuasionId": str(event.persuasion_id) if event.persuasion_id is not None else None,
        }
