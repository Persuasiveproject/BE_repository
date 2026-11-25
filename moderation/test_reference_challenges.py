from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Debate, DebateParticipant, Stance
from discussions.models import Opinion, OpinionReference
from moderation.models import Report
from moderation.test_reports import ReportFixture


class ReferenceChallengeTests(ReportFixture, TestCase):
    def challenge(self, reference=None, body=None, client=None):
        url = reverse("reference-challenge", args=[(reference or self.reference).pk])
        return (client or self.client).post(url, {"reason": "Broken link"} if body is None else body, format="json")

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_creates_reference_report(self):
        response = self.challenge(body={"reason": "  Broken link  "})

        self.assertEqual(response.status_code, 201, response.content)
        report = Report.objects.get()
        self.assertEqual(response.json(), {"data": {"reportId": str(report.pk), "status": "PENDING"}})
        self.assertEqual(
            (report.target_type, report.reference, report.reason, report.reporter),
            (Report.TargetType.REFERENCE, self.reference, "Broken link", self.member),
        )

    def test_invalid_reason(self):
        for body in ({}, {"reason": "   "}, {"reason": "x" * 256}, {"reason": 1}):
            with self.subTest(body=body):
                self.assert_error(self.challenge(body=body), 400, "REASON_INVALID")
        self.assertEqual(self.challenge(body={"reason": "x" * 255}).status_code, 201)

    def test_reference_not_found(self):
        deleted = Opinion.objects.create(
            debate=self.debate, participant=self.opinion.participant, stance_at_write=Stance.CON,
            content="Gone", content_plain="Gone", status=Opinion.Status.DELETED,
        )
        links = [OpinionReference.objects.create(opinion=deleted, url="https://example.com/b")]
        for status in (Debate.Status.DRAFT, Debate.Status.CANCELED):
            debate = Debate.objects.create(
                creator=self.creator, category=self.debate.category, type=Debate.Type.QUICK,
                title=status, status=status,
            )
            author = DebateParticipant.objects.create(
                debate=debate, user=self.creator, anonymous_no=1,
                initial_stance=Stance.CON, current_stance=Stance.CON,
            )
            opinion = Opinion.objects.create(
                debate=debate, participant=author, stance_at_write=Stance.CON, content="R", content_plain="R",
            )
            links.append(OpinionReference.objects.create(opinion=opinion, url="https://example.com/c"))

        response = self.client.post(reverse("reference-challenge", args=[999999]), {"reason": "x"}, format="json")
        self.assert_error(response, 404, "REFERENCE_NOT_FOUND")
        for link in links:
            with self.subTest(link=link.opinion.debate.status):
                self.assert_error(self.challenge(link), 404, "REFERENCE_NOT_FOUND")
        self.assertFalse(Report.objects.exists())

    def test_out_of_range_id(self):
        response = self.client.post(f"/v1/references/{2**63}/challenges", {"reason": "x"}, format="json")
        self.assert_error(response, 400, "VALIDATION_ERROR")

    def test_duplicate_challenge(self):
        self.assertEqual(self.challenge().status_code, 201)
        self.assert_error(self.challenge(), 409, "ALREADY_REPORTED")
        self.assertEqual(Report.objects.count(), 1)

    def test_link_reported_through_reports_api_counts_as_duplicate(self):
        self.assertEqual(self.report("REFERENCE", str(self.reference.pk)).status_code, 201)
        self.assert_error(self.challenge(), 409, "ALREADY_REPORTED")
        self.assertEqual(Report.objects.count(), 1)

    def test_auth_rules(self):
        self.assertEqual(self.challenge(client=APIClient()).status_code, 401)

        User.objects.filter(pk=self.member.pk).update(status=User.Status.SUSPENDED)
        self.client.force_authenticate(User.objects.get(pk=self.member.pk))
        self.assertEqual(self.challenge().status_code, 201)
