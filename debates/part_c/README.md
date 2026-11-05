# 3번 파트 백엔드

| 폴더 | 기능 | API |
| --- | --- | --- |
| [joined](joined) | 참여 토론 목록, 상태 필터와 커서 | #28 |
| [stance](stance) | 내 입장, 변경과 이력 | #26·27 및 입장 상태 GET |
| [timing](timing) | 서버 기준 타이머와 읽기 전용 전환 | 타이머 GET |
| [result](result) | 집계 대기·실패 표시와 최종 결과 | #44 |

각 폴더에 뷰와 URL이 있다. 경로는 [debates/urls.py](../urls.py)에서 연결한다.
입장 변경 트랜잭션은 [stance.py](../stance.py), 자동 마감과 집계 실행은 [tasks.py](../tasks.py),
집계 계산은 [results.py](../results.py)에 있다. 공통 응답 형식과 모델은 기존 debates 앱을 사용한다.

프론트에서는 내 입장 GET의 `selectableStances`로 현재 입장이나 마감된 토론의 선택을 막고,
타이머 GET의 `isReadOnly`로 마감 시 입력과 버튼을 비활성화할 수 있다.
최종 결과 준비 전에는 기존 409 응답의 `error.details.status`를 유지하며 제목과 종료 시각도 함께 반환한다.
실제 종료 시각이 남아 있지 않은 이전 데이터는 `null`이다.

작성 이후의 입장 기록, 의견 목록에서의 영업 버튼, 읽기 전용 화면 표시와 AI 요약 화면 연결에는
각 담당 API와 프론트 연동이 필요하다.
