from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import APIIdempotencyRecord, User, UserIdentityVerification
from discussions.models import Opinion, OpinionReference
from economy.models import Badge, UserBadge

from .models import Category, Debate, DebateImage, DebateParticipant, Stance


class DebateBFixture:
    def setUp(self):
        self.member = User.objects.create_user("member@example.com", "Private nickname")
        self.creator = User.objects.create_user("creator@example.com", "Creator")
        self.now = timezone.now()
        UserIdentityVerification.objects.create(
            user=self.member, provider="test", request_id="identity-test",
            status=UserIdentityVerification.Status.VERIFIED, expires_at=self.now + timedelta(minutes=5),
            verified_at=self.now,
        )
        self.debate = Debate.objects.create(
            creator=self.creator, category=Category.objects.create(name="Society"), type=Debate.Type.QUICK,
            title="Debate", content="Topic", duration_minutes=10, started_at=self.now,
            ends_at=self.now + timedelta(minutes=10), status=Debate.Status.OPEN,
        )
        self.detail_url = reverse("debate-detail", args=[self.debate.pk])
        self.join_url = reverse("debate-join", args=[self.debate.pk])
        self.posts_url = reverse("debate-posts", args=[self.debate.pk])
        self.client = APIClient()
        self.client.force_authenticate(user=self.member)

    def join(self, key="join", stance=Stance.PRO):
        return self.client.post(self.join_url, {"stance": stance}, format="json", headers={"Idempotency-Key": key})

    def write(self, key, content, **fields):
        return self.client.post(
            self.posts_url, {"content": content, **fields}, format="json", headers={"Idempotency-Key": key},
        )


class DebateBApiTests(DebateBFixture, TestCase):
    def test_public_detail_only_exposes_ready_images_and_total_participants(self):
        ready = DebateImage.objects.create(
            debate=self.debate, status=DebateImage.Status.READY, image_url="https://example.com/ready.png",
        )
        DebateImage.objects.create(
            debate=self.debate, status=DebateImage.Status.REJECTED,
            image_url="https://example.com/rejected.png", rejection_reason="Unsafe image",
        )
        response = APIClient().get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(set(data), {"debate", "images", "participantCount", "myParticipation"})
        self.assertEqual(data["debate"]["id"], str(self.debate.pk))
        self.assertEqual(data["participantCount"], 0)
        self.assertIsNone(data["myParticipation"])
        self.assertEqual(data["images"], [{
            "assetId": str(ready.pk), "status": "READY", "imageUrl": ready.image_url,
            "sortOrder": 0, "rejectionReason": None,
        }])

    def test_draft_detail_is_creator_only_and_includes_asset_status(self):
        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.DRAFT, started_at=None, ends_at=None)
        DebateImage.objects.create(
            debate=self.debate, status=DebateImage.Status.REJECTED, rejection_reason="Unsafe image",
        )
        self.assertEqual(self.client.get(self.detail_url).json()["error"]["code"], "DRAFT_NOT_ACCESSIBLE")
        self.client.force_authenticate(user=self.creator)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["images"][0]["rejectionReason"], "Unsafe image")

    def test_join_is_visible_on_detail_and_retry_does_not_create_another_row(self):
        response = self.join()
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertEqual(set(data), {"participantId", "anonymousNo", "initialStance", "currentStance", "version"})
        self.assertEqual((data["anonymousNo"], data["initialStance"], data["currentStance"], data["version"]),
                         (1, "PRO", "PRO", 0))
        self.assertEqual(self.join().json(), response.json())
        self.assertEqual(self.join(stance=Stance.CON).json()["error"]["code"], "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual(self.join(key="different-key").json()["error"]["code"], "ALREADY_PARTICIPATING")
        detail = self.client.get(self.detail_url).json()["data"]
        self.assertEqual(detail["myParticipation"], data)
        self.assertEqual(detail["participantCount"], 1)
        self.assertEqual(DebateParticipant.objects.count(), 1)

    def test_join_requires_verified_identity_and_a_valid_key(self):
        self.client.force_authenticate(user=self.creator)
        self.assertEqual(self.join().json()["error"]["code"], "IDENTITY_VERIFICATION_REQUIRED")
        response = self.client.post(self.join_url, {"stance": Stance.PRO}, format="json")
        self.assertEqual(response.json()["error"]["code"], "IDEMPOTENCY_KEY_INVALID")
        self.assertFalse(DebateParticipant.objects.exists())
        self.assertFalse(APIIdempotencyRecord.objects.exists())

    def test_elapsed_deadline_closes_detail_and_blocks_new_writes_before_worker_runs(self):
        self.assertEqual(self.join().status_code, 201)
        Debate.objects.filter(pk=self.debate.pk).update(ends_at=self.now - timedelta(seconds=1))
        data = self.client.get(self.detail_url).json()["data"]["debate"]
        self.assertEqual(data["status"], "CLOSED")
        self.assertEqual(data["closedAt"], data["endsAt"])
        self.assertEqual(self.join("after-close").json()["error"]["code"], "DEBATE_NOT_OPEN")
        self.assertEqual(self.write("late-post", "Late").json()["error"]["code"], "DEBATE_NOT_OPEN")
        self.assertFalse(Opinion.objects.exists())

    def test_post_uses_written_stance_and_badge_and_replays_original_response(self):
        self.assertEqual(self.join().status_code, 201)
        badge = Badge.objects.create(name="Debater", icon_url="https://example.com/badge.png", required_credit=40)
        awarded = UserBadge.objects.create(user=self.member, badge=badge, is_representative=True)
        request = {"references": [{"url": "https://example.com/evidence"}]}
        response = self.write("post", "Reason", **request)
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertEqual(data["references"][0]["status"], "PENDING")
        self.assertIsNone(data["references"][0]["url"])

        DebateParticipant.objects.filter(user=self.member).update(current_stance=Stance.CON)
        UserBadge.objects.filter(pk=awarded.pk).update(is_representative=False)
        self.assertEqual(self.write("post", "Reason", **request).json(), response.json())
        self.assertEqual(self.write("post", "Different").json()["error"]["code"], "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual(Opinion.objects.count(), 1)
        self.assertEqual(OpinionReference.objects.count(), 1)
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.opinion_count, 1)

        card = APIClient().get(self.posts_url).json()["data"]["posts"][0]
        self.assertEqual(card["stanceAtWrite"], "PRO")
        self.assertEqual(card["anonymousNo"], 1)
        self.assertEqual(card["badge"]["badgeId"], str(badge.pk))
        self.assertNotIn("userId", card)
        self.assertNotIn("nickname", card)
        self.assertIsNone(card["references"][0]["url"])
        OpinionReference.objects.update(safe_browsing_status=OpinionReference.SafetyStatus.SAFE)
        card = APIClient().get(self.posts_url).json()["data"]["posts"][0]
        self.assertEqual(card["references"][0]["url"], "https://example.com/evidence")

    def test_thread_returns_direct_replies_and_preserves_root_across_nesting(self):
        self.assertEqual(self.join().status_code, 201)
        root = self.write("root", "Root").json()["data"]["postId"]
        reply = self.write("reply", "Reply", parentId=root).json()["data"]["postId"]
        nested = self.write("nested", "Nested", parentId=reply).json()["data"]["postId"]
        root_card, = self.client.get(self.posts_url).json()["data"]["posts"]
        self.assertEqual((root_card["postId"], root_card["replyCount"]), (root, 1))
        parent_card, = self.client.get(reverse("post-thread", args=[root])).json()["data"]["replies"]
        child_card, = self.client.get(reverse("post-thread", args=[reply])).json()["data"]["replies"]
        self.assertEqual((parent_card["postId"], parent_card["rootId"], parent_card["replyCount"]), (reply, root, 1))
        self.assertEqual((child_card["postId"], child_card["parentId"], child_card["rootId"]), (nested, reply, root))
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.opinion_count, 1)

    def test_post_rejects_nonparticipant_wrong_debate_parent_and_invalid_input(self):
        self.assertEqual(self.write("nonparticipant", "Reason").json()["error"]["code"], "NOT_PARTICIPANT")
        self.assertEqual(self.join().status_code, 201)
        other_debate = Debate.objects.create(
            creator=self.creator, category=self.debate.category, type=Debate.Type.QUICK, title="Other debate",
        )
        author = DebateParticipant.objects.create(
            debate=other_debate, user=self.creator, anonymous_no=1, initial_stance=Stance.CON,
            current_stance=Stance.CON,
        )
        parent = Opinion.objects.create(
            debate=other_debate, participant=author, stance_at_write=Stance.CON,
            content="Other opinion", content_plain="Other opinion",
        )
        result = self.write("wrong-parent", "Reply", parentId=str(parent.pk))
        self.assertEqual(result.json()["error"]["code"], "PARENT_DEBATE_MISMATCH")
        for content in ("   ", "x" * 1001, 12):
            with self.subTest(content=str(content)[:10]):
                self.assertEqual(self.write("invalid", content).status_code, 400)
        self.assertEqual(self.write("invalid-url", "Reason", references=[{"url": "ftp://example.com/a"}]).status_code, 400)
        self.assertFalse(Opinion.objects.filter(debate=self.debate).exists())

    def test_cursor_is_stable_for_equal_timestamps_and_scoped_to_stance_filter(self):
        self.assertEqual(self.join().status_code, 201)
        participant = DebateParticipant.objects.get(user=self.member)
        rows = Opinion.objects.bulk_create([
            Opinion(debate=self.debate, participant=participant, stance_at_write=Stance.PRO,
                    content=f"Opinion {number}", content_plain=f"Opinion {number}")
            for number in range(21)
        ])
        Opinion.objects.update(created_at=self.now)
        first = self.client.get(self.posts_url, {"stance": "PRO"}).json()
        cursor = first["meta"]["nextCursor"]
        self.assertEqual(len(first["data"]["posts"]), 20)
        Opinion.objects.create(
            debate=self.debate, participant=participant, stance_at_write=Stance.PRO,
            content="Inserted later", content_plain="Inserted later",
        )
        second = self.client.get(self.posts_url, {"stance": "PRO", "cursor": cursor}).json()
        self.assertEqual([post["postId"] for post in second["data"]["posts"]], [str(rows[0].pk)])
        self.assertIsNone(second["meta"]["nextCursor"])
        self.assertEqual(self.client.get(self.posts_url, {"cursor": cursor}).json()["error"]["code"], "CURSOR_INVALID")


class DebateBConcurrencyTests(DebateBFixture, TransactionTestCase):
    def submit_concurrently(self, url, data, keys):
        ready = Barrier(2)

        def submit(key):
            try:
                client = APIClient()
                client.force_authenticate(user=self.member)
                ready.wait(timeout=10)
                response = client.post(url, data, format="json", headers={"Idempotency-Key": key})
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(submit, keys))

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_join_with_different_keys_creates_one_participant(self):
        responses = self.submit_concurrently(self.join_url, {"stance": Stance.PRO}, ["first", "second"])
        self.assertCountEqual([status for status, _ in responses], [201, 409])
        failure = next(body for status, body in responses if status == 409)
        self.assertEqual(failure["error"]["code"], "ALREADY_PARTICIPATING")
        self.assertEqual(DebateParticipant.objects.count(), 1)
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.participant_count, 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_post_retry_replays_one_response(self):
        self.assertEqual(self.join().status_code, 201)
        first, second = self.submit_concurrently(self.posts_url, {"content": "Reason"}, ["same", "same"])
        self.assertEqual(first[0], 201)
        self.assertEqual(first, second)
        self.assertEqual(Opinion.objects.count(), 1)
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.opinion_count, 1)
