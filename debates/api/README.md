# 토론 API 개발 기반

인증과 오류 응답은 debates/api_contract.py를 사용한다.
UTC 시각 변환은 debates/serializers.py의 utc 함수를 사용한다.
기능별 URL은 debates/urls.py에서 연결하며, 각 기능이 구현될 때 해당 모듈의 urlpatterns를 추가한다.

목록과 입장 serializer는 각 기능 폴더의 serializers.py에서 관리한다.
자동 마감 작업은 debates/tasks/closing.py, 결과 집계 작업은 debates/tasks/results.py에 둔다.
작업 스케줄은 모델을 불러오지 않는 debates/schedules/ 아래 모듈에서 관리한다.

## API 명세와 반환값

대조 기준은 `영업중_API명세서_정합성반영본.xlsx`의 상세 계약안, 필드·DTO 정의, 공통 규칙이다.
기능 동작 테스트와 별도로 요청·응답의 전체 필드, 타입·필수·null, 상태·오류 코드를 확인한다.
기능명세와 API 명세가 충돌하거나 표현이 보류된 항목은 테스트 통과와 별개로 합의 필요로 남긴다.

### #23, #25, #29, #30, #31 토론 참여와 글

- #23은 공개 토론과 작성자 본인의 초안을 반환한다. 공개 응답에는 검사 통과 이미지만 포함한다. 마감 작업이 실행되기 전에도 예정 마감이 지나면 종료 상태와 종료 시각을 반환한다.
- #25와 #30은 참여 또는 글 저장과 카운터, 멱등 응답 저장을 같은 트랜잭션에서 처리한다. 멱등 기록은 #26과 같이 24시간 동안 재사용한다.
- #29는 최상위 의견을 20개씩 조회한다. `sort=latest`가 기본값이며, `sort=upvotes`는 공감 수, `sort=persuasion`은 저장된 설득 성공 횟수 기준으로 내림차순 정렬한다. 동점은 작성 시각과 ID 내림차순으로 정렬하고 다음 페이지에도 같은 `sort`와 입장 필터를 사용한다.
- #31은 대상 글의 직접 반박을 최신순으로 조회한다. 필터링·삭제 글의 별도 표시 정책은 보류 상태다.
- P1 본문은 앞뒤 공백을 제거한 일반 텍스트 1~1,000자로 저장한다. 앱에서도 일반 텍스트로 표시한다. 첨부 근거 링크는 글당 최대 3개의 HTTP(S) 주소이며 검사 대기로 저장한다.
- 의견과 반박에는 작성 당시 대표 배지를 연결한다. 공개 카드의 `badge`는 `{badgeId, name, iconUrl}` 또는 `null`이다. 이 필드는 P1 카드 요구사항을 반영한 추가 응답으로 API 엑셀과 동기화가 필요하다.

### #15 목록 조회와 #17 초안 생성

`/v1/debates`는 `debates/api/debates/views.py`의 `DebateListView` 한 곳에서 처리한다.
GET은 공개 목록, POST는 ACTIVE 회원의 초안 생성이며 URL 이름은 `debate-list`다.
초안 생성의 입력 검증·오류·응답과 코인 미차감 동작은 유지한다.

### #44 최종 통계

`GET /v1/debates/:debateId/result`의 성공 응답은 `data` 아래에 다음 값만 반환한다.

- `counts`: `totalParticipants`, `opinionCount`, `rebuttalCount`, `finalPro`, `finalCon`, `finalNeutral`, `stanceChangedCount`, `stanceKeptCount`
- `sankeyData`, `genderData`, `ageData`, `regionData`, `genderHidden`, `ageHidden`, `computedAt`

명세에 없는 `title`, `status`, `closedAt`, `finalStancePercentages`는 반환하지 않는다.
기본 정보는 상세 조회 #23의 명세에 있고, 비율은 최종 인원으로 계산할 수 있다. 기능명세의 표시는 소수 첫째 자리다.
#23에서 제목과 종료 시각을 확인할 수 있다.
대기·실패는 `409 RESULT_NOT_READY`와 `error.details.status=PENDING/FAILED`로 구분한다.
이 오류에는 토론 제목·종료 시각을 넣지 않는다.

사용자 확정: `counts.stanceChangedCount`와 `counts.stanceKeptCount`는 키를 유지하며
참여자 20명 미만이면 null, 20명 이상이면 정수를 반환한다. 공통 API 엑셀에는 이 결정을 동기화해야 한다.

### #28 참여 목록과 카테고리

#28은 원본 DebateListItem의 13개 필드만 반환하며 `currentStance`는 제외한다.
목록에서 현재 입장을 표시하는 기능의 데이터 공급 방식은 후속 논의 대상으로 남긴다.
`categories.is_active`는 비활성 카테고리 숨김 기능을 위해 유지한다.
이 브랜치의 이전 커밋에 있던 필드 삭제와 `0006_remove_category_is_active`는 철회했다.
초기 마이그레이션에 이미 필드가 있으므로 중복 추가 마이그레이션은 없다. 공통 ERD 동기화는 별도 작업이다.

### #26, #27 오류와 계정 상태

#26은 Debate → User → Participant → Opinion 순서로 잠그며, 저장 전 DB의 최신 계정 상태를 재확인한다.
잠금을 기다리는 동안 계정이 정지되면 변경하지 않는다.
없는 참조 opinionId는 사용자 결정에 따라 `400 OPINION_DEBATE_MISMATCH`로 반환한다.
토론 경로 자체의 부재와 DRAFT는 404이며, #27도 토론 존재 확인 후 비참여자에 403 NOT_PARTICIPANT를 반환한다.

### 기능상 필요하여 유지하는 추가 정보

| 항목 | 필요 근거 | 현재 계약 |
|---|---|---|
| 다른 기기의 입장 변경 감지 | FC-DB-200-P02-006: 충돌 안내 후 최신 입장 갱신 | 선택 요청 `version`, `409 STANCE_CONFLICT`, 오류 details의 현재 입장·버전 |
| 서버 기준 시간 보정 | FC-DB-200-002 | 별도 timing GET의 시간 정보 |
| 집계 중·실패 안내 구분 | FC-DB-300-009 | `RESULT_NOT_READY`의 PENDING/FAILED |

위 정보의 기능상 필요성이 API 명세 등록을 대신하지는 않는다. 필드·경로·오류 정의를 팀과 확정해야 한다.
현재 #26 PATCH 성공 응답에는 `version`이 없다. 충돌 오류는 최신 version을 제공하고, 요청에서 version을 생략하면 버전 비교를 수행하지 않는다. 내 입장 GET은 제거했으며 #23 Participation에 version을 추가해 초기값을 제공할지는 명세 합의가 필요하다.

### 합의 전 유지하는 부분과 후속 작업

- 명세에 없는 내 입장 GET은 제거했다. 같은 주소의 PATCH #26과 이력 GET #27은 유지한다. 최초·현재 입장 조회는 명세 #23의 myParticipation을 사용하며 #23 연동은 별도다. timing GET은 유지하고 #23 통합 또는 명세 등록 여부를 후속 결정한다.
- timing GET의 `data` 포장·숫자 ID·시간대·오류 형식 등 공통 계약 차이도 후속 정리 대상이다. 현재 응답을 완전한 명세 일치로 판정하지 않는다.
- `sankeyData=[]`, JSON 내부 구조, 멱등 TTL·처리 중 오류, 자기 의견/동일 입장 정책 등 나머지 표현·정책 보류 항목은 이번 정리에서 변경하지 않는다.
- 성별·연령·지역 통계는 현재 개발 범위 밖이다. 보상 지급 등 미구현도 완료로 표시하지 않는다.
- #23 응답과 timing GET의 통합 여부는 후속 합의한다. #23 상세 조회와 새 글 작성 API는 #29로 통합됐으며, 내 입장 GET은 제거한 상태다.

`debates.test_api_contract`는 #44 성공 DTO의 전체 키·정수·boolean·UTC와 대기·실패 오류의 중복 정보 제거를 검사한다.
`debates.test_spec_contract_independent`는 구현·기존 테스트를 보지 않고 작성한 #26/#27/#28/#44의
응답 전체 키·타입·null·입력·권한·오류와 공개 HTTP 기능 검사이며, 합의된 null/400 변경을 반영했다.
테스트 통과가 보류 JSON 내부 구조·모든 입력 조합·timing GET까지 전체 명세 적합을 보장하지 않는다.
인증은 force_authenticate로 주입하며 실제 로그인·프론트·배포 연동은 별도다.

## 기능 PR 순서

| 기능 계열 | 선행 기능에서 후속 기능으로 진행하는 순서 |
| --- | --- |
| 참여 목록 | joined-debates-list → joined-debates-pagination |
| 타이머와 마감 | debate-remaining-time → debate-read-only |
| 입장 변경 | stance-opinion-validation → stance-version-validation → stance-deadline-validation → stance-change-apply |
| 결과 조회 | debate-result-header → debate-result-counts → debate-final-stance-statistics → debate-result-computation |

브랜치 이름에는 feat/ 접두사를 사용한다. 각 PR은 아직 병합되지 않은 선행 기능을 대상으로 비교하고,
선행 PR이 develop에 반영되면 최신 develop 기준으로 갱신하여 develop 대상 PR로 정리한다.
결과 집계는 자동 마감에도 의존하므로 debate-read-only를 먼저 반영한다.
공통 기반만 반영된 상태에서는 기능 API와 마감·집계 일정이 활성화되지 않는다.

커밋 설명은 한국어로 작성하며 기능은 feat:, 수정은 fix:, 문서는 docs: 접두사를 사용한다.

### #18 수정과 #23 상세 조회 통합

`/v1/debates/:debateId`는 `debates/api/detail/views.py`에서 GET과 PATCH를 함께 처리하고 한 번만 등록한다. GET의 공개 조회·초안 작성자 제한과 PATCH의 ACTIVE 회원·작성자·DRAFT 검증은 각각 유지한다. 참여 경로에는 최초 참여 POST만 등록한다.
