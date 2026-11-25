from rest_framework import serializers

from debates.api_contract import MAX_ID
from debates.serializers import utc
from discussions.models import Opinion, OpinionReference


class StringField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class ReferenceInput(serializers.Serializer):
    # Checked by check_reference_url in the view so errors use URL_INVALID.
    url = serializers.JSONField(allow_null=True)


class PostInput(serializers.Serializer):
    content = StringField(max_length=1000)
    parentId = StringField(required=False, max_length=19, trim_whitespace=False)
    references = ReferenceInput(many=True, max_length=3, default=list)

    def validate_parentId(self, value):
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= MAX_ID:
            raise serializers.ValidationError("Use a positive decimal ID.")
        return int(value)


class CoachInput(serializers.Serializer):
    content = StringField(max_length=1000)
    parentId = StringField(required=False, max_length=19, trim_whitespace=False)

    validate_parentId = PostInput.validate_parentId


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
    visible = post.status == Opinion.Status.VISIBLE
    badge = post.badge if visible else None
    return {
        **post_created_data(post, post.opinionreference_set.all() if visible else ()),
        "debateId": str(post.debate_id),
        "parentId": str(post.parent_id) if post.parent_id is not None else None,
        "rootId": str(post.root_id) if post.root_id is not None else None,
        "content": post.content if visible else "",
        "upvoteCount": post.upvote_count,
        "downvoteCount": post.downvote_count,
        "persuasionCount": post.persuasion_count,
        "replyCount": post.reply_count,
        "updatedAt": utc(post.updated_at),
        "anonymousNo": post.participant.anonymous_no,
        "badge": {"badgeId": str(badge.pk), "name": badge.name, "iconUrl": badge.icon_url} if badge else None,
    }
