"""운영 API 설정. 로컬 Compose는 Django 개발 서버를 명시적으로 사용한다."""

import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
timeout = 30
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
capture_output = True
# 프록시 신뢰는 운영 ALB 구성 후 명시적으로 설정한다.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")
