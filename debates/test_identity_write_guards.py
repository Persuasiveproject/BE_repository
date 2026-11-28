from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.api.tokens import store
from accounts.identity import issue_mock_proof
from accounts.models import APIIdempotencyRecord, UserIdentityVerification
from discussions.models import Opinion, OpinionReference
from engagement.models import Notification

from .models import DebateParticipant, Stance
from .test_b_api import DebateBFixture


class IdentityWriteGuardTests(DebateBFixture, TestCase):
    def state(self):
        self.debate.refresh_from_db()
        return (self.debate.participant_count, self.debate.opinion_count,
                DebateParticipant.objects.count(), Opinion.objects.count(),
                OpinionReference.objects.count(), Notification.objects.count(),
                APIIdempotencyRecord.objects.count())

    def assert_identity_required(self, response):
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.json(), {"error": {
            "code": "IDENTITY_VERIFICATION_REQUIRED", "message": "Verify identity to continue.",
        }})

    def test_missing_pending_failed_identity_cannot_join_or_write_even_if_already_participating(self):
        participant = DebateParticipant.objects.create(
            debate=self.debate, user=self.member, anonymous_no=1,
            initial_stance=Stance.PRO, current_stance=Stance.PRO,
        )
        parent = Opinion.objects.create(
            debate=self.debate, participant=participant, stance_at_write=Stance.PRO,
            content="Existing", content_plain="Existing",
        )
        for status in (None, "PENDING", "FAILED"):
            UserIdentityVerification.objects.filter(user=self.member).delete()
            if status:
                UserIdentityVerification.objects.create(
                    user=self.member, provider="test", request_id="request", status=status,
                    expires_at=timezone.now() + timedelta(minutes=5),
                )
            before = self.state()
            self.assert_identity_required(self.join(f"join-{status}"))
            self.assert_identity_required(self.write(f"opinion-{status}", "New opinion"))
            self.assert_identity_required(self.write(f"reply-{status}", "New reply", parentId=str(parent.pk)))
            self.assertEqual(self.state(), before)
            parent.refresh_from_db()
            self.assertEqual(parent.reply_count, 0)

    def test_replay_does_not_bypass_current_identity_requirement(self):
        self.assertEqual(self.join("successful-join").status_code, 201)
        self.assertEqual(self.write("successful-post", "Content").status_code, 201)
        UserIdentityVerification.objects.filter(user=self.member).update(status="FAILED")
        before = self.state()
        self.assert_identity_required(self.join("successful-join"))
        self.assert_identity_required(self.write("successful-post", "Content"))
        self.assertEqual(self.state(), before)

    def test_unverified_public_reads_are_available_and_account_state_is_rechecked(self):
        UserIdentityVerification.objects.filter(user=self.member).delete()
        for client in (self.client, APIClient()):
            self.assertEqual(client.get(self.detail_url).status_code, 200)
            self.assertEqual(client.get(self.posts_url).status_code, 200)
        # An authenticated object can be stale; the transaction must check the DB status.
        type(self.member).objects.filter(pk=self.member.pk).update(status="SUSPENDED")
        response = self.join()
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "FORBIDDEN"))

    @override_settings(MOCK_IDENTITY_ENABLED=True, MOCK_IDENTITY_SIGNING_KEY="write-guard-test-key",
                       IDENTITY_HASH_KEY="write-guard-test-hash")
    def test_bearer_identity_completion_unlocks_join_opinion_and_reply_with_unchanged_success_contracts(self):
        UserIdentityVerification.objects.filter(user=self.member).delete()
        token = store().create(user_id=self.member.pk)
        self.addCleanup(store().revoke, token.access_token)
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        self.assert_identity_required(self.join())
        response = self.client.post(reverse("identity-start"), {}, format="json")
        self.assertEqual(response.status_code, 201)
        request_id = response.json()["data"]["requestId"]
        proof = issue_mock_proof(user_id=self.member.pk, request_id=request_id, person_key="person")
        response = self.client.post(reverse("identity-complete"), {"requestId": request_id, "proof": proof}, format="json")
        self.assertEqual(response.status_code, 200)
        joined = self.join()
        self.assertEqual(joined.status_code, 201, joined.content)
        self.assertEqual(set(joined.json()), {"data"})
        participation = DebateParticipant.objects.get(user=self.member)
        self.assertEqual(joined.json()["data"], {
            "participantId": str(participation.pk), "anonymousNo": 1,
            "initialStance": "PRO", "currentStance": "PRO", "version": 0,
        })
        posted = self.write("opinion", "Content", references=[{"url": "https://example.com/source"}])
        self.assertEqual(posted.status_code, 201, posted.content)
        self.assertEqual(set(posted.json()), {"data"})
        data = posted.json()["data"]
        opinion = Opinion.objects.get(pk=data["postId"])
        reference = OpinionReference.objects.get(opinion=opinion)
        self.assertEqual(data, {
            "postId": str(opinion.pk), "stanceAtWrite": "PRO", "status": "VISIBLE",
            "createdAt": opinion.created_at.isoformat().replace("+00:00", "Z"),
            "references": [{"referenceId": str(reference.pk), "status": "PENDING", "url": None, "checkedAt": None}],
        })
        reply = self.write("reply", "Reply", parentId=data["postId"])
        self.assertEqual(reply.status_code, 201, reply.content)
        reply_data = reply.json()["data"]
        self.assertEqual(set(reply_data), set(data))
        self.assertTrue(reply_data["postId"].isdecimal())
        self.assertEqual(reply_data["references"], [])
        self.assertEqual(reply_data["stanceAtWrite"], "PRO")
        self.assertEqual(reply_data["status"], "VISIBLE")
        self.assertTrue(reply_data["createdAt"].endswith("Z"))
        self.assertEqual(Opinion.objects.get(pk=reply_data["postId"]).parent_id, opinion.pk)
        self.assertEqual(self.join().json(), joined.json())
        self.assertEqual(self.write("reply", "Reply", parentId=data["postId"]).json(), reply.json())
