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

### #44 최종 통계

`GET /v1/debates/:debateId/result`의 성공 응답은 `data` 아래에 다음 값만 반환한다.

- `counts`: `totalParticipants`, `opinionCount`, `rebuttalCount`, `finalPro`, `finalCon`, `finalNeutral`, `stanceChangedCount`, `stanceKeptCount`
- `sankeyData`, `genderData`, `ageData`, `regionData`, `genderHidden`, `ageHidden`, `computedAt`

명세에 없는 `title`, `status`, `closedAt`, `finalStancePercentages`는 반환하지 않는다.
기본 정보는 상세 조회 #23의 명세에 있고, 비율은 최종 인원으로 계산할 수 있다. 기능명세의 표시는 소수 첫째 자리다.
현재 develop에는 #23 경로가 아직 연결되지 않았으므로 상세 조회 담당자와 실제 구현을 연결해야 한다.
대기·실패는 `409 RESULT_NOT_READY`와 `error.details.status=PENDING/FAILED`로 구분한다.
이 오류에는 토론 제목·종료 시각을 넣지 않는다.

### 기능상 필요하여 유지하는 추가 정보

| 항목 | 필요 근거 | 현재 계약 |
|---|---|---|
| #28 목록의 현재 입장 | FC-JN-100-001: 카드에 현재 입장 표시 | 각 항목의 `currentStance` |
| 다른 기기의 입장 변경 감지 | FC-DB-200-P02-006: 충돌 안내 후 최신 입장 갱신 | 선택 요청 `version`, `409 STANCE_CONFLICT`, 오류 details의 현재 입장·버전 |
| 서버 기준 시간 보정 | FC-DB-200-002 | 별도 timing GET의 시간 정보 |
| 집계 중·실패 안내 구분 | FC-DB-300-009 | `RESULT_NOT_READY`의 PENDING/FAILED |

위 정보의 기능상 필요성이 API 명세 등록을 대신하지는 않는다. 필드·경로·오류 정의를 팀과 확정해야 한다.
현재 #26 PATCH 성공 응답에는 `version`이 없다. 내 입장 GET 또는 충돌 오류에서 얻으며, 요청에서 생략하면 버전 비교는 수행하지 않는다.

### 합의 전 유지하는 부분과 후속 작업

- 내 입장 GET과 timing GET은 사용자 요청에 따라 이번 정리에서 그대로 유지한다. 두 경로를 상세 API #23에 통합할지 또는 별도 명세에 등록할지는 후속 결정한다.
- 두 GET의 `data` 포장, timing의 숫자 ID·시간대·오류 형식 등 공통 계약 차이도 후속 정리 대상이다. 현재 응답을 완전한 명세 일치로 판정하지 않는다.
- 20명 미만 입장 변경·유지 인원 `null`, `sankeyData=[]`, JSON 내부 구조, 멱등 TTL·처리 중 오류, 자기 의견/동일 입장 정책 등 표현·정책 보류 항목은 이번 정리에서 변경하지 않는다.
- 인구통계 집계·보상 지급은 미구현이며 완료로 표시하지 않는다.
- `categories.is_active`는 미사용 필드이므로 0006 후속 마이그레이션으로 제거한다. 기존 마이그레이션은 수정하지 않는다. 제거 시 해당 컬럼의 값은 없어지며, 역마이그레이션은 기본값 True로 컬럼을 복원한다.

`debates.test_api_contract`는 #44 성공 DTO의 전체 키·정수·boolean·UTC와 대기·실패 오류의 중복 정보 제거를 검사한다.
20명 미만 null 표현이나 다른 API의 미합의 확장을 포함한 전체 명세 적합성을 보장하는 테스트는 아니다.

## 기능 PR 순서

| 기능 계열 | 선행 기능에서 후속 기능으로 진행하는 순서 |
| --- | --- |
| 참여 목록 | joined-debates-list → joined-debates-pagination |
| 타이머와 마감 | debate-remaining-time → debate-read-only |
| 입장 변경 | my-stance-state → stance-opinion-validation → stance-version-validation → stance-deadline-validation → stance-change-apply |
| 결과 조회 | debate-result-header → debate-result-counts → debate-final-stance-statistics → debate-result-computation |

브랜치 이름에는 feat/ 접두사를 사용한다. 각 PR은 아직 병합되지 않은 선행 기능을 대상으로 비교하고,
선행 PR이 develop에 반영되면 최신 develop 기준으로 갱신하여 develop 대상 PR로 정리한다.
결과 집계는 자동 마감에도 의존하므로 debate-read-only를 먼저 반영한다.
공통 기반만 반영된 상태에서는 기능 API와 마감·집계 일정이 활성화되지 않는다.

커밋 설명은 한국어로 작성하며 기능은 feat:, 수정은 fix:, 문서는 docs: 접두사를 사용한다.
