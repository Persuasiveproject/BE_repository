from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase

from accounts.models import User
from accounts.registration import register_kakao_user
from economy.models import CoinTransaction, CreditHistory


def signup(**overrides):
    return register_kakao_user(**{
        "provider_id": "verified-kakao-123", "email": "member@example.com",
        "nickname": "새회원", **overrides,
    })


class RegistrationTests(TestCase):
    def test_new_account_balances_and_complete_ledgers(self):
        user, created = signup()
        user.refresh_from_db()
        self.assertTrue(created)
        self.assertFalse(user.has_usable_password())
        self.assertEqual((user.provider, user.provider_id), ("KAKAO", "verified-kakao-123"))
        self.assertEqual((user.coin_balance, user.credit), (10, Decimal("30.000000")))
        self.assertEqual(CoinTransaction.objects.count(), 1)
        coin = CoinTransaction.objects.get(user=user)
        self.assertEqual(
            (coin.amount, coin.type, coin.balance_after, coin.ref_type, coin.ref_id),
            (10, "SIGNUP_BONUS", 10, "USER", user.pk),
        )
        self.assertIsNone(coin.created_by_id)
        self.assertIsNone(coin.reverses_transaction_id)
        credit = CreditHistory.objects.get(user=user)
        self.assertEqual((credit.amount, credit.reason, credit.ref_id),
                         (Decimal("30.000000"), "SIGNUP_BONUS", user.pk))

    def test_repeat_after_spending_does_not_refill_or_overwrite_profile(self):
        user, _ = signup()
        User.objects.filter(pk=user.pk).update(coin_balance=2, credit=Decimal("25.5"))
        again, created = signup(email="changed@example.com", nickname="다른이름")
        self.assertFalse(created)
        self.assertEqual(again.pk, user.pk)
        self.assertEqual((again.coin_balance, again.credit), (2, Decimal("25.5")))
        self.assertEqual((again.email, again.nickname), ("member@example.com", "새회원"))
        self.assertEqual(CoinTransaction.objects.count(), 1)
        self.assertEqual(CreditHistory.objects.count(), 1)

    def test_existing_account_without_bonus_is_not_backfilled(self):
        user = User.objects.create_user(
            email="old@example.com", nickname="기존", provider="KAKAO",
            provider_id="verified-kakao-123",
        )
        again, created = signup()
        self.assertEqual(again.pk, user.pk)
        self.assertFalse(created)
        self.assertEqual((again.coin_balance, again.credit), (0, 0))
        self.assertFalse(CoinTransaction.objects.exists())
        self.assertFalse(CreditHistory.objects.exists())

    def test_deleted_and_suspended_accounts_do_not_receive_bonus_again(self):
        user, _ = signup()
        for status in (User.Status.DELETED, User.Status.SUSPENDED):
            User.objects.filter(pk=user.pk).update(status=status)
            again, created = signup()
            self.assertEqual((again.pk, again.status, created), (user.pk, status, False))
        self.assertEqual(CoinTransaction.objects.count(), 1)
        self.assertEqual(CreditHistory.objects.count(), 1)

    def test_distinct_kakao_accounts_each_receive_bonus_even_with_same_email(self):
        first, _ = signup()
        second, created = signup(provider_id="verified-kakao-456")
        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(CoinTransaction.objects.count(), 2)
        self.assertEqual(CreditHistory.objects.count(), 2)

    def test_ledger_failure_rolls_back_account_and_both_balances_then_retry_succeeds(self):
        for model in (CoinTransaction, CreditHistory):
            with self.subTest(model=model.__name__):
                with patch.object(model.objects, "create", side_effect=RuntimeError("ledger failed")):
                    with self.assertRaises(RuntimeError):
                        signup()
                self.assertFalse(User.objects.exists())
                self.assertFalse(CoinTransaction.objects.exists())
                self.assertFalse(CreditHistory.objects.exists())
        user, created = signup()
        self.assertTrue(created)
        self.assertEqual((user.coin_balance, user.credit), (10, Decimal("30")))

    def test_invalid_new_account_data_never_grants_rewards(self):
        for invalid in ({"provider_id": ""}, {"provider_id": None},
                        {"provider_id": "x" * 256}, {"email": ""},
                        {"email": "invalid"}, {"nickname": "   "},
                        {"nickname": "x" * 31}):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                signup(**invalid)
        self.assertFalse(User.objects.exists())
        self.assertFalse(CoinTransaction.objects.exists())
        self.assertFalse(CreditHistory.objects.exists())

    def test_database_rejects_duplicate_provider_identity(self):
        signup()
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(email="other@example.com", nickname="중복",
                                     provider="KAKAO", provider_id="verified-kakao-123")

    def test_non_social_accounts_can_still_have_null_provider_identity(self):
        for index in range(2):
            User.objects.create_user(email=f"internal{index}@example.com", nickname="내부")
        self.assertEqual(User.objects.count(), 2)
        self.assertFalse(CoinTransaction.objects.exists())


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL concurrency")
class ConcurrentRegistrationTests(TransactionTestCase):
    def test_simultaneous_signup_creates_one_account_and_one_bonus(self):
        barrier = Barrier(2)

        def synchronized_password(value):
            # Both requests must observe no account before racing to insert.
            barrier.wait(timeout=10)
            return make_password(value)

        def worker():
            close_old_connections()
            try:
                user, created = signup()
                return user.pk, created
            finally:
                close_old_connections()

        with patch("accounts.registration.make_password", side_effect=synchronized_password), \
                ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(CoinTransaction.objects.count(), 1)
        self.assertEqual(CreditHistory.objects.count(), 1)
        user = User.objects.get()
        self.assertEqual((user.coin_balance, user.credit), (10, Decimal("30")))
