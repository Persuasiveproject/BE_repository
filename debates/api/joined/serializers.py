from rest_framework import serializers

from debates.listing import debate_list_item


class JoinedDebateSerializer(serializers.BaseSerializer):
    def to_representation(self, participant):
        return debate_list_item(participant.debate, self.context["as_of"])
