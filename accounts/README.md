# 회원 앱 초기 설정

제공된 ERD의 `users` 테이블에 맞춘 Django 모델이다. 카카오 로그인 API는 아직 구현하지 않았다.

- `AUTH_USER_MODEL`은 `accounts.User`다. 다른 앱에서는 `settings.AUTH_USER_MODEL`로 참조한다.
- `AbstractBaseUser`를 사용하고 `last_login` 컬럼을 제거했다. `password` 속성은 DB의 `password_hash` 컬럼에 저장된다.
- ERD에 ID 발급 방식이 없어 초기 구현은 `BigAutoField`로 자동 발급한다.
- 이메일에는 원본 ERD에 없는 유일성 제약을 추가하지 않았다. Django의 회원 식별 필드는 고유한 내부 `id`이며, 이메일/비밀번호 로그인은 구현하지 않았다.
- 카카오 로그인에서는 서버가 검증한 `provider`와 `provider_id`로 회원을 찾는다. 로그인 구현 전에 이 조합의 유일성 제약을 확정해야 한다.
- `User.objects.create_user(email=..., nickname=...)`로 생성하면 비밀번호 로그인이 불가능한 표식을 저장한다. 원본의 소셜 회원 비밀번호 NULL 설명과 다른 Django 인증용 표현이다.
- 초기 코인 10개와 크레딧 30점 지급은 가입 서비스가 거래 내역과 함께 처리할 작업이다. 모델 기본값은 ERD대로 0이다.
- `SUSPENDED`는 로그인 전체 차단 상태가 아니다. 쓰기 제한은 각 API의 권한 검사에서 적용해야 한다. `DELETED`는 비활성 회원으로 취급한다.
- 활성 회원 중 `role=ADMIN`인 계정만 관리자 권한을 갖는다. 관리자 계정 발급 흐름은 별도로 구현한다.
- `UserIdentityVerification`은 회원별 본인 확인 상태를 저장한다. 본인 확인 제공자 연동과 재가입 승계는 이후 구현 범위다.

저장소를 받은 뒤 각 개발 환경에서 루트 README의 절차에 따라
`docker compose exec api python manage.py migrate`를 실행한다.
