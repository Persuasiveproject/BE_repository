"""SSM에 등록할 문서를 검토 가능한 셸 원본으로부터 생성한다."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parameters = {
    "ImageTag": r"^[a-f0-9]{40}$",
    "AwsAccountId": r"^[0-9]{12}$",
    "AwsRegion": r"^[a-z]{2}(-[a-z]+)+-[0-9]+$",
    "EcrRepository": r"^[a-z0-9][a-z0-9._/-]*$",
}
document = {
    "schemaVersion": "2.2",
    "description": "Deploy the main API image to a dedicated production EC2",
    "parameters": {
        name: {"type": "String", "allowedPattern": pattern, "interpolationType": "ENV_VAR"}
        for name, pattern in parameters.items()
    },
    "mainSteps": [{
        "action": "aws:runShellScript",
        "name": "deployProductionApi",
        "inputs": {
            "timeoutSeconds": "420",
            "runCommand": (ROOT / "infra/ssm/deploy-production.sh").read_text(encoding="utf-8").splitlines(),
        },
    }],
}
(ROOT / "infra/ssm/deploy-production.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
