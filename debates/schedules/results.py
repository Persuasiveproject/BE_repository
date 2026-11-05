BEAT_SCHEDULE = {
    "compute-pending-results": {
        "task": "debates.tasks.compute_pending_results",
        "schedule": 10.0,
        "options": {"expires": 10},
    },
}
