import io
import json
import urllib.error
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from discussions.models import Opinion, OpinionReference

from .api.references.safety import lookup_unsafe
from .models import Debate
from .tasks.references import check_references
from .test_references import ReferenceFixture

SAFE, UNSAFE, PENDING = (OpinionReference.SafetyStatus.SAFE, OpinionReference.SafetyStatus.UNSAFE,
                         OpinionReference.SafetyStatus.PENDING)
LOOKUP = "debates.api.references.safety.lookup_unsafe"


class LookupUnsafeTests(SimpleTestCase):
    def lookup(self, body):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(body).encode())) as urlopen:
            result = lookup_unsafe(["https://a.example", "https://b.example"])
        return result, urlopen

    @override_settings(SAFE_BROWSING_API_KEY="secret")
    def test_request_body_and_empty_response(self):
        result, urlopen = self.lookup({})

        self.assertEqual(result, set())
        request = urlopen.call_args.args[0]
        self.assertEqual(urlopen.call_args.kwargs, {"timeout": 10})
        self.assertEqual(request.full_url, "https://safebrowsing.googleapis.com/v4/threatMatches:find")
        self.assertEqual(request.get_header("X-goog-api-key"), "secret")
        info = json.loads(request.data)["threatInfo"]
        self.assertEqual(info["threatTypes"], [
            "MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION",
        ])
        self.assertEqual((info["platformTypes"], info["threatEntryTypes"]), (["ANY_PLATFORM"], ["URL"]))
        self.assertEqual(info["threatEntries"], [{"url": "https://a.example"}, {"url": "https://b.example"}])

    def test_matches_are_parsed(self):
        result, _ = self.lookup({"matches": [
            {"threatType": "MALWARE", "threat": {"url": "https://b.example"}},
            {"threatType": "SOCIAL_ENGINEERING", "threat": {"url": "https://b.example"}},
        ]})
        self.assertEqual(result, {"https://b.example"})


class SafetyFixture(ReferenceFixture):
    def link(self, url="https://example.com/a", status=PENDING, checked_hours_ago=None, post=None):
        checked_at = None if checked_hours_ago is None else timezone.now() - timedelta(hours=checked_hours_ago)
        return OpinionReference.objects.create(
            opinion=post or self.post, url=url, safe_browsing_status=status, checked_at=checked_at,
        )


@override_settings(SAFE_BROWSING_ENABLED=True, SAFE_BROWSING_API_KEY="key")
class CheckReferencesTaskTests(SafetyFixture, TestCase):
    def test_checks_pending_and_expired_links_only(self):
        pending = self.link("https://pending.example")
        expired = self.link("https://bad.example", SAFE, checked_hours_ago=25)
        recent = self.link("https://recent.example", SAFE, checked_hours_ago=1)

        with patch(LOOKUP, return_value={"https://bad.example"}) as lookup:
            self.assertEqual(check_references(), 2)

        lookup.assert_called_once_with(["https://bad.example", "https://pending.example"])
        for link in (pending, expired, recent):
            link.refresh_from_db()
        self.assertEqual((pending.safe_browsing_status, expired.safe_browsing_status), (SAFE, UNSAFE))
        self.assertLess(timezone.now() - pending.checked_at, timedelta(minutes=1))
        self.assertLess(timezone.now() - expired.checked_at, timedelta(minutes=1))
        self.assertGreater(timezone.now() - recent.checked_at, timedelta(minutes=30))

    def test_lookup_failure_changes_nothing(self):
        link = self.link()
        with patch(LOOKUP, side_effect=urllib.error.URLError("down")), \
                self.assertLogs("debates.tasks.references", "WARNING"):
            self.assertEqual(check_references(), 0)
        link.refresh_from_db()
        self.assertEqual((link.safe_browsing_status, link.checked_at), (PENDING, None))

    def test_disabled_or_keyless_skips_lookup(self):
        self.link()
        for overrides in ({"SAFE_BROWSING_ENABLED": False}, {"SAFE_BROWSING_API_KEY": ""}):
            with self.subTest(overrides), override_settings(**overrides), patch(LOOKUP) as lookup:
                self.assertEqual(check_references(), 0)
                lookup.assert_not_called()

    def test_link_replaced_during_lookup_keeps_pending(self):
        link = self.link("https://old.example")

        def replace_then_answer(urls):
            OpinionReference.objects.filter(pk=link.pk).update(url="https://new.example")
            return set()

        with patch(LOOKUP, side_effect=replace_then_answer):
            self.assertEqual(check_references(), 0)
        link.refresh_from_db()
        self.assertEqual((link.url, link.safe_browsing_status, link.checked_at), ("https://new.example", PENDING, None))


class ReferenceDetailGetTests(SafetyFixture, TestCase):
    def get(self, reference_id):
        return APIClient().get(reverse("reference-detail", args=[reference_id]))

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_safe_link_returns_url_to_guests(self):
        link = self.link(status=SAFE, checked_hours_ago=1)
        response = self.get(link.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"data": {"url": "https://example.com/a"}})

    def test_pending_unsafe_and_expired_links_hide_url(self):
        for link, status, code in (
            (self.link(), 409, "REFERENCE_CHECK_PENDING"),
            (self.link(status=UNSAFE, checked_hours_ago=1), 403, "REFERENCE_UNSAFE"),
            (self.link(status=SAFE, checked_hours_ago=25), 409, "REFERENCE_CHECK_PENDING"),
        ):
            with self.subTest(code=code):
                response = self.get(link.pk)
                self.assert_error(response, status, code)
                self.assertNotIn("example.com", response.content.decode())

    def test_hidden_links_are_not_found(self):
        deleted = self.opinion(self.participant, status=Opinion.Status.DELETED)
        draft = Debate.objects.create(
            creator=self.creator, category=self.debate.category, type=Debate.Type.QUICK, title="Draft",
        )
        Opinion.objects.filter(pk=self.post.pk).update(debate=draft)
        for reference_id in (
            999999,
            self.link(status=SAFE, checked_hours_ago=1, post=deleted).pk,
            self.link(status=SAFE, checked_hours_ago=1).pk,
        ):
            with self.subTest(reference_id=reference_id):
                self.assert_error(self.get(reference_id), 404, "REFERENCE_NOT_FOUND")

    def test_out_of_range_id(self):
        self.assert_error(self.get(2**63), 400, "VALIDATION_ERROR")
