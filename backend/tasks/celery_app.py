import sys
import os

# Ensure backend/ is on sys.path so sibling modules (config, database, models) are importable
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from celery import Celery
from celery.signals import worker_process_init
from config import settings, validate_startup_security, ensure_secret_key

# Same secret enforcement as the API entrypoint (main.py) — the worker holds the
# same DB/Redis credentials, so it must refuse an unsafe production boot too.
ensure_secret_key()
validate_startup_security()

app = Celery('vapt')


@worker_process_init.connect
def _init_worker_process(**kwargs):
    """Re-insert backend/ into sys.path for every forked worker process."""
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)

app.conf.update(
    broker_url=settings.REDIS_URL,
    result_backend=settings.REDIS_URL,
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    # Default budget for modules with no per-task override (ssl_tls, headers).
    # Scaled by SCAN_TIMEOUT_MULTIPLIER - see tasks/base_task.py's scaled_timeout().
    task_soft_time_limit=round(300 * settings.SCAN_TIMEOUT_MULTIPLIER),
    task_time_limit=round(360 * settings.SCAN_TIMEOUT_MULTIPLIER),
    worker_concurrency=5,
    # Dev shortcut: set to True to run tasks synchronously without Redis.
    # REMOVE before Step 9 / Docker.
    task_always_eager=False,
    broker_connection_retry_on_startup=True,
    # Every module defining an @app.task must be listed here: `include` is what
    # the WORKER imports at boot, and a task the worker never imported is not in
    # its registry. The API can still enqueue it (routers import the task
    # directly), so the message reaches Redis fine and the worker then discards
    # it with "Received unregistered task of type ..." - the job sits at
    # 'queued' forever with no error surfaced anywhere the operator can see.
    # tests/test_celery_registry.py fails if a task module is missing from here.
    include=[
        'tasks.recon',
        'tasks.webscan',
        'tasks.ssl_tls',
        'tasks.headers',
        'tasks.owasp',
        'tasks.tech_fingerprint',
        'tasks.nuclei_scan',
        'tasks.enumeration',
        'tasks.scan_orchestrator',
        # Load testing. loadtest_orchestrator is dispatched by name from
        # routers/loadtest.py and MUST be registered. k6_runner is called
        # directly (synchronously) by the orchestrator rather than dispatched,
        # but is listed because it is a task module - registration is the
        # contract, not an optimisation.
        'tasks.k6_runner',
        'tasks.loadtest_orchestrator',
    ],
)
