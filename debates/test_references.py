from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import APIIdempotencyRecord, User
from discussions.models import Opinion, OpinionReference
from moderation.models import Report

from .api.references.validation import check_reference_url
from .api_contract import ContractError
from .models import Debate, DebateParticipant, Stance
from .test_b_api import DebateBFixture


class CheckReferenceUrlTests(SimpleTestCase):
    def reason(self, url):
        with self.assertRaises(ContractError) as raised:
            check_reference_url(url)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.error["code"], "URL_INVALID")
        return raised.exception.error["details"]["reason"]

    def test_accepts_https(self):
        url = "https://example.com/report?id=1"
        self.assertEqual(check_reference_url(url), url)
        self.assertEqual(check_reference_url("https://bit.ly.example.com/a"), "https://bit.ly.example.com/a")

    def test_reasons(self):
        cases = {
            "http://example.com/a": "HTTPS_REQUIRED",
            "ftp://example.com/a": "HTTPS_REQUIRED",
            "not a url": "FORMAT",
            "https://": "FORMAT",
            "https://exa mple.com": "FORMAT",
            "": "FORMAT",
            None: "FORMAT",
            123: "FORMAT",
            "https://example.com/" + "a" * 2029: "FORMAT",
            "https://bit.ly/abc": "SHORTENED_URL",
            "https://BIT.LY/abc": "SHORTENED_URL",
            "https://www.tinyurl.com/abc": "SHORTENED_URL",
            "https://naver.me/x": "SHORTENED_URL",
        }
        for url, reason in cases.items():
            with self.subTest(url=str(url)[:40]):
                self.assertEqual(self.reason(url), reason)

    def test_2048_characters_allowed(self):
        url = "https://example.com/" + "a" * 2028
        self.assertEqual(len(url), 2048)
        self.assertEqual(check_reference_url(url), url)

    def test_shortened_message(self):
        with self.assertRaises(ContractError) as raised:
            check_reference_url("https://t.co/x")
        self.assertEqual(raised.exception.error["message"], "Enter the original URL instead of a shortened link.")


class ReferenceFixture(DebateBFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.join().status_code, 201)
        self.participant = DebateParticipant.objects.get(debate=self.debate, user=self.member)
        self.post = self.opinion(self.participant)
        self.refs_url = reverse("post-references", args=[self.post.pk])

    def opinion(self, participant, **fields):
        return Opinion.objects.create(
            debate=participant.debate, participant=participant, stance_at_write=Stance.PRO,
            content="Reason", content_plain="Reason", **fields,
        )

    def add(self, url="https://example.com/a", url_path=None, **body):
        return self.client.post(url_path or self.refs_url, {"url": url, **body}, format="json")

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)


class PostCreateUrlTests(ReferenceFixture, TestCase):
    def test_rejects_http_and_shortened_links_without_side_effects(self):
        before = (Opinion.objects.count(), OpinionReference.objects.count(), APIIdempotencyRecord.objects.count())
        for index, url in enumerate(("http://example.com/a", "https://bit.ly/a")):
            response = self.write(f"bad-{index}", "Reason", references=[{"url": url}])
            self.assert_error(response, 400, "URL_INVALID")
        after = (Opinion.objects.count(), OpinionReference.objects.count(), APIIdempotencyRecord.objects.count())
        self.assertEqual(after, before)

    def test_https_link_is_saved_pending_and_hidden_in_list(self):
        response = self.write("ok", "Reason", references=[{"url": "https://example.com/evidence"}])
        self.assertEqual(response.status_code, 201)

        card = APIClient().get(self.posts_url).json()["data"]["posts"][0]
        self.assertEqual(card["references"][0]["status"], "PENDING")
        self.assertIsNone(card["references"][0]["url"])


class AddReferenceTests(ReferenceFixture, TestCase):
    def test_add_until_limit(self):
        for _ in range(3):
            response = self.add()
            self.assertEqual(response.status_code, 201)
            data = response.json()["data"]
            self.assertEqual(set(data), {"referenceId", "status"})
            self.assertEqual(data["status"], "PENDING")
        self.assert_error(self.add(), 400, "REFERENCE_LIMIT_EXCEEDED")
        self.assertEqual(OpinionReference.objects.filter(opinion=self.post).count(), 3)

    def test_replace_resets_check(self):
        reference = OpinionReference.objects.create(
            opinion=self.post, url="https://example.com/old",
            safe_browsing_status=OpinionReference.SafetyStatus.SAFE,
        )
        OpinionReference.objects.filter(pk=reference.pk).update(checked_at="2026-01-01T00:00:00Z")

        response = self.add("https://example.com/new", referenceId=str(reference.pk))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"referenceId": str(reference.pk), "status": "PENDING"})
        reference.refresh_from_db()
        self.assertEqual(reference.url, "https://example.com/new")
        self.assertEqual(reference.safe_browsing_status, "PENDING")
        self.assertIsNone(reference.checked_at)

    def test_reference_of_other_post(self):
        other = OpinionReference.objects.create(opinion=self.opinion(self.participant), url="https://example.com/x")

        self.assert_error(self.add(referenceId=str(other.pk)), 404, "REFERENCE_NOT_FOUND")
        self.assert_error(self.add(referenceId="abc"), 400, "VALIDATION_ERROR")

    def test_url_checked(self):
        response = self.add("http://example.com/a")
        self.assert_error(response, 400, "URL_INVALID")
        self.assertEqual(response.json()["error"]["details"], {"reason": "HTTPS_REQUIRED"})

    def test_rejections(self):
        self.client.force_authenticate(self.creator)
        self.assert_error(self.add(), 403, "NOT_POST_AUTHOR")

        self.client.force_authenticate(self.member)
        deleted = self.opinion(self.participant, status=Opinion.Status.DELETED)
        self.assert_error(self.add(url_path=reverse("post-references", args=[deleted.pk])), 404, "POST_NOT_FOUND")
        self.assert_error(self.add(url_path=f"/v1/posts/{2**63}/references"), 400, "VALIDATION_ERROR")

        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.CLOSED)
        self.assert_error(self.add(), 409, "DEBATE_NOT_OPEN")

        self.assertEqual(APIClient().post(self.refs_url, {"url": "https://example.com"}, format="json").status_code, 401)
        self.assertFalse(OpinionReference.objects.exists())


class DeleteReferenceTests(ReferenceFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.reference = OpinionReference.objects.create(opinion=self.post, url="https://example.com/a")
        self.url = reverse("reference-detail", args=[self.reference.pk])

    def test_author_deletes(self):
        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertFalse(OpinionReference.objects.exists())
        self.assert_error(self.client.delete(self.url), 404, "REFERENCE_NOT_FOUND")

    def test_reported_link_is_kept(self):
        Report.objects.create(
            reporter=self.creator, target_type=Report.TargetType.REFERENCE,
            reference=self.reference, reason="Spam",
        )

        self.assert_error(self.client.delete(self.url), 409, "REFERENCE_HAS_REPORTS")
        self.assertTrue(OpinionReference.objects.filter(pk=self.reference.pk).exists())

    def test_rejections(self):
        self.client.force_authenticate(self.creator)
        self.assert_error(self.client.delete(self.url), 403, "NOT_POST_AUTHOR")

        self.client.force_authenticate(self.member)
        self.assert_error(self.client.delete(f"/v1/references/{2**63}"), 400, "VALIDATION_ERROR")

        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.CLOSED)
        self.assert_error(self.client.delete(self.url), 409, "DEBATE_NOT_OPEN")

        Opinion.objects.filter(pk=self.post.pk).update(status=Opinion.Status.DELETED)
        self.assert_error(self.client.delete(self.url), 404, "POST_NOT_FOUND")
        self.assertTrue(OpinionReference.objects.exists())

    def test_suspended_member_forbidden(self):
        User.objects.filter(pk=self.member.pk).update(status=User.Status.SUSPENDED)
        self.client.force_authenticate(User.objects.get(pk=self.member.pk))

        self.assertEqual(self.client.delete(self.url).status_code, 403)
