from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate


class DetailIntegrationTests(TestCase):
    """Use the specified URL, independent of duplicate route names or view classes."""

    def setUp(self):
        self.creator = User.objects.create_user("detail-owner@example.com", "Owner")
        self.other = User.objects.create_user("detail-other@example.com", "Other")
        self.category = Category.objects.create(name="Society")
        self.debate = Debate.objects.create(
            creator=self.creator, category=self.category, type="QUICK",
            title="Original title", content="Original content", duration_minutes=10,
        )
        self.url = f"/v1/debates/{self.debate.pk}"
        self.client = APIClient()

    def test_creator_can_read_patch_and_read_same_url(self):
        self.client.force_authenticate(self.creator)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.client.patch(self.url, {"title": "Updated title"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["draft"]["title"], "Updated title")
        detail = self.client.get(self.url)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["debate"]["title"], "Updated title")

    def test_guest_can_read_public_detail_but_cannot_patch(self):
        now = timezone.now()
        Debate.objects.filter(pk=self.debate.pk).update(
            status="OPEN", started_at=now, ends_at=now + timedelta(minutes=10),
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"data"})
        self.assertEqual(set(response.json()["data"]), {
            "debate", "images", "participantCount", "myParticipation",
        })
        self.assertIsNone(response.json()["data"]["myParticipation"])
        self.assertEqual(self.client.patch(self.url, {"title": "Attack"}, format="json").status_code, 401)

    def test_other_member_cannot_read_or_patch_draft(self):
        self.client.force_authenticate(self.other)
        read = self.client.get(self.url)
        self.assertEqual((read.status_code, read.json()["error"]["code"]), (403, "DRAFT_NOT_ACCESSIBLE"))
        edit = self.client.patch(self.url, {"title": "Attack"}, format="json")
        self.assertEqual((edit.status_code, edit.json()["error"]["code"]), (403, "NOT_DEBATE_CREATOR"))
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.title, "Original title")

    def test_inactive_creator_cannot_read_or_patch_draft(self):
        self.creator.status = User.Status.SUSPENDED
        self.creator.save(update_fields=["status"])
        self.client.force_authenticate(self.creator)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.patch(self.url, {"title": "Attack"}, format="json").status_code, 403)

    def test_creator_can_read_open_debate_but_cannot_edit_it(self):
        now = timezone.now()
        Debate.objects.filter(pk=self.debate.pk).update(
            status="OPEN", started_at=now, ends_at=now + timedelta(minutes=10),
        )
        self.client.force_authenticate(self.creator)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.client.patch(self.url, {"title": "Late edit"}, format="json")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "NOT_DRAFT"))

    def test_options_advertises_both_methods_on_same_resource(self):
        response = self.client.options(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("GET", response["Allow"])
        self.assertIn("PATCH", response["Allow"])
