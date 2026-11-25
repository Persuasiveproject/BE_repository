"""Opaque session primitives on an explicitly supplied shared Redis client.

No HTTP authentication or token delivery policy is enabled by this module.
Callers choose access lifetime and refresh rotation after team agreement.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math
import secrets
import time
from uuid import uuid4

from redis.exceptions import WatchError

from accounts.models import User


class InvalidSession(ValueError):
    def __init__(self):
        super().__init__("The session is invalid or expired.")


@dataclass(frozen=True)
class SessionTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: float
    session_expires_at: float


def digest(token):
    if not isinstance(token, str) or not 32 <= len(token) <= 256 or not token.isascii():
        raise InvalidSession()
    return hashlib.sha256(token.encode()).hexdigest()


def text(value):
    return value.decode() if isinstance(value, bytes) else value


class SessionStore:
    lifetime = 14 * 24 * 60 * 60

    def __init__(self, client, *, access_lifetime):
        if type(access_lifetime) is not int or not 0 < access_lifetime <= self.lifetime:
            raise ValueError("Specify an access lifetime between 1 second and 14 days.")
        self.client = client
        self.access_lifetime = access_lifetime

    def _key(self, kind, value):
        return f"ye:auth:v1:{kind}:{value}"

    def _write(self, pipe, sid, record, access, refresh, now):
        ttl = max(1, math.ceil(record["expires"] - now))
        access_ttl = max(1, math.ceil(record["access_expires"] - now))
        pipe.set(self._key("session", sid), json.dumps(record), ex=ttl)
        pipe.set(self._key("access", digest(access)), sid, ex=access_ttl)
        pipe.set(self._key("refresh", digest(refresh)), sid, ex=ttl)

    def create(self, *, user_id):
        # Restricted members may log in for permitted read/delete/report actions.
        if not User.objects.filter(pk=user_id).exclude(status=User.Status.DELETED).exists():
            raise InvalidSession()
        now = time.time()
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sid = uuid4().hex
        record = {"user": user_id, "access": digest(access), "refresh": digest(refresh),
                  "expires": now + self.lifetime, "access_expires": now + self.access_lifetime}
        with self.client.pipeline() as pipe:
            self._write(pipe, sid, record, access, refresh, now)
            pipe.execute()
        return SessionTokens(access, refresh, record["access_expires"], record["expires"])

    def authenticate(self, access_token):
        key = digest(access_token)
        sid = text(self.client.get(self._key("access", key)))
        raw = self.client.get(self._key("session", sid)) if sid else None
        if raw is None:
            raise InvalidSession()
        record = json.loads(raw)
        now = time.time()
        if record["access"] != key or record["access_expires"] <= now or record["expires"] <= now:
            raise InvalidSession()
        user = User.objects.filter(pk=record["user"]).exclude(status=User.Status.DELETED).first()
        if user is None:
            raise InvalidSession()
        return user

    def refresh(self, refresh_token, *, rotate_refresh):
        if type(rotate_refresh) is not bool:
            raise ValueError("The caller must explicitly select the refresh rotation policy.")
        token_hash = digest(refresh_token)
        index = self._key("refresh", token_hash)
        for _ in range(5):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(index)
                    sid = text(pipe.get(index))
                    if not sid:
                        raise InvalidSession()
                    key = self._key("session", sid)
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if raw is None:
                        raise InvalidSession()
                    record = json.loads(raw)
                    now = time.time()
                    if record["refresh"] != token_hash or record["expires"] <= now:
                        raise InvalidSession()
                    # API #2 currently specifies ACTIVE for refresh.
                    if not User.objects.filter(pk=record["user"], status=User.Status.ACTIVE).exists():
                        raise InvalidSession()
                    access = secrets.token_urlsafe(32)
                    refresh = secrets.token_urlsafe(32) if rotate_refresh else refresh_token
                    old_access, old_refresh = record["access"], record["refresh"]
                    record.update(access=digest(access), refresh=digest(refresh),
                                  access_expires=min(now + self.access_lifetime, record["expires"]))
                    pipe.multi()
                    pipe.delete(self._key("access", old_access), self._key("refresh", old_refresh))
                    self._write(pipe, sid, record, access, refresh, now)
                    pipe.execute()
                    return SessionTokens(access, refresh, record["access_expires"], record["expires"])
                except WatchError:
                    continue
        raise InvalidSession()

    def revoke(self, access_token):
        """Revoke only the session identified by this access token (no HTTP semantics)."""
        return self._revoke(access_token, "access")

    def revoke_refresh(self, refresh_token):
        """Also support revocation after access expiry without choosing HTTP transport."""
        return self._revoke(refresh_token, "refresh")

    def _revoke(self, token, kind):
        token_hash = digest(token)
        index = self._key(kind, token_hash)
        for _ in range(5):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(index)
                    sid = text(pipe.get(index))
                    if not sid:
                        raise InvalidSession()
                    key = self._key("session", sid)
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if raw is None:
                        raise InvalidSession()
                    record = json.loads(raw)
                    if record[kind] != token_hash:
                        raise InvalidSession()
                    pipe.multi()
                    pipe.delete(key, self._key("access", record["access"]),
                                self._key("refresh", record["refresh"]))
                    pipe.execute()
                    return
                except WatchError:
                    continue
        raise InvalidSession()
