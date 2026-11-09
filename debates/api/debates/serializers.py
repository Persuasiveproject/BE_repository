from rest_framework import serializers

from debates.listing import debate_list_item


class DebateListSerializer(serializers.BaseSerializer):
    def to_representation(self, debate):
        return debate_list_item(debate, self.context["as_of"])
