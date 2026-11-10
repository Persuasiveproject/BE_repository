"""FC-CM-000-006: a newly suspended member cannot commit a write."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.db import connections, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from discussions.models import Persuasion
from .models import Debate, Stance, StanceHistory
from .test_stance import StanceFixture


class AccountStateConcurrencyTests(StanceFixture, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_suspension_while_waiting_for_debate_lock_blocks_write(self):
        waiting = Event()

        def submit():
            client = APIClient()
            # Deliberately keep the ACTIVE instance read before the suspension.
            client.force_authenticate(user=self.user)

            def observe_query(execute, sql, params, many, context):
                if 'FROM "debates"' in sql and "FOR UPDATE" in sql:
                    waiting.set()
                return execute(sql, params, many, context)

            try:
                with connections["default"].execute_wrapper(observe_query):
                    return client.patch(
                        reverse("my-stance", args=[self.debate.pk]),
                        {"stance": Stance.CON, "opinionId": str(self.opinion.pk), "version": 0},
                        format="json", headers={"Idempotency-Key": "suspension-race"},
                    )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Debate.objects.select_for_update().get(pk=self.debate.pk)
                result = pool.submit(submit)
                self.assertTrue(waiting.wait(timeout=10), "Request did not reach the debate lock")
                User.objects.filter(pk=self.user.pk).update(status=User.Status.SUSPENDED)
            response = result.result(timeout=15)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "FORBIDDEN")
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.current_stance, Stance.PRO)
        self.assertEqual(self.participant.version, 0)
        self.assertFalse(StanceHistory.objects.exists())
        self.assertFalse(Persuasion.objects.exists())
