BEAT_SCHEDULE = {
    "check-references": {
        "task": "debates.tasks.check_references",
        "schedule": 60.0,
        "options": {"expires": 60},
    },
}
