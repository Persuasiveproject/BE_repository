from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from discussions.models import Opinion, OpinionReference
from economy.models import Badge

from .models import Debate, DebateParticipant, Stance
from .test_b_api import DebateBFixture


class PostDeleteTests(DebateBFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.join().status_code, 201)
        self.participant = DebateParticipant.objects.get(debate=self.debate, user=self.member)
        badge = Badge.objects.create(name="Debater", icon_url="https://example.com/b.png", required_credit=1)
        self.post = Opinion.objects.create(
            debate=self.debate, participant=self.participant, stance_at_write=Stance.PRO,
            content="Original reason", content_plain="Original reason", badge=badge,
        )
        OpinionReference.objects.create(opinion=self.post, url="https://example.com/evidence")
        self.reply = self.reply_to(self.post)
        Debate.objects.filter(pk=self.debate.pk).update(opinion_count=1)
        Opinion.objects.filter(pk=self.post.pk).update(reply_count=1)

    def reply_to(self, parent):
        author = DebateParticipant.objects.get_or_create(
            debate=self.debate, user=self.creator,
            defaults={"anonymous_no": 99, "initial_stance": Stance.CON, "current_stance": Stance.CON},
        )[0]
        return Opinion.objects.create(
            debate=self.debate, participant=author, parent=parent, root_id=parent.pk,
            stance_at_write=Stance.CON, content="Reply", content_plain="Reply",
        )

    def delete(self, post_id=None):
        return self.client.delete(reverse("post-detail", args=[post_id or self.post.pk]))

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)

    def status(self):
        self.post.refresh_from_db()
        return self.post.status

    def card(self):
        posts = APIClient().get(self.posts_url).json()["data"]["posts"]
        return next(post for post in posts if post["postId"] == str(self.post.pk))

    def test_author_deletes_and_repeats(self):
        response = self.delete()
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.status(), Opinion.Status.DELETED)
        self.assertEqual(self.post.content, "Original reason")

        self.assertEqual(self.delete().status_code, 204)
        self.assertEqual(self.status(), Opinion.Status.DELETED)

    def test_rejections(self):
        self.client.force_authenticate(self.creator)
        self.assert_error(self.delete(), 403, "NOT_POST_AUTHOR")
        self.assertEqual(APIClient().delete(reverse("post-detail", args=[self.post.pk])).status_code, 401)
        self.client.force_authenticate(self.member)
        self.assert_error(self.delete(999999), 404, "POST_NOT_FOUND")
        self.assert_error(self.client.delete(f"/v1/posts/{2**63}"), 400, "VALIDATION_ERROR")
        self.assertEqual(self.status(), Opinion.Status.VISIBLE)

    def test_draft_debate_post_not_found(self):
        draft = Debate.objects.create(
            creator=self.member, category=self.debate.category, type=Debate.Type.QUICK, title="Draft",
        )
        author = DebateParticipant.objects.create(
            debate=draft, user=self.member, anonymous_no=1,
            initial_stance=Stance.PRO, current_stance=Stance.PRO,
        )
        post = Opinion.objects.create(
            debate=draft, participant=author, stance_at_write=Stance.PRO, content="X", content_plain="X",
        )

        self.assert_error(self.delete(post.pk), 404, "POST_NOT_FOUND")

    def test_suspended_author_and_closed_debate(self):
        User.objects.filter(pk=self.member.pk).update(status=User.Status.SUSPENDED)
        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.CLOSED)
        self.client.force_authenticate(User.objects.get(pk=self.member.pk))

        self.assertEqual(self.delete().status_code, 204)
        self.assertEqual(self.status(), Opinion.Status.DELETED)

    def test_filtered_post_can_be_deleted(self):
        Opinion.objects.filter(pk=self.post.pk).update(status=Opinion.Status.FILTERED)

        self.assertEqual(self.delete().status_code, 204)
        self.assertEqual(self.status(), Opinion.Status.DELETED)

    def test_deleted_and_filtered_posts_stay_listed_without_content(self):
        before = self.card()
        self.assertEqual(before["content"], "Original reason")
        self.assertEqual(len(before["references"]), 1)
        self.assertIsNotNone(before["badge"])

        for status in (Opinion.Status.FILTERED, Opinion.Status.DELETED):
            Opinion.objects.filter(pk=self.post.pk).update(status=status)
            card = self.card()
            self.assertEqual(set(card), set(before))
            self.assertEqual(card["status"], status)
            self.assertEqual(card["content"], "")
            self.assertEqual(card["references"], [])
            self.assertIsNone(card["badge"])
            for key in set(before) - {"status", "content", "references", "badge", "updatedAt"}:
                self.assertEqual(card[key], before[key], key)

    def test_replies_of_deleted_post_remain(self):
        self.assertEqual(self.delete().status_code, 204)

        response = APIClient().get(reverse("post-thread", args=[self.post.pk]))

        self.assertEqual(response.status_code, 200)
        replies = response.json()["data"]["replies"]
        self.assertEqual([reply["postId"] for reply in replies], [str(self.reply.pk)])
        self.assertEqual(replies[0]["content"], "Reply")

    def test_deleted_post_blocks_new_replies_and_keeps_counts(self):
        self.assertEqual(self.delete().status_code, 204)

        response = self.write("reply", "New reply", parentId=str(self.post.pk))
        self.assert_error(response, 400, "PARENT_DEBATE_MISMATCH")
        self.debate.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(self.debate.opinion_count, 1)
        self.assertEqual(self.post.reply_count, 1)
