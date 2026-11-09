"""Persist overdue OPEN debates as CLOSED using the shared closing logic."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = 'Close up to 100 overdue debates; result recovery runs separately.'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('Debate closing requires PostgreSQL locking.')
        from debates.tasks.closing import close_due_debates

        # Execute synchronously, without publishing a Celery message. The shared
        # transaction uses SKIP LOCKED so concurrent invocations do not wait.
        closed = close_due_debates()
        return json.dumps({'closed': closed})
