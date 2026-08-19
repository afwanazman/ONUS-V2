"""
Load-test scoring tests: analysis/load_analyzer.py plus the contract that
GET /api/loadtest/{id}/results actually hands the stored score to the frontend.

These exist because of a real bug: the score was computed here, stored on the
scan row, and then never exposed - so the frontend reimplemented the formula and
hardcoded the threshold component (15% of the total) to a neutral 50. Any run
with a configured latency/error budget therefore displayed a number the backend
never produced. The tests below pin both halves: the threshold component must
move the score, and the endpoint must return the stored value.

Run with:
    cd backend && python3 -m pytest tests/test_load_analyzer.py -v
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid
from unittest.mock import MagicMock, patch

import pytest

from analysis.load_analyzer import compute_performance_score, detect_breaking_point


def _metrics(p95=120.0, err=0.0, avg=100.0):
    return {
        'http_req_duration_p95': p95,
        'http_req_failed_rate': err,
        'http_req_duration_avg': avg,
    }


class TestComputePerformanceScore:
    def test_no_metrics_is_zero(self):
        assert compute_performance_score(None) == 0
        assert compute_performance_score({}) == 0

    def test_fast_and_errorless_scores_high(self):
        assert compute_performance_score(_metrics(p95=80.0, err=0.0, avg=70.0)) >= 85

    def test_slow_and_failing_scores_low(self):
        assert compute_performance_score(_metrics(p95=6000.0, err=0.5, avg=1000.0)) <= 20

    def test_latency_dominates_over_consistency(self):
        """p95 is 40% of the score and consistency only 15%, so a much slower
        run cannot outscore a fast one by being more consistent."""
        fast_jittery = compute_performance_score(_metrics(p95=90.0, err=0.0, avg=20.0))
        slow_steady = compute_performance_score(_metrics(p95=3000.0, err=0.0, avg=2900.0))
        assert fast_jittery > slow_steady

    # ── The threshold component: the exact 15% the client copy got wrong ──

    def test_passing_thresholds_beats_no_thresholds(self):
        """Thresholds that pass award the full 100 for that component; absent
        thresholds award a neutral 50. The client's hardcoded 50 therefore
        under-reported every passing run by 7.5 points."""
        m = _metrics(p95=120.0, err=0.0)
        neutral = compute_performance_score(m, None)
        passing = compute_performance_score(m, {'http_req_duration_p95': 500})
        assert passing > neutral
        assert passing - neutral == pytest.approx(7.5, abs=1)

    def test_breaching_one_budget_lands_on_the_neutral_value(self):
        """Documenting real behaviour, not asserting an ideal: the component
        starts at 100 and one breach costs 50, which is exactly the value used
        when no thresholds are set. So a single breach scores the same as not
        having declared a budget at all - only the second breach is visible.
        Worth knowing before anyone reads a threshold breach off this number."""
        m = _metrics(p95=900.0, err=0.0)
        neutral = compute_performance_score(m, None)
        breached = compute_performance_score(m, {'http_req_duration_p95': 500})
        assert breached == neutral
        # thresholds_passed on the results payload is the unambiguous signal.

    def test_breaching_both_budgets_scores_below_neutral(self):
        m = _metrics(p95=900.0, err=0.2)
        neutral = compute_performance_score(m, None)
        both = compute_performance_score(
            m, {'http_req_duration_p95': 500, 'http_req_failed_rate': 0.01})
        assert both < neutral

    def test_score_is_always_in_range(self):
        for m in (_metrics(p95=0.0, err=0.0, avg=0.0),
                  _metrics(p95=99999.0, err=1.0, avg=99999.0)):
            for th in (None, {'http_req_duration_p95': 1, 'http_req_failed_rate': 0.0}):
                assert 0 <= compute_performance_score(m, th) <= 100

    def test_is_deterministic(self):
        """The measurements vary run to run; the score derived from a given set
        of them must not."""
        m = _metrics(p95=430.0, err=0.02, avg=210.0)
        th = {'http_req_duration_p95': 500}
        assert len({compute_performance_score(m, th) for _ in range(20)}) == 1


class TestDetectBreakingPoint:
    def _point(self, t, vus, latency, errors=0):
        return {'t': t, 'vus': vus, 'latency_p95': latency, 'rps': 10, 'errors': errors}

    def test_too_few_points_is_none(self):
        assert detect_breaking_point([]) is None
        assert detect_breaking_point([self._point(i, 10, 100) for i in range(4)]) is None

    def test_flat_run_has_no_breaking_point(self):
        series = [self._point(i, 10 + i, 100) for i in range(30)]
        assert detect_breaking_point(series) is None

    def test_latency_doubling_is_detected(self):
        baseline = [self._point(i, 10, 100) for i in range(5)]
        degraded = [self._point(5 + i, 200, 900) for i in range(10)]
        assert detect_breaking_point(baseline + degraded) == 200

    def test_zero_baseline_is_none_not_a_divide_by_zero(self):
        series = [self._point(i, 10, 0) for i in range(10)]
        assert detect_breaking_point(series) is None


class TestResultsEndpointExposesStoredScore:
    """The endpoint must hand back the score the orchestrator computed, not a
    recomputation - one implementation, one number."""

    def _call(self, risk_score):
        from routers.loadtest import get_loadtest_results
        from models import ScanStatus

        job_id = uuid.uuid4()
        scan = MagicMock()
        scan.id = job_id
        scan.scan_type = 'loadtest'
        scan.status = ScanStatus.complete
        scan.user_id = None
        scan.risk_score = risk_score
        scan.domain = 'example.com'
        scan.started_at = None
        scan.completed_at = None

        lt = MagicMock()
        lt.target_urls = ['https://example.com/api']
        lt.scenario = 'ramp'
        lt.metrics = None
        lt.timeseries = None
        lt.breaking_point_vus = 200
        lt.thresholds_passed = True
        lt.ai_analysis = None
        lt.duration_seconds = 30

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [scan, lt]

        with patch("routers.loadtest._require_user", return_value=None):
            return get_loadtest_results(job_id, MagicMock(), db)

    def test_stored_score_is_returned(self):
        assert self._call(87).performance_score == 87

    def test_zero_is_preserved_not_treated_as_missing(self):
        """0 is a real, terrible score. It must not collapse to None."""
        assert self._call(0).performance_score == 0

    def test_missing_score_stays_none(self):
        """A run that failed before producing metrics has no score. None must
        survive to the client so the gauge can render an absence instead of
        claiming the target scored 0."""
        assert self._call(None).performance_score is None
