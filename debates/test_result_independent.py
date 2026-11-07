"""Independent black-box checks from FC-DB-300 and the original API/ERD.

Expected before observing implementation:
- Anonymous completed results expose all eight counts and computedAt.
- CLOSED without aggregation is 409 RESULT_NOT_READY / PENDING.
- OPEN never exposes a result, even if an aggregation row exists.
- Below 20 participants, movement counts are null and Sankey empty;
  summary/final counts remain visible.
- At exactly 20 the small-population suppression no longer applies.
- Unknown IDs are 404. Invalid-route status is unspecified.
JSON internals, demographic projection, FAILED state fixture interface, and UI percentages are not
specified by the source API/ERD and are intentionally not invented here.

Ambiguous cases retained from the initial run (not normative assertions):
- Initially expected non-null demographic JSON at 20; genderData was null.
  Source JSON schema is deferred, so the arbitrary fixture is inconclusive.
- Initially inferred invalid IDs should return 400; -1/abc/1.5 returned HTML404.
  Source requires a positive decimal ID but does not define invalid-route status.
Neither observation authorizes an unrelated implementation change.
"""
from datetime import timedelta

from django.apps import apps
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient


def table_model(table):
    return next(model for model in apps.get_models() if model._meta.db_table == table)


class IndependentResultContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.now = timezone.now()
        self.creator = table_model("users").objects.create(
            email="independent-result@example.com", nickname="independent-result"
        )
        self.category = table_model("categories").objects.create(name="independent")

    def debate(self, status="CLOSED"):
        return table_model("debates").objects.create(
            creator_id=self.creator.pk,
            category_id=self.category.pk,
            type="QUICK",
            title="Independent source-spec result",
            content="Original specification contract check",
            duration_minutes=10,
            status=status,
            started_at=self.now - timedelta(minutes=20),
            ends_at=self.now - timedelta(minutes=10),
            closed_at=self.now - timedelta(minutes=10) if status == "CLOSED" else None,
        )

    def statistics(self, debate, total):
        return table_model("debate_statistics").objects.create(
            debate_id=debate.pk,
            total_participants=total,
            opinion_count=5,
            rebuttal_count=3,
            final_pro=total,
            final_con=0,
            final_neutral=0,
            stance_changed_count=0,
            stance_kept_count=total,
            sankey_data=[{"source": "PRO", "target": "PRO", "value": total}],
            gender_data={"M": total},
            age_data={"20s": total},
            region_data={"Seoul": total},
            gender_hidden=False,
            age_hidden=False,
            computed_at=self.now,
        )

    def get_result(self, debate):
        return self.client.get(f"/v1/debates/{debate.pk}/result")

    def test_closed_unaggregated_is_pending(self):
        response = self.get_result(self.debate())
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "RESULT_NOT_READY")
        self.assertEqual(response.json()["error"]["details"]["status"], "PENDING")

    def test_open_cannot_disclose_completed_row(self):
        debate = self.debate("OPEN")
        self.statistics(debate, 20)
        response = self.get_result(debate)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "RESULT_NOT_READY")

    def test_completed_anonymous_result_contract_at_twenty(self):
        debate = self.debate()
        self.statistics(debate, 20)
        response = self.get_result(debate)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["counts"], {
            "totalParticipants": 20, "opinionCount": 5, "rebuttalCount": 3,
            "finalPro": 20, "finalCon": 0, "finalNeutral": 0,
            "stanceChangedCount": 0, "stanceKeptCount": 20,
        })
        self.assertTrue(data["computedAt"])
        self.assertTrue(data["sankeyData"])

    def test_zero_and_nineteen_hide_movement_keep_summary(self):
        for total in (0, 19):
            with self.subTest(total=total):
                debate = self.debate()
                self.statistics(debate, total)
                response = self.get_result(debate)
                self.assertEqual(response.status_code, 200, response.content)
                data = response.json()["data"]
                counts = data["counts"]
                self.assertEqual(counts["totalParticipants"], total)
                self.assertEqual(counts["opinionCount"], 5)
                self.assertEqual(counts["rebuttalCount"], 3)
                self.assertEqual(counts["finalPro"], total)
                self.assertEqual(counts["finalCon"], 0)
                self.assertEqual(counts["finalNeutral"], 0)
                self.assertIsNone(counts["stanceChangedCount"])
                self.assertIsNone(counts["stanceKeptCount"])
                self.assertEqual(data["sankeyData"], [])

    def test_unknown_debate(self):
        response = self.client.get("/v1/debates/999999999/result")
        self.assertEqual(response.status_code, 404, response.content)
