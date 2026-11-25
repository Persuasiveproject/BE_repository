"""Kakao server verification shared by either future login transport."""
from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from accounts.agreements import save_agreements
from accounts.models import User
from accounts.registration import register_kakao_user
from debates.api_contract import ContractError


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def authentication_failed():
    return ContractError(401, "OAUTH_AUTHENTICATION_FAILED", "Kakao authentication failed.")


def _request_json(url, *, token=None, form=None):
    headers = {"Accept": "application/json"}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=utf-8"
    try:
        request = Request(url, data=data, headers=headers)
        with build_opener(NoRedirects()).open(request, timeout=5) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError("Response too large")
        result = json.loads(raw)
        if not isinstance(result, dict) or "error" in result or "code" in result:
            raise ValueError("Invalid response")
        return result
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError):
        # Never propagate token, authorization code, secret or provider response.
        raise authentication_failed() from None


def _credential(value):
    if (not isinstance(value, str) or not value or len(value) > 8192
            or any(ord(char) < 33 or ord(char) == 127 for char in value)):
        raise authentication_failed()
    return value


@dataclass(frozen=True)
class KakaoIdentity:
    provider_id: str
    email: str | None
    nickname: str | None


def verify_access_token(access_token):
    token = _credential(access_token)
    app_id = getattr(settings, "KAKAO_APP_ID", None)
    if type(app_id) is not int or app_id <= 0:
        raise ImproperlyConfigured("KAKAO_APP_ID must be configured before verification.")
    info = _request_json("https://kapi.kakao.com/v1/user/access_token_info", token=token)
    if (type(info.get("app_id")) is not int or info["app_id"] != app_id
            or type(info.get("id")) is not int or info["id"] <= 0
            or type(info.get("expires_in")) is not int or info["expires_in"] <= 0):
        raise authentication_failed()
    profile = _request_json("https://kapi.kakao.com/v2/user/me", token=token)
    if type(profile.get("id")) is not int or profile["id"] != info["id"]:
        raise authentication_failed()
    account = profile.get("kakao_account") or {}
    if not isinstance(account, dict) or not isinstance(account.get("profile", {}), dict):
        raise authentication_failed()
    email = account.get("email") if (
        account.get("is_email_valid") is True and account.get("is_email_verified") is True
    ) else None
    nickname = account.get("profile", {}).get("nickname")
    if email is not None and not isinstance(email, str):
        raise authentication_failed()
    if nickname is not None and not isinstance(nickname, str):
        raise authentication_failed()
    return KakaoIdentity(str(info["id"]), email, nickname)


def verify_authorization_code(code, *, redirect_uri):
    # Caller must validate OAuth state and bind this code to the initiating flow.
    code = _credential(code)
    key = getattr(settings, "KAKAO_REST_API_KEY", "")
    secret = getattr(settings, "KAKAO_CLIENT_SECRET", "")
    allowed = getattr(settings, "KAKAO_REDIRECT_URIS", ())
    if not key or not secret or redirect_uri not in allowed:
        raise ImproperlyConfigured("Configure Kakao key, secret and allowed redirect URIs.")
    result = _request_json("https://kauth.kakao.com/oauth/token", form={
        "grant_type": "authorization_code", "client_id": key, "client_secret": secret,
        "redirect_uri": redirect_uri, "code": code,
    })
    return verify_access_token(result.get("access_token"))


@transaction.atomic
def register_verified_identity(identity, *, agreements):
    """Internal only: identity must come from one of the verification functions."""
    user = User.objects.select_for_update().filter(
        provider="KAKAO", provider_id=identity.provider_id,
    ).first()
    if user is not None:
        if user.status == User.Status.DELETED:
            raise ContractError(409, "ACCOUNT_UNAVAILABLE", "This account is unavailable.")
        return user, False
    # No fabricated profile or email. Missing-profile signup policy remains open.
    user, created = register_kakao_user(
        provider_id=identity.provider_id, email=identity.email, nickname=identity.nickname,
    )
    if created:
        save_agreements(user_id=user.pk, decisions=agreements)
    elif user.status == User.Status.DELETED:
        raise ContractError(409, "ACCOUNT_UNAVAILABLE", "This account is unavailable.")
    return user, created
