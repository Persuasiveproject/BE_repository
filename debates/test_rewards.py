from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from discussions.models import Opinion, Persuasion
from economy.models import CoinTransaction, CreditHistory
from engagement.models import Notification

from .models import Category, Debate, DebateParticipant, DebateStatistics, Stance, StanceHistory
from .result_state import clear_result_state
from .results import compute_debate_result
from .rewards import PERSUASION_REWARD


class DebateRewardTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user("creator@example.com", "Creator")
        self.author = User.objects.create_user("writer@example.com", "Writer", credit=Decimal("30"))
        self.category = Category.objects.create(name="Rewards")

    def debate_with_opinion(self):
        debate = Debate.objects.create(
            creator=self.creator, category=self.category, type=Debate.Type.ENDLESS,
            title="Reward", content="Topic", status=Debate.Status.CLOSED, closed_at=timezone.now(),
        )
        self.addCleanup(clear_result_state, debate.pk)
        part = DebateParticipant.objects.create(
            debate=debate, user=self.author, anonymous_no=1, initial_stance=Stance.PRO, current_stance=Stance.PRO,
        )
        opinion = Opinion.objects.create(
            debate=debate, participant=part, stance_at_write=Stance.PRO,
            content="Reason", content_plain="Reason",
        )
        return debate, opinion

    def participant(self, debate, initial=Stance.CON, current=Stance.PRO):
        index = DebateParticipant.objects.filter(debate=debate).count() + 1
        user = User.objects.create_user(f"member-{debate.pk}-{index}@example.com", f"Member {index}")
        return DebateParticipant.objects.create(
            debate=debate, user=user, anonymous_no=index, initial_stance=initial, current_stance=current,
        )

    def change(self, part, from_stance, to_stance, opinion=None):
        persuasion = None
        if opinion is not None:
            persuasion = Persuasion.objects.create(
                opinion=opinion, persuaded_user=part.user, from_stance=from_stance, to_stance=to_stance,
            )
        return StanceHistory.objects.create(
            participant=part, from_stance=from_stance, to_stance=to_stance, persuasion=persuasion,
        )

    def test_settlement_rewards_last_opinion_only_and_is_idempotent(self):
        debate, opinion = self.debate_with_opinion()
        person = self.participant(debate)
        full = self.change(person, Stance.CON, Stance.PRO, opinion)
        half = self.change(
            self.participant(debate, current=Stance.NEUTRAL), Stance.CON, Stance.NEUTRAL, opinion,
        )
        kept = self.participant(debate, current=Stance.CON)
        before_returning = self.change(kept, Stance.CON, Stance.PRO, opinion)
        self.change(kept, Stance.PRO, Stance.CON, opinion)
        direct = self.participant(debate, current=Stance.NEUTRAL)
        before_direct = self.change(direct, Stance.CON, Stance.PRO, opinion)
        self.change(direct, Stance.PRO, Stance.NEUTRAL)
        for _ in range(15):
            self.participant(debate, current=Stance.CON)

        call_command("recover_results")
        call_command("recover_results")

        self.author.refresh_from_db()
        self.creator.refresh_from_db()
        opinion.refresh_from_db()
        self.assertEqual(self.author.credit, Decimal("30.375000"))
        self.assertEqual(self.creator.coin_balance, 1)
        self.assertEqual(opinion.persuasion_count, 2)
        self.assertEqual(DebateStatistics.objects.get(debate=debate).total_participants, 20)
        self.assertEqual(CreditHistory.objects.get(user=self.author).ref_id, debate.pk)
        self.assertEqual(CoinTransaction.objects.get(user=self.creator).amount, 1)
        self.assertCountEqual(
            Persuasion.objects.filter(reward_given=True).values_list("pk", flat=True),
            [full.persuasion_id, half.persuasion_id],
        )
        self.assertFalse(Persuasion.objects.filter(
            pk__in=[before_returning.persuasion_id, before_direct.persuasion_id], reward_given=True,
        ).exists())
        notification = Notification.objects.get(user=self.author)
        self.assertEqual((notification.type, notification.target_id), ("PERSUASION_REWARD", debate.pk))
        self.assertIn("2건", notification.body)
        self.assertIn("0.375000", notification.body)
        self.assertNotIn(person.user.nickname, notification.body)

    def test_daily_caps_reset_at_korea_midnight(self):
        before_midnight = datetime(2026, 11, 17, 14, 59, tzinfo=UTC)
        expected = [Decimal("1"), Decimal("0.5"), Decimal("0.25"), None, Decimal("1")]
        for index, amount in enumerate(expected):
            moment = before_midnight if index < 4 else before_midnight + timedelta(minutes=1)
            with patch("django.utils.timezone.now", return_value=moment):
                debate, opinion = self.debate_with_opinion()
                for _ in range(5):
                    self.change(self.participant(debate), Stance.CON, Stance.PRO, opinion)
                self.assertTrue(compute_debate_result(debate.pk))
                entry = CreditHistory.objects.filter(reason=PERSUASION_REWARD, ref_id=debate.pk).first()
                self.assertEqual(entry.amount if entry else None, amount)
                opinion.refresh_from_db()
                self.assertEqual(opinion.persuasion_count, 5)
        self.assertEqual(Notification.objects.count(), 4)
        self.assertFalse(CoinTransaction.objects.exists())

    def test_reward_failure_rolls_back_result_and_payment(self):
        debate, opinion = self.debate_with_opinion()
        self.change(self.participant(debate), Stance.CON, Stance.PRO, opinion)
        with patch("debates.rewards.Notification.objects.create", side_effect=RuntimeError("retry")):
            with self.assertLogs("debates.results", level="ERROR"):
                self.assertFalse(compute_debate_result(debate.pk))
        self.author.refresh_from_db()
        self.assertEqual(self.author.credit, Decimal("30"))
        self.assertFalse(DebateStatistics.objects.filter(debate=debate).exists())
        self.assertFalse(CreditHistory.objects.exists())
        self.assertFalse(Persuasion.objects.filter(reward_given=True).exists())
        clear_result_state(debate.pk)
        self.assertTrue(compute_debate_result(debate.pk))
        self.assertEqual(CreditHistory.objects.count(), 1)
