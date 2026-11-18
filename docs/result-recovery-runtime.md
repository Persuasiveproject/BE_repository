# 토론 자동 마감과 결과 집계·복구 실행

`python manage.py recover_results`는 CLOSED인데 통계가 없는 토론을 찾아 기존 집계 로직을 실행한다. OPEN 토론을 종료하거나 보상·AI 작업을 실행하지 않는다.

`python manage.py close_debates`는 예정 마감이 지난 OPEN 토론을 최대 100개씩 CLOSED로 저장하고 종료 시각을 예정 마감으로 기록한다. 기존 공통 마감 함수를 동기 실행하며 Celery broker에 작업을 보내지 않는다. 이미 종료된 토론은 변경하지 않는다. 잠긴 행은 건너뛰고 다음 실행에서 다시 처리한다. 마감 명령은 결과를 직접 계산하지 않으며 기존 결과 복구 타이머가 후속 집계를 담당한다.

## 코드 위치

- `debates/result_state.py`: Redis PENDING/FAILED·60초 재시도 cooldown·TTL. 기존 구현.
- `debates/results.py`: PostgreSQL 잠금·집계·저장과 실패 힌트 처리. 기존 구현.
- `debates/management/commands/recover_results.py`: 누락 결과 재발견과 PostgreSQL advisory lock.
- `debates/management/commands/close_debates.py`: PostgreSQL 행 잠금을 사용하는 자동 마감 명령.
- `infra/runtime/runtime.py`: 현재 API 이미지·환경을 사용하는 worker 및 관리 명령 실행기.
- `scripts/install-result-runtime.py`: 환경별 systemd worker/service/timer 설치.

## 로컬에서 명령 실행

```bash
docker compose run --rm api python manage.py recover_results --batch-size 100
docker compose run --rm api python manage.py close_debates
```

이미 집계된 결과는 변경하지 않는다. 실패/냉각 행 때문에 뒤의 후보가 빠지지 않도록 순회하며, 정상 완료 건수가 batch size에 도달하면 끝난다. 동시 sweep은 PostgreSQL advisory lock으로 하나만 실행한다. Redis 힌트가 없어도 PostgreSQL의 결과 누락 상태로 재발견한다.

## EC2에 자동 실행 설치

새 관리 명령이 포함된 API 이미지를 배포하고 필요한 마이그레이션 및 전용 DB·Redis 연결을 먼저 준비한다. 검토한 저장소 전체를 대상 호스트에 배치한 후 루트 권한으로 한 환경만 선택한다. 설치기는 실행 중 API에 `help close_debates`를 요청해 명령 존재를 확인한 뒤에만 호스트 파일을 변경한다. 마감 명령이 없는 이전 이미지에 코드를 주입하지 않는다. 설치 시점부터 기한이 지난 기존 OPEN 토론도 마감 대상이 된다.

```bash
python3 scripts/install-result-runtime.py development --render-only
sudo python3 scripts/install-result-runtime.py development
# 운영 호스트에서만: sudo python3 scripts/install-result-runtime.py production
```

운영은 `/etc/yeongeopjung/environment`가 production이고 전용 운영 API 이미지가 실행 중이어야 한다. 설치는 Redis 생성·환경 비밀값 변경·DB 마이그레이션·beat 실행을 하지 않는다. 저장소에 코드가 있다는 사실만으로 새 호스트의 timer가 켜지지는 않는다.

- `ye-worker-development.service`, `ye-results-development.service`, `ye-results-development.timer`
- `ye-worker-production.service`, `ye-results-production.service`, `ye-results-production.timer`
- `ye-closing-development.service`, `ye-closing-development.timer`
- `ye-closing-production.service`, `ye-closing-production.timer`

타이머는 이전 작업 종료 후 약 10초 뒤 재실행한다. 새 API 이미지에서는 이미지 내부의 `python manage.py recover_results`를 실행하므로 명령 업데이트도 API CD를 따라간다. 명령이 없는 이전 이미지에서만 `/opt/ye-runtime/recover_results.py`를 stdin으로 공급해 Django `call_command(Command())`로 실행한다. API 이미지가 바뀌면 worker를 새 이미지로 재시작한다. 실행기·unit 설정을 변경할 때는 설치기를 다시 실행해야 한다.

worker는 non-root, 메모리 192MiB·CPU 0.5개 제한, 외부 포트 없이 실행한다. API의 CA mount·환경을 재사용하며 비밀값은 root 전용 `/run/ye-runtime-환경/worker.env`에만 기록한다. 컨테이너의 root/특권 설정이나 쓰기 가능한 mount를 임의로 가져오지 않는다.

## 스케줄러 선택

서버에서는 이 관리 명령 timer를 마감·결과 재시도의 실행기로 사용한다. `compose.yaml`의 beat는 기존 로컬 개발 옵션이며 `close-due-debates`도 포함한다. **이 timer들과 마감/결과 sweep beat를 함께 켜지 않는다.** `task.retry()`도 함께 사용하지 않는다. 마감 타이머는 이전 작업이 끝난 뒤 약 10초 후 재실행하므로 밀리초 단위의 정확한 전환 시각을 보장하지 않는다. API의 마감 이후 쓰기 차단은 별도로 즉시 적용된다.

## 확인·중지

```bash
systemctl status ye-worker-development.service ye-results-development.timer ye-closing-development.timer
journalctl -u ye-results-development.service --no-pager -n 20
journalctl -u ye-closing-development.service --no-pager -n 20
sudo systemctl disable --now ye-closing-development.timer
sudo systemctl stop ye-closing-development.service
sudo systemctl disable --now ye-results-development.timer
sudo systemctl stop ye-results-development.service
sudo systemctl disable --now ye-worker-development.service
```

운영은 unit 이름의 development를 production으로 바꾼다. 중지해도 API·DB·Redis는 별도로 실행된다.

## 검증 범위

신규 관리 명령 테스트와 기존 복구 테스트는 실제 로컬 PostgreSQL·Redis 환경에서 검사한다. 기존 복구 테스트 중 일부는 Redis 장애를 모킹한다. 2026-11-09에는 개발·운영 서버에서 각각 실제 Celery pong, 타이머 자동 집계, FAILED cooldown 후 재집계, Redis 힌트 유실, 중복 집계 방지 및 OPEN 유지 등 11개 검사를 통과했다. 테스트가 집계 함수를 직접 호출하지 않았고 임시 데이터는 삭제했다.

이는 전체 API 명세·카카오/JWT·FE 흐름·자동 마감 완료 판정이 아니다. 자동 부팅 설정은 확인했지만 실제 EC2 재부팅 검사는 하지 않았다. GitHub main 릴리스와 운영 CD의 성공 여부도 별도로 확인한다.

이번 자동 마감 추가의 서버 설치·timer 활성화는 별도 단계다. 로컬 DB 검증과 Docker를 mock한 실행기·unit 생성 검사만으로 실제 서버의 자동 마감 완료를 판정하지 않는다.

이번 변경 검증(기준 develop `66685e5`): PostgreSQL 17·Redis에서 신규 마감 검사 6개 및 통합 전체 196개 테스트 통과(6개 포함). QUICK/ENDLESS의 저장·누락 마감 계산, 미래/DRAFT/CANCELED/CLOSED 유지, 반복 실행, 101개 배치, 다른 연결이 잠근 행 건너뛰기와 다음 실행 처리, 마감→결과 복구를 확인했다. `scripts/check-result-runtime.py`는 Docker를 mock해 개발·운영 unit 생성, 마감 명령 전달·실패 전파, 이미지·사용자·특권 검증을 확인했다. `git diff --check` 통과. 코드·보안 검토에서는 기존 마감 트랜잭션/행 잠금을 재사용하고, 실행 명령·환경 선택은 고정하며, 호스트의 기존 API 이미지 검증을 거치도록 확인했다. 새 API 응답이나 권한 변경은 없다. 원래 API 전체 계약·실제 인증·FE 연동과 서버 활성화는 이 검증에 포함하지 않는다.
