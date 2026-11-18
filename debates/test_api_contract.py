"""Exact response keys from the API workbook, separate from flow tests.

Source: 영업중_API명세서_정합성반영본.xlsx, 상세 계약안 G49,
필드·DTO 정의 A65:J72 and A121:J128. These sets are source DTO
snapshots, not keys inferred from the response or the view implementation.

The source does not finalize small-population/JSON projection or processing
state details. Those are retained pending agreement and checked separately;
passing this module does not resolve those policy gaps or other endpoints.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate, DebateStatistics


RESULT_FIELDS = {
    "counts", "sankeyData", "genderData", "ageData", "regionData",
    "genderHidden", "ageHidden", "computedAt",
}
COUNT_FIELDS = {
    "totalParticipants", "opinionCount", "rebuttalCount", "finalPro",
    "finalCon", "finalNeutral", "stanceChangedCount", "stanceKeptCount",
}


class ResultWorkbookContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.now = timezone.now()
        user = User.objects.create_user("contract@example.com", "Contract")
        category = Category.objects.create(name="Contract")
        self.debate = Debate.objects.create(
            creator=user, category=category, type="QUICK", title="Contract",
            content="Contract", duration_minutes=10, status="CLOSED",
            started_at=self.now - timedelta(minutes=20),
            ends_at=self.now - timedelta(minutes=10),
            closed_at=self.now - timedelta(minutes=10),
        )
        self.url = f"/v1/debates/{self.debate.pk}/result"

    def test_success_has_exact_workbook_fields_and_scalar_types(self):
        DebateStatistics.objects.create(
            debate=self.debate, total_participants=20,
            opinion_count=5, rebuttal_count=3,
            final_pro=10, final_con=8, final_neutral=2,
            stance_changed_count=4, stance_kept_count=16,
            sankey_data=[], gender_hidden=True, age_hidden=True,
            computed_at=self.now,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"data"})
        data = body["data"]
        self.assertEqual(set(data), RESULT_FIELDS)
        self.assertEqual(set(data["counts"]), COUNT_FIELDS)
        for value in data["counts"].values():
            self.assertIs(type(value), int)
            self.assertGreaterEqual(value, 0)
        for field in ("genderHidden", "ageHidden"):
            self.assertIs(type(data[field]), bool)
        for field in ("genderData", "ageData", "regionData"):
            self.assertIsNone(data[field])
        self.assertTrue(data["computedAt"].endswith("Z"))
        self.assertEqual(parse_datetime(data["computedAt"]), self.now)

    def test_pending_retains_required_state_without_unregistered_header_fields(self):
        with patch("debates.api.result.views.get_result_state", return_value=None):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        self.assertEqual(body["error"]["code"], "RESULT_NOT_READY")
        self.assertEqual(body["error"]["details"], {"status": "PENDING"})

    def test_failed_retains_required_state_without_unregistered_header_fields(self):
        with patch("debates.api.result.views.get_result_state", return_value={"status": "FAILED"}):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        self.assertEqual(body["error"]["code"], "RESULT_NOT_READY")
        self.assertEqual(body["error"]["details"], {"status": "FAILED"})
