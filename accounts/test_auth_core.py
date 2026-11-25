from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from threading import Barrier
import time
from unittest import skipUnless
from unittest.mock import patch
from urllib.error import URLError
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
import redis

from accounts.agreements import save_agreements
from accounts.identity import (complete_mock_verification, issue_mock_proof,
                               require_verified_identity, start_mock_verification)
from accounts.kakao import (KakaoIdentity, _request_json, register_verified_identity,
                            verify_access_token, verify_authorization_code)
from accounts.models import Term, User, UserIdentityVerification, UserTermAgreement
from accounts.sessions import InvalidSession, SessionStore, digest
from debates.api_contract import ContractError
from economy.models import CoinTransaction, CreditHistory


def member(label="member"):
    return User.objects.create_user(email=f"{label}@example.com", nickname=label)


def terms():
    now = timezone.now() - timedelta(days=1)
    return [Term.objects.create(type=kind, version="1", content="Terms", is_required=True,
                                effective_at=now) for kind in ("SERVICE", "PRIVACY")]


class AgreementTests(TestCase):
    def setUp(self):
        self.user = member()
        self.terms = terms()

    def test_required_terms_and_optional_refusal_saved(self):
        optional = Term.objects.create(type="MARKETING", version="1", content="Optional",
                                       is_required=False, effective_at=timezone.now())
        result = save_agreements(user_id=self.user.pk, decisions={
            **{term.pk: True for term in self.terms}, optional.pk: False,
        })
        self.assertEqual(len(result), 3)
        self.assertFalse(UserTermAgreement.objects.get(user=self.user, term=optional).agreed)
        self.assertTrue(all(timezone.is_aware(item.agreed_at) for item in result))

    def test_required_refusal_or_missing_is_atomic(self):
        for decisions in ({}, {self.terms[0].pk: True},
                          {term.pk: False for term in self.terms}):
            with self.assertRaises(ContractError) as ctx:
                save_agreements(user_id=self.user.pk, decisions=decisions)
            self.assertEqual(ctx.exception.error["code"], "REQUIRED_TERM_NOT_AGREED")
            self.assertFalse(UserTermAgreement.objects.exists())

    def test_unknown_and_future_term_are_not_saved(self):
        future = Term.objects.create(type="SERVICE", version="future", content="Future",
                                     is_required=True, effective_at=timezone.now() + timedelta(days=1))
        for term_id in (999999, future.pk):
            with self.assertRaises(ContractError) as ctx:
                save_agreements(user_id=self.user.pk, decisions={term_id: True})
            self.assertEqual(ctx.exception.error["code"], "TERM_NOT_FOUND")
        self.assertFalse(UserTermAgreement.objects.exists())

    def test_updated_required_version_needs_new_consent(self):
        decisions = {term.pk: True for term in self.terms}
        save_agreements(user_id=self.user.pk, decisions=decisions)
        newer = Term.objects.create(type="SERVICE", version="new", content="New",
                                    is_required=True, effective_at=timezone.now())
        with self.assertRaises(ContractError):
            save_agreements(user_id=self.user.pk, decisions=decisions)
        save_agreements(user_id=self.user.pk, decisions={newer.pk: True})
        self.assertEqual(UserTermAgreement.objects.count(), 3)

    def test_repeated_consent_upserts_and_rejects_invalid_types(self):
        decisions = {term.pk: True for term in self.terms}
        for _ in range(2):
            save_agreements(user_id=self.user.pk, decisions=decisions)
        self.assertEqual(UserTermAgreement.objects.count(), 2)
        for invalid in ({"1": True}, {1: 1}, {True: True}, []):
            with self.assertRaises(ValueError):
                save_agreements(user_id=self.user.pk, decisions=invalid)

    def test_no_required_catalogue_fails_closed(self):
        Term.objects.all().delete()
        with self.assertRaises(ImproperlyConfigured):
            save_agreements(user_id=self.user.pk, decisions={})


@override_settings(KAKAO_APP_ID=123)
class KakaoTests(TestCase):
    def payloads(self, **changes):
        info = {"id": 456, "app_id": 123, "expires_in": 60, **changes}
        profile = {"id": 456, "kakao_account": {"email": "member@example.com",
                   "is_email_valid": True, "is_email_verified": True,
                   "profile": {"nickname": "카카오회원"}}}
        return info, profile

    def test_token_app_and_profile_verified_by_server(self):
        with patch("accounts.kakao._request_json", side_effect=self.payloads()) as call:
            identity = verify_access_token("provider-token")
        self.assertEqual(identity, KakaoIdentity("456", "member@example.com", "카카오회원"))
        self.assertEqual(call.call_count, 2)
        self.assertTrue(call.call_args_list[0].args[0].endswith("access_token_info"))

    def test_wrong_app_expired_and_invalid_id_rejected_before_profile_lookup(self):
        for changes in ({"app_id": 999}, {"expires_in": 0}, {"id": True}, {"id": -1}):
            with patch("accounts.kakao._request_json", side_effect=self.payloads(**changes)) as call:
                with self.assertRaises(ContractError) as ctx:
                    verify_access_token("provider-token")
                self.assertEqual(ctx.exception.status_code, 401)
                self.assertEqual(ctx.exception.error["code"], "OAUTH_AUTHENTICATION_FAILED")
                self.assertEqual(call.call_count, 1)

    def test_mismatching_profile_id_rejected(self):
        info, profile = self.payloads()
        profile["id"] = 789
        with patch("accounts.kakao._request_json", side_effect=[info, profile]):
            with self.assertRaises(ContractError):
                verify_access_token("provider-token")

    def test_email_without_verification_not_treated_as_verified(self):
        info, profile = self.payloads()
        profile["kakao_account"]["is_email_verified"] = False
        with patch("accounts.kakao._request_json", side_effect=[info, profile]):
            self.assertIsNone(verify_access_token("provider-token").email)

    def test_bad_credentials_do_not_make_network_request(self):
        for token in (None, "", "bad\r\nheader", "x" * 8193):
            with patch("accounts.kakao._request_json") as call:
                with self.assertRaises(ContractError):
                    verify_access_token(token)
                call.assert_not_called()

    def test_provider_failure_does_not_leak_credentials(self):
        with patch("accounts.kakao.build_opener") as opener:
            opener.return_value.open.side_effect = URLError("private-provider-token")
            with self.assertRaises(ContractError) as ctx:
                _request_json("https://kapi.kakao.com/v2/user/me", token="private-provider-token")
        self.assertNotIn("private-provider-token", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    @override_settings(KAKAO_REST_API_KEY="server-key", KAKAO_CLIENT_SECRET="server-secret",
                       KAKAO_REDIRECT_URIS=("https://example.com/callback",))
    def test_code_exchange_allowlists_redirect_and_verifies_result(self):
        with patch("accounts.kakao._request_json", return_value={"access_token": "token"}) as request, \
                patch("accounts.kakao.verify_access_token", return_value="verified") as verify:
            self.assertEqual(verify_authorization_code("code", redirect_uri="https://example.com/callback"),
                             "verified")
            self.assertEqual(request.call_args.kwargs["form"]["client_secret"], "server-secret")
            verify.assert_called_once_with("token")
        with self.assertRaises(ImproperlyConfigured):
            verify_authorization_code("code", redirect_uri="https://attacker.example/callback")

    def test_signup_terms_and_bonuses_commit_together(self):
        catalogue = terms()
        identity = KakaoIdentity("456", "member@example.com", "가입회원")
        with self.assertRaises(ContractError):
            register_verified_identity(identity, agreements={})
        self.assertFalse(User.objects.exists())
        self.assertFalse(CoinTransaction.objects.exists())
        self.assertFalse(CreditHistory.objects.exists())
        user, created = register_verified_identity(identity, agreements={term.pk: True for term in catalogue})
        self.assertTrue(created)
        again, created = register_verified_identity(identity, agreements={})
        self.assertFalse(created)
        self.assertEqual(again.pk, user.pk)
        self.assertEqual(CoinTransaction.objects.count(), 1)
        self.assertEqual(UserTermAgreement.objects.count(), 2)

    def test_missing_email_never_fabricates_account_and_deleted_account_not_reopened(self):
        with self.assertRaises(ValidationError):
            register_verified_identity(KakaoIdentity("456", None, "회원"), agreements={})
        self.assertFalse(User.objects.exists())
        user = member()
        user.provider, user.provider_id, user.status = "KAKAO", "456", "DELETED"
        user.save()
        with self.assertRaises(ContractError) as ctx:
            register_verified_identity(KakaoIdentity("456", None, None), agreements={})
        self.assertEqual(ctx.exception.error["code"], "ACCOUNT_UNAVAILABLE")


@override_settings(MOCK_IDENTITY_ENABLED=True, MOCK_IDENTITY_SIGNING_KEY="mock-test-key",
                   IDENTITY_HASH_KEY="identity-test-key")
class IdentityTests(TestCase):
    def setUp(self):
        self.user = member()

    def start(self, user=None):
        return start_mock_verification(user_id=(user or self.user).pk, valid_for=timedelta(minutes=5))

    def proof(self, row, person="person-one"):
        return issue_mock_proof(user_id=row.user_id, request_id=row.request_id, person_key=person)

    def complete(self, row, proof=None):
        return complete_mock_verification(user_id=row.user_id, request_id=row.request_id,
                                          proof=proof or self.proof(row))

    def test_complete_repeat_and_permission_check(self):
        with self.assertRaises(ContractError) as ctx:
            require_verified_identity(self.user.pk)
        self.assertEqual(ctx.exception.error["code"], "IDENTITY_VERIFICATION_REQUIRED")
        row = self.start()
        result = self.complete(row)
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(len(result.identity_key_hash), 64)
        self.assertNotIn("person-one", result.identity_key_hash)
        self.assertEqual(self.complete(row).verified_at, result.verified_at)
        require_verified_identity(self.user.pk)
        with self.assertRaises(ContractError) as ctx:
            self.start()
        self.assertEqual(ctx.exception.error["code"], "ALREADY_VERIFIED")

    def test_second_account_same_person_blocked_without_verifying_it(self):
        self.complete(self.start())
        other = self.start(member("other"))
        with self.assertRaises(ContractError) as ctx:
            self.complete(other)
        self.assertEqual(ctx.exception.error["code"], "IDENTITY_ALREADY_REGISTERED")
        other.refresh_from_db()
        self.assertEqual(other.status, "PENDING")
        self.assertIsNone(other.identity_key_hash)

    def test_forged_wrong_owner_and_stale_request_proofs_rejected(self):
        first = self.start()
        old_proof = self.proof(first)
        second = self.start()
        for proof in (old_proof, "forged", issue_mock_proof(
                user_id=999, request_id=second.request_id, person_key="person")):
            with self.assertRaises(ContractError):
                self.complete(second, proof)
        second.refresh_from_db()
        self.assertEqual(second.status, "PENDING")

    def test_expiry_and_changed_account_status(self):
        row = self.start()
        UserIdentityVerification.objects.filter(pk=row.pk).update(expires_at=timezone.now())
        with self.assertRaises(ContractError) as ctx:
            self.complete(row)
        self.assertEqual(ctx.exception.error["code"], "VERIFICATION_EXPIRED")
        self.user.status = "SUSPENDED"
        self.user.save()
        with self.assertRaises(ContractError) as ctx:
            self.start()
        self.assertEqual(ctx.exception.status_code, 403)

    @override_settings(MOCK_IDENTITY_ENABLED=False)
    def test_mock_disabled_by_default(self):
        with self.assertRaises(ImproperlyConfigured):
            self.start()


class SessionTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.redis = redis.Redis.from_url(os.environ.get("P3_TEST_REDIS_URL", settings.CELERY_RESULT_BACKEND))
        cls.redis.ping()

    def setUp(self):
        self.user = member()
        prefix = f"ye:auth:test:{uuid4().hex}"

        class IsolatedStore(SessionStore):
            def _key(self, kind, value):
                return f"{prefix}:{kind}:{value}"

        self.prefix = prefix
        self.store = IsolatedStore(self.redis, access_lifetime=60)

    def tearDown(self):
        keys = list(self.redis.scan_iter(f"{self.prefix}:*"))
        if keys:
            self.redis.delete(*keys)

    def test_create_store_hashes_and_authenticate(self):
        token = self.store.create(user_id=self.user.pk)
        self.assertEqual(self.store.authenticate(token.access_token).pk, self.user.pk)
        self.assertAlmostEqual(token.session_expires_at - time.time(), 14 * 86400, delta=3)
        self.assertNotIn(token.access_token, repr(token))
        self.assertNotIn(token.refresh_token, repr(token))
        for key in self.redis.scan_iter(f"{self.prefix}:*"):
            value = self.redis.get(key).decode()
            self.assertNotIn(token.access_token, value)
            self.assertNotIn(token.refresh_token, value)

    def test_refresh_rotates_once_invalidates_old_tokens_and_does_not_extend_14_days(self):
        first = self.store.create(user_id=self.user.pk)
        second = self.store.refresh(first.refresh_token, rotate_refresh=True)
        self.assertEqual(first.session_expires_at, second.session_expires_at)
        self.assertNotEqual(first.refresh_token, second.refresh_token)
        with self.assertRaises(InvalidSession):
            self.store.authenticate(first.access_token)
        with self.assertRaises(InvalidSession):
            self.store.refresh(first.refresh_token, rotate_refresh=True)
        self.assertEqual(self.store.authenticate(second.access_token).pk, self.user.pk)

    def test_nonrotating_refresh_supported_only_when_explicitly_selected(self):
        first = self.store.create(user_id=self.user.pk)
        second = self.store.refresh(first.refresh_token, rotate_refresh=False)
        self.assertEqual(first.refresh_token, second.refresh_token)
        with self.assertRaises(TypeError):
            self.store.refresh(second.refresh_token)

    def test_revoke_only_one_device_and_prevent_refresh(self):
        first = self.store.create(user_id=self.user.pk)
        other = self.store.create(user_id=self.user.pk)
        self.store.revoke(first.access_token)
        with self.assertRaises(InvalidSession):
            self.store.authenticate(first.access_token)
        with self.assertRaises(InvalidSession):
            self.store.refresh(first.refresh_token, rotate_refresh=True)
        self.assertEqual(self.store.authenticate(other.access_token).pk, self.user.pk)

    def test_expired_access_can_refresh_but_expired_session_cannot(self):
        first = self.store.create(user_id=self.user.pk)
        with patch("accounts.sessions.time.time", return_value=first.access_expires_at + 1):
            with self.assertRaises(InvalidSession):
                self.store.authenticate(first.access_token)
            self.store.refresh(first.refresh_token, rotate_refresh=False)
        with patch("accounts.sessions.time.time", return_value=first.session_expires_at + 1):
            with self.assertRaises(InvalidSession):
                self.store.refresh(first.refresh_token, rotate_refresh=True)

    def test_refresh_requires_active_and_deleted_accounts_cannot_authenticate(self):
        first = self.store.create(user_id=self.user.pk)
        User.objects.filter(pk=self.user.pk).update(status="SUSPENDED")
        self.assertEqual(self.store.authenticate(first.access_token).pk, self.user.pk)
        with self.assertRaises(InvalidSession):
            self.store.refresh(first.refresh_token, rotate_refresh=True)
        User.objects.filter(pk=self.user.pk).update(status="DELETED")
        with self.assertRaises(InvalidSession):
            self.store.authenticate(first.access_token)
        with self.assertRaises(InvalidSession):
            self.store.create(user_id=self.user.pk)

    def test_revocation_with_refresh_after_access_index_expired(self):
        first = self.store.create(user_id=self.user.pk)
        self.redis.delete(self.store._key("access", digest(first.access_token)))
        self.store.revoke_refresh(first.refresh_token)
        with self.assertRaises(InvalidSession):
            self.store.refresh(first.refresh_token, rotate_refresh=True)

    @skipUnless(connection.vendor == "postgresql", "Real PostgreSQL threads required")
    def test_simultaneous_rotating_refresh_has_one_winner(self):
        first = self.store.create(user_id=self.user.pk)
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return self.store.refresh(first.refresh_token, rotate_refresh=True)
                except InvalidSession:
                    return None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_invalid_tokens_cannot_be_used_as_either_token_type(self):
        first = self.store.create(user_id=self.user.pk)
        for token in (None, "", "guess", first.refresh_token):
            with self.assertRaises(InvalidSession):
                self.store.authenticate(token)
        with self.assertRaises(InvalidSession):
            self.store.refresh(first.access_token, rotate_refresh=True)


@override_settings(MOCK_IDENTITY_ENABLED=True, MOCK_IDENTITY_SIGNING_KEY="mock-test-key",
                   IDENTITY_HASH_KEY="identity-test-key")
@skipUnless(connection.vendor == "postgresql", "Real PostgreSQL threads required")
class ConcurrentIdentityTests(TransactionTestCase):
    def test_two_accounts_same_person_have_one_verified_winner(self):
        rows = [start_mock_verification(user_id=member(label).pk, valid_for=timedelta(minutes=5))
                for label in ("first", "second")]
        barrier = Barrier(2)

        def worker(row):
            close_old_connections()
            try:
                proof = issue_mock_proof(user_id=row.user_id, request_id=row.request_id,
                                         person_key="same-person")
                barrier.wait(timeout=10)
                try:
                    complete_mock_verification(user_id=row.user_id, request_id=row.request_id, proof=proof)
                    return "VERIFIED"
                except ContractError as error:
                    return error.error["code"]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, rows))
        self.assertCountEqual(results, ["VERIFIED", "IDENTITY_ALREADY_REGISTERED"])
        self.assertEqual(UserIdentityVerification.objects.filter(status="VERIFIED").count(), 1)
