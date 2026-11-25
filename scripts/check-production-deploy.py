"""운영 대상 오설정 및 오래된 main 배포가 외부 명령 전에 차단되는지 검사한다."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "infra/ssm/deploy-production.sh"
document = json.loads((ROOT / "infra/ssm/deploy-production.json").read_text(encoding="utf-8"))
assert document["mainSteps"][0]["inputs"]["runCommand"] == source.read_text(encoding="utf-8").splitlines()
assert document["parameters"]["ImageTag"]["interpolationType"] == "ENV_VAR"
bash = shutil.which("bash")
if not bash:
    raise SystemExit("bash is required")
for script in (ROOT / "scripts/deploy-production.sh", source):
    subprocess.run([bash, "-n", str(script)], check=True)

base = {
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": "refs/heads/main",
    "AWS_REGION": "ap-southeast-2",
    "AWS_ACCOUNT_ID": "123456789012",
    "ECR_REPOSITORY": "yeongeopjung-production-api",
    "INSTANCE_ID": "i-0123456789abcdef0",
    "SSM_DOCUMENT": "yeongeopjung-production-deploy",
    "AWS_ACCESS_KEY_ID": "test-only",
    "AWS_SECRET_ACCESS_KEY": "test-only",
    "GH_TOKEN": "test-only",
    "GITHUB_SHA": "a" * 40,
    "GITHUB_REPOSITORY": "test/repository",
}
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    calls = root / "calls"
    # 오설정은 AWS·Docker·GitHub 호출 전에 차단되어야 한다.
    for command in ("aws", "docker", "gh"):
        path = root / command
        path.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$AUDIT_CALLS"\nexit 97\n', encoding="utf-8")
        path.chmod(0o755)
    environment = {**os.environ, **base, "PATH": folder + os.pathsep + os.environ["PATH"], "AUDIT_CALLS": str(calls)}
    cases = [
        ({"GITHUB_EVENT_NAME": "pull_request"}, "push to main"),
        ({"GITHUB_REF": "refs/heads/develop"}, "push to main"),
        ({"AWS_REGION": ""}, "Missing production configuration"),
        ({"AWS_ACCESS_KEY_ID": ""}, "Missing production configuration"),
        ({"INSTANCE_ID": "i-07799fedd264604b9"}, "Development EC2"),
        ({"ECR_REPOSITORY": "yeongeopjung-dev-api"}, "dedicated production ECR"),
        ({"SSM_DOCUMENT": "yeongeopjung-dev-deploy"}, "dedicated production SSM"),
    ]
    for override, expected in cases:
        result = subprocess.run([bash, "scripts/deploy-production.sh"], cwd=ROOT, env={**environment, **override}, text=True, capture_output=True)
        assert result.returncode != 0 and expected in result.stderr, (override, result)
        assert not calls.exists(), f"External command used for invalid configuration: {override}"
    # 이전 main 실행은 성공적인 생략으로 기록하며 AWS나 Docker를 호출하지 않는다.
    (root / "gh").write_text('#!/bin/sh\nprintf "%s\\n" bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n', encoding="utf-8")
    result = subprocess.run([bash, "scripts/deploy-production.sh"], cwd=ROOT, env=environment, text=True, capture_output=True)
    assert result.returncode == 0 and "Skipped stale commit" in result.stdout, result
    assert not calls.exists(), "Stale deployment invoked AWS or Docker"
print("Production document sync, shell syntax and 8 deployment safeguard checks passed")
