from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from discussions.models import Opinion, OpinionVote

from .models import Debate, DebateParticipant, Stance
from .test_b_api import DebateBFixture


class ReactionFixture(DebateBFixture):
    def setUp(self):
        super().setUp()
        self.author = DebateParticipant.objects.create(
            debate=self.debate, user=self.creator, anonymous_no=1,
            initial_stance=Stance.CON, current_stance=Stance.CON,
        )
        self.post = self.opinion()
        self.url = reverse("post-reaction", args=[self.post.pk])

    def opinion(self, debate=None, participant=None, **fields):
        return Opinion.objects.create(
            debate=debate or self.debate, participant=participant or self.author,
            stance_at_write=Stance.CON, content="Reason", content_plain="Reason", **fields,
        )

    def react(self, reaction, url=None):
        return self.client.put(url or self.url, {"reaction": reaction}, format="json")

    def counts(self, post=None):
        post = post or self.post
        post.refresh_from_db()
        return post.upvote_count, post.downvote_count


class PostReactionTests(ReactionFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.join().status_code, 201)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_put_and_delete_cycle(self):
        response = self.react("UP")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"data": {"reaction": "UP", "upvoteCount": 1, "downvoteCount": 0}})

        self.assertEqual(self.react("UP").json()["data"], {"reaction": "UP", "upvoteCount": 1, "downvoteCount": 0})

        self.assertEqual(self.react("DOWN").json()["data"], {"reaction": "DOWN", "upvoteCount": 0, "downvoteCount": 1})
        self.assertEqual(OpinionVote.objects.get().vote_type, "DOWN")

        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.counts(), (0, 0))
        self.assertFalse(OpinionVote.objects.exists())

        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertEqual(self.counts(), (0, 0))

    def test_reply_reaction(self):
        reply = self.opinion(parent=self.post, root_id=self.post.pk)
        url = reverse("post-reaction", args=[reply.pk])

        self.assertEqual(self.react("DOWN", url).json()["data"]["downvoteCount"], 1)
        self.assertEqual(self.counts(reply), (0, 1))
        self.assertEqual(self.counts(), (0, 0))

    def test_auth_and_membership(self):
        guest = APIClient()
        self.assertEqual(guest.put(self.url, {"reaction": "UP"}, format="json").status_code, 401)

        outsider = User.objects.create_user("outsider@example.com", "Outsider")
        self.client.force_authenticate(outsider)
        self.assert_error(self.react("UP"), 403, "NOT_PARTICIPANT")
        self.assert_error(self.client.delete(self.url), 403, "NOT_PARTICIPANT")

        outsider.status = User.Status.SUSPENDED
        outsider.save()
        self.assertEqual(self.react("UP").status_code, 403)

        self.client.force_authenticate(self.creator)
        self.assert_error(self.react("UP"), 403, "OWN_POST_REACTION")
        self.assertEqual(self.counts(), (0, 0))

    def test_suspended_member_forbidden(self):
        self.member.status = User.Status.SUSPENDED
        self.member.save()

        self.assertEqual(self.react("UP").status_code, 403)
        self.assertEqual(self.client.delete(self.url).status_code, 403)

    def test_post_not_found(self):
        deleted = self.opinion(status=Opinion.Status.DELETED)
        draft = Debate.objects.create(
            creator=self.creator, category=self.debate.category, type=Debate.Type.QUICK, title="Draft",
        )
        draft_author = DebateParticipant.objects.create(
            debate=draft, user=self.creator, anonymous_no=1,
            initial_stance=Stance.CON, current_stance=Stance.CON,
        )
        draft_post = self.opinion(debate=draft, participant=draft_author)
        for post_id in (999999, deleted.pk, draft_post.pk):
            self.assert_error(self.react("UP", reverse("post-reaction", args=[post_id])), 404, "POST_NOT_FOUND")

    def test_invalid_input(self):
        for reaction in ("up", 1, None, "SIDEWAYS"):
            self.assert_error(self.react(reaction), 400, "VALIDATION_ERROR")
        self.assert_error(self.react("UP", f"/v1/posts/{2**63}/reaction"), 400, "VALIDATION_ERROR")
        self.assertFalse(OpinionVote.objects.exists())

    def test_closed_debate(self):
        self.react("UP")
        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.CLOSED)

        self.assert_error(self.react("DOWN"), 409, "DEBATE_NOT_OPEN")
        self.assert_error(self.client.delete(self.url), 409, "DEBATE_NOT_OPEN")
        self.assertEqual(self.counts(), (1, 0))


class PostReactionConcurrencyTests(ReactionFixture, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_up_counts_once(self):
        self.assertEqual(self.join().status_code, 201)
        ready = Barrier(2)

        def submit(_):
            client = APIClient()
            client.force_authenticate(self.member)
            try:
                ready.wait(timeout=10)
                return client.put(self.url, {"reaction": "UP"}, format="json").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(submit, range(2)))

        self.assertEqual(statuses, [200, 200])
        self.assertEqual(self.counts(), (1, 0))
        self.assertEqual(OpinionVote.objects.count(), 1)
