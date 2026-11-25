"""일회용 컨테이너에서 AWS/Docker를 대체해 SSM 실패 복구 분기를 검사한다."""

import os
from pathlib import Path
import subprocess
import tempfile

if not Path("/.dockerenv").exists():
    raise SystemExit("Run this check in a disposable Docker container")
config = Path("/etc/yeongeopjung")
config.mkdir(exist_ok=True)
(config / "environment").write_text("production\n")
(config / "production.env").write_text("# test only\n")
(config / "rds-ca.pem").write_text("test only\n")
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    (root / "aws").write_text('#!/bin/sh\necho fake-login-password\n')
    (root / "docker").write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$AUDIT_CALLS"
case "$1 $2" in
  'container inspect')
    case "$3" in *-previous) exit 1;; *) exit 0;; esac ;;
  'run -d') [ "$FAIL_AT" != start ] ;;
  'exec yeongeopjung-production-api') [ "$FAIL_AT" != health ] ;;
  *) exit 0 ;;
esac
''')
    for file in root.iterdir():
        file.chmod(0o755)
    for mode in ("start", "health", "none"):
        calls = root / f"{mode}.calls"
        env = {
            **os.environ,
            "PATH": folder + os.pathsep + os.environ["PATH"],
            "AUDIT_CALLS": str(calls),
            "FAIL_AT": mode,
            "SSM_ImageTag": "a" * 40,
            "SSM_AwsAccountId": "123456789012",
            "SSM_AwsRegion": "ap-southeast-2",
            "SSM_EcrRepository": "yeongeopjung-production-api",
        }
        run = subprocess.run(["bash", "infra/ssm/deploy-production.sh"], env=env, text=True, capture_output=True)
        events = calls.read_text().splitlines()
        restored = "rename yeongeopjung-production-api-previous yeongeopjung-production-api" in events
        restarted = "start yeongeopjung-production-api" in events
        if mode == "none":
            assert run.returncode == 0 and not restored and not restarted, run
        else:
            assert run.returncode != 0 and restored and restarted, (mode, run, events)
        migrations = [line for line in events if "manage.py migrate --noinput" in line]
        assert len(migrations) == 1, events
    # 운영 표식이 없으면 외부 명령을 실행하지 않는다.
    (config / "environment").write_text("development\n")
    bad_calls = root / "wrong-environment.calls"
    run = subprocess.run(["bash", "infra/ssm/deploy-production.sh"], env={**env, "AUDIT_CALLS": str(bad_calls)}, text=True, capture_output=True)
    assert run.returncode != 0 and not bad_calls.exists(), run
print("SSM startup failure, health failure, success and environment isolation checks passed (AWS/Docker mocked)")
