# API #25 시연용 참여 권한 결정

결정일: 2026-11-09. 사용자 요청: “api 25의 참여는 시연시에 쓸수 있게 verified 되지 않은 계정도 가능하게 수정하고 이거 문서로 꼭 남겨”.

## 적용 계약

`POST /v1/debates/:debateId/participants`는 로그인한 **ACTIVE 회원**이면 본인 확인 없이 참여할 수 있다. 본인 확인 기록이 없거나 PENDING/FAILED인 회원도 허용하며 VERIFIED 회원도 계속 허용한다.

- 시연/MVP를 위한 본인 확인 선행 조건 생략이다. 비회원 참여나 정지·탈퇴 계정 참여를 허용하는 변경이 아니다.
- 이 변경이 배포된 API #25 전체에 적용한다. 계정 화이트리스트나 환경별 토글은 두지 않는다.
- 본인 확인 기록을 생성하거나 VERIFIED로 바꾸지 않는다. `/v1/me`의 identityVerified도 실제 확인 상태를 유지한다.
- 본인 확인 제공자 연동·실제 로그인 구현을 완료한 것으로 취급하지 않는다. 실제 로그인한 ACTIVE 계정은 여전히 필요하다.

## 최신 명세와의 관계

기준은 [영업중_API명세서_정합성반영본_수정1.xlsx](https://docs.google.com/spreadsheets/d/1mtQPFwWV_zMRopfznOLvw7ikrtFh0ztj/edit), Drive 수정 시각 2026-11-07 13:35:58 KST다.

원본 `API 명세서`의 API #25 권한과 `상세 계약안!D30/H30`은 본인 확인 회원을 요구한다. 이번 사용자 결정은 **API #25에 한해 그 선행 조건을 ACTIVE 회원으로 변경하는 보충 계약**이다. 공통 VERIFIED 상태의 의미나 다른 API의 권한을 일괄 변경하지 않는다. 원본 Drive 엑셀은 이 코드 변경으로 수정하지 않았다. 팀 원본 동기화 시 아래를 반영한다.

| 위치 | 반영할 내용 |
|---|---|
| API 명세서의 #25 권한 | 회원(ACTIVE), 시연용 본인 확인 생략 |
| 상세 계약안 D30/H30 | ACTIVE 회원의 OPEN 토론 참여. VERIFIED 선행 조건 생략 |
| 공통 권한 설명/변경 기록 | #25의 본인 확인 생략 범위와 결정일을 기록 |

명세 적합성 검사는 원본 단독이 아니라 이 사용자 승인 보충 계약까지 적용한다. 이전의 IDENTITY_VERIFICATION_REQUIRED 기대 테스트는 이 결정으로 갱신한다. 시연 종료 후 VERIFIED 요구를 복구할지는 별도 결정하고, 복구 시 구현·테스트·공통 명세를 함께 갱신한다. 자동 만료/원복은 구현하지 않는다.

## 유지하는 처리

- Idempotency-Key 필수, 다른 내용으로 같은 키 재사용 시409 IDEMPOTENCY_KEY_REUSED.
- 같은 키 재시도는201 원응답 재생, 참여·익명번호·카운터 중복 생성 방지.
- 새로운 키로 이미 참여한 토론 재참여는409 ALREADY_PARTICIPATING.
- OPEN 및 서버 마감 시각 검증. 종료 시409 DEBATE_NOT_OPEN.
- 참여·익명번호·카운터·멱등 응답의 동일 트랜잭션과 최신 계정 상태 재검증.
- 성공201의 data: participantId/anonymousNo/initialStance/currentStance/version. 최초 version0.

## 검증 범위

저장소 회귀 검사는 VERIFIED 없는 회원을 기본 참여 fixture로 사용한다. 본인 확인 기록 없음/PENDING/FAILED/VERIFIED의 참여, 확인 상태 보존, 비회원·정지·탈퇴 차단, 기존 마감·중복·동시성 동작을 검사한다.

로컬 HTTP 검사에서 인증은 force_authenticate로 주입한다. 테스트 통과는 실제 OAuth/FE/배포나 본인 확인 제공자 연동의 증거가 아니다. 이 문서와 코드가 PR에 존재하는 것과 develop 병합·서버 배포는 구분한다.

2026-11-09 로컬 검증 결과: PostgreSQL 17·Redis 기반 전체 회귀199개와 별도 HTTP 계약·연동 검사4개 통과. 미인증 계정 생성→게시→참여→의견 작성→입장 변경을 확인했고, identityVerified=false와 확인 기록 부재를 유지했다. 비회원/정지/탈퇴 차단, 마감/키 검증, 재시도/중복 참여/동시성 회귀도 통과했다. Django 시스템 검사 이상 없음, makemigrations --check --dry-run 변경 없음, git diff --check 통과. 코드·보안 검토에서는 기존 잠금·계정 상태 재검증·트랜잭션을 유지하고 본인 확인 데이터에 쓰기를 추가하지 않았음을 확인했다.
