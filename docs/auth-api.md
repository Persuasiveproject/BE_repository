# 공개 가입·로그인 API (PR #74 위)

#73(가입 보상)·#74(카카오·약관·세션·모의 본인 확인)의 서버 로직을 그대로 쓰고, 그 위에 앱이 부르는 HTTP 계층만 더한다. 새 테이블·마이그레이션은 없다.

## 결정 사항 (하현승, 2026-11-25)

- 가입 수단: **휴대폰 번호 또는 카카오 중 하나**. 두 수단은 서로 다른 계정이다.
- 휴대폰 인증 문자: 지금은 모의 발송(mock). 실제 업체는 나중에 `accounts/api/phone.py: send_sms`에 붙인다.
- 토큰 전달: #74 `SessionStore`의 access·refresh를 응답 본문으로 주고, 요청은 `Authorization: Bearer <access>`.

## 팀 확인이 필요한 것

1. **이메일 없는 휴대폰 가입**: 휴대폰 회원은 이메일이 없어 빈 값으로 둔다. #73은 카카오 가입에서 이메일 없는 가입을 보류했으므로 카카오 쪽은 그대로 따르고(`KAKAO_EMAIL_REQUIRED`), 휴대폰만 예외다.
2. **access 유효기간**: 기본 14일(세션 최대)로 앱이 갱신 없이 쓰게 했다. 짧게 하려면 `AUTH_ACCESS_SECONDS`를 줄이고 앱에 `/v1/auth/refresh` 호출을 붙인다. refresh는 회전한다.
3. **휴대폰 가입은 본인 확인(`identityVerified`)으로 치지 않는다.** 문자 인증은 번호 소유 확인일 뿐이라 #74의 모의 본인 확인과 섞지 않았다.

## 엔드포인트

| 경로 | 본문 | 응답 |
| --- | --- | --- |
| `POST /v1/auth/phone/code` | `{phoneNumber}` | `{expiresInSeconds, resendAfterSeconds, devCode?}` |
| `POST /v1/auth/phone/verify` | `{phoneNumber, code}` | LOGGED_IN 또는 SIGNUP_REQUIRED |
| `POST /v1/auth/kakao` | `{code, redirectUri}` | LOGGED_IN 또는 SIGNUP_REQUIRED |
| `POST /v1/auth/signup` | `{signupToken, nickname, agreedTermIds}` | 201 LOGGED_IN |
| `POST /v1/auth/refresh` | `{refreshToken}` | LOGGED_IN 모양(새 토큰) |
| `POST /v1/auth/logout` | 헤더의 Bearer | 204 |
| `GET /v1/terms` | | 시행 중 최신 약관, 필수부터 |

LOGGED_IN: `{status, accessToken, refreshToken, expiresAt, sessionExpiresAt, user}`(`user`는 GET /v1/me와 같다).
SIGNUP_REQUIRED: `{status, signupToken, method}`. 가입표는 30분 유효한 서명 문자열이다(저장하지 않음).

## 휴대폰 인증 규칙

- 번호는 숫자만(`+82`·하이픈 허용), 01X 휴대폰만. 회원은 `provider=PHONE`, `provider_id=HMAC(번호)`로 저장해 원문을 남기지 않고 #73의 유일 제약을 그대로 쓴다.
- 인증번호 6자리, 3분, 한 번만 사용, 5번까지 틀릴 수 있음. 재발송 30초, 번호당 하루 5회, IP당 한 시간 20회.
- 인증번호·시도 횟수는 세션과 같은 Redis(`ye:auth:v1:phone:*`)에 HMAC 해시로 둔다.

## 설정

| 이름 | 기본 | 설명 |
| --- | --- | --- |
| `AUTH_ACCESS_SECONDS` | 1209600 | access 유효기간(초) |
| `AUTH_SESSION_REDIS_URL` | RESULT_STATE_REDIS_URL | 세션·인증번호 Redis |
| `AUTH_SMS_PROVIDER` | mock | 문자 발송 업체 |
| `AUTH_SMS_DEV_CODE_VISIBLE` | false | mock일 때 응답에 인증번호 포함(시연 서버만 true) |
| `AUTH_PHONE_HASH_KEY` | (SECRET_KEY) | 번호 해시 키. 운영 중 바꾸면 기존 휴대폰 회원을 못 찾는다 |
| `KAKAO_*` | | #74 설정 그대로(`KAKAO_APP_ID`, 키, 시크릿, Redirect URI) |

시연 약관: `python manage.py seed_demo_terms`(필수 2개·선택 1개). #74 `save_agreements`는 필수 약관이 없으면 가입을 막는다.
