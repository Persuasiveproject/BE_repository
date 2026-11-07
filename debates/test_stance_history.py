from django.test import TestCase
from django.urls import reverse

from .models import Debate, Stance
from .test_stance import StanceFixture


class StanceHistoryTests(StanceFixture, TestCase):
    def test_draft_history_is_hidden_even_from_a_participant(self):
        self.assertEqual(self.patch_stance().status_code, 200)
        self.debate.status = Debate.Status.DRAFT
        self.debate.save(update_fields=["status"])

        response = self.client.get(reverse("my-stance-events", args=[self.debate.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(set(response.data), {"error"})
        self.assertEqual(response.data["error"]["code"], "NOT_FOUND")

    def test_lists_only_my_changes(self):
        change = self.patch_stance()
        self.assertEqual(change.status_code, 200)
        url = reverse("my-stance-events", args=[self.debate.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        event, = response.data["data"]["events"]
        self.assertEqual(event["fromStance"], Stance.PRO)
        self.assertEqual(event["toStance"], Stance.CON)
        self.assertEqual(event["persuasionId"], change.data["data"]["persuasionId"])
        self.assertEqual(event["id"], change.data["data"]["eventId"])
        self.assertTrue(event["changedAt"].endswith("Z"))

        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(url).data, {"data": {"events": []}})
