BEAT_SCHEDULE = {
    "refresh-ai-summaries": {
        "task": "discussions.tasks.refresh_ai_summaries",
        "schedule": 30.0,
        "options": {"expires": 30},
    },
}
