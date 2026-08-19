"""Load testing API router — mirrors routers/scan.py's patterns for load test
jobs. All endpoints are under /api/loadtest.

Load tests reuse the Scan model (with scan_type='loadtest') so the scans-list
dashboard, stuck-scan reaper, and hosted queue work identically. The LoadTest
model stores k6-specific config and results as a 1:1 extension.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
from uuid import UUID
import logging

from database import get_db
from models import Scan, LoadTest, ScanStatus
from schemas import (
    LoadTestRequest, LoadTestResponse, LoadTestStatusResponse,
    LoadTestResultsResponse, LoadTestMetrics, LoadTestTimeseriesPoint,
)
from config import settings

router = APIRouter(prefix="/api", tags=["loadtest"])
logger = logging.getLogger(__name__)


def _require_user(request: Request, db: Session):
    """Same auth guard as routers/scan.py — returns the authenticated User
    in hosted mode, or None in self-hosted mode."""
    if not settings.REQUIRE_AUTH:
        return None
    import security
    user = security.get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


@router.post("/loadtest", status_code=202)
def create_loadtest(
    body: LoadTestRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create and dispatch a load test job.

    Returns 202 Accepted with {job_id, status, target_url}. The load test
    runs asynchronously via Celery (tasks/loadtest_orchestrator.py).
    """
    user = _require_user(request, db)

    # Authorization check — load testing is inherently heavy traffic,
    # so the authorization gate is stricter: always required.
    if not body.authorized:
        raise HTTPException(
            status_code=403,
            detail=(
                "Load testing generates heavy traffic against the target. "
                "You must confirm authorization by setting authorized=true."
            ),
        )

    # Concurrent load test limit — stricter than VAPT scans since load tests
    # are resource-intensive. Max 2 concurrent load tests.
    MAX_CONCURRENT_LOADTESTS = 2
    active_count = db.query(Scan).filter(
        Scan.scan_type == 'loadtest',
        Scan.status.in_([ScanStatus.queued, ScanStatus.running, ScanStatus.analysing]),
    ).count()
    if active_count >= MAX_CONCURRENT_LOADTESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {MAX_CONCURRENT_LOADTESTS} concurrent load tests. "
                   f"Wait for an existing test to complete.",
        )

    # Build target URL list
    target_urls = [body.target_url]
    if body.target_urls:
        target_urls.extend(body.target_urls)
    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for url in target_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    target_urls = unique_urls

    # Extract domain from primary URL for the Scan row
    from urllib.parse import urlparse
    parsed = urlparse(body.target_url)
    domain = parsed.hostname or body.target_url

    # Create Scan row (reuses the VAPT lifecycle infrastructure)
    scan = Scan(
        domain=domain,
        status=ScanStatus.queued,
        authorized=True,
        scan_type='loadtest',
        user_id=user.id if user else None,
        notes=body.notes,
        module_statuses={'k6': 'queued'},
    )
    db.add(scan)
    db.flush()

    # Create LoadTest config row
    lt = LoadTest(
        scan_id=scan.id,
        target_urls=target_urls,
        scenario=body.scenario,
        virtual_users=body.virtual_users,
        duration_seconds=body.duration_seconds,
        ramp_stages=[s.model_dump() for s in body.ramp_stages] if body.ramp_stages else None,
        http_method=body.http_method,
        headers_config=body.headers if body.headers else None,
        request_body=body.request_body,
        thresholds=body.thresholds,
    )
    db.add(lt)
    db.commit()

    if body.auth:
        from tasks.auth_store import store_scan_auth
        store_scan_auth(str(scan.id), body.auth.model_dump())

    # Dispatch to Celery
    try:
        from tasks.loadtest_orchestrator import loadtest_orchestrator
        loadtest_orchestrator.delay(str(scan.id))
    except Exception as e:
        logger.error("Failed to dispatch loadtest orchestrator for scan %s: %s", scan.id, e)
        scan.status = ScanStatus.failed
        db.commit()
        raise HTTPException(status_code=500, detail="Failed to start load test. Try again.")

    logger.info("create_loadtest: job %s created for %s (scenario=%s, vus=%d, duration=%ds)",
                scan.id, body.target_url, body.scenario, body.virtual_users, body.duration_seconds)

    return LoadTestResponse(
        job_id=scan.id,
        status=scan.status.value,
        target_url=body.target_url,
    )


@router.get("/loadtest/{job_id}/status")
def get_loadtest_status(
    job_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get the current status and live metrics of a load test."""
    user = _require_user(request, db)

    scan = db.query(Scan).filter(Scan.id == job_id).first()
    if not scan or scan.scan_type != 'loadtest':
        raise HTTPException(status_code=404, detail="Load test not found")

    if settings.REQUIRE_AUTH and user and scan.user_id != user.id:
        raise HTTPException(status_code=404, detail="Load test not found")

    lt = db.query(LoadTest).filter(LoadTest.scan_id == job_id).first()
    if not lt:
        raise HTTPException(status_code=404, detail="Load test config not found")

    # Progress estimation
    progress = 0
    if scan.status == ScanStatus.complete:
        progress = 100
    elif scan.status == ScanStatus.analysing:
        progress = 90
    elif scan.status == ScanStatus.running and scan.started_at:
        elapsed = (datetime.utcnow() - scan.started_at).total_seconds()
        progress = min(85, int((elapsed / max(1, lt.duration_seconds)) * 85))

    # Live metrics from the latest timeseries point (if available)
    current_rps = None
    current_latency_p95 = None
    current_error_rate = None
    current_vus = None
    if lt.timeseries and len(lt.timeseries) > 0:
        latest = lt.timeseries[-1]
        current_rps = latest.get('rps')
        current_latency_p95 = latest.get('latency_p95')
        total_errors = sum(p.get('errors', 0) for p in lt.timeseries)
        total_rps = sum(p.get('rps', 0) for p in lt.timeseries)
        current_error_rate = total_errors / total_rps if total_rps > 0 else 0
        current_vus = latest.get('vus')

    return LoadTestStatusResponse(
        job_id=scan.id,
        target_url=lt.target_urls[0] if lt.target_urls else scan.domain,
        status=scan.status.value,
        progress=progress,
        started_at=scan.started_at,
        scenario=lt.scenario,
        virtual_users=lt.virtual_users,
        duration_seconds=lt.duration_seconds,
        current_rps=current_rps,
        current_latency_p95=current_latency_p95,
        current_error_rate=current_error_rate,
        current_vus=current_vus,
    )


@router.get("/loadtest/{job_id}/results")
def get_loadtest_results(
    job_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get the full results of a completed load test."""
    user = _require_user(request, db)

    scan = db.query(Scan).filter(Scan.id == job_id).first()
    if not scan or scan.scan_type != 'loadtest':
        raise HTTPException(status_code=404, detail="Load test not found")

    if settings.REQUIRE_AUTH and user and scan.user_id != user.id:
        raise HTTPException(status_code=404, detail="Load test not found")

    if scan.status not in (ScanStatus.complete, ScanStatus.failed):
        raise HTTPException(
            status_code=202,
            detail={"status": scan.status.value, "message": "Load test still in progress"},
        )

    lt = db.query(LoadTest).filter(LoadTest.scan_id == job_id).first()
    if not lt:
        raise HTTPException(status_code=404, detail="Load test results not found")

    # Build metrics response
    metrics = None
    if lt.metrics:
        try:
            metrics = LoadTestMetrics(**lt.metrics)
        except Exception:
            logger.warning("Failed to parse load test metrics for %s", job_id)

    # Build timeseries
    timeseries = None
    if lt.timeseries:
        try:
            timeseries = [LoadTestTimeseriesPoint(**p) for p in lt.timeseries]
        except Exception:
            logger.warning("Failed to parse timeseries for %s", job_id)

    # AI analysis
    ai_analysis = None
    ai_recommendations = None
    if lt.ai_analysis:
        ai_analysis = lt.ai_analysis.get('executive_summary')
        ai_recommendations = lt.ai_analysis.get('recommendations')

    return LoadTestResultsResponse(
        job_id=scan.id,
        target_url=lt.target_urls[0] if lt.target_urls else scan.domain,
        scenario=lt.scenario,
        status=scan.status.value,
        metrics=metrics,
        timeseries=timeseries,
        breaking_point_vus=lt.breaking_point_vus,
        # loadtest_orchestrator stores compute_performance_score()'s result on
        # the scan row (same column a VAPT scan uses for risk_score).
        performance_score=scan.risk_score,
        thresholds_passed=lt.thresholds_passed,
        ai_analysis=ai_analysis,
        ai_recommendations=ai_recommendations,
        duration_seconds=lt.duration_seconds,
        started_at=scan.started_at,
        completed_at=scan.completed_at,
    )


@router.post("/loadtest/{job_id}/cancel")
def cancel_loadtest(
    job_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Cancel a running or queued load test."""
    user = _require_user(request, db)

    scan = db.query(Scan).filter(Scan.id == job_id).first()
    if not scan or scan.scan_type != 'loadtest':
        raise HTTPException(status_code=404, detail="Load test not found")

    if settings.REQUIRE_AUTH and user and scan.user_id != user.id:
        raise HTTPException(status_code=404, detail="Load test not found")

    if scan.status in (ScanStatus.complete, ScanStatus.failed, ScanStatus.cancelled):
        raise HTTPException(status_code=409, detail="Load test already terminated")

    scan.status = ScanStatus.cancelled
    scan.completed_at = datetime.utcnow()
    db.commit()

    logger.info("cancel_loadtest: job %s cancelled", job_id)
    return {"status": "cancelled", "job_id": str(job_id)}


@router.get("/loadtest/scenarios")
def list_scenarios():
    """Return the available load test scenario types with descriptions."""
    return {
        "scenarios": [
            {
                "id": "ramp",
                "label": "Ramp Up/Down",
                "description": "Gradually increase VUs to the target, hold, then ramp down. "
                               "The standard load test — reveals how performance scales.",
                "icon_hint": "trending-up",
            },
            {
                "id": "constant",
                "label": "Constant Load",
                "description": "Maintain a fixed number of VUs for the entire duration. "
                               "Tests steady-state performance at a known concurrency.",
                "icon_hint": "minus",
            },
            {
                "id": "spike",
                "label": "Spike Test",
                "description": "Sudden burst from 0 to target VUs. Tests resilience under "
                               "unexpected traffic surges (flash sales, viral events).",
                "icon_hint": "zap",
            },
            {
                "id": "soak",
                "label": "Soak Test",
                "description": "Moderate load sustained for an extended period. Reveals memory "
                               "leaks, connection pool exhaustion, and gradual degradation.",
                "icon_hint": "clock",
            },
            {
                "id": "stress",
                "label": "Stress Test",
                "description": "Progressively increase load beyond expected capacity. "
                               "Finds the breaking point — at what concurrency does it fail?",
                "icon_hint": "alert-triangle",
            },
        ]
    }
