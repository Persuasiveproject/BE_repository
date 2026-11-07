"""실제 HTTP 서버와 DB의 배포 상태를 확인한다."""

import json
import time
import urllib.error
import urllib.request

for attempt in range(30):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health/", timeout=3) as response:
            payload = json.load(response)
            if response.status == 200 and payload == {"status": "ok", "database": "ok"}:
                print("Gunicorn HTTP and database health check passed")
                break
    except (OSError, ValueError, urllib.error.URLError):
        pass
    time.sleep(1)
else:
    raise SystemExit("API/database health check did not become ready")
