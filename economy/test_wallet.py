"""API 45: GET /v1/me/wallet.

성공 계약: 200, {"data": {"coinBalance": integer}}.
실제 Basic 인증을 사용하며 인증 강제 주입이나 DB mock을 사용하지 않는다.
오류 코드는 API 45 개별 명세에 없으므로 기존 공통 처리 규칙을 검증한다.
"""
from base64 import b64encode

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from economy.models import CoinTransaction


class WalletTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/v1/me/wallet"
        self.password = "wallet-test-password"
        self.user = User.objects.create_user(
            "wallet@example.com", "Wallet member",
            password=self.password, coin_balance=10,
        )
        self.other_user = User.objects.create_user(
            "other-wallet@example.com", "Other member",
            password=self.password, coin_balance=73,
        )

    def authenticate(self, user=None, password=None):
        user = user or self.user
        password = self.password if password is None else password
        credentials = b64encode(f"{user.pk}:{password}".encode()).decode()
        self.client.credentials(HTTP_AUTHORIZATION=f"Basic {credentials}")

    def assert_wallet(self, response, balance):
        self.assertEqual(response.status_code, 200)
        # 전체 구조를 비교하므로 잘못된 대소문자와 미등록 추가 필드도 잡는다.
        self.assertEqual(response.json(), {"data": {"coinBalance": balance}})
        # bool도 int의 하위 타입이므로 assertIsInstance 대신 정확한 타입 비교.
        self.assertIs(type(response.json()["data"]["coinBalance"]), int)

    def assert_auth_error(self, response):
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": {
            "code": "AUTHENTICATION_REQUIRED",
            "message": "Sign in to continue.",
        }})
        self.assertTrue(response["WWW-Authenticate"].startswith("Basic "))

    def test_wallet_url(self):
        self.assertEqual(reverse("wallet"), self.url)

    def test_authenticated_response_contract(self):
        self.authenticate()
        self.assert_wallet(self.client.get(self.url), 10)

    def test_zero_balance_is_returned_as_integer(self):
        User.objects.filter(pk=self.user.pk).update(coin_balance=0)
        self.authenticate()
        self.assert_wallet(self.client.get(self.url), 0)

    def test_large_integer_balance_is_not_formatted_as_string(self):
        User.objects.filter(pk=self.user.pk).update(coin_balance=2_147_483_647)
        self.authenticate()
        self.assert_wallet(self.client.get(self.url), 2_147_483_647)

    def test_switching_accounts_returns_each_members_own_balance(self):
        self.authenticate()
        self.assert_wallet(self.client.get(self.url), 10)
        self.authenticate(self.other_user)
        self.assert_wallet(self.client.get(self.url), 73)

    def test_next_request_reads_updated_database_balance(self):
        self.authenticate()
        self.assert_wallet(self.client.get(self.url), 10)
        User.objects.filter(pk=self.user.pk).update(coin_balance=7)
        self.assert_wallet(self.client.get(self.url), 7)

    def test_reading_does_not_change_balances_or_create_transactions(self):
        self.authenticate()
        users_before = list(User.objects.order_by("pk").values())
        transactions_before = list(CoinTransaction.objects.order_by("pk").values())
        for _ in range(2):
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(list(User.objects.order_by("pk").values()), users_before)
        self.assertEqual(
            list(CoinTransaction.objects.order_by("pk").values()), transactions_before,
        )

    def test_anonymous_request_is_rejected(self):
        self.assert_auth_error(self.client.get(self.url))

    def test_wrong_password_is_rejected(self):
        self.authenticate(password="wrong-wallet-password")
        response = self.client.get(self.url)
        self.assert_auth_error(response)
        self.assertNotIn("wrong-wallet-password", response.content.decode())

    def test_deleted_account_is_rejected(self):
        User.objects.filter(pk=self.user.pk).update(status=User.Status.DELETED)
        self.authenticate()
        self.assert_auth_error(self.client.get(self.url))

