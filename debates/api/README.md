# 토론 API 개발 기반

인증과 오류 응답은 debates/api_contract.py를 사용한다.
UTC 시각 변환은 debates/serializers.py의 utc 함수를 사용한다.
기능별 URL은 debates/urls.py에서 연결하며, 각 기능이 구현될 때 해당 모듈의 urlpatterns를 추가한다.

목록과 입장 serializer는 각 기능 폴더의 serializers.py에서 관리한다.
자동 마감 작업은 debates/tasks/closing.py, 결과 집계 작업은 debates/tasks/results.py에 둔다.
작업 스케줄은 모델을 불러오지 않는 debates/schedules/ 아래 모듈에서 관리한다.

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
