BEAT_SCHEDULE = {
    "close-due-debates": {
        "task": "debates.tasks.close_due_debates",
        "schedule": 10.0,
        "options": {"expires": 10},
    },
}
