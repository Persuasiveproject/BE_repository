from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import APIIdempotencyRecord, User
from discussions.models import Opinion

from .models import Debate, DebateParticipant, Stance
from .test_b_api import DebateBFixture


class PostSpamTests(DebateBFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.join().status_code, 201)
        self.other_debate = Debate.objects.create(
            creator=self.creator, category=self.debate.category, type=Debate.Type.QUICK,
            title="Other", content="Topic", duration_minutes=10, started_at=self.now,
            ends_at=self.now + timedelta(minutes=10), status=Debate.Status.OPEN,
        )
        DebateParticipant.objects.create(
            debate=self.other_debate, user=self.member, anonymous_no=1,
            initial_stance=Stance.PRO, current_stance=Stance.PRO,
        )
        self.other_url = reverse("debate-posts", args=[self.other_debate.pk])

    def write_other(self, key, content):
        return self.client.post(
            self.other_url, {"content": content}, format="json", headers={"Idempotency-Key": key},
        )

    def assert_limited(self, response, reason):
        self.assertEqual(response.status_code, 429, response.content)
        self.assertEqual(response.json()["error"], {
            "code": "POST_RATE_LIMITED", "message": "Too many posts. Try again shortly.",
            "details": {"reason": reason},
        })

    def state(self):
        self.debate.refresh_from_db()
        return Opinion.objects.count(), self.debate.opinion_count, APIIdempotencyRecord.objects.count()

    def age_posts(self, **delta):
        Opinion.objects.update(created_at=timezone.now() - timedelta(**delta))

    def test_eleventh_post_in_a_minute_is_blocked(self):
        for index in range(10):
            self.assertEqual(self.write(f"post-{index}", f"Reason {index}").status_code, 201)
        before = self.state()

        self.assert_limited(self.write("post-10", "Reason 10"), "RATE")

        self.assertEqual(self.state(), before)

    def test_allowed_again_after_window(self):
        for index in range(10):
            self.write(f"post-{index}", f"Reason {index}")
        self.age_posts(seconds=61)

        self.assertEqual(self.write("later", "Later reason").status_code, 201)

    def test_posts_in_other_debates_count(self):
        for index in range(5):
            self.assertEqual(self.write(f"a-{index}", f"A {index}").status_code, 201)
            self.assertEqual(self.write_other(f"b-{index}", f"B {index}").status_code, 201)

        self.assert_limited(self.write("a-5", "A 5"), "RATE")

    def test_other_members_posts_do_not_count(self):
        other = User.objects.create_user("other@example.com", "Other")
        author = DebateParticipant.objects.create(
            debate=self.debate, user=other, anonymous_no=50,
            initial_stance=Stance.CON, current_stance=Stance.CON,
        )
        for index in range(10):
            Opinion.objects.create(
                debate=self.debate, participant=author, stance_at_write=Stance.CON,
                content="Same reason", content_plain="Same reason",
            )

        self.assertEqual(self.write("mine", "Same reason").status_code, 201)

    def test_duplicate_content_in_same_debate(self):
        self.assertEqual(self.write("first", "Same   reason here").status_code, 201)
        before = self.state()

        self.assert_limited(self.write("second", "  Same reason\n here  "), "DUPLICATE")
        self.assertEqual(self.state(), before)

    def test_duplicate_allowed_in_other_debate_or_after_window(self):
        self.assertEqual(self.write("first", "Same reason").status_code, 201)
        self.assertEqual(self.write_other("other", "Same reason").status_code, 201)

        self.age_posts(minutes=10, seconds=1)
        self.assertEqual(self.write("later", "Same reason").status_code, 201)

    def test_reply_follows_same_rules(self):
        parent = self.write("parent", "Root reason").json()["data"]["postId"]
        self.assertEqual(self.write("reply", "Reply text", parentId=parent).status_code, 201)

        self.assert_limited(self.write("reply-again", "Reply  text", parentId=parent), "DUPLICATE")

        for index in range(8):
            self.assertEqual(self.write(f"r-{index}", f"Reply {index}", parentId=parent).status_code, 201)
        self.assert_limited(self.write("r-8", "Reply 8", parentId=parent), "RATE")

    def test_same_key_replays_instead_of_blocking(self):
        first = self.write("same-key", "Retry reason")
        self.assertEqual(first.status_code, 201)

        retry = self.write("same-key", "Retry reason")

        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry.json(), first.json())
        self.assertEqual(Opinion.objects.count(), 1)

    def test_same_key_replays_even_after_rate_limit(self):
        first = self.write("key-0", "Reason 0")
        for index in range(1, 10):
            self.write(f"key-{index}", f"Reason {index}")

        self.assertEqual(self.write("key-0", "Reason 0").json(), first.json())
