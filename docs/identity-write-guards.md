# 동일인 중복 본인 확인 차단 및 참여·글쓰기 권한

## 기준과 범위

2026-11-27 사용자 요청: 시연 설정과 실제 인증 업체 연결은 나중에 진행하고, A 담당의 동일인 중복 가입 차단과 미확인 회원 참여·글쓰기 제한 코드를 추가한다.

- 기준 코드: 원격 `develop` `ddc33710d8fcbd7e747e0bfcdd5b26ad6aa04e03`.
- 기준 API: [API 수정1 원본](https://docs.google.com/spreadsheets/d/1mtQPFwWV_zMRopfznOLvw7ikrtFh0ztj/edit), 2026-11-27 연결 도구로 본문 재조회. #4·#5·#25·#30 및 공통 인증·응답 규칙을 대조했다.
- 기능 범위: [P3 A 분배](https://app.notion.com/p/41c4196cf5f38035bfd7e01e6ea06c3d)의 FC-AU-110-003, FC-CM-000-005 중 참여·의견·반박 작성.
- API #25는 이번 요청에 따라 VERIFIED 요구를 복구한다. 과거 `api25-demo-participation.md`의 시연 예외를 이 변경이 대체한다. 실제 서버 반영에는 별도 병합·배포가 필요하다.
- 초안·게시·입장 변경·반응·신고 등 다른 API의 권한은 이번 변경 대상이 아니다. FC-CM-000-005 전체 완료를 뜻하지 않는다.

## 동작

### 본인 확인 시작과 완료

기존 모의 제공자 서비스와 DB 유일 제약을 HTTP 계층에서 호출한다. 실제 제공자 어댑터나 증명 발급 API는 만들지 않는다. `issue_mock_proof()`는 신뢰하는 테스트 제공자 측에서만 사용한다. 앱이 보낸 이름·전화번호·personKey·해시를 신뢰해 본인 확인을 완료하지 않는다.

| Method/URL | 요청 | 성공 |
| --- | --- | --- |
| POST /v1/auth/identity/start | 모의 제공자: 빈 JSON 객체 `{}` | 201 `data{requestId:string,expiresAt:UTC string}` |
| POST /v1/auth/identity/complete | `{requestId:string,proof:string}`; 두 필드 필수, null 불가, 추가 필드 거절; requestId 1~128자 | 200 `data{identityVerified:true,verifiedAt:UTC string}` |

둘 다 로그인한 ACTIVE 회원만 가능하다. 비로그인은 401, 비활성 회원은 403이다. 기존 Basic·Bearer 인증기를 사용하며 공개 조회 권한을 바꾸지 않는다.

- 시작 요청은 회원당 현재 요청 1행을 유지한다. 재시작하면 이전 요청의 증명은 사용할 수 없다. 이미 확인된 회원은 `409 ALREADY_VERIFIED`.
- 완료 시 서명, 요청 소유자, requestId, 만료, 결과 해시 형식을 검증한다. 만료·없는 요청은 `400 VERIFICATION_EXPIRED`, 위조·다른 사용자 증명은 `502 IDENTITY_PROVIDER_ERROR`.
- 같은 사람의 두 번째 계정은 `409 IDENTITY_ALREADY_REGISTERED`. 실패한 계정은 PENDING을 유지하고 해시·확인 시각을 기록하지 않는다. 계정 공급자(PHONE/KAKAO)에 관계없이 동일인 해시로 검사한다.
- 동시 요청은 기존 `verified_identity_hash_uniq` DB 제약으로 한 계정만 성공한다. 관련 없는 IntegrityError를 중복 오류로 숨기지 않는다.
- 중복 판별 시점은 **본인 확인 완료**다. 현재 가입 API에는 본인 확인 증명이 없으므로 계정 행 생성 자체를 동일인 기준으로 차단하지는 않는다. 미확인 추가 계정은 아래 제한을 적용받는다. 탈퇴 후 재가입·식별값 보관 정책은 C 담당과 별도 합의가 필요하다.

### 참여·의견·반박 작성

`POST /v1/debates/:debateId/participants`와 `POST /v1/debates/:debateId/posts`에 적용한다. 후자는 parentId가 있는 반박도 포함한다.

사용자 행을 잠그고 ACTIVE 상태를 DB에서 다시 확인한 후 본인 확인 상태를 검사한다. 기록 없음·PENDING·FAILED는 `403 error{code:IDENTITY_VERIFICATION_REQUIRED,message:string}`. 멱등 성공 응답 재사용 전에도 현재 권한을 확인한다.

거절 시 참여·글·링크·알림·카운터·멱등 기록을 변경하지 않는다. 기존 참여 기록이 있어도 미확인 회원은 새 글을 작성할 수 없다. VERIFIED 회원의 기존 성공 DTO·멱등·마감·중복 참여 검사는 유지한다.

## 모의 설정 및 합의 필요

- `MOCK_IDENTITY_ENABLED=false` 기본값 유지. 활성화 및 전용 서명/HMAC 키가 없으면 두 API는 `502 IDENTITY_PROVIDER_ERROR`로 거절한다.
- `MOCK_IDENTITY_REQUEST_SECONDS=300`은 모의 제공자 요청의 기본 유효기간이며 실제 제공자의 유효기간 정책을 확정한 값이 아니다.
- 공통 명세의 시작 요청 정보와 완료 증명 JSON 구조는 아직 제공자 계약 보류다. 이 변경의 `{}`·`{requestId,proof}` 형식은 **모의 제공자용 계약**이다. 실제 제공자 계약과 일치했다고 판정하지 않는다.
- #25·#30의 `403 IDENTITY_VERIFICATION_REQUIRED`는 기존 내부 검사 코드에 맞춘 응답이다. 기능 분배에는 전용 오류 요구가 있지만 API 원본의 개별 오류 목록에는 없어 명세 등록이 필요하다.
- #30 API의 직접 권한은 참여자로 되어 있지만 P3 기능 분배는 미확인 회원 글쓰기 차단을 요구한다. 이번 사용자 요청에 따라 추가 검사하며 API 원본에도 보충해야 한다.
- #5 완료의 `502 IDENTITY_PROVIDER_ERROR`도 기존 내부 서비스의 증명 검증 오류를 HTTP로 노출한 값이다. #4에는 등록돼 있지만 #5 개별 오류 목록에는 없어 명세 보충이 필요하다.
- Basic 인증, SMS mock, 인증번호 응답 옵션, 시연 데이터, 실제 SMS·본인 확인 업체 연결은 유지/보류한다. 배포 환경은 변경하지 않는다.

## 검증 범위

### 기능 동작

격리된 로컬 PostgreSQL 17·Redis 7.4에서 `docker compose -p ye-identity-guards run --rm --no-deps -T api python manage.py test -v 1` 전체 **431개 통과**(262.340초). 동시 동일인 확인의 단일 성공, 서로 다른 공급자 계정의 동일인 차단, 위조·소유자 불일치·만료·비활성 제공자, 미확인 기존 참여자의 의견·반박 거절, 거절 시 DB 부작용 없음, 확인 후 쓰기 성공·멱등 재시도를 확인했다.

기존 시연 참여 허용 테스트는 새 요구에 맞춰 거절 검사로 변경했다. 답글 알림 테스트는 새 권한을 우회하지 않고 확인된 테스트 계정을 준비하도록 수정했다. 결과 저장소 장애 경고는 기존 장애 주입 테스트에서 나온 것이며 최종 테스트 결과는 OK다.

Django `check`, `makemigrations --check --dry-run`, `git diff --check` 통과. 새 마이그레이션 없음.

### API 명세 일치

#4·#5의 URL·인증·필수/추가/누락 필드·문자열/배열/숫자/null·requestId 길이·성공/오류 envelope·상태/오류 코드·UTC 시각을 검사했다. #25·#30의 새 403 응답과 기존 성공 DTO(중첩 references 포함), ID 문자열·UTC·멱등 동작도 검사했다. 그 밖의 기존 기능은 전체 회귀 테스트 범위로 확인했으며 모든 API 계약을 새로 전수 검증한 것은 아니다.

모의 요청 형식, #25·#30 전용 권한 오류, #5 제공자 오류 및 기능명세와 #30 권한 표의 차이는 위에 명시한 **합의 필요** 항목이다. 기능 통과를 명세 전체 적합 판정으로 사용하지 않는다.

### 실제 연동

PostgreSQL·Redis는 실제 실행했다. Django APIClient에서 실제 Basic 및 Bearer 인증기로 시작→완료→참여→의견→반박 흐름을 검사했고, 다른 권한·경계 테스트에는 `force_authenticate`도 사용했다. 본인 확인 증명은 모의 제공자에서 발행한 테스트 값이다. 실제 인증 업체·SMS·카카오 실호출·FE·AWS 배포 연동은 수행하지 않았다.
