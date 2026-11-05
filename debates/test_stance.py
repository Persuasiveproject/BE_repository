import hashlib
import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import APIIdempotencyRecord, User
from discussions.models import Opinion, Persuasion

from .api_contract import ContractError
from .models import Category, Debate, DebateParticipant, Stance, StanceHistory
from .stance import change_stance


class StanceFixture:
    def setUp(self):
        self.user = User.objects.create_user("member@example.com", "Member")
        self.other = User.objects.create_user("other@example.com", "Other")
        self.category = Category.objects.create(name="Society")
        self.debate = Debate.objects.create(
            creator=self.other, category=self.category, type=Debate.Type.QUICK,
            title="Debate", content="Topic", started_at=timezone.now(), status=Debate.Status.OPEN,
        )
        self.participant = self.join(self.user, 1, Stance.PRO)
        self.author = self.join(self.other, 2, Stance.CON)
        self.opinion = self.write_opinion(self.author)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def join(self, user, number, stance):
        return DebateParticipant.objects.create(
            debate=self.debate, user=user, anonymous_no=number,
            initial_stance=stance, current_stance=stance,
        )

    def write_opinion(self, participant, **kwargs):
        return Opinion.objects.create(
            debate=self.debate, participant=participant,
            stance_at_write=participant.current_stance, content="Reason",
            content_plain="Reason", **kwargs,
        )

    def patch_stance(self, *, key="test-stance", **overrides):
        data = {"stance": Stance.CON, "opinionId": str(self.opinion.id), "version": 0}
        data.update(overrides)
        headers = {"Idempotency-Key": key} if key is not None else {}
        return self.client.patch(
            reverse("my-stance", args=[self.debate.id]), data, format="json", headers=headers,
        )


class ChangeStanceServiceTests(StanceFixture, TestCase):
    def test_rejects_own_and_hidden_opinions_without_creating_history(self):
        own = self.write_opinion(self.participant)
        hidden = self.write_opinion(self.author, status=Opinion.Status.FILTERED)

        for opinion in (own, hidden):
            with self.subTest(opinion=opinion.pk), self.assertRaises(ContractError):
                change_stance(
                    debate_id=self.debate.id, user=self.user, stance=Stance.CON,
                    opinion_id=opinion.id, version=0, idempotency_key="invalid-opinion",
                    request_path=reverse("my-stance", args=[self.debate.id]),
                )
        self.assertFalse(StanceHistory.objects.exists())
        self.assertFalse(Persuasion.objects.exists())


class ChangeStanceAPITests(StanceFixture, TestCase):
    def test_changes_stance_and_rejects_a_stale_retry(self):
        original = self.write_opinion(self.participant)
        state = self.client.get(reverse("my-stance", args=[self.debate.id]))
        self.assertEqual(state.data["version"], 0)

        response = self.patch_stance()
        self.assertEqual(response.status_code, 200)
        original.refresh_from_db()
        self.assertEqual(original.stance_at_write, Stance.PRO)
        history = StanceHistory.objects.get(participant=self.participant)
        self.assertEqual(history.persuasion.opinion_id, self.opinion.id)
        self.assertEqual(response.data, {"data": {
            "currentStance": Stance.CON, "eventId": str(history.pk),
            "persuasionId": str(history.persuasion_id), "reward": None,
        }})
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.initial_stance, Stance.PRO)

        conflict = self.patch_stance(key="another-device", stance=Stance.NEUTRAL)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["error"]["code"], "STANCE_CONFLICT")
        self.assertEqual(conflict.data["error"]["details"]["version"], 1)
        self.assertEqual(StanceHistory.objects.count(), 1)
        self.assertEqual(Persuasion.objects.count(), 1)

    def test_rejects_expired_debate_before_worker_closes_it(self):
        self.debate.ends_at = timezone.now()
        self.debate.save(update_fields=["ends_at"])

        response = self.patch_stance()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "DEBATE_NOT_OPEN")
        self.assertFalse(StanceHistory.objects.exists())

    def test_idempotency_replays_success_and_rejects_different_payload(self):
        first = self.patch_stance(key="mobile-1")
        repeated = self.patch_stance(key="mobile-1")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data, first.data)
        conflict = self.patch_stance(key="mobile-1", stance=Stance.NEUTRAL)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data["error"]["code"], "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual(StanceHistory.objects.count(), 1)

    def test_legacy_cached_response_cannot_replay_or_mutate(self):
        APIIdempotencyRecord.objects.create(
            user=self.user,
            http_method="PATCH",
            request_path=reverse("my-stance", args=[self.debate.id]),
            idempotency_key="legacy",
            request_hash=hashlib.sha256(json.dumps([Stance.CON, self.opinion.pk, 0]).encode()).hexdigest(),
            status=APIIdempotencyRecord.Status.COMPLETED,
            response_status=200,
            response_body={"initialStance": "PRO", "currentStance": "CON", "version": 1},
            expires_at=timezone.now() + timedelta(hours=1),
        )
        response = self.patch_stance(key="legacy")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "IDEMPOTENCY_KEY_REUSED")
        self.assertFalse(StanceHistory.objects.exists())

    def test_overlong_path_cannot_reach_idempotency_storage(self):
        response = self.client.patch(
            f"/v1/debates/{'0' * 240}{self.debate.pk}/me/stance",
            {"stance": "CON"}, format="json", headers={"Idempotency-Key": "overlong"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(APIIdempotencyRecord.objects.exists())

    def test_suspended_member_cannot_use_participation_endpoints(self):
        self.user.status = User.Status.SUSPENDED
        self.user.save(update_fields=["status"])
        responses = (
            self.patch_stance(),
            self.client.get(reverse("my-stance", args=[self.debate.id])),
            self.client.get(reverse("my-stance-events", args=[self.debate.id])),
        )
        for response in responses:
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data["error"]["code"], "FORBIDDEN")

    def test_draft_is_hidden_and_canceled_debate_cannot_be_changed(self):
        self.debate.status = Debate.Status.DRAFT
        self.debate.save(update_fields=["status"])
        self.assertEqual(self.patch_stance().status_code, 404)
        self.debate.status = Debate.Status.CANCELED
        self.debate.save(update_fields=["status"])
        result = self.patch_stance()
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.data["error"]["code"], "DEBATE_NOT_OPEN")
