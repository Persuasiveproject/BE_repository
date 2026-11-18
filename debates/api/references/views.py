import re

from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api.posts.views import PUBLIC_DEBATE_STATES, visible_opinions
from debates.api.writes import lock_debate_for_write
from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin, IsActiveMember
from debates.stance_checks import require_open_debate
from discussions.models import OpinionReference
from moderation.models import Report

from .validation import check_reference_url


MAX_REFERENCES = 3
ID_PATTERN = re.compile(r"[1-9][0-9]{0,18}")


def require_valid_id(value, name):
    if not 1 <= value <= MAX_ID:
        raise ContractError(400, "VALIDATION_ERROR", f"Use a valid {name}.")


def post_not_found():
    return ContractError(404, "POST_NOT_FOUND", "Post not found.")


def reference_not_found():
    return ContractError(404, "REFERENCE_NOT_FOUND", "Reference not found.")


def lock_own_post(post_id, user):
    """Lock debate -> user -> post and return the caller's post in an open debate."""
    debate_id = visible_opinions().filter(
        pk=post_id, debate__status__in=PUBLIC_DEBATE_STATES,
    ).values_list("debate_id", flat=True).first()
    if debate_id is None:
        raise post_not_found()
    debate = lock_debate_for_write(debate_id, user.pk)
    post = visible_opinions().select_for_update(of=("self",)).select_related("participant").filter(
        pk=post_id, debate=debate,
    ).first()
    if post is None or debate.status not in PUBLIC_DEBATE_STATES:
        raise post_not_found()
    if post.participant.user_id != user.pk:
        raise ContractError(403, "NOT_POST_AUTHOR", "Only the author can change links.")
    require_open_debate(debate)
    return post


def reference_response(reference, status):
    return Response({"data": {
        "referenceId": str(reference.pk),
        "status": reference.safe_browsing_status,
    }}, status=status)


class PostReferenceView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def post(self, request, post_id):
        require_valid_id(post_id, "post ID")
        if not visible_opinions().filter(pk=post_id, debate__status__in=PUBLIC_DEBATE_STATES).exists():
            raise post_not_found()
        body = request.data if isinstance(request.data, dict) else {}
        url = check_reference_url(body.get("url"))
        reference_id = body.get("referenceId")
        if reference_id is not None and (
            not isinstance(reference_id, str) or not ID_PATTERN.fullmatch(reference_id)
            or int(reference_id) > MAX_ID
        ):
            raise ContractError(400, "VALIDATION_ERROR", "Use a positive decimal reference ID.")

        with transaction.atomic():
            post = lock_own_post(post_id, request.user)
            if reference_id is None:
                if OpinionReference.objects.filter(opinion=post).count() >= MAX_REFERENCES:
                    raise ContractError(400, "REFERENCE_LIMIT_EXCEEDED", "A post can have up to 3 links.")
                reference = OpinionReference.objects.create(opinion=post, url=url)
                return reference_response(reference, 201)

            reference = OpinionReference.objects.select_for_update().filter(pk=reference_id, opinion=post).first()
            if reference is None:
                raise reference_not_found()
            reference.url = url
            reference.safe_browsing_status = OpinionReference.SafetyStatus.PENDING
            reference.checked_at = None
            reference.save(update_fields=["url", "safe_browsing_status", "checked_at"])
            return reference_response(reference, 200)


class ReferenceDetailView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    @transaction.atomic
    def delete(self, request, reference_id):
        require_valid_id(reference_id, "reference ID")
        post_id = OpinionReference.objects.filter(pk=reference_id).values_list("opinion_id", flat=True).first()
        if post_id is None:
            raise reference_not_found()
        post = lock_own_post(post_id, request.user)
        reference = OpinionReference.objects.select_for_update().filter(pk=reference_id, opinion=post).first()
        if reference is None:
            raise reference_not_found()
        if Report.objects.filter(reference=reference).exists():
            raise ContractError(409, "REFERENCE_HAS_REPORTS", "Reported links cannot be deleted.")
        reference.delete()
        return Response(status=204)
