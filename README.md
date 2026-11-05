# 영업중 백엔드

영업중의 Django REST Framework 백엔드입니다. 이 문서는 이 브랜치에 포함된 코드와 개발 환경을 기준으로 설명합니다. 기능 변경 PR에는 관련 API와 실행 방법의 문서 변경을 함께 포함합니다.

## 프로젝트 구조

| 경로 | 역할 |
|---|---|
| `config/` | Django 설정, URL 연결, DB 헬스 체크, Celery 설정 |
| `accounts/` | 회원, 약관, 관심 카테고리, 본인 확인, 멱등 요청 기록 모델 |
| `debates/`, `discussions/` | 토론, 참여, 의견, 입장 이력 등 토론 관련 모델 |
| `economy/`, `engagement/`, `moderation/` | 재화·보상, 알림·스크랩, 신고 관련 모델 |
| `Dockerfile`, `compose.yaml`, `.env.example` | 컨테이너 빌드와 로컬 실행 설정 |
| `.github/workflows/` | CI 검사와 개발 서버 자동 배포 |

현재 [URL 설정](config/urls.py)에는 `GET /health/`, `/admin/`, 참여 토론 목록, 내 입장 조회·변경·이력, 타이머, 결과 조회와 기능별 API가 연결되어 있습니다. 세부 경로는 [토론 기능 문서](debates/part_c/README.md)와 [기능별 API 문서](features/README.md)를 참고합니다.

회원 모델은 [accounts/models.py](accounts/models.py)의 `accounts.User`이며, `users` 테이블을 사용합니다. 이메일은 고유 로그인 ID가 아니고, 크레딧은 소수점 아래 6자리까지 저장합니다. 다른 앱에서 회원을 참조할 때는 모델 관계에 `settings.AUTH_USER_MODEL`을, 실행 코드에는 `get_user_model()`을 사용합니다.

## 협업 규칙

`main`은 서비스 배포 브랜치이며 `develop`은 기능을 통합하는 브랜치다.
기능 개발은 최신 `develop`에서 `feat/기능이름` 브랜치를 만들고, 버그 수정은 `fix/수정내용`을 사용한다.
작업 브랜치를 원격 저장소에 푸시한 뒤 `develop` 대상 PR을 생성하고 리뷰와 CI 확인 후 병합한다.

커밋 설명은 한국어로 작성한다. 기능 추가는 `feat:`, 버그 수정은 `fix:`, 문서 수정은 `docs:`를 붙인다.

```text
feat: 참여한 토론 목록 조회 구현
fix: 토론 마감 이후 입장 변경 제한 수정
docs: 백엔드 실행 방법 수정
```

공통 코드가 여러 기능에 필요하면 공통 변경을 먼저 PR로 병합하고 각 기능 브랜치를 최신 `develop` 기준으로 정리한다.
README의 구현 상태와 API 목록은 해당 코드가 포함된 PR에서 함께 갱신한다.

## 로컬 개발환경

Docker Desktop을 실행한 상태에서 사용한다. Windows에서도 Linux 컨테이너를 사용한다.
호스트의 Python/가상환경 대신 컨테이너의 Python으로 실행한다.

- Python 3.11 계열 (`python:3.11-slim`)
- Django/DRF/psycopg: `requirements.txt`에 정확한 버전 지정
- PostgreSQL 17 계열 (`postgres:17-bookworm`)
- Redis 서버 7.4 계열 (`redis:7.4-alpine`)
- Celery 5.6.3 / Python Redis 클라이언트 6.4.0 (`requirements.txt`)

Redis 서버와 Python Redis 클라이언트는 별도 프로그램이므로 버전 번호가 서로 다르다.

기반 이미지는 계열 태그를 사용해 패치 업데이트를 받을 수 있다. 이미지 digest까지 고정한 구성은 아니다.

### 처음 실행

```powershell
Copy-Item .env.example .env
```

`.env`의 `DJANGO_SECRET_KEY`와 `POSTGRES_PASSWORD`를 각자 다른 임의 값으로 바꾼다.
`.env`는 Git과 이미지 빌드에서 제외되며 `.env.example`만 공유한다.
Compose가 `.env`를 읽어 컨테이너에 전달한다. Django 자체에서 `.env`를 읽는 코드는 없다.

```powershell
docker compose up -d --build
docker compose exec api python manage.py migrate
docker compose exec api python manage.py check
docker compose ps
```

브라우저에서 <http://localhost:8000/health/>에 접속한다.
정상 응답은 `{"status": "ok", "database": "ok"}`다.
기본 `/` 주소에는 아직 화면/API가 없으므로 404가 나오는 것이 정상이다.

API는 PC의 `127.0.0.1:8000`으로만 공개한다. DB는 호스트에 포트를 공개하지 않는다.
컨테이너 사이에서는 서비스 이름 `db`로 PostgreSQL에 연결한다.

### Redis와 Celery 구성

별도 저장소 없이 이 저장소의 코드와 Compose 설정으로 함께 관리한다.
`api`, `worker`, `beat`는 같은 Docker 이미지와 프로젝트 코드를 사용하며 실행 명령만 다르다.

| 서비스 | 역할 | 실행 방식 |
|---|---|---|
| `api` | HTTP 요청 처리 | Django 개발 서버 |
| `db` | 회원 등 서비스 데이터 저장 | PostgreSQL |
| `redis` | Celery 작업 큐와 임시 결과 저장 | Redis 서버 |
| `worker` | 큐에서 작업을 받아 실제 실행 | `celery -A config worker`, 동시 작업 수 2 |
| `beat` | 일정에 따라 큐로 작업 전송 | `celery -A config beat`, 한 개만 실행 |

흐름은 `Django 또는 beat → Redis 큐 → worker → 결과 저장`이다.
Redis의 DB 0은 작업 큐, DB 1은 임시 결과용이며 같은 Redis 인스턴스를 공유한다.
작업 결과는 1시간 후 만료되므로 영구적인 요약·보상 등의 서비스 데이터 저장소로 사용하지 않는다.
Redis는 호스트 포트를 열지 않고 Compose 내부에서만 접근한다.

로컬 `.env`에는 아래 설정을 사용한다. 기존 환경을 업데이트할 때는 빠진 키만 추가한다.

```dotenv
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
CELERY_HEARTBEAT_ENABLED=true
```

`CELERY_HEARTBEAT_ENABLED=true`이면 beat가 개발환경 확인용 `config.tasks.ping`을
30초마다 보내고 worker가 `pong`을 반환한다. 실제 토론 마감·보상·AI 요약 작업은 아니다.
확인용 주기 작업을 끄려면 값을 `false`로 바꾸고 `docker compose up -d`를 실행한다.
beat는 작업을 예약할 뿐 실행하지 않으므로 worker도 실행 중이어야 한다.

```powershell
# Redis 연결 확인: PONG 응답
docker compose exec redis redis-cli ping

# worker와 beat 로그 확인
docker compose logs --tail 50 worker beat

# 작업을 직접 보내고 worker가 반환한 결과 확인: pong 출력
docker compose exec api python manage.py shell -c "from config.tasks import ping; result = ping.delay(); print(result.get(timeout=20))"
```

`api`, `db`, `redis`, `worker`는 healthcheck가 있으며 `beat`는 실행 상태와 예약 로그로 확인한다.
`/health/`는 API·PostgreSQL 연결 확인용이며 Redis·worker·beat 상태까지 보장하는 경로는 아니다.

각 기능 앱의 `tasks.py`에 `@shared_task` 작업을 추가하면 자동으로 발견한다.
회원가입이나 토론 게시 등 DB 저장 후 작업을 보낼 때는 트랜잭션 커밋 후 전송하도록 구현한다.
Celery에는 중복 실행 가능성이 있으므로 보상 등 중요한 작업에는 별도의 멱등성 처리가 필요하다.
`debates/tasks.py`에는 자동 마감과 결과 집계 작업이 있으며 beat가 각 작업을 10초마다 예약한다. worker와 beat가 함께 실행되어야 예약 작업을 처리한다.

beat는 같은 일정에 한 개만 실행해야 한다. 로컬 예약 상태 파일은 컨테이너의
`/tmp/celerybeat-schedule`에 저장되므로 재생성 시 초기화될 수 있다.
현재의 확인용 일정은 상태 초기화에 영향이 없지만 실제 업무 일정을 추가할 때는 영속화·재실행 정책을 정한다.

### 자주 쓰는 명령

```powershell
# 로그 확인
docker compose logs --tail 100 api

# Django 명령 실행
docker compose exec api python manage.py showmigrations
docker compose exec api python manage.py makemigrations
docker compose exec api python manage.py migrate

# PostgreSQL 접속
docker compose exec db psql -U ye -d ye

# 종료 (DB 데이터 유지)
docker compose down

# 다시 시작
docker compose up -d

# requirements.txt나 Dockerfile 변경 후 다시 빌드
docker compose up -d --build

# 작업 코드나 Celery 설정 변경 후 worker·beat 재시작
docker compose restart worker beat
```

프로젝트 폴더를 `/app`에 연결했으므로 Python 코드 수정은 API 개발 서버에 자동 반영된다.
worker와 beat는 자동 재시작되지 않으므로 관련 코드 수정 후 직접 재시작한다.
DB 데이터는 `postgres_data`, Redis 데이터는 `redis_data` 볼륨에 저장된다.
`docker compose down -v`는 두 볼륨의 데이터를 삭제하므로 일반 종료에 사용하지 않는다.
DB가 초기화된 뒤 `.env`의 DB 비밀번호만 바꿔도 기존 DB 비밀번호는 바뀌지 않는다.

## CI

[Backend CI](.github/workflows/ci.yml)에서 Django 검사와 마이그레이션 및 테스트를 확인한다.
배포 설정과 실행 결과는 배포 대상 브랜치의 워크플로와 GitHub Actions에서 확인한다.
