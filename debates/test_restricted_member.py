from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from discussions.models import Opinion

from .models import DebateParticipant, Stance
from .test_b_api import DebateBFixture


class RestrictedMemberTests(DebateBFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.join().status_code, 201)
        author = DebateParticipant.objects.create(
            debate=self.debate, user=self.creator, anonymous_no=2,
            initial_stance=Stance.CON, current_stance=Stance.CON,
        )
        self.other_post = Opinion.objects.create(
            debate=self.debate, participant=author, stance_at_write=Stance.CON,
            content="Reason", content_plain="Reason",
        )

    def suspend(self):
        User.objects.filter(pk=self.member.pk).update(status=User.Status.SUSPENDED)
        self.member.refresh_from_db()
        self.client.force_authenticate(user=self.member)

    def writes(self, client):
        post_id = self.other_post.pk
        key = {"Idempotency-Key": "restricted"}
        return {
            "post": lambda: client.post(self.posts_url, {"content": "Hi"}, format="json", headers=key),
            "reaction": lambda: client.put(reverse("post-reaction", args=[post_id]), {"reaction": "UP"}, format="json"),
            "reference": lambda: client.post(
                reverse("post-references", args=[post_id]), {"url": "https://example.com/a"}, format="json",
            ),
            "bookmark": lambda: client.put(reverse("post-bookmark", args=[post_id])),
            "join": lambda: client.post(self.join_url, {"stance": Stance.PRO}, format="json", headers=key),
            "stance": lambda: client.patch(
                reverse("my-stance", args=[self.debate.pk]), {"stance": Stance.CON}, format="json", headers=key,
            ),
        }

    def reads(self, client):
        return {
            name: lambda url=url: client.get(url) for name, url in {
                "bookmarks": reverse("bookmark-list"),
                "joined": reverse("joined-debate-list"),
                "credits": reverse("me-credits"),
                "badges": reverse("me-badges"),
                "credit-history": reverse("me-credit-history"),
                "stance-events": reverse("my-stance-events", args=[self.debate.pk]),
            }.items()
        }

    def test_suspended_member_cannot_write(self):
        self.suspend()
        for name, send in self.writes(self.client).items():
            with self.subTest(name):
                response = send()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "ACCOUNT_SUSPENDED")
                self.assertIn("이용이 제한된 계정", response.json()["error"]["message"])
        self.assertEqual(Opinion.objects.count(), 1)

    def test_suspended_member_can_read_own_data_and_report(self):
        self.suspend()
        for name, send in self.reads(self.client).items():
            with self.subTest(name):
                self.assertEqual(send().status_code, 200)
        report = self.client.post(
            reverse("report-create"),
            {"targetType": "OPINION", "targetId": str(self.other_post.pk), "reason": "Spam"}, format="json",
        )
        self.assertEqual(report.status_code, 201, report.content)

    def test_active_member_is_unchanged(self):
        for name, send in self.reads(self.client).items():
            with self.subTest(name):
                self.assertEqual(send().status_code, 200)
        self.assertEqual(self.write("active", "Hello").status_code, 201)

    def test_deleted_account_keeps_forbidden(self):
        User.objects.filter(pk=self.member.pk).update(status=User.Status.DELETED)
        self.member.refresh_from_db()
        self.client.force_authenticate(user=self.member)
        response = self.write("deleted", "Hello")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "FORBIDDEN"))

    def test_guest_still_gets_401(self):
        guest = APIClient()
        for name, send in {**self.writes(guest), **self.reads(guest)}.items():
            with self.subTest(name):
                response = send()
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
