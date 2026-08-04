import logging

from django.conf import settings
from django.db.backends.signals import connection_created
from django.dispatch import receiver


logger = logging.getLogger('queue')


@receiver(connection_created, dispatch_uid='core.configure_development_sqlite')
def configure_development_sqlite(sender, connection, **kwargs):
    if not settings.DEBUG or connection.vendor != 'sqlite':
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA busy_timeout = 20000')
            cursor.execute('PRAGMA journal_mode = WAL')
    except Exception:
        logger.warning('sqlite.wal_configuration_failed', exc_info=True)
