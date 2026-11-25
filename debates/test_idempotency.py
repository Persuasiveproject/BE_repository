from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import APIIdempotencyRecord, User
from debates.api_contract import ContractError
from debates.idempotency import request_hash, require_valid_key, save_response, saved_response


class RequireValidKeyTests(TestCase):
    def test_accepts_1_to_128_characters(self):
        require_valid_key("a")
        require_valid_key("a" * 128)

    def test_rejects_invalid_keys(self):
        for key in ("a" * 129, "", None, "   ", "key\x00", "key\n", "key\x7f"):
            with self.assertRaises(ContractError) as raised:
                require_valid_key(key)
            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(raised.exception.error["code"], "IDEMPOTENCY_KEY_INVALID")


class SavedResponseTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("idem@example.com", "Idem")
        self.scope = {
            "user": self.user, "http_method": "POST",
            "request_path": "/v1/debates/1/publish", "idempotency_key": "key-1",
        }
        self.hash = request_hash(["body"])

    def record(self, **fields):
        values = {
            "request_hash": self.hash, "status": APIIdempotencyRecord.Status.COMPLETED,
            "response_status": 201, "response_body": {"data": {"ok": True}},
            "expires_at": timezone.now() + timedelta(hours=1),
        }
        values.update(fields)
        return APIIdempotencyRecord.objects.create(**self.scope, **values)

    def assert_conflict(self, code):
        with self.assertRaises(ContractError) as raised:
            saved_response(self.scope, self.hash)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.error["code"], code)

    def test_none_without_record(self):
        self.assertIsNone(saved_response(self.scope, self.hash))

    def test_none_when_expired(self):
        self.record(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(saved_response(self.scope, self.hash))

    def test_replays_completed_response(self):
        self.record()
        self.assertEqual(saved_response(self.scope, self.hash), ({"data": {"ok": True}}, 201))

    def test_other_request_hash_conflicts(self):
        self.record(request_hash=request_hash(["other"]))
        self.assert_conflict("IDEMPOTENCY_KEY_REUSED")

    def test_processing_conflicts(self):
        self.record(status=APIIdempotencyRecord.Status.PROCESSING)
        self.assert_conflict("REQUEST_PROCESSING")

    def test_legacy_body_conflicts(self):
        self.record(response_body={"currentStance": "PRO"})
        self.assert_conflict("IDEMPOTENCY_KEY_REUSED")

    def test_save_overwrites_expired_row_in_same_scope(self):
        self.record(request_hash="old", expires_at=timezone.now() - timedelta(seconds=1))

        save_response(self.scope, self.hash, {"data": {"new": 1}}, 201)

        saved = APIIdempotencyRecord.objects.get()
        self.assertEqual(saved.request_hash, self.hash)
        self.assertEqual(saved.status, APIIdempotencyRecord.Status.COMPLETED)
        self.assertEqual(saved.response_body, {"data": {"new": 1}})
        self.assertGreater(saved.expires_at, timezone.now() + timedelta(hours=23))
        self.assertEqual(saved_response(self.scope, self.hash), ({"data": {"new": 1}}, 201))
