from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Debate, DebateParticipant, Stance
from debates.test_b_api import DebateBFixture
from discussions.models import Opinion, OpinionReference
from moderation.models import Report


class ReportFixture(DebateBFixture):
    def setUp(self):
        super().setUp()
        self.url = reverse("report-create")
        author = DebateParticipant.objects.create(
            debate=self.debate, user=self.creator, anonymous_no=1,
            initial_stance=Stance.CON, current_stance=Stance.CON,
        )
        self.opinion = Opinion.objects.create(
            debate=self.debate, participant=author, stance_at_write=Stance.CON,
            content="Reason", content_plain="Reason",
        )
        self.reference = OpinionReference.objects.create(opinion=self.opinion, url="https://example.com/a")

    def report(self, target_type="OPINION", target_id=None, reason="Spam", client=None):
        if target_id is None:
            target_id = str(self.opinion.pk)
        body = {"targetType": target_type, "targetId": target_id, "reason": reason}
        return (client or self.client).post(self.url, body, format="json")


class ReportCreateTests(ReportFixture, TestCase):
    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_reports_each_target_type(self):
        targets = {
            "OPINION": ("opinion", self.opinion),
            "DEBATE": ("debate", self.debate),
            "REFERENCE": ("reference", self.reference),
        }
        for target_type, (field, target) in targets.items():
            with self.subTest(target_type=target_type):
                response = self.report(target_type, str(target.pk), reason="  Abusive words  ")
                self.assertEqual(response.status_code, 201)
                data = response.json()["data"]
                self.assertEqual(set(data), {"reportId", "status"})
                self.assertEqual(data["status"], "PENDING")
                report = Report.objects.get(pk=data["reportId"])
                self.assertEqual(report.reporter, self.member)
                self.assertEqual(report.target_type, target_type)
                self.assertEqual(report.reason, "Abusive words")
                for other in ("opinion", "debate", "reference"):
                    value = getattr(report, f"{other}_id")
                    self.assertEqual(value, target.pk if other == field else None)

    def test_duplicate_rules(self):
        self.assertEqual(self.report().status_code, 201)
        self.assert_error(self.report(reason="Again"), 409, "ALREADY_REPORTED")

        self.assertEqual(self.report("REFERENCE", str(self.reference.pk)).status_code, 201)
        other = APIClient()
        other.force_authenticate(User.objects.create_user("other@example.com", "Other"))
        self.assertEqual(self.report(client=other).status_code, 201)
        self.assertEqual(Report.objects.count(), 3)

    def test_target_not_found(self):
        deleted = Opinion.objects.create(
            debate=self.debate, participant=self.opinion.participant, stance_at_write=Stance.CON,
            content="Gone", content_plain="Gone", status=Opinion.Status.DELETED,
        )
        deleted_link = OpinionReference.objects.create(opinion=deleted, url="https://example.com/b")
        draft = Debate.objects.create(
            creator=self.creator, category=self.debate.category, type=Debate.Type.QUICK, title="Draft",
        )
        for target_type, target_id in (
            ("OPINION", "999999"), ("OPINION", str(deleted.pk)), ("DEBATE", str(draft.pk)),
            ("DEBATE", "999999"), ("REFERENCE", str(deleted_link.pk)), ("REFERENCE", "999999"),
        ):
            with self.subTest(target_type=target_type, target_id=target_id):
                self.assert_error(self.report(target_type, target_id), 404, "REPORT_TARGET_NOT_FOUND")
        self.assertFalse(Report.objects.exists())

    def test_filtered_opinion_can_be_reported(self):
        Opinion.objects.filter(pk=self.opinion.pk).update(status=Opinion.Status.FILTERED)

        self.assertEqual(self.report().status_code, 201)

    def test_invalid_input(self):
        cases = [
            {"target_type": "USER"},
            {"target_type": 1},
            {"target_id": self.opinion.pk},
            {"target_id": "0"},
            {"target_id": "abc"},
            {"target_id": str(2**63)},
            {"reason": ""},
            {"reason": "   "},
            {"reason": "x" * 256},
            {"reason": 12},
        ]
        for case in cases:
            with self.subTest(case=str(case)[:40]):
                self.assert_error(self.report(**case), 400, "REPORT_INVALID")
        self.assert_error(self.client.post(self.url, [], format="json"), 400, "REPORT_INVALID")
        self.assertEqual(self.report(reason="x" * 255).status_code, 201)

    def test_auth_rules(self):
        self.assertEqual(self.report(client=APIClient()).status_code, 401)

        User.objects.filter(pk=self.member.pk).update(status=User.Status.SUSPENDED)
        self.client.force_authenticate(User.objects.get(pk=self.member.pk))
        self.assertEqual(self.report().status_code, 201)

    def test_target_is_not_hidden(self):
        for _ in range(3):
            client = APIClient()
            client.force_authenticate(User.objects.create_user(f"r{Report.objects.count()}@example.com", "R"))
            self.assertEqual(self.report(client=client).status_code, 201)

        self.opinion.refresh_from_db()
        self.debate.refresh_from_db()
        self.assertEqual(self.opinion.status, Opinion.Status.VISIBLE)
        self.assertEqual(self.debate.status, Debate.Status.OPEN)


class ReportConcurrencyTests(ReportFixture, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_duplicate_reports_create_one(self):
        ready = Barrier(2)

        def submit(_):
            client = APIClient()
            client.force_authenticate(self.member)
            try:
                ready.wait(timeout=10)
                return self.report(client=client).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(submit, range(2)))

        self.assertCountEqual(statuses, [201, 409])
        self.assertEqual(Report.objects.count(), 1)
