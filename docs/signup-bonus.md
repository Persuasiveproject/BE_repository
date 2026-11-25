# 신규 가입 보상 구현·검증 (2026-11-25)

- 기준 코드: 원격 develop `233849d9a9f71362fe20914c41f12eaf8a779716`에서 분기한 `feat/signup-bonus`.
- 기능 기준: [Notion P3](https://app.notion.com/p/41c4196cf5f380e5a554ec748512a8b3)의 FC-AU-100-002. 신규 카카오 계정에 코인 10개·크레딧 30점, 동일 계정에는 한 번만 지급.
- 보조 계약: API 정합성반영본 수정1 Drive 추출본(2026-11-09) #1의 회원·두 원장 단일 트랜잭션 및 제공자/사용자 ID 유일성. 최신 HTTP 계약 전체를 재확인한 검증은 아니다.

## 기능 동작

`accounts.registration.register_kakao_user`가 검증된 카카오 식별자로 신규 회원과 보상 원장을 생성한다. 동일 식별자에 대한 순차·동시 재요청은 기존 회원을 반환하고 추가 보상을 만들지 않는다. 내부 계정 생성과 기존 회원 소급 지급은 대상이 아니다.

실제 로컬 PostgreSQL 17에서 신규 가입 테스트 10개와 기존 계정·관심 카테고리·크레딧·지갑 회귀 테스트 37개, 총 47개가 모두 통과했다. 동시 가입은 두 요청이 계정이 없는 상태를 함께 관찰하도록 비밀번호 생성 시점에 barrier를 걸었고, 실제 별도 DB 연결과 유일 제약으로 계정 1개·각 원장 1개를 검증했다. 원장 실패 테스트는 예외를 주입해 전체 롤백을 검증했다.

```text
python manage.py test accounts.test_registration accounts.test_me accounts.test_interests accounts.test_credits economy.test_wallet
Ran 47 tests — OK
python manage.py makemigrations --check --dry-run — No changes detected
python manage.py check — no issues
```

## API 명세 일치

이번 변경은 HTTP 경로·입출력·상태 코드·인증 계약을 추가하거나 변경하지 않는다. 가입 서비스의 지급량·중복 방지·원장·원자성을 검사했다. 카카오 로그인 API의 중첩 응답·토큰·성공/오류 전체 계약 적합성은 미검증이며 해당 API 자체가 아직 구현되지 않았다.

## 실제 연동

실제 로컬 PostgreSQL로 검증했으며 AWS·카카오 서버·앱 가입 흐름 연동과 배포는 하지 않았다. 카카오 로그인 구현 시 서버에서 검증한 사용자 ID로 이 함수를 호출해야 한다. 약관 동의와 토큰 발급은 별도 작업이다.

이메일 미제공 가입 정책은 합의 필요다. 현재 신규 가입 함수는 유효한 이메일을 요구하며 가짜 이메일을 생성하지 않는다. 탈퇴 후 재가입·점수 승계는 C파트와 연결할 정책으로 남는다.

배포에는 `accounts.0004_user_provider_identity_unique` 마이그레이션이 필요하다. 기존 제공자 식별자가 중복된 DB에서는 제약 추가가 실패하며, 이 변경은 기존 중복 계정을 자동 삭제·병합하지 않는다.
