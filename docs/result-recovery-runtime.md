# 종료 토론 결과 집계·복구 실행

`python manage.py recover_results`는 CLOSED인데 통계가 없는 토론을 찾아 기존 집계 로직을 실행한다. OPEN 토론을 종료하거나 보상·AI 작업을 실행하지 않는다.

## 코드 위치

- `debates/result_state.py`: Redis PENDING/FAILED·60초 재시도 cooldown·TTL. 기존 구현.
- `debates/results.py`: PostgreSQL 잠금·집계·저장과 실패 힌트 처리. 기존 구현.
- `debates/management/commands/recover_results.py`: 누락 결과 재발견과 PostgreSQL advisory lock.
- `infra/runtime/runtime.py`: 현재 API 이미지·환경을 사용하는 worker 및 관리 명령 실행기.
- `scripts/install-result-runtime.py`: 환경별 systemd worker/service/timer 설치.

## 로컬에서 명령 실행

```bash
docker compose run --rm api python manage.py recover_results --batch-size 100
```

이미 집계된 결과는 변경하지 않는다. 실패/냉각 행 때문에 뒤의 후보가 빠지지 않도록 순회하며, 정상 완료 건수가 batch size에 도달하면 끝난다. 동시 sweep은 PostgreSQL advisory lock으로 하나만 실행한다. Redis 힌트가 없어도 PostgreSQL의 결과 누락 상태로 재발견한다.

## EC2에 자동 실행 설치

API 컨테이너와 전용 DB·Redis 연결을 먼저 준비한다. 검토한 저장소 전체를 대상 호스트에 배치한 후 루트 권한으로 한 환경만 선택한다.

```bash
python3 scripts/install-result-runtime.py development --render-only
sudo python3 scripts/install-result-runtime.py development
# 운영 호스트에서만: sudo python3 scripts/install-result-runtime.py production
```

운영은 `/etc/yeongeopjung/environment`가 production이고 전용 운영 API 이미지가 실행 중이어야 한다. 설치는 Redis 생성·환경 비밀값 변경·DB 마이그레이션·beat 실행을 하지 않는다. 저장소에 코드가 있다는 사실만으로 새 호스트의 timer가 켜지지는 않는다.

- `ye-worker-development.service`, `ye-results-development.service`, `ye-results-development.timer`
- `ye-worker-production.service`, `ye-results-production.service`, `ye-results-production.timer`

타이머는 이전 작업 종료 후 약 10초 뒤 재실행한다. 새 API 이미지에서는 이미지 내부의 `python manage.py recover_results`를 실행하므로 명령 업데이트도 API CD를 따라간다. 명령이 없는 이전 이미지에서만 `/opt/ye-runtime/recover_results.py`를 stdin으로 공급해 Django `call_command(Command())`로 실행한다. API 이미지가 바뀌면 worker를 새 이미지로 재시작한다. 실행기·unit 설정을 변경할 때는 설치기를 다시 실행해야 한다.

worker는 non-root, 메모리 192MiB·CPU 0.5개 제한, 외부 포트 없이 실행한다. API의 CA mount·환경을 재사용하며 비밀값은 root 전용 `/run/ye-runtime-환경/worker.env`에만 기록한다. 컨테이너의 root/특권 설정이나 쓰기 가능한 mount를 임의로 가져오지 않는다.

## 스케줄러 선택

서버에서는 이 관리 명령 timer를 재시도 주인으로 사용한다. `compose.yaml`의 beat는 기존 로컬 개발 옵션이며 `close-due-debates`도 포함한다. **이 timer와 결과 sweep beat를 함께 켜지 않는다.** `task.retry()`도 함께 사용하지 않는다. 미합의 자동 마감은 별도로 결정·검증한 뒤 활성화한다.

## 확인·중지

```bash
systemctl status ye-worker-development.service ye-results-development.timer
journalctl -u ye-results-development.service --no-pager -n 20
sudo systemctl disable --now ye-results-development.timer
sudo systemctl stop ye-results-development.service
sudo systemctl disable --now ye-worker-development.service
```

운영은 unit 이름의 development를 production으로 바꾼다. 중지해도 API·DB·Redis는 별도로 실행된다.

## 검증 범위

신규 관리 명령 테스트와 기존 복구 테스트는 실제 로컬 PostgreSQL·Redis 환경에서 검사한다. 기존 복구 테스트 중 일부는 Redis 장애를 모킹한다. 2026-11-09에는 개발·운영 서버에서 각각 실제 Celery pong, 타이머 자동 집계, FAILED cooldown 후 재집계, Redis 힌트 유실, 중복 집계 방지 및 OPEN 유지 등 11개 검사를 통과했다. 테스트가 집계 함수를 직접 호출하지 않았고 임시 데이터는 삭제했다.

이는 전체 API 명세·카카오/JWT·FE 흐름·자동 마감 완료 판정이 아니다. 자동 부팅 설정은 확인했지만 실제 EC2 재부팅 검사는 하지 않았다. GitHub main 릴리스와 운영 CD의 성공 여부도 별도로 확인한다.
