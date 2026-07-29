from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def health(request):
    database_ok = False
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            database_ok = cursor.fetchone() == (1,)
    except Exception:
        database_ok = False

    healthy = database_ok
    return JsonResponse(
        {
            'status': 'ok' if healthy else 'unavailable',
            'application': 'ok',
            'database': 'ok' if database_ok else 'unavailable',
            'database_engine': connection.vendor,
        },
        status=200 if healthy else 503,
    )
