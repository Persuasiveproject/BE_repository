"""Recover missing CLOSED debate results without changing debate status."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = 'Aggregate missing results for CLOSED debates; never close OPEN debates.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=100)

    def handle(self, *args, **options):
        from debates.models import Debate
        from debates.results import compute_debate_result

        batch_size = options['batch_size']
        if not 1 <= batch_size <= 1000:
            raise CommandError('Require 1 <= batch-size <= 1000.')
        if connection.vendor != 'postgresql':
            raise CommandError('Result recovery requires PostgreSQL locking.')
        lock_key = 1927410315
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [lock_key])
            locked = cursor.fetchone()[0]
        counts = {'examined': 0, 'completed': 0, 'deferred': 0, 'errors': 0,
                  'skippedLocked': not locked}
        if not locked:
            return json.dumps(counts)
        try:
            pending = Debate.objects.filter(
                status=Debate.Status.CLOSED, debatestatistics__isnull=True,
            ).order_by('closed_at', 'id').values_list('id', flat=True)
            for debate_id in pending.iterator(chunk_size=100):
                counts['examined'] += 1
                try:
                    if compute_debate_result(debate_id):
                        counts['completed'] += 1
                    else:
                        counts['deferred'] += 1
                except Exception:
                    counts['errors'] += 1
                    self.stderr.write(f'Result recovery failed for debate {debate_id}; next sweep will retry.')
                if counts['completed'] >= batch_size:
                    break
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [lock_key])
        return json.dumps(counts)


if __name__ == '__main__':
    # Host timer can feed this reviewed command through stdin to the deployed
    # image until the source branch is integrated; use Django's command runner.
    import os
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    from django.core.management import call_command
    call_command(Command())
