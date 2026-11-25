from rest_framework import serializers

from debates.models import Debate, DebateImage, Stance
from debates.serializers import utc


class JoinDebateInput(serializers.Serializer):
    stance = serializers.ChoiceField(choices=Stance.choices)


def participation_data(participant):
    return {
        "participantId": str(participant.pk),
        "anonymousNo": participant.anonymous_no,
        "initialStance": participant.initial_stance,
        "currentStance": participant.current_stance,
        "version": participant.version,
    }


def debate_detail_data(debate, status):
    category = debate.category
    closed_at = debate.closed_at
    if debate.status == Debate.Status.OPEN and status == Debate.Status.CLOSED:
        closed_at = debate.deadline
    return {
        "id": str(debate.pk),
        "kind": debate.type,
        "title": debate.title,
        "status": status,
        "createdAt": utc(debate.created_at),
        "category": {"id": str(category.pk), "name": category.name, "sortOrder": category.sort_order},
        "content": debate.content,
        "durationMinutes": debate.duration_minutes,
        "startedAt": utc(debate.started_at),
        "endsAt": utc(debate.deadline),
        "closedAt": utc(closed_at),
        "canceledAt": utc(debate.canceled_at),
        "participantCount": debate.participant_count,
    }


def image_data(image, draft):
    return {
        "assetId": str(image.pk),
        "status": image.status,
        "imageUrl": image.image_url if image.status == DebateImage.Status.READY else None,
        "sortOrder": image.sort_order,
        "rejectionReason": image.rejection_reason if draft else None,
    }
