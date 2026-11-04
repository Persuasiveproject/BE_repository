# 영업중 백엔드

Django REST Framework 기반 백엔드다. 로컬 개발 환경은 Docker Compose로 실행하며 PostgreSQL, Redis, Celery를 사용한다.

## 로컬 실행

Docker Desktop을 실행한 뒤 저장소 루트에서 아래 명령을 실행한다.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

처음 만든 `.env`에는 로컬 개발용 `DJANGO_SECRET_KEY`와 `POSTGRES_PASSWORD`를 설정한다. 실제 환경변수는 Git에서 제외한다.

```powershell
docker compose up -d --build
docker compose exec api python manage.py migrate
docker compose exec api python manage.py check
docker compose ps
```

- API 기본 주소는 `http://127.0.0.1:8000/v1/`이다.
- [서버 상태 확인](http://127.0.0.1:8000/health/)에서 API와 PostgreSQL 연결을 확인할 수 있다.
- [Django 관리자](http://127.0.0.1:8000/admin/)에 접속하려면 관리자 권한이 있는 계정이 필요하다.

## 내 참여 토론 목록

`feat/joined-debates-api` 브랜치는 API 명세 #28인 **내 참여 토론 목록**을 추가한다.

| 항목 | 내용 |
| --- | --- |
| 요청 | `GET /v1/me/debates` |
| 권한 | 로그인한 활성 회원. 요청자 본인의 참여 토론만 조회 |
| 상태 필터 | 선택 항목 `status=OPEN` 또는 `status=CLOSED` |
| 다음 페이지 | 응답의 `meta.nextCursor`를 다음 요청의 `cursor`에 전달 |
| 응답 | `data.debates`에 토론 카드와 현재 입장을 최대 20개 반환 |

마감 시각에 도달한 토론은 종료 처리 작업이 실행되기 전에도 종료 목록에 표시된다. 초안과 취소된 토론은 목록에서 제외한다.

## 코드 위치

| 위치 | 내용 |
| --- | --- |
| [config/urls.py](config/urls.py) | API 경로 연결 |
| [debates/part_c/joined](debates/part_c/joined) | 참여 목록 경로, 상태 필터, 페이지 조회 |
| [debates/serializers.py](debates/serializers.py) | 토론 카드 응답 형식 |
| [debates/test_joined_contract.py](debates/test_joined_contract.py) | 참여 목록 API 계약 테스트 |
| [accounts](accounts), [discussions](discussions), [economy](economy), [engagement](engagement), [moderation](moderation) | 도메인별 데이터 모델 |

## 테스트와 로그

테스트는 PostgreSQL에 별도의 테스트 DB를 생성한다.

```powershell
docker compose exec -T api python manage.py test debates
docker compose logs --tail 100 api
```
