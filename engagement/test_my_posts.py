from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Debate, DebateParticipant, Stance
from debates.test_b_api import DebateBFixture
from discussions.models import Opinion


class MyPostListTests(DebateBFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("my-posts")
        self.mine = self.participant(self.member, 1)
        self.theirs = self.participant(self.creator, 2)

    def participant(self, user, number, debate=None):
        return DebateParticipant.objects.create(
            debate=debate or self.debate, user=user, anonymous_no=number,
            initial_stance=Stance.PRO, current_stance=Stance.PRO,
        )

    def opinion(self, participant=None, minutes_ago=0, **fields):
        post = Opinion.objects.create(
            debate=participant.debate if participant else self.debate, participant=participant or self.mine,
            stance_at_write=Stance.PRO, content="Mine", content_plain="Mine", **fields,
        )
        Opinion.objects.filter(pk=post.pk).update(created_at=self.now - timedelta(minutes=minutes_ago))
        return post

    def get(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def ids(self, body):
        return [item["postId"] for item in body["data"]["posts"]]

    def test_lists_my_opinions_and_replies_newest_first(self):
        old = self.opinion(minutes_ago=5)
        theirs = self.opinion(self.theirs, minutes_ago=3)
        reply = self.opinion(minutes_ago=1, parent=theirs, root=theirs)

        body = self.get()

        self.assertEqual(self.ids(body), [str(reply.pk), str(old.pk)])
        self.assertEqual(body["data"]["posts"][0]["parentId"], str(theirs.pk))
        self.assertEqual(body["meta"], {"nextCursor": None})

    def test_pages_through_twenty_five_posts(self):
        posts = [self.opinion(minutes_ago=i) for i in range(25)]

        first = self.get()
        second = self.get(cursor=first["meta"]["nextCursor"])

        self.assertEqual(self.ids(first), [str(p.pk) for p in posts[:20]])
        self.assertIsNotNone(first["meta"]["nextCursor"])
        self.assertEqual(self.ids(second), [str(p.pk) for p in posts[20:]])
        self.assertIsNone(second["meta"]["nextCursor"])

    def test_invalid_or_other_members_cursor_is_rejected(self):
        for i in range(21):
            self.opinion(minutes_ago=i)
        cursor = self.get()["meta"]["nextCursor"]

        for client, value in ((self.client, "broken"), (APIClient(), cursor)):
            client.force_authenticate(user=self.creator)
            response = client.get(self.url, {"cursor": value})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "CURSOR_INVALID")

    def test_deleted_is_left_out_and_filtered_is_a_placeholder(self):
        self.opinion(minutes_ago=2, status=Opinion.Status.DELETED)
        filtered = self.opinion(minutes_ago=1, status=Opinion.Status.FILTERED)

        [item] = self.get()["data"]["posts"]

        self.assertEqual(item["postId"], str(filtered.pk))
        self.assertEqual((item["content"], item["references"]), ("삭제된 글입니다", []))

    def test_posts_in_draft_and_canceled_debates_are_left_out(self):
        visible = self.opinion()
        for status in (Debate.Status.DRAFT, Debate.Status.CANCELED):
            debate = Debate.objects.create(
                creator=self.creator, category=self.debate.category, type=Debate.Type.ENDLESS,
                title=status, content="Topic", status=status,
            )
            self.opinion(self.participant(self.member, 1, debate))

        self.assertEqual(self.ids(self.get()), [str(visible.pk)])

    def test_requires_login_but_allows_suspended_members(self):
        self.opinion()
        self.assertEqual(APIClient().get(self.url).status_code, 401)
        User.objects.filter(pk=self.member.pk).update(status=User.Status.SUSPENDED)
        self.member.refresh_from_db()
        self.client.force_authenticate(user=self.member)

        self.assertEqual(len(self.get()["data"]["posts"]), 1)

    def test_item_matches_bookmark_shape_without_member_identity(self):
        self.opinion()

        [item] = self.get()["data"]["posts"]

        self.assertNotIn("badge", item)
        self.assertTrue({"userId", "email", "nickname"}.isdisjoint(item))
        self.assertEqual(item["anonymousNo"], 1)
        text = str(item)
        self.assertNotIn(self.member.email, text)
        self.assertNotIn("Private nickname", text)
