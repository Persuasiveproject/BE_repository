from celery import shared_task


@shared_task(name="config.tasks.ping")
def ping():
    """로컬 환경에서 큐 전달·worker 실행·결과 저장을 확인하는 작업."""
    return "pong"
