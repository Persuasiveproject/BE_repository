from datetime import timezone

from rest_framework import serializers

from .api_contract import MAX_ID
from .models import DebateParticipant, Stance


def utc(value):
    if value is not None:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return None
