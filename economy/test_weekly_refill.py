from datetime import UTC, date, datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User
from economy.models import CoinTransaction
from economy.weekly_refill import refill_weekly_coins


# 15:00 UTC Sunday is Monday 00:00 in Seoul.
WEEK_START = datetime(2026, 11, 22, 15, tzinfo=UTC)
WEEK = date(2026, 11, 23)


class WeeklyRefillTests(TestCase):
    def account(self, coin_balance, status=User.Status.ACTIVE, *, created_at=None):
        user = User.objects.create_user(
            f"member{User.objects.count()}@example.com", "Member", coin_balance=coin_balance, status=status,
        )
        User.objects.filter(pk=user.pk).update(created_at=created_at or WEEK_START - timedelta(days=1))
        return user

    def run_for(self, instant=WEEK_START):
        with patch("economy.weekly_refill.timezone.now", return_value=instant):
            return refill_weekly_coins()

    def test_refills_only_active_members_below_three_with_ledger_entries(self):
        empty = self.account(0)
        low = self.account(2)
        at_target = self.account(3)
        above_target = self.account(5)
        suspended = self.account(1, User.Status.SUSPENDED)
        deleted = self.account(1, User.Status.DELETED)
        late_joiner = self.account(1, created_at=WEEK_START + timedelta(seconds=1))

        with patch("economy.weekly_refill.timezone.now", return_value=WEEK_START):
            output = StringIO()
            call_command("refill_weekly_coins", stdout=output)
        self.assertEqual(output.getvalue().strip(), '{"refilled": 2}')

        for account, balance, processed in (
            (empty, 3, WEEK), (low, 3, WEEK), (at_target, 3, WEEK), (above_target, 5, WEEK),
            (suspended, 1, None), (deleted, 1, None), (late_joiner, 1, None),
        ):
            account.refresh_from_db()
            self.assertEqual((account.coin_balance, account.last_weekly_coin_refill_on), (balance, processed))
        self.assertEqual(list(CoinTransaction.objects.order_by("user_id").values_list(
            "user_id", "type", "amount", "balance_after", "ref_type", "ref_id",
        )), [(empty.pk, "WEEKLY_REFILL", 3, 3, "USER", empty.pk),
             (low.pk, "WEEKLY_REFILL", 1, 3, "USER", low.pk)])

    def test_retry_after_spending_and_next_korean_week(self):
        low = self.account(1)
        full = self.account(4)
        self.assertEqual(self.run_for(), 1)
        User.objects.filter(pk__in=[low.pk, full.pk]).update(coin_balance=1)

        self.assertEqual(self.run_for(WEEK_START + timedelta(days=7, seconds=-1)), 0)
        self.assertEqual(CoinTransaction.objects.count(), 1)
        self.assertEqual(self.run_for(WEEK_START + timedelta(days=7)), 2)
        self.assertEqual(CoinTransaction.objects.count(), 3)

    def test_ledger_failure_rolls_back_balance_and_week_marker(self):
        account = self.account(1)
        with patch("economy.weekly_refill.CoinTransaction.objects.create", side_effect=RuntimeError("ledger failed")):
            with self.assertRaisesMessage(RuntimeError, "ledger failed"):
                self.run_for()
        account.refresh_from_db()
        self.assertEqual(account.coin_balance, 1)
        self.assertIsNone(account.last_weekly_coin_refill_on)
        self.assertEqual(self.run_for(), 1)
