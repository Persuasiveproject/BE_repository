"""공개 가입·로그인 API. 서버 로직은 #73(가입 보상)·#74(카카오·약관·세션)을 그대로 쓴다.

1) 수단 확인: POST /v1/auth/phone/code → /v1/auth/phone/verify, 또는 POST /v1/auth/kakao
2) 회원이면 LOGGED_IN(access·refresh 토큰), 처음이면 SIGNUP_REQUIRED(가입표, 30분)
3) POST /v1/auth/signup {signupToken, nickname, agreedTermIds}
4) POST /v1/auth/refresh {refreshToken}, POST /v1/auth/logout, GET /v1/terms
"""
from collections.abc import Mapping
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from redis.exceptions import RedisError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import kakao
from accounts.agreements import save_agreements
from accounts.models import Term, User
from accounts.sessions import InvalidSession
from accounts.views import me_data
from debates.api_contract import ContractError, ContractResponseMixin
from debates.serializers import utc
from moderation.checks import require_no_banned_words

from . import phone
from .tokens import ROTATE_REFRESH, bearer_token, store

SIGNUP_SALT = "accounts.api.signup-ticket"
SIGNUP_MAX_AGE = 30 * 60
TERM_ORDER = {Term.Type.SERVICE: 0, Term.Type.PRIVACY: 1}


def _fields(data, required):
    if not isinstance(data, Mapping) or set(data) != set(required):
        raise ContractError(400, "VALIDATION_ERROR", "Check the request fields.")
    return data


def _iso(ts):
    return utc(datetime.fromtimestamp(ts, tz=dt_timezone.utc))


def _tokens(tokens, user):
    return {"status": "LOGGED_IN", "accessToken": tokens.access_token, "refreshToken": tokens.refresh_token,
            "expiresAt": _iso(tokens.access_expires_at), "sessionExpiresAt": _iso(tokens.session_expires_at),
            "user": me_data(user)}


def logged_in(user, status=200):
    if user.status == User.Status.DELETED:
        raise ContractError(409, "ACCOUNT_UNAVAILABLE", "탈퇴한 계정이에요.")
    try:
        tokens = store().create(user_id=user.pk)
    except (RedisError, InvalidSession) as e:
        raise phone.unavailable() from e
    return Response({"data": _tokens(tokens, user)}, status=status)


def signup_required(method, **identity):
    ticket = signing.dumps({"m": method, **identity}, salt=SIGNUP_SALT)
    return Response({"data": {"status": "SIGNUP_REQUIRED", "signupToken": ticket, "method": method}})


def current_terms():
    """유형마다 지금 시행 중인 최신 약관(#74 save_agreements와 같은 기준), 필수부터."""
    now = timezone.now()
    latest = Term.objects.filter(type=OuterRef("type"), effective_at__lte=now).order_by("-effective_at", "-pk")
    rows = Term.objects.filter(effective_at__lte=now, pk=Subquery(latest.values("pk")[:1]))
    return sorted(rows, key=lambda t: (not t.is_required, TERM_ORDER.get(t.type, 9), t.type))


class AuthView(ContractResponseMixin, APIView):
    authentication_classes = []  # 가입·로그인은 남아 있는 옛 토큰과 상관없이 처리한다
    permission_classes = [AllowAny]


class PhoneCodeView(AuthView):
    def post(self, request):
        number = phone.normalize_phone(_fields(request.data, {"phoneNumber"})["phoneNumber"])
        if number is None:
            raise ContractError(400, "PHONE_INVALID", "휴대폰 번호를 확인해 주세요.")
        code = phone.send_code(number, request.META.get("REMOTE_ADDR"))
        data = {"expiresInSeconds": phone.CODE_SECONDS, "resendAfterSeconds": phone.RESEND_SECONDS}
        if settings.AUTH_SMS_PROVIDER == "mock" and settings.AUTH_SMS_DEV_CODE_VISIBLE:
            data["devCode"] = code  # 모의 발송 시연용. 실제 업체를 쓰면 내려가지 않는다.
        return Response({"data": data})


class PhoneVerifyView(AuthView):
    def post(self, request):
        data = _fields(request.data, {"phoneNumber", "code"})
        number = phone.normalize_phone(data["phoneNumber"])
        if number is None:
            raise ContractError(400, "PHONE_INVALID", "휴대폰 번호를 확인해 주세요.")
        phone.check_code(number, data["code"])
        user = User.objects.filter(provider=phone.PROVIDER, provider_id=phone.phone_id(number)).first()
        if user is not None:
            return logged_in(user)
        return signup_required(phone.PROVIDER, p=number)


class KakaoLoginView(AuthView):
    def post(self, request):
        data = _fields(request.data, {"code", "redirectUri"})
        try:
            identity = kakao.verify_authorization_code(data["code"], redirect_uri=data["redirectUri"])
        except ImproperlyConfigured as e:
            raise ContractError(503, "KAKAO_NOT_CONFIGURED", "카카오 로그인을 준비 중이에요.") from e
        except ContractError as e:
            raise ContractError(401, "OAUTH_AUTHENTICATION_FAILED", "카카오 로그인을 확인하지 못했어요. 다시 시도해 주세요.") from e
        user = User.objects.filter(provider="KAKAO", provider_id=identity.provider_id).first()
        if user is not None:
            return logged_in(user)
        return signup_required("KAKAO", k=identity.provider_id, e=identity.email)


class TermsView(AuthView):
    def get(self, request):
        return Response({"data": {"terms": [{
            "id": str(t.pk), "type": t.type, "version": t.version, "isRequired": t.is_required,
            "content": t.content, "effectiveAt": utc(t.effective_at),
        } for t in current_terms()]}})


class SignupView(AuthView):
    def post(self, request):
        data = _fields(request.data, {"signupToken", "nickname", "agreedTermIds"})
        try:
            ticket = signing.loads(str(data["signupToken"]), salt=SIGNUP_SALT, max_age=SIGNUP_MAX_AGE)
        except signing.SignatureExpired as e:
            raise ContractError(400, "SIGNUP_EXPIRED", "가입 시간이 지났어요. 처음부터 다시 해 주세요.") from e
        except signing.BadSignature as e:
            raise ContractError(400, "SIGNUP_INVALID", "가입 정보를 확인하지 못했어요. 처음부터 다시 해 주세요.") from e

        nickname = data["nickname"]
        if not isinstance(nickname, str) or not 1 <= len(nickname.strip()) <= 30 or "\x00" in nickname:
            raise ContractError(400, "NICKNAME_INVALID", "닉네임은 1~30자로 정해 주세요.")
        nickname = nickname.strip()
        try:
            require_no_banned_words(nickname)
        except ContractError as e:
            raise ContractError(400, "NICKNAME_INVALID", e.error["message"]) from e

        agreed = data["agreedTermIds"]
        if not isinstance(agreed, list) or not all(isinstance(i, str) and i.isdecimal() for i in agreed):
            raise ContractError(400, "VALIDATION_ERROR", "Check the request fields.")
        terms = current_terms()
        if not any(t.is_required for t in terms):
            raise ContractError(503, "TERMS_NOT_PUBLISHED", "가입을 준비 중이에요. 잠시 후 다시 시도해 주세요.")
        missing = [str(t.pk) for t in terms if t.is_required and str(t.pk) not in agreed]
        if missing:
            raise ContractError(400, "REQUIRED_TERM_NOT_AGREED", "필수 약관에 동의해 주세요.", details={"termIds": missing})
        decisions = {t.pk: str(t.pk) in agreed for t in terms}

        method = ticket.get("m")
        try:
            if method == phone.PROVIDER:
                with transaction.atomic():
                    user, created = phone.register_phone_user(number=ticket["p"], nickname=nickname)
                    if created:
                        save_agreements(user_id=user.pk, decisions=decisions)
            elif method == "KAKAO":
                identity = kakao.KakaoIdentity(ticket["k"], ticket.get("e"), nickname)
                user, created = kakao.register_verified_identity(identity, agreements=decisions)
            else:
                raise ContractError(400, "SIGNUP_INVALID", "가입 정보를 확인하지 못했어요. 처음부터 다시 해 주세요.")
        except ValidationError as e:
            if method == "KAKAO" and not ticket.get("e"):
                # #73 정책: 이메일 없는 카카오 가입은 아직 보류(가짜 이메일을 만들지 않는다).
                raise ContractError(400, "KAKAO_EMAIL_REQUIRED", "카카오 계정 이메일 제공에 동의해야 가입할 수 있어요.") from e
            raise ContractError(400, "NICKNAME_INVALID", "닉네임을 확인해 주세요.") from e
        except IntegrityError as e:
            raise ContractError(409, "ALREADY_REGISTERED", "이미 가입된 계정이에요. 로그인해 주세요.") from e
        if not created:
            # 같은 가입표를 두 번 보냈거나 그사이 가입이 끝났다. 보상·약관은 다시 처리하지 않는다.
            raise ContractError(409, "ALREADY_REGISTERED", "이미 가입된 계정이에요. 로그인해 주세요.")
        return logged_in(user, status=201)


class RefreshView(AuthView):
    def post(self, request):
        token = _fields(request.data, {"refreshToken"})["refreshToken"]
        try:
            tokens = store().refresh(token, rotate_refresh=ROTATE_REFRESH)
            user = store().authenticate(tokens.access_token)
        except InvalidSession as e:
            raise ContractError(401, "SESSION_EXPIRED", "다시 로그인해 주세요.") from e
        except RedisError as e:
            raise phone.unavailable() from e
        return Response({"data": _tokens(tokens, user)})


class LogoutView(AuthView):
    def post(self, request):
        token = bearer_token(request)
        if token is None:
            raise ContractError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
        try:
            store().revoke(token)
        except InvalidSession:
            pass  # 이미 끝난 세션이면 그대로 로그아웃된 것으로 본다
        except RedisError as e:
            raise phone.unavailable() from e
        return Response(status=204)
