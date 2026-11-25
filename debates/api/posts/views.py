from django.db import transaction
from django.db.models import F, Prefetch
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api.references.validation import check_reference_url
from debates.api.writes import IdempotentWrite, lock_debate_for_write
from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Debate, DebateParticipant, Stance
from debates.stance_checks import require_before_deadline, require_open_debate
from discussions.ai.coach import coach
from discussions.models import Opinion, OpinionReference, OpinionVote
from economy.models import UserBadge
from engagement.notifications import notify_reply
from moderation.checks import require_no_banned_words

from .pagination import post_page
from .serializers import CoachInput, PostInput, post_created_data
from .spam import require_not_spamming


PUBLIC_DEBATE_STATES = (Debate.Status.OPEN, Debate.Status.CLOSED)
VOTE_COUNT_FIELDS = {
    OpinionVote.VoteType.UP: "upvote_count",
    OpinionVote.VoteType.DOWN: "downvote_count",
}
POST_SORT_FIELDS = {
    "latest": None,
    "upvotes": "upvote_count",
    "persuasion": "persuasion_count",
}
PERSUASION_SORT_MIN_POSTS = 20


def visible_opinions():
    return Opinion.objects.filter(
        status=Opinion.Status.VISIBLE,
        participant__debate_id=F("debate_id"),
    )


def opinion_cards(queryset):
    return queryset.select_related("participant", "badge").prefetch_related(
        Prefetch("opinionreference_set", queryset=OpinionReference.objects.order_by("id")),
    )


def reply_target(debate, parent_id):
    error = ContractError(400, "PARENT_DEBATE_MISMATCH", "Choose a visible parent opinion in this debate.")
    parent = visible_opinions().select_for_update(of=("self",)).filter(debate=debate, pk=parent_id).first()
    if parent is None:
        raise error
    if parent.parent_id is None:
        return parent, parent.pk

    ancestors = Opinion.objects.filter(debate=debate)
    if parent.root_id is not None and ancestors.filter(pk=parent.root_id, parent__isnull=True).exists():
        return parent, parent.root_id

    # Older nested replies can lack a root pointer. Resolve their ancestry in this debate.
    seen = {parent.pk}
    ancestor_id = parent.parent_id
    while ancestor_id not in seen:
        seen.add(ancestor_id)
        ancestor = ancestors.values_list("pk", "parent_id").filter(pk=ancestor_id).first()
        if ancestor is None:
            break
        if ancestor[1] is None:
            return parent, ancestor[0]
        ancestor_id = ancestor[1]
    raise error


class DebatePostListView(ContractResponseMixin, APIView):
    def get_permissions(self):
        return [IsActiveMember()] if self.request.method == "POST" else [AllowAny()]

    def get(self, request, debate_id):
        if not Debate.objects.filter(pk=debate_id, status__in=PUBLIC_DEBATE_STATES).exists():
            raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")
        sort = request.query_params.get("sort", "latest").lower()
        if sort not in POST_SORT_FIELDS:
            raise ContractError(400, "SORT_INVALID", "Use latest, upvotes, or persuasion.")
        stance = request.query_params.get("stance")
        posts = visible_opinions().filter(
            debate_id=debate_id, debate__status__in=PUBLIC_DEBATE_STATES, parent__isnull=True,
        )
        score_field = POST_SORT_FIELDS[sort]
        # Few posts: persuasion falls back to upvotes. They fit on one page, so no cursor crosses the switch.
        if sort == "persuasion" and posts.count() < PERSUASION_SORT_MIN_POSTS:
            score_field = POST_SORT_FIELDS["upvotes"]
        if stance is not None:
            if stance not in Stance.values:
                raise ContractError(400, "VALIDATION_ERROR", "Choose a valid stance.")
            posts = posts.filter(stance_at_write=stance)
        return post_page(
            request, opinion_cards(posts), "posts", stance=stance, score_field=score_field,
        )

    @transaction.atomic
    def post(self, request, debate_id):
        payload = PostInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        write = IdempotentWrite(request, data)
        debate = lock_debate_for_write(debate_id, request.user.pk)
        if (saved := write.replay()) is not None:
            return Response(saved[0], status=saved[1])
          
        for item in data["references"]:
            check_reference_url(item["url"])
        require_no_banned_words(data["content"])
        participant = DebateParticipant.objects.select_for_update().filter(debate=debate, user=request.user).first()
        if participant is None:
            raise ContractError(403, "NOT_PARTICIPANT", "Join this debate before posting.")
        require_open_debate(debate)
        require_not_spamming(request.user, debate, data["content"])
        parent, root_id = None, None
        if "parentId" in data:
            parent, root_id = reply_target(debate, data["parentId"])
        badge_id = UserBadge.objects.filter(user=request.user, is_representative=True).order_by(
            "-acquired_at", "badge_id",
        ).values_list("badge_id", flat=True).first()

        require_before_deadline(debate)
        post = Opinion.objects.create(
            debate=debate, participant=participant, parent=parent, root_id=root_id, badge_id=badge_id,
            stance_at_write=participant.current_stance, content=data["content"], content_plain=data["content"],
        )
        references = OpinionReference.objects.bulk_create([
            OpinionReference(opinion=post, url=item["url"]) for item in data["references"]
        ])
        if parent is None:
            Debate.objects.filter(pk=debate.pk).update(opinion_count=F("opinion_count") + 1)
        else:
            Opinion.objects.filter(pk=parent.pk).update(reply_count=F("reply_count") + 1)
            notify_reply(parent, post)
        response = {"data": post_created_data(post, references)}
        write.save(response, 201)
        return Response(response, status=201)


class PostThreadView(ContractResponseMixin, APIView):
    permission_classes = [AllowAny]

    def get(self, request, post_id):
        if not 1 <= post_id <= MAX_ID:
            raise ContractError(400, "VALIDATION_ERROR", "Use a valid post ID.")
        parent = visible_opinions().filter(pk=post_id, debate__status__in=PUBLIC_DEBATE_STATES).first()
        if parent is None:
            raise ContractError(404, "POST_NOT_FOUND", "Post not found.")
        replies = visible_opinions().filter(
            parent=parent, debate_id=parent.debate_id, debate__status__in=PUBLIC_DEBATE_STATES,
        )
        return post_page(request, opinion_cards(replies), "replies")


@transaction.atomic
def set_reaction(user, post_id, reaction):
    """Set the user's vote on a post; reaction=None removes it."""
    if not 1 <= post_id <= MAX_ID:
        raise ContractError(400, "VALIDATION_ERROR", "Use a valid post ID.")
    not_found = ContractError(404, "POST_NOT_FOUND", "Post not found.")
    debate_id = visible_opinions().filter(
        pk=post_id, debate__status__in=PUBLIC_DEBATE_STATES,
    ).values_list("debate_id", flat=True).first()
    if debate_id is None:
        raise not_found
    debate = lock_debate_for_write(debate_id, user.pk)
    participant = DebateParticipant.objects.select_for_update().filter(debate=debate, user=user).first()
    if participant is None:
        raise ContractError(403, "NOT_PARTICIPANT", "Join this debate before reacting.")
    # Recheck after locking: the post or debate may have changed while waiting.
    post = visible_opinions().select_for_update(of=("self",)).filter(pk=post_id, debate=debate).first()
    if post is None or debate.status not in PUBLIC_DEBATE_STATES:
        raise not_found
    require_open_debate(debate)
    if reaction is not None and post.participant_id == participant.pk:
        raise ContractError(403, "OWN_POST_REACTION", "You cannot react to your own post.")

    votes = OpinionVote.objects.filter(user=user, opinion=post)
    previous = votes.values_list("vote_type", flat=True).first()
    if previous != reaction:
        if previous is not None:
            field = VOTE_COUNT_FIELDS[previous]
            Opinion.objects.filter(pk=post.pk, **{f"{field}__gt": 0}).update(**{field: F(field) - 1})
        if reaction is None:
            votes.delete()
        else:
            field = VOTE_COUNT_FIELDS[reaction]
            Opinion.objects.filter(pk=post.pk).update(**{field: F(field) + 1})
            if previous is None:
                OpinionVote.objects.create(user=user, opinion=post, vote_type=reaction)
            else:
                votes.update(vote_type=reaction)
    post.refresh_from_db(fields=["upvote_count", "downvote_count"])
    return post


class PostReactionView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def put(self, request, post_id):
        reaction = request.data.get("reaction") if isinstance(request.data, dict) else None
        if not isinstance(reaction, str) or reaction not in VOTE_COUNT_FIELDS:
            raise ContractError(400, "VALIDATION_ERROR", "Use UP or DOWN.")
        post = set_reaction(request.user, post_id, reaction)
        return Response({"data": {
            "reaction": reaction,
            "upvoteCount": post.upvote_count,
            "downvoteCount": post.downvote_count,
        }})

    def delete(self, request, post_id):
        set_reaction(request.user, post_id, None)
        return Response(status=204)


class PostCoachView(ContractResponseMixin, APIView):
    """POST /v1/debates/:debateId/posts/coach — 올리기 전 작성 코치.

    본문: {content, parentId?}. 응답: {verdict: OK|SUGGEST|UNAVAILABLE, reasons: [...], suggestion}.
    글을 저장하지 않는다. 코치가 쓸 수 없으면 UNAVAILABLE(200)을 돌려주고 화면은 그대로 올린다.
    """

    permission_classes = [IsActiveMember]

    def post(self, request, debate_id):
        payload = CoachInput(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        debate = Debate.objects.with_deadline().filter(pk=debate_id, status__in=PUBLIC_DEBATE_STATES).first()
        if debate is None:
            raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")
        if not DebateParticipant.objects.filter(debate=debate, user=request.user).exists():
            raise ContractError(403, "NOT_PARTICIPANT", "Join this debate before posting.")
        require_open_debate(debate)
        parent_text = None
        if "parentId" in data:
            parent = visible_opinions().filter(debate=debate, pk=data["parentId"]).only("content_plain", "content").first()
            if parent is None:
                raise ContractError(400, "PARENT_DEBATE_MISMATCH", "Choose a visible parent opinion in this debate.")
            parent_text = (parent.content_plain or parent.content or "").strip()[:1000]
        return Response({"data": coach(request.user.pk, debate.title, data["content"], parent_text)})
