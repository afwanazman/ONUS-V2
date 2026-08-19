"""Load test orchestrator — the top-level Celery task for a load test job.

Mirrors scan_orchestrator.py's pattern but simpler: no multi-module chord
(one k6 invocation per test), no operator decision pause (load tests either
succeed or fail, no partial-retry semantics).

Pipeline: dispatch k6_runner → analyze metrics → AI prose → update DB → complete.
"""
import logging
from datetime import datetime

from tasks.celery_app import app
from tasks.base_task import scaled_timeout

logger = logging.getLogger(__name__)

_LT_ORCHESTRATOR_SOFT = scaled_timeout(800)
_LT_ORCHESTRATOR_HARD = scaled_timeout(900)


@app.task(bind=True, name='tasks.loadtest_orchestrator.loadtest_orchestrator',
          soft_time_limit=_LT_ORCHESTRATOR_SOFT, time_limit=_LT_ORCHESTRATOR_HARD)
def loadtest_orchestrator(self, scan_id: str) -> None:
    """Main Celery task for a load test: reads config from the LoadTest row,
    runs k6, analyzes results, and writes everything back to the DB.

    The Scan row's status transitions are:
      queued → running → analysing → complete/failed
    """
    from database import SessionLocal
    from models import Scan, LoadTest, ScanStatus

    db = SessionLocal()
    try:
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            logger.error("loadtest_orchestrator: scan %s not found", scan_id)
            return

        lt = db.query(LoadTest).filter(LoadTest.scan_id == scan_id).first()
        if not lt:
            logger.error("loadtest_orchestrator: LoadTest config for scan %s not found", scan_id)
            scan.status = ScanStatus.failed
            db.commit()
            return

        # Mark running
        scan.status = ScanStatus.running
        scan.started_at = datetime.utcnow()
        scan.module_statuses = {'k6': 'running'}
        db.commit()
        logger.info("loadtest_orchestrator: scan %s started for %s (scenario=%s, vus=%d)",
                     scan_id, scan.domain, lt.scenario, lt.virtual_users)

        # Build config dict from LoadTest model
        config = {
            'target_urls': lt.target_urls,
            'scenario': lt.scenario,
            'virtual_users': lt.virtual_users,
            'duration_seconds': lt.duration_seconds,
            'ramp_stages': lt.ramp_stages,
            'http_method': lt.http_method,
            'headers_config': lt.headers_config,
            'request_body': lt.request_body,
            'thresholds': lt.thresholds,
        }
        
        from tasks.auth_store import get_scan_auth
        config['auth'] = get_scan_auth(scan_id)
    finally:
        db.close()

    # --- Run k6 synchronously (within this task's own Celery time limit) ---
    try:
        from tasks.k6_runner import run_k6_loadtest
        # Call the function directly (not .delay()) — we want synchronous
        # execution within this orchestrator task, same as how _finalize()
        # calls aggregate() synchronously in scan_orchestrator.py.
        k6_result = run_k6_loadtest(scan_id, config)
    except Exception as e:
        logger.exception("loadtest_orchestrator: k6 execution failed for scan %s: %s", scan_id, e)
        k6_result = {
            'status': 'failed',
            'metrics': None,
            'timeseries': [],
            'k6_summary': None,
            'tool_versions': {},
            'duration_seconds': 0,
            'error': str(e)[:500],
        }

    db = SessionLocal()
    try:
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        lt = db.query(LoadTest).filter(LoadTest.scan_id == scan_id).first()
        if not scan or not lt:
            logger.error("loadtest_orchestrator: scan/loadtest %s disappeared mid-run", scan_id)
            return

        if k6_result.get('status') in ('failed', 'timeout'):
            scan.status = ScanStatus.failed
            scan.module_statuses = {'k6': k6_result['status']}
            lt.k6_summary = k6_result.get('k6_summary')
            lt.metrics = k6_result.get('metrics')
            db.commit()
            logger.error("loadtest_orchestrator: scan %s failed: %s", scan_id, k6_result.get('error'))
            return

        # --- Analyze ---
        scan.status = ScanStatus.analysing
        scan.module_statuses = {'k6': 'complete', 'analysis': 'running'}
        db.commit()

        # Store raw k6 output
        lt.k6_summary = k6_result.get('k6_summary')
        lt.metrics = k6_result.get('metrics')
        lt.timeseries = k6_result.get('timeseries')
        lt.thresholds_passed = k6_result.get('thresholds_passed')

        # Detect breaking point
        try:
            from analysis.load_analyzer import detect_breaking_point, compute_performance_score
            timeseries = k6_result.get('timeseries', [])
            breaking_point = detect_breaking_point(timeseries)
            lt.breaking_point_vus = breaking_point

            # Compute a performance score (0–100, similar to risk_score)
            perf_score = compute_performance_score(k6_result.get('metrics'), lt.thresholds)
            scan.risk_score = perf_score
        except Exception as e:
            logger.error("loadtest_orchestrator: breaking-point detection failed for scan %s: %s",
                         scan_id, e)

        # AI analysis
        try:
            from analysis.load_analyzer import analyse_load_test
            ai_result = analyse_load_test(
                metrics=k6_result.get('metrics'),
                timeseries=k6_result.get('timeseries', []),
                config=config,
                breaking_point_vus=lt.breaking_point_vus,
                thresholds_passed=lt.thresholds_passed,
            )
            lt.ai_analysis = ai_result
            scan.ai_analysis = ai_result
        except Exception as e:
            logger.error("loadtest_orchestrator: AI analysis failed for scan %s: %s", scan_id, e)
            lt.ai_analysis = {
                'executive_summary': 'Load test completed. AI analysis unavailable.',
                'recommendations': [],
                'ai_unavailable': True,
            }
            scan.ai_analysis = lt.ai_analysis

        # Complete
        scan.status = ScanStatus.complete
        scan.completed_at = datetime.utcnow()
        scan.module_statuses = {'k6': 'complete', 'analysis': 'complete'}
        db.commit()

        logger.info("loadtest_orchestrator: scan %s complete, performance_score=%s",
                     scan_id, scan.risk_score)

    except Exception:
        logger.exception("loadtest_orchestrator: unhandled error for scan %s", scan_id)
        try:
            if scan is not None:
                scan.status = ScanStatus.failed
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
