"""요약 입력 선별 (FC-DB-220-002).

- 찬성·반대: 공감 상위 의견 10개씩, 유보: 최대 5개
- 의견마다 공감 상위 반박 2개
- 공개(VISIBLE) 글만, 글 본문만 전달한다. 회원 정보·입장 이력·공감 수는 넘기지 않는다.
"""
import hashlib
import json

from debates.models import Stance
from discussions.models import Opinion

OPINION_LIMITS = {Stance.PRO: 10, Stance.CON: 10, Stance.NEUTRAL: 5}
REPLY_LIMIT = 2
MIN_PUBLIC_POSTS = 5
ORDER = ("-upvote_count", "created_at", "id")


def visible_posts(debate_id):
    return Opinion.objects.filter(debate_id=debate_id, status=Opinion.Status.VISIBLE)


def public_post_count(debate_id):
    return visible_posts(debate_id).count()


def _text(post):
    return (post.content_plain or post.content or "").strip()


def select_inputs(debate_id):
    """입장별 의견과 반박을 고른다. 반환: {stance: [{id, text, replies: [{id, text}]}]}"""
    posts = visible_posts(debate_id)
    roots = posts.filter(parent__isnull=True)
    groups = {}
    for stance, limit in OPINION_LIMITS.items():
        items = []
        for opinion in roots.filter(stance_at_write=stance).order_by(*ORDER)[:limit]:
            replies = posts.filter(parent=opinion).order_by(*ORDER)[:REPLY_LIMIT]
            items.append({
                "id": str(opinion.pk),
                "text": _text(opinion),
                "replies": [{"id": str(r.pk), "text": _text(r)} for r in replies],
            })
        groups[stance] = items
    return groups


def input_ids(groups):
    ids = set()
    for items in groups.values():
        for item in items:
            ids.add(item["id"])
            ids.update(r["id"] for r in item["replies"])
    return ids


def fingerprint(groups):
    """선정된 의견 구성이 같으면 같은 값. 같으면 다시 만들지 않는다(FC-DB-220-003)."""
    shape = {s: [[i["id"], [r["id"] for r in i["replies"]]] for i in items] for s, items in groups.items()}
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()
