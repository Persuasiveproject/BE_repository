from rest_framework import serializers

from debates.api_contract import MAX_ID
from debates.models import Debate
from debates.serializers import utc


class DraftInput(serializers.Serializer):
    categoryId = serializers.RegexField(r"\A[1-9][0-9]{0,18}\Z", trim_whitespace=False)
    kind = serializers.ChoiceField(choices=Debate.Type.choices)
    title = serializers.CharField(max_length=100, trim_whitespace=False)
    content = serializers.CharField(trim_whitespace=False)
    durationMinutes = serializers.IntegerField(
        min_value=5, max_value=15, required=False, allow_null=True,
    )

    def validate_categoryId(self, value):
        if not isinstance(self.initial_data["categoryId"], str) or int(value) > MAX_ID:
            raise serializers.ValidationError("Use a positive 64-bit ID as a decimal string.")
        return int(value)

    def validate_title(self, value):
        if not isinstance(self.initial_data["title"], str):
            raise serializers.ValidationError("Use a string.")
        return self._not_whitespace(value)

    def validate_content(self, value):
        if not isinstance(self.initial_data["content"], str):
            raise serializers.ValidationError("Use a string.")
        return self._not_whitespace(value)

    def validate_durationMinutes(self, value):
        raw = self.initial_data["durationMinutes"]
        if raw is not None and type(raw) is not int:
            raise serializers.ValidationError("Use an integer.")
        return value

    def validate(self, attrs):
        # On PATCH, omitted kind/durationMinutes keep the draft's current values.
        draft = self.instance
        kind = attrs.get("kind", draft.type if draft else None)
        duration = attrs.get("durationMinutes", draft.duration_minutes if draft else None)
        if kind == Debate.Type.QUICK and duration is None:
            raise serializers.ValidationError({"durationMinutes": ["Required for QUICK debates."]})
        if kind == Debate.Type.ENDLESS and duration is not None:
            raise serializers.ValidationError({"durationMinutes": ["Must be null for ENDLESS debates."]})
        return attrs

    @staticmethod
    def _not_whitespace(value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value


def draft_data(debate):
    return {
        "debateId": str(debate.pk),
        "kind": debate.type,
        "title": debate.title,
        "status": debate.status,
        "createdAt": utc(debate.created_at),
        "categoryId": str(debate.category_id),
        "content": debate.content,
        "durationMinutes": debate.duration_minutes,
    }
