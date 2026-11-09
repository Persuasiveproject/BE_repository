from urllib.parse import urlsplit

from rest_framework import serializers

from debates.api_contract import MAX_ID
from debates.serializers import utc
from discussions.models import OpinionReference


class StringField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class ReferenceInput(serializers.Serializer):
    url = serializers.URLField(max_length=2048)

    def validate_url(self, url):
        if urlsplit(url).scheme not in {"http", "https"}:
            raise serializers.ValidationError("Use an HTTP or HTTPS URL.")
        return url


class PostInput(serializers.Serializer):
    content = StringField(max_length=1000)
    parentId = StringField(required=False, max_length=19, trim_whitespace=False)
    references = ReferenceInput(many=True, max_length=3, default=list)

    def validate_parentId(self, value):
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= MAX_ID:
            raise serializers.ValidationError("Use a positive decimal ID.")
        return int(value)


def reference_data(reference):
    return {
        "referenceId": str(reference.pk),
        "status": reference.safe_browsing_status,
        "url": reference.url if reference.safe_browsing_status == OpinionReference.SafetyStatus.SAFE else None,
        "checkedAt": utc(reference.checked_at),
    }


def post_created_data(post, references):
    return {
        "postId": str(post.pk),
        "stanceAtWrite": post.stance_at_write,
        "status": post.status,
        "createdAt": utc(post.created_at),
        "references": [reference_data(reference) for reference in references],
    }


def opinion_data(post):
    badge = post.badge
    return {
        **post_created_data(post, post.opinionreference_set.all()),
        "debateId": str(post.debate_id),
        "parentId": str(post.parent_id) if post.parent_id is not None else None,
        "rootId": str(post.root_id) if post.root_id is not None else None,
        "content": post.content,
        "upvoteCount": post.upvote_count,
        "downvoteCount": post.downvote_count,
        "persuasionCount": post.persuasion_count,
        "replyCount": post.reply_count,
        "updatedAt": utc(post.updated_at),
        "anonymousNo": post.participant.anonymous_no,
        "badge": {"badgeId": str(badge.pk), "name": badge.name, "iconUrl": badge.icon_url} if badge else None,
    }
