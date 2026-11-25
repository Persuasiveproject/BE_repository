# 회원 앱 초기 설정

제공된 ERD의 `users` 테이블에 맞춘 Django 모델이다. 카카오 로그인 API는 아직 구현하지 않았다.

- `AUTH_USER_MODEL`은 `accounts.User`다. 다른 앱에서는 `settings.AUTH_USER_MODEL`로 참조한다.
- `AbstractBaseUser`를 사용하고 `last_login` 컬럼을 제거했다. `password` 속성은 DB의 `password_hash` 컬럼에 저장된다.
- ERD에 ID 발급 방식이 없어 초기 구현은 `BigAutoField`로 자동 발급한다.
- 이메일에는 원본 ERD에 없는 유일성 제약을 추가하지 않았다. Django의 회원 식별 필드는 고유한 내부 `id`이며, 이메일/비밀번호 로그인은 구현하지 않았다.
- 카카오 로그인에서는 서버가 검증한 `provider`와 `provider_id`로 회원을 찾는다. 이 조합에는 `user_provider_identity_uniq` DB 유일 제약을 적용한다.
- `User.objects.create_user(email=..., nickname=...)`로 생성하면 비밀번호 로그인이 불가능한 표식을 저장한다. 원본의 소셜 회원 비밀번호 NULL 설명과 다른 Django 인증용 표현이다.
- 초기 코인 10개와 크레딧 30점은 아래 가입 함수가 두 원장과 함께 원자적으로 지급한다. 모델 기본값은 ERD대로 0이며 내부 계정 생성과 기존 회원에게 자동 소급 지급하지 않는다.
- `SUSPENDED`는 로그인 전체 차단 상태가 아니다. 쓰기 제한은 각 API의 권한 검사에서 적용해야 한다. `DELETED`는 비활성 회원으로 취급한다.
- 활성 회원 중 `role=ADMIN`인 계정만 관리자 권한을 갖는다. 관리자 계정 발급 흐름은 별도로 구현한다.
- `UserIdentityVerification`은 회원별 본인 확인 상태를 저장한다. 본인 확인 제공자 연동과 재가입 승계는 이후 구현 범위다.

각 개발 환경에서 저장소를 받은 뒤 루트 README의 절차에 따라 `docker compose exec api python manage.py migrate`를 실행한다.

## 신규 가입 보상 (FC-AU-100-002)

카카오 인증 API에서 **카카오 서버를 통해 신원을 검증한 뒤** 호출한다.

```python
from accounts.registration import register_kakao_user

user, is_new_user = register_kakao_user(
    provider_id=verified_kakao_user_id,  # 검증된 카카오 사용자 ID 문자열
    email=verified_email,
    nickname=nickname,
)
```

- 신규 계정에 코인 10개·크레딧 30점과 `SIGNUP_BONUS` 코인/크레딧 이력을 생성한다. 코인 원장에는 변경 후 잔액 10과 해당 회원 참조를 남긴다.
- 회원 생성과 잔액·이력은 한 트랜잭션으로 처리한다. 어느 단계든 실패하면 모두 취소되며 재시도할 수 있다.
- 동일한 `KAKAO`/사용자 ID로 재호출하거나 동시에 요청해도 계정과 보상은 한 번만 생성된다. 기존 잔액·프로필·상태를 덮어쓰지 않는다.
- 기존·제한·탈퇴 계정은 `is_new_user=False`로 반환하며 지급하지 않는다. 로그인 허용과 재가입 점수 승계는 호출 측 인증/재가입 정책에서 판단해야 한다.
- 이메일 미제공 정책은 보류 중이므로 신규 계정의 빈 이메일을 거부하며 가짜 이메일을 만들지 않는다.
- 카카오 인증, 약관 동의, 토큰 발급, HTTP 가입 경로는 아직 구현하지 않았다. 이 함수는 외부 요청에서 받은 사용자 ID만으로 호출하면 안 된다.

기준: Notion P3 `FC-AU-100-002`(코인 10·크레딧 30·같은 계정 1회) 및 API 수정1 추출본 2026-11-09 #1(회원·두 이력 단일 트랜잭션, 제공자 식별자 UNIQUE). HTTP 계약은 추가하거나 변경하지 않는다.

배포 전 `accounts.0004` 마이그레이션이 필요하다. 기존 DB에 동일한 제공자/사용자 ID가 중복되면 마이그레이션이 실패하므로 먼저 확인한다. 이 변경은 기존 계정을 임의 병합하거나 삭제하지 않는다.

검증: `python manage.py test accounts.test_registration`. PostgreSQL에서는 동시 가입 테스트까지 실행하며 SQLite에서는 해당 테스트를 건너뛴다.
