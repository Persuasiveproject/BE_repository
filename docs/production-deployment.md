# main 운영 배포

`develop`는 기존 개발 EC2/ECR/SSM에 배포한다. `main`은 CI 통과 후 별도 운영 EC2에 배포한다. PR·다른 브랜치의 push는 운영 배포를 실행하지 않는다. 처음 확인한 main에는 README만 있었으므로 이 설정과 애플리케이션 코드가 main에 반영되어야 최초 push 배포가 실행된다.

## 운영 리소스 사전 조건

현재 개발 리소스를 운영 대상으로 재사용하지 않는다. 아래 리소스를 실제로 만든 뒤 값을 등록한다. 이 PR 자체가 리소스를 만들지는 않는다.

- 비공개 운영 API EC2: Docker/AWS CLI/SSM Agent, 인스턴스 역할에 SSM 및 운영 ECR pull 권한. `Project=yeongeopjung`, `Environment=production` 태그 필수. API 보안 그룹의 8000은 운영 ALB에서만 허용한다.
- 운영 DB와 자격 증명: 개발 DB 접속 정보를 복사하지 않는다. ALB/ACM·DNS, 외부 호출 경로는 사용자 확정 아키텍처에 맞춰 준비한다.
- 별도 ECR 저장소: 태그 IMMUTABLE. 이미지 빌드는 현재 x86_64이며 운영 호스트도 amd64여야 한다.
- 호스트 `/etc/yeongeopjung/environment`: 내용 `production`, root 소유. `/etc/yeongeopjung/production.env`: root만 읽기 가능. 컨테이너의 uid 1000이 읽을 수 있는 RDS CA를 `/etc/yeongeopjung/rds-ca.pem`에 둔다.
- env 필수: 충분히 긴 무작위 `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=API도메인,127.0.0.1,localhost`, 운영 `POSTGRES_DB/USER/PASSWORD/HOST/PORT`. Redis가 준비되면 인증·TLS URL을 설정한다. 비밀 파일은 Git에 넣지 않는다.
- SSM 문서: `infra/ssm/deploy-production.json`을 별도 운영 이름으로 등록한다. 원본 셸 변경 시 `python scripts/build-ssm-document.py`로 재생성하고 검토한다. AWS 문서의 기본 버전도 갱신해야 한다. CD는 AWS 문서와 저장소의 전체 JSON을 대조하고 숫자 버전을 고정해 실행한다.

## GitHub 설정

GitHub Environment `production`의 배포 가능 브랜치를 `main`으로 제한한다. 다음 Variables/Secrets는 해당 Environment에 등록할 수 있다. 개발용 키로 자동 대체하지 않는다.

| 종류 | 이름 | 값 |
|---|---|---|
| Variable | PRODUCTION_AWS_REGION | 실제 운영 리전 |
| Variable | PRODUCTION_AWS_ACCOUNT_ID | 실제 운영 계정 ID |
| Variable | PRODUCTION_ECR_REPOSITORY | 별도 운영 저장소 이름 |
| Variable | PRODUCTION_INSTANCE_ID | 별도 운영 EC2 ID |
| Variable | PRODUCTION_SSM_DOCUMENT | 별도 운영 SSM 문서 이름 |
| Secret | PRODUCTION_AWS_ACCESS_KEY_ID | 운영 배포용 자격 증명 |
| Secret | PRODUCTION_AWS_SECRET_ACCESS_KEY | 운영 배포용 자격 증명 |
| Secret (선택) | PRODUCTION_AWS_SESSION_TOKEN | 임시 자격 증명일 때 필요 |

자격 증명의 권한은 운영 ECR 조회/게시·로그인, EC2 대상 태그 조회, 지정 SSM 문서 조회 및 지정 인스턴스 실행/결과 조회에 맞춰 제한한다. 이 PR은 키를 생성하거나 기존 키를 복사하지 않는다. OIDC 전환은 조직 정책과 허용 역할을 확인한 후 별도 적용한다.

## 배포 동작과 복구

1. Backend checks 성공 → main push의 Deploy production 실행.
2. 설정·계정·운영 EC2 태그·ECR 불변 태그·SSM 문서 일치 확인. 잘못된 개발 대상은 거절한다.
3. main 최신 SHA 확인 → SHA 태그 이미지 게시 → SHA 재확인 → SSM 고정 버전 실행. 작업 대기열은 개발과 분리한다.
4. 호스트에서 운영 표식 확인·배포 잠금·이미지 pull·Django 배포 검사·마이그레이션 1회 수행.
5. 이전 API 컨테이너 정지/보관 → Gunicorn 이미지 시작 → 실제 HTTP+DB health 확인. 기동/health 실패는 이전 컨테이너를 재시작한다.

Gunicorn은 기본 2 worker, `WEB_CONCURRENCY`로 조정한다. 프록시 헤더는 컨테이너의 Gunicorn 기본 신뢰 범위로 두며 Django는 ALB가 넘기는 HTTPS 헤더를 사용한다. 따라서 ALB 외의 주체가 API 포트에 접근할 수 없도록 SG를 제한해야 한다. `/health/`는 ALB의 내부 HTTP 상태 확인을 위해 HTTPS 리다이렉트에서 제외한다. 정적 파일·업로드·인증·AI·예약 작업은 별도 실제 연동 검사 대상이다.

앱 복구는 DB 스키마를 되돌리지 않는다. DB 변경은 이전 버전과 호환되도록 설계하고 변경별 백업/복구 절차를 검토한다. 이전 컨테이너 재시작 뒤 정상 서비스까지 자동 검증한 것으로 간주하지 않는다. Actions 타임아웃/취소가 이미 전송된 SSM 명령을 중단하지 않을 수 있으므로 SSM 명령 상태를 확인하고 재배포한다.

이 작업은 API 배포만 다룬다. 최신 설계의 별도 AI 서버·LLM Router 및 예약/복구 실행 방식은 각 담당 계약과 정책이 정해진 후 별도 배포에 연결한다. CI의 Celery ping은 최종 업무 정책이나 실제 운영 AI 연동 완료를 뜻하지 않는다.
