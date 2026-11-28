from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from unittest.mock import patch

from django.db import connections, IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.identity import issue_mock_proof
from accounts.models import User, UserIdentityVerification


MOCK_SETTINGS = dict(MOCK_IDENTITY_ENABLED=True, MOCK_IDENTITY_SIGNING_KEY="identity-api-test-key",
                     IDENTITY_HASH_KEY="identity-api-test-hash-key", MOCK_IDENTITY_REQUEST_SECONDS=300)


class IdentityApiFixture:
    def setUp(self):
        self.user = User.objects.create_user("identity@example.com", "Identity", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.start_url = reverse("identity-start")
        self.complete_url = reverse("identity-complete")

    def start(self):
        response = self.client.post(self.start_url, {}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]["requestId"]

    def payload(self, request_id, *, user=None, person="same-person"):
        return {"requestId": request_id, "proof": issue_mock_proof(
            user_id=(user or self.user).pk, request_id=request_id, person_key=person,
        )}

    def complete(self, payload):
        return self.client.post(self.complete_url, payload, format="json")

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertIn(set(body["error"]), ({"code", "message"}, {"code", "message", "details"}))
        self.assertEqual(body["error"]["code"], code)
        self.assertIsInstance(body["error"]["message"], str)


@override_settings(**MOCK_SETTINGS)
class IdentityApiTests(IdentityApiFixture, TestCase):
    def test_full_start_complete_and_me_contract_with_basic_authentication(self):
        self.assertEqual(self.start_url, "/v1/auth/identity/start")
        self.assertEqual(self.complete_url, "/v1/auth/identity/complete")
        # Exercise the actual authenticator instead of force_authenticate for this flow.
        import base64
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION="Basic " + base64.b64encode(
            f"{self.user.pk}:test-password".encode()).decode())
        before = timezone.now()
        response = self.client.post(self.start_url, {}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"data"})
        data = response.json()["data"]
        self.assertEqual(set(data), {"requestId", "expiresAt"})
        self.assertIsInstance(data["requestId"], str)
        self.assertTrue(1 <= len(data["requestId"]) <= 128)
        self.assertTrue(data["expiresAt"].endswith("Z"))
        expires = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
        self.assertAlmostEqual((expires - before).total_seconds(), 300, delta=3)
        payload = self.payload(data["requestId"])
        response = self.complete(payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {"data"})
        data = response.json()["data"]
        self.assertEqual(set(data), {"identityVerified", "verifiedAt"})
        self.assertIs(data["identityVerified"], True)
        self.assertTrue(data["verifiedAt"].endswith("Z"))
        datetime.fromisoformat(data["verifiedAt"].replace("Z", "+00:00"))
        self.assertIs(self.client.get("/v1/me").json()["data"]["identityVerified"], True)
        row = UserIdentityVerification.objects.get(user=self.user)
        self.assertEqual(len(row.identity_key_hash), 64)
        self.assertNotIn("same-person", row.identity_key_hash)
        self.assertEqual(self.complete(payload).json(), response.json())
        self.assert_error(self.client.post(self.start_url, {}, format="json"), 409, "ALREADY_VERIFIED")

    def test_duplicate_person_across_phone_and_kakao_accounts_is_rejected(self):
        self.user.provider = "PHONE"
        self.user.provider_id = "phone-id"
        self.user.save()
        self.assertEqual(self.complete(self.payload(self.start())).status_code, 200)
        other = User.objects.create_user("other@example.com", "Other", provider="KAKAO", provider_id="kakao-id")
        self.client.force_authenticate(other)
        payload = self.payload(self.start(), user=other)
        response = self.complete(payload)
        self.assert_error(response, 409, "IDENTITY_ALREADY_REGISTERED")
        self.assertEqual(set(response.json()["error"]), {"code", "message"})
        row = UserIdentityVerification.objects.get(user=other)
        self.assertEqual(row.status, "PENDING")
        self.assertIsNone(row.identity_key_hash)
        self.assertIsNone(row.verified_at)
        self.assertIs(self.client.get("/v1/me").json()["data"]["identityVerified"], False)

    def test_different_people_can_verify(self):
        self.assertEqual(self.complete(self.payload(self.start())).status_code, 200)
        other = User.objects.create_user("other@example.com", "Other")
        self.client.force_authenticate(other)
        self.assertEqual(self.complete(self.payload(self.start(), user=other, person="other-person")).status_code, 200)
        self.assertEqual(UserIdentityVerification.objects.filter(status="VERIFIED").count(), 2)

    def test_authentication_status_and_methods(self):
        for url in (self.start_url, self.complete_url):
            self.client.force_authenticate(None)
            self.assert_error(self.client.post(url, {}, format="json"), 401, "AUTHENTICATION_REQUIRED")
            for status in ("SUSPENDED", "DELETED"):
                self.user.status = status
                self.user.save()
                self.client.force_authenticate(self.user)
                self.assert_error(self.client.post(url, {}, format="json"), 403, "FORBIDDEN")
            self.user.status = "ACTIVE"
            self.user.save()
            self.client.force_authenticate(self.user)
            for method in ("get", "put", "patch", "delete"):
                self.assert_error(getattr(self.client, method)(url), 405, "METHOD_NOT_ALLOWED")

    def test_invalid_start_and_complete_fields_do_not_change_identity(self):
        for body in ([], {"personKey": "untrusted"}, {"provider": "MOCK"}):
            response = self.client.post(self.start_url, body, format="json")
            self.assert_error(response, 400, "VALIDATION_ERROR")
        request_id = self.start()
        valid = self.payload(request_id)
        for body in ({}, [], {"requestId": request_id}, {"proof": valid["proof"]},
                     {**valid, "personKey": "untrusted"}, {**valid, "proof": None},
                     {**valid, "proof": ""}, {**valid, "proof": 123},
                     {**valid, "requestId": None}, {**valid, "requestId": 123},
                     {**valid, "requestId": ""}, {**valid, "requestId": "x" * 129}):
            with self.subTest(body=body):
                self.assert_error(self.complete(body), 400, "VALIDATION_ERROR")
        row = UserIdentityVerification.objects.get(user=self.user)
        self.assertEqual(row.status, "PENDING")
        self.assertIsNone(row.identity_key_hash)

    def test_tampered_wrong_owner_and_wrong_request_proofs_cannot_verify(self):
        first = self.start()
        stale = self.payload(first)
        current = self.start()
        for payload in (stale, {**stale, "requestId": current},
                        {"requestId": current, "proof": "forged"},
                        {"requestId": current, "proof": issue_mock_proof(
                            user_id=self.user.pk + 1, request_id=current, person_key="same-person")}):
            expected = (400, "VERIFICATION_EXPIRED") if payload == stale else (502, "IDENTITY_PROVIDER_ERROR")
            self.assert_error(self.complete(payload), *expected)
        self.assertEqual(UserIdentityVerification.objects.get(user=self.user).status, "PENDING")

    def test_expired_request_and_unknown_request_are_rejected(self):
        payload = self.payload(self.start())
        UserIdentityVerification.objects.filter(user=self.user).update(expires_at=timezone.now())
        self.assert_error(self.complete(payload), 400, "VERIFICATION_EXPIRED")
        self.assert_error(self.complete({**payload, "requestId": "unknown"}), 400, "VERIFICATION_EXPIRED")

    @override_settings(MOCK_IDENTITY_ENABLED=False)
    def test_disabled_provider_fails_closed(self):
        self.assert_error(self.client.post(self.start_url, {}, format="json"), 502, "IDENTITY_PROVIDER_ERROR")
        self.assert_error(self.complete({"requestId": "request", "proof": "signed"}), 502, "IDENTITY_PROVIDER_ERROR")
        self.assertFalse(UserIdentityVerification.objects.exists())

    def test_missing_keys_and_invalid_lifetime_fail_closed(self):
        for overrides in ({"MOCK_IDENTITY_SIGNING_KEY": ""}, {"IDENTITY_HASH_KEY": ""},
                          {"MOCK_IDENTITY_REQUEST_SECONDS": 0}):
            with self.settings(**overrides):
                self.assert_error(self.client.post(self.start_url, {}, format="json"), 502, "IDENTITY_PROVIDER_ERROR")
        self.assertFalse(UserIdentityVerification.objects.exists())

    def test_unrelated_integrity_errors_are_not_misreported_as_duplicate_identity(self):
        payload = self.payload(self.start())
        with patch.object(UserIdentityVerification, "save", side_effect=IntegrityError("unrelated constraint")):
            with self.assertRaises(IntegrityError):
                self.complete(payload)
        self.assertEqual(UserIdentityVerification.objects.get(user=self.user).status, "PENDING")


@override_settings(**MOCK_SETTINGS)
class ConcurrentIdentityApiTests(IdentityApiFixture, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_accounts_completing_same_person_concurrently_have_one_winner(self):
        payloads = [(self.user, self.payload(self.start()))]
        other = User.objects.create_user("other@example.com", "Other")
        self.client.force_authenticate(other)
        payloads.append((other, self.payload(self.start(), user=other)))
        ready = Barrier(2)

        def submit(item):
            user, payload = item
            try:
                client = APIClient()
                client.force_authenticate(user)
                ready.wait(timeout=10)
                response = client.post(self.complete_url, payload, format="json")
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, payloads))
        self.assertCountEqual([status for status, body in results], [200, 409])
        self.assertEqual(next(body for status, body in results if status == 409)["error"]["code"],
                         "IDENTITY_ALREADY_REGISTERED")
        self.assertEqual(UserIdentityVerification.objects.filter(status="VERIFIED").count(), 1)
