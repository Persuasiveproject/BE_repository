# 기능별 API

각 폴더의 `urls.py`에 해당 기능의 경로와 처리가 들어 있고 `tests.py`에 검증이 들어 있다.
경로 연결은 [config/urls.py](../config/urls.py)에서 기능마다 한 줄씩 관리한다.

| 폴더 | API 번호 | 기능 |
| --- | --- | --- |
| `agreements` | #11 | 약관 동의 저장 |
| `categories` | #13 | 공개 카테고리 조회 |
| `credits` | #47~49 | 크레딧, 크레딧 이력, 배지 조회 |
| `demographics` | #9~10 | 인구통계 정보 저장, 공개 동의 철회 |
| `interests` | #8 | 관심 카테고리 전체 교체 |
| `notifications` | #50~52 | 알림 목록·미확인 개수, 개별·전체 읽음 처리 |
| `reference_lookup` | #41 | 검사를 통과한 공개 글의 근거 링크 조회 |
| `trend` | #24 | 저장된 토론 추세 조회 |
| `wallet` | #45~46 | 코인 잔액과 거래 원장 조회 |

공통 응답과 권한 처리는 `features/common.py`를 사용한다. 모델은 기존 Django 앱에서 가져온다.
새 API를 추가할 때는 기능 모듈과 `config/urls.py`의 경로 연결을 함께 변경한다.

목록은 20개씩 반환한다. 관심 카테고리 중복은 제거하며 요청 배열은 최대 5개다.
약관 동의는 활성화 시각이 지난 약관을 대상으로 하며 빈 요청은 변경 없이 처리하고 중복 항목은 거절한다.
통계 동의 철회는 회원의 동의 상태를 변경한다. 과거 집계의 철회 반영 시점은 명세에서 보류돼 있다.
추세와 근거 링크 조회는 각각 저장된 스냅샷과 검사 상태를 사용한다.

```powershell
docker compose exec -T api python manage.py test features
```
