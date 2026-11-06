from rest_framework import serializers

from debates.serializers import utc


class JoinedDebateSerializer(serializers.BaseSerializer):
    def to_representation(self, participant):
        debate = participant.debate
        status = debate.effective_status(self.context["as_of"])
        closed_at = debate.deadline if debate.status != status else debate.closed_at
        return {
            "id": str(debate.pk),
            "kind": debate.type,
            "title": debate.title,
            "status": status,
            "createdAt": utc(debate.created_at),
            "categoryId": str(debate.category_id),
            "participantCount": debate.participant_count,
            "opinionCount": debate.opinion_count,
            "viewCount": debate.view_count,
            "startedAt": utc(debate.started_at),
            "endsAt": utc(debate.deadline),
            "closedAt": utc(closed_at),
            "canceledAt": utc(debate.canceled_at),
        }
