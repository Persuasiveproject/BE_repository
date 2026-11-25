from datetime import datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.api.phone import phone_id, register_phone_user
from accounts.api.tokens import store
from accounts.models import User, WithdrawnIdentity
from accounts.tasks import purge_expired_withdrawn_identities
from discussions.models import Opinion, OpinionReference, Persuasion
from economy.models import CoinTransaction, CreditHistory
from engagement.models import Notification
from moderation.models import Report

from .models import Category, Debate, DebateParticipant, Stance
from .results import save_debate_statistics


class P3CIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("p3@example.com", "Member", credit=32)
        self.category = Category.objects.create(name="P3")
        self.now = timezone.now()
        self.debate = Debate.objects.create(
            creator=self.user, category=self.category, title="P3", content="body", type=Debate.Type.QUICK,
            duration_minutes=10, status=Debate.Status.OPEN, started_at=self.now - timedelta(minutes=5),
            ends_at=self.now + timedelta(minutes=5),
        )

    def join(self, user, *, stance=Stance.PRO):
        return DebateParticipant.objects.create(
            debate=self.debate, user=user, anonymous_no=DebateParticipant.objects.filter(debate=self.debate).count() + 1,
            initial_stance=stance, current_stance=stance,
        )

    def post(self, participant, *, parent=None):
        return Opinion.objects.create(
            debate=self.debate, participant=participant, stance_at_write=participant.current_stance,
            content="private body", content_plain="private body", parent=parent, root=parent,
        )

    def close(self):
        self.debate.status = Debate.Status.CLOSED
        self.debate.closed_at = self.now
        self.debate.save(update_fields=["status", "closed_at"])
        return save_debate_statistics(self.debate)

    def test_trend_counts_new_participants_per_bucket_without_revealing_stance(self):
        first = datetime(2026, 11, 25, 9, 10, 0, tzinfo=utc_timezone.utc)
        self.debate.started_at = first
        self.debate.ends_at = first + timedelta(minutes=10)
        self.debate.save(update_fields=["started_at", "ends_at"])
        for index, seconds in enumerate((12, 59, 65)):
            user = User.objects.create_user(f"trend{index}@example.com", "Member")
            participant = self.join(user, stance=Stance.CON)
            DebateParticipant.objects.filter(pk=participant.pk).update(joined_at=first + timedelta(seconds=seconds))
        url = reverse("debate-trend", args=[self.debate.pk])
        with patch("debates.api.timing.trend.timezone.now", return_value=first + timedelta(minutes=3)):
            quick = self.client.get(url).json()["data"]["points"]
            self.assertEqual([(p["recordedAt"], p["total"]) for p in quick], [
                ("2026-11-25T09:10:00Z", 2), ("2026-11-25T09:11:00Z", 1),
            ])
            for point in quick:
                self.assertEqual((point["pro"], point["con"], point["neutral"]), (None, None, None))
            Debate.objects.filter(pk=self.debate.pk).update(type=Debate.Type.ENDLESS)
            self.assertEqual(self.client.get(url).json()["data"]["points"][0]["total"], 3)
            Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.CANCELED)
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_consent_withdrawal_changes_already_published_statistics(self):
        self.join(self.user)
        for index in range(19):
            self.join(User.objects.create_user(f"result{index}@example.com", "Member"))
        self.client.force_authenticate(self.user)
        url = reverse("me-demographic")
        body = {"gender": "F", "birthYear": timezone.localdate().year - 25,
                "region": "서울", "statisticsConsent": True}
        self.assertEqual(self.client.put(url, {**body, "region": "서울 강남구"}, format="json").status_code, 400)
        self.assertEqual(self.client.put(url, {**body, "birthYear": True}, format="json").status_code, 400)
        saved = self.client.put(url, body, format="json")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["data"]["demographic"]["region"], "서울특별시")
        self.user.refresh_from_db()
        self.assertEqual(self.client.get(reverse("me")).json()["data"]["demographic"],
                         saved.json()["data"]["demographic"])
        result = self.close()
        result_url = f"/v1/debates/{self.debate.pk}/result"
        data = self.client.get(result_url).json()["data"]
        self.assertEqual(data["counts"]["totalParticipants"], 20)
        self.assertEqual(data["genderData"][0], {"label": "F", "count": 1, "percentage": 5.0})
        self.assertEqual(data["ageData"][0]["label"], "20대")
        self.assertEqual(data["regionData"][0]["label"], "서울특별시")
        self.assertEqual(self.client.delete(reverse("me-demographic-consent")).status_code, 204)
        self.user.refresh_from_db()
        changed_at = self.user.statistics_consent_updated_at
        self.assertEqual(self.client.delete(reverse("me-demographic-consent")).status_code, 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.statistics_consent_updated_at, changed_at)
        self.assertEqual(self.user.birth_year, body["birthYear"])
        revised = self.client.get(result_url).json()["data"]
        for key in ("genderData", "ageData", "regionData"):
            self.assertEqual(revised[key], [{"label": "미응답", "count": 20, "percentage": 100.0}])
        result.refresh_from_db()
        self.assertEqual(result.total_participants, 20)

    def test_under_twenty_hides_distribution_and_changes_but_preserves_counts(self):
        self.join(self.user)
        self.close()
        response = self.client.get(f"/v1/debates/{self.debate.pk}/result")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["counts"]["totalParticipants"], 1)
        self.assertIsNone(data["counts"]["stanceChangedCount"])
        self.assertEqual(data["sankeyData"], [])
        for key in ("genderData", "ageData", "regionData"):
            self.assertIsNone(data[key])

    def test_withdrawal_invalidates_all_tokens_scrubs_posts_and_restores_rejoin_state(self):
        number = "01012345678"
        self.user.provider = "PHONE"
        self.user.provider_id = phone_id(number)
        self.user.status = User.Status.SUSPENDED
        self.user.save(update_fields=["provider", "provider_id", "status"])
        participant = self.join(self.user)
        other = self.join(User.objects.create_user("reply@example.com", "Other"))
        self.debate.participant_count = 2
        self.debate.save(update_fields=["participant_count"])
        post = self.post(participant)
        reply = self.post(other, parent=post)
        reference = OpinionReference.objects.create(opinion=post, url="https://example.com/private")
        tokens = [store().create(user_id=self.user.pk) for _ in range(2)]
        bearer = APIClient()
        bearer.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens[0].access_token}")
        self.assertEqual(bearer.delete(reverse("me")).status_code, 204)
        for token in tokens:
            bearer.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
            self.assertEqual(bearer.get(reverse("me")).status_code, 401)
            self.assertEqual(bearer.post(reverse("auth-refresh"), {"refreshToken": token.refresh_token},
                                         format="json").status_code, 401)
        self.user.refresh_from_db()
        post.refresh_from_db()
        reference.refresh_from_db()
        self.assertEqual((self.user.email, self.user.provider_id, self.user.status), ("", None, User.Status.DELETED))
        self.assertEqual((post.content, post.content_plain, post.status, reference.url), ("", "", "DELETED", ""))
        self.assertEqual(self.client.get(f"/v1/debates/{self.debate.pk}").json()["data"]["participantCount"], 1)
        posts = self.client.get(f"/v1/debates/{self.debate.pk}/posts").json()["data"]["posts"]
        self.assertEqual((posts[0]["status"], posts[0]["content"], posts[0]["references"]), ("DELETED", "", []))
        replies = self.client.get(f"/v1/posts/{post.pk}/thread").json()["data"]["replies"]
        self.assertEqual(replies[0]["postId"], str(reply.pk))
        rejoined, created = register_phone_user(number=number, nickname="Back")
        self.assertTrue(created)
        self.assertNotEqual(rejoined.pk, self.user.pk)
        self.assertEqual((rejoined.credit, rejoined.status, rejoined.coin_balance), (Decimal("32"), "SUSPENDED", 0))
        self.assertFalse(CoinTransaction.objects.filter(user=rejoined).exists())
        self.assertFalse(WithdrawnIdentity.objects.exists())

    def test_expired_rejoin_state_is_purged_and_new_account_starts_at_thirty(self):
        number = "01022223333"
        original, _ = register_phone_user(number=number, nickname="Original")
        User.objects.filter(pk=original.pk).update(credit=Decimal("10"), status=User.Status.SUSPENDED)
        self.client.force_authenticate(original)
        self.assertEqual(self.client.delete(reverse("me")).status_code, 204)
        WithdrawnIdentity.objects.update(expires_at=self.now - timedelta(seconds=1))
        self.assertEqual(purge_expired_withdrawn_identities(), 1)
        rejoined, _ = register_phone_user(number=number, nickname="Back")
        self.assertEqual((rejoined.credit, rejoined.status), (Decimal("30"), "ACTIVE"))

    def test_admin_violation_reclaims_only_that_posts_share_and_penalizes_once(self):
        participant = self.join(self.user)
        first, second = self.post(participant), self.post(participant)
        reporter = User.objects.create_user("reporter@example.com", "Reporter")
        for post, to_stance in ((first, Stance.PRO), (second, Stance.NEUTRAL)):
            Persuasion.objects.create(opinion=post, persuaded_user=reporter, from_stance=Stance.CON,
                                      to_stance=to_stance, reward_given=True)
        CreditHistory.objects.create(user=self.user, amount=Decimal("0.75"), reason="PERSUASION_REWARD",
                                     ref_id=self.debate.pk)
        reports = [Report.objects.create(reporter=reporter, opinion=first, reason="Rules") for _ in range(2)]
        operator = User.objects.create_user("admin@example.com", "Admin", role=User.Role.ADMIN)
        self.client.force_login(operator)
        url = reverse("admin:moderation_report_changelist")
        for report in reports:
            result = self.client.post(url, {"action": "accept_mild", "_selected_action": [report.pk], "index": 0})
            self.assertEqual(result.status_code, 302)
            report.refresh_from_db()
            self.assertEqual(report.status, Report.Status.ACCEPTED)
        self.user.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.user.credit, Decimal("31"))
        self.assertEqual((first.status, second.status), (Opinion.Status.FILTERED, Opinion.Status.VISIBLE))
        self.assertEqual(list(CreditHistory.objects.filter(ref_id=first.pk).exclude(reason="PERSUASION_REWARD")
                              .order_by("id").values_list("amount", flat=True)), [Decimal("-0.5"), Decimal("-0.5")])

    def test_cancel_notifies_only_live_participants_and_retry_does_not_duplicate(self):
        self.join(self.user)
        self.join(User.objects.create_user("deleted@example.com", "Deleted", status=User.Status.DELETED))
        operator = User.objects.create_user("operator@example.com", "Operator", role=User.Role.ADMIN)
        self.client.force_authenticate(operator)
        url = reverse("debate-cancel", args=[self.debate.pk])
        self.assertEqual(self.client.post(url, {"refund": False}, format="json").status_code, 200)
        self.assertEqual(self.client.post(url, {"refund": False}, format="json").status_code, 409)
        notification = Notification.objects.get(type=Notification.Type.DEBATE_CANCELED)
        self.assertEqual((notification.user_id, notification.target_id), (self.user.pk, self.debate.pk))
        self.client.force_authenticate(self.user)
        data = self.client.get("/v1/me/notifications").json()["data"]
        self.assertEqual(data["notifications"][0]["type"], "DEBATE_CANCELED")
