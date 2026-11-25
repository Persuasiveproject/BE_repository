"""#74 SessionStore를 Bearer 헤더로 쓰는 연결부.

- 로그인하면 access·refresh 토큰을 응답 본문으로 준다. 요청에는 `Authorization: Bearer <access>`를 붙인다.
- access 유효기간은 AUTH_ACCESS_SECONDS(기본 14일 = 세션 최대). refresh 회전은 켠다(갱신마다 새 refresh).
- 기존 시연 계정의 Basic 인증은 그대로다. 401 응답의 WWW-Authenticate는 기존 계약대로 Basic이 앞선다.
"""
from django.conf import settings
from redis.exceptions import RedisError
from rest_framework import authentication, exceptions

from accounts.sessions import InvalidSession, SessionStore
from debates.result_state import _client

ROTATE_REFRESH = True


def store():
    return SessionStore(_client(settings.AUTH_SESSION_REDIS_URL), access_lifetime=settings.AUTH_ACCESS_SECONDS)


def bearer_token(request):
    parts = authentication.get_authorization_header(request).split()
    if len(parts) != 2 or parts[0].lower() != b"bearer":
        return None
    try:
        return parts[1].decode("ascii")
    except UnicodeError:
        return None


class BearerSessionAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        token = bearer_token(request)
        if token is None:
            return None
        try:
            return store().authenticate(token), token
        except InvalidSession as e:
            raise exceptions.AuthenticationFailed("Sign in again.") from e
        except RedisError as e:
            # 저장소 장애를 로그인 성공으로 처리하지 않는다.
            raise exceptions.AuthenticationFailed("Sign in again.") from e

    def authenticate_header(self, request):
        return 'Bearer realm="api"'
