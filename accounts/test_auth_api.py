from typing import ClassVar
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import kakao
from accounts.api import phone
from accounts.api.tokens import store
from accounts.models import Term, User, UserTermAgreement, WithdrawnIdentity
from economy.models import CoinTransaction, CreditHistory


def clear_auth_keys():
    r = phone.redis()
    for key in r.scan_iter("ye:auth:v1:*"):
        r.delete(key)


class AuthFixture:
    def setUp(self):
        clear_auth_keys()
        self.addCleanup(clear_auth_keys)
        self.client = APIClient()
        now = timezone.now()
        self.service = Term.objects.create(type="SERVICE", version="1", content="s", is_required=True, effective_at=now)
        self.privacy = Term.objects.create(type="PRIVACY", version="1", content="p", is_required=True, effective_at=now)
        self.marketing = Term.objects.create(type="MARKETING", version="1", content="m", is_required=False, effective_at=now)
        self.required = [str(self.service.pk), str(self.privacy.pk)]

    def signup(self, ticket, **extra):
        body = {"signupToken": ticket, "nickname": "판다", "agreedTermIds": self.required, **extra}
        return self.client.post(reverse("auth-signup"), body, format="json")

    def assert_suspended_account_can_rejoin(self, begin):
        initial = self.signup(begin().json()["data"]["signupToken"], nickname="Before")
        self.assertEqual(initial.status_code, 201, initial.content)
        old = initial.json()["data"]
        User.objects.filter(pk=old["user"]["id"]).update(credit="12.500000", status=User.Status.SUSPENDED)
        member = APIClient()
        member.credentials(HTTP_AUTHORIZATION=f"Bearer {old['accessToken']}")
        self.assertEqual(member.delete(reverse("me")).status_code, 204)

        again = begin()
        self.assertEqual(again.status_code, 200, again.content)
        rejoined = self.signup(again.json()["data"]["signupToken"], nickname="After")
        self.assertEqual(rejoined.status_code, 201, rejoined.content)
        new = rejoined.json()["data"]
        self.assertNotEqual(new["user"]["id"], old["user"]["id"])
        self.assertEqual((new["user"]["status"], new["user"]["credit"]), ("SUSPENDED", "12.500000"))
        self.assertFalse(CoinTransaction.objects.filter(user_id=new["user"]["id"]).exists())
        self.assertEqual(UserTermAgreement.objects.filter(user_id=new["user"]["id"], agreed=True).count(), 2)
        self.assertFalse(WithdrawnIdentity.objects.exists())
        member.credentials(HTTP_AUTHORIZATION=f"Bearer {new['accessToken']}")
        self.assertEqual(member.get(reverse("me")).json()["data"], new["user"])


@override_settings(AUTH_SMS_PROVIDER="mock", AUTH_SMS_DEV_CODE_VISIBLE=True)
class PhoneFlowTests(AuthFixture, TestCase):
    def code(self, number="010-1234-5678"):
        res = self.client.post(reverse("auth-phone-code"), {"phoneNumber": number}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()["data"]["devCode"]

    def verify(self, code, number="010-1234-5678"):
        return self.client.post(reverse("auth-phone-verify"), {"phoneNumber": number, "code": code}, format="json")

    def test_suspended_rejoin_through_phone_and_signup_apis(self):
        def begin():
            phone.redis().delete(phone._key("cooldown", phone.phone_id("01012345678")))
            return self.verify(self.code())

        self.assert_suspended_account_can_rejoin(begin)

    def test_signup_then_bearer_session_then_logout_then_direct_login(self):
        res = self.verify(self.code())
        self.assertEqual(res.json()["data"]["status"], "SIGNUP_REQUIRED")
        res = self.signup(res.json()["data"]["signupToken"])
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()["data"]
        self.assertEqual(data["user"]["nickname"], "판다")
        self.assertTrue(data["refreshToken"])

        user = User.objects.get(provider="PHONE")
        self.assertNotIn("01012345678", user.provider_id)  # 번호 원문은 남기지 않는다
        self.assertEqual(user.email, "")
        self.assertEqual((user.coin_balance, int(user.credit)), (10, 30))
        self.assertEqual(CoinTransaction.objects.get(user=user).type, "SIGNUP_BONUS")
        self.assertEqual(CreditHistory.objects.get(user=user).reason, "SIGNUP_BONUS")
        self.assertEqual(
            {(a.term_id, a.agreed) for a in UserTermAgreement.objects.filter(user=user)},
            {(self.service.pk, True), (self.privacy.pk, True), (self.marketing.pk, False)},
        )

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {data['accessToken']}")
        self.assertEqual(api.get(reverse("me")).json()["data"]["id"], str(user.pk))
        self.assertEqual(api.post(reverse("auth-logout")).status_code, 204)
        self.assertEqual(api.get(reverse("me")).status_code, 401)

        r = phone.redis()
        for key in r.scan_iter("ye:auth:v1:phone:cooldown:*"):
            r.delete(key)
        res = self.verify(self.code())
        self.assertEqual(res.json()["data"]["status"], "LOGGED_IN")
        self.assertEqual(res.json()["data"]["user"]["id"], str(user.pk))
        self.assertEqual(CoinTransaction.objects.filter(user=user).count(), 1)  # 다시 지급하지 않는다

    def test_refresh_rotates_tokens(self):
        ticket = self.verify(self.code()).json()["data"]["signupToken"]
        data = self.signup(ticket).json()["data"]
        res = self.client.post(reverse("auth-refresh"), {"refreshToken": data["refreshToken"]}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        fresh = res.json()["data"]
        self.assertNotEqual(fresh["refreshToken"], data["refreshToken"])
        old = APIClient()
        old.credentials(HTTP_AUTHORIZATION=f"Bearer {data['accessToken']}")
        self.assertEqual(old.get(reverse("me")).status_code, 401)  # 갱신하면 이전 access는 끝난다
        res = self.client.post(reverse("auth-refresh"), {"refreshToken": data["refreshToken"]}, format="json")
        self.assertEqual(res.json()["error"]["code"], "SESSION_EXPIRED")

    def test_wrong_code_attempts_single_use_and_limits(self):
        code = self.code()
        wrong = "000000" if code != "000000" else "111111"
        res = self.verify(wrong)
        self.assertEqual(res.json()["error"]["details"]["attemptsLeft"], phone.MAX_ATTEMPTS - 1)
        self.assertEqual(self.verify(code).json()["data"]["status"], "SIGNUP_REQUIRED")
        self.assertEqual(self.verify(code).json()["error"]["code"], "CODE_EXPIRED")
        res = self.client.post(reverse("auth-phone-code"), {"phoneNumber": "01012345678"}, format="json")
        self.assertEqual(res.json()["error"]["code"], "CODE_RESEND_TOO_SOON")

        code = None
        r = phone.redis()
        for _ in range(phone.MAX_ATTEMPTS):
            r.delete(*r.keys("ye:auth:v1:phone:cooldown:*") or ["x"])
        r.delete(*r.keys("ye:auth:v1:phone:cooldown:*") or ["x"])
        code = self.code("01099998888")
        for _ in range(phone.MAX_ATTEMPTS):
            self.verify("000000" if code != "000000" else "111111", "01099998888")
        self.assertEqual(self.verify(code, "01099998888").json()["error"]["code"], "CODE_ATTEMPTS_EXCEEDED")

    def test_signup_validation(self):
        ticket = self.verify(self.code()).json()["data"]["signupToken"]
        res = self.signup(ticket, agreedTermIds=[str(self.service.pk)])
        self.assertEqual(res.json()["error"]["code"], "REQUIRED_TERM_NOT_AGREED")
        self.assertEqual(res.json()["error"]["details"]["termIds"], [str(self.privacy.pk)])
        self.assertEqual(self.signup(ticket, nickname="  ").json()["error"]["code"], "NICKNAME_INVALID")
        self.assertEqual(self.signup("forged").json()["error"]["code"], "SIGNUP_INVALID")
        self.assertEqual(self.signup(ticket).status_code, 201)
        self.assertEqual(self.signup(ticket).json()["error"]["code"], "ALREADY_REGISTERED")
        self.assertEqual(CoinTransaction.objects.count(), 1)

    def test_terms_and_dev_code_visibility(self):
        terms = self.client.get(reverse("terms")).json()["data"]["terms"]
        self.assertEqual([t["type"] for t in terms], ["SERVICE", "PRIVACY", "MARKETING"])
        with override_settings(AUTH_SMS_DEV_CODE_VISIBLE=False):
            res = self.client.post(reverse("auth-phone-code"), {"phoneNumber": "01011112222"}, format="json")
        self.assertNotIn("devCode", res.json()["data"])
        res = self.client.post(reverse("auth-phone-code"), {"phoneNumber": "02-123-4567"}, format="json")
        self.assertEqual(res.json()["error"]["code"], "PHONE_INVALID")


class KakaoFlowTests(AuthFixture, TestCase):
    body: ClassVar = {"code": "kakao-code", "redirectUri": "https://app.example/auth/kakao"}

    def kakao(self, identity):
        with patch("accounts.api.views.kakao.verify_authorization_code", return_value=identity):
            return self.client.post(reverse("auth-kakao"), self.body, format="json")

    def test_suspended_rejoin_through_kakao_and_signup_apis(self):
        identity = kakao.KakaoIdentity("9231", None, None)
        self.assert_suspended_account_can_rejoin(lambda: self.kakao(identity))

    def test_signup_with_verified_email_then_login(self):
        res = self.kakao(kakao.KakaoIdentity("777", "m@example.com", "카카오닉"))
        self.assertEqual(res.json()["data"]["status"], "SIGNUP_REQUIRED")
        res = self.signup(res.json()["data"]["signupToken"], nickname="펭귄")
        self.assertEqual(res.status_code, 201, res.content)
        user = User.objects.get(provider="KAKAO", provider_id="777")
        self.assertEqual((user.nickname, user.email), ("펭귄", "m@example.com"))  # 앱에서 고른 닉네임
        self.assertEqual(self.kakao(kakao.KakaoIdentity("777", None, None)).json()["data"]["status"], "LOGGED_IN")

    def test_email_less_kakao_signup_is_allowed(self):
        ticket = self.kakao(kakao.KakaoIdentity("888", None, None)).json()["data"]["signupToken"]
        res = self.signup(ticket)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(User.objects.get(provider="KAKAO", provider_id="888").email, "")

    def test_not_configured_and_failed_exchange(self):
        res = self.client.post(reverse("auth-kakao"), self.body, format="json")
        self.assertEqual(res.json()["error"]["code"], "KAKAO_NOT_CONFIGURED")
        with patch("accounts.api.views.kakao.verify_authorization_code", side_effect=kakao.authentication_failed()):
            res = self.client.post(reverse("auth-kakao"), self.body, format="json")
        self.assertEqual(res.status_code, 401)


class SessionAuthTests(TestCase):
    def setUp(self):
        clear_auth_keys()
        self.addCleanup(clear_auth_keys)

    def test_deleted_user_session_rejected_and_basic_header_kept(self):
        user = User.objects.create_user("t@example.com", "T")
        tokens = store().create(user_id=user.pk)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access_token}")
        self.assertEqual(api.get(reverse("me")).status_code, 200)
        User.objects.filter(pk=user.pk).update(status=User.Status.DELETED)
        res = api.get(reverse("me"))
        self.assertEqual(res.status_code, 401)
        self.assertTrue(res["WWW-Authenticate"].startswith("Basic"))
