"""Load test analysis — processes k6 output metrics, detects breaking points,
computes a performance score, and feeds Ollama for prose analysis.

This is the load testing counterpart of analysis/cvss_scorer.py +
analysis/ollama_client.py: deterministic metrics processing first,
then AI-generated prose on top.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def detect_breaking_point(timeseries: List[dict]) -> Optional[int]:
    """Detect the VU count where performance significantly degraded.

    Heuristic: find the point where p95 latency jumps by >100% relative to
    the baseline (first 5 data points average) OR error rate exceeds 5%.
    Returns the VU count at that point, or None if no breaking point found.
    """
    if not timeseries or len(timeseries) < 5:
        return None

    # Baseline: average of first 5 data points
    baseline_points = timeseries[:5]
    baseline_latency = sum(p.get('latency_p95', 0) for p in baseline_points) / len(baseline_points)

    if baseline_latency <= 0:
        return None

    for point in timeseries[5:]:
        latency = point.get('latency_p95', 0)
        vus = point.get('vus', 0)
        rps = point.get('rps', 0)
        errors = point.get('errors', 0)

        # Breaking point conditions:
        # 1. Latency jumped >100% above baseline
        if latency > baseline_latency * 2:
            return vus

        # 2. Error rate >5% of RPS
        if rps > 0 and errors / rps > 0.05:
            return vus

    return None


def compute_performance_score(metrics: Optional[dict], thresholds: Optional[dict] = None) -> int:
    """Compute a performance score 0–100 (higher = better performance).

    Scoring factors (weighted):
    - p95 latency: 40% weight (lower is better)
    - Error rate: 30% weight (lower is better)
    - Throughput consistency: 15% weight
    - Threshold pass/fail: 15% weight
    """
    if not metrics:
        return 0

    score = 0.0

    # --- p95 latency score (40%) ---
    p95 = metrics.get('http_req_duration_p95', 0)
    if p95 <= 100:
        latency_score = 100
    elif p95 <= 200:
        latency_score = 90
    elif p95 <= 500:
        latency_score = 70
    elif p95 <= 1000:
        latency_score = 50
    elif p95 <= 2000:
        latency_score = 30
    elif p95 <= 5000:
        latency_score = 10
    else:
        latency_score = 0
    score += latency_score * 0.4

    # --- Error rate score (30%) ---
    error_rate = metrics.get('http_req_failed_rate', 0)
    if error_rate <= 0.001:
        error_score = 100
    elif error_rate <= 0.01:
        error_score = 80
    elif error_rate <= 0.05:
        error_score = 50
    elif error_rate <= 0.10:
        error_score = 20
    else:
        error_score = 0
    score += error_score * 0.3

    # --- Throughput consistency score (15%) ---
    # Based on whether avg latency is close to p95 (consistent response times)
    avg = metrics.get('http_req_duration_avg', 0)
    if avg > 0 and p95 > 0:
        consistency_ratio = avg / p95
        if consistency_ratio > 0.8:
            consistency_score = 90
        elif consistency_ratio > 0.6:
            consistency_score = 70
        elif consistency_ratio > 0.4:
            consistency_score = 50
        else:
            consistency_score = 20
    else:
        consistency_score = 50
    score += consistency_score * 0.15

    # --- Threshold score (15%) ---
    if thresholds:
        threshold_score = 100  # start optimistic
        if thresholds.get('http_req_duration_p95'):
            if p95 > thresholds['http_req_duration_p95']:
                threshold_score -= 50
        if thresholds.get('http_req_failed_rate') is not None:
            if error_rate > thresholds['http_req_failed_rate']:
                threshold_score -= 50
        threshold_score = max(0, threshold_score)
    else:
        # No thresholds defined — neutral
        threshold_score = 50
    score += threshold_score * 0.15

    return min(100, max(0, round(score)))


def _format_duration(ms: float) -> str:
    """Format milliseconds into a human-readable string."""
    if ms < 1:
        return f"{ms:.2f}ms"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def _severity_for_metric(value: float, thresholds: List[tuple]) -> str:
    """Return a severity label based on threshold brackets."""
    for limit, label in thresholds:
        if value <= limit:
            return label
    return thresholds[-1][1] if thresholds else 'unknown'


def analyse_load_test(
    metrics: Optional[dict],
    timeseries: List[dict],
    config: dict,
    breaking_point_vus: Optional[int] = None,
    thresholds_passed: Optional[bool] = None,
) -> dict:
    """Generate analysis for load test results.

    Returns a dict with:
    - executive_summary: prose summary
    - recommendations: list of actionable items
    - metrics_assessment: per-metric assessments
    - ai_unavailable: bool (always False for deterministic analysis)

    Tries Ollama/GitHub Models first for the executive_summary prose; falls
    back to a deterministic template if the AI backend is unreachable.
    """
    if not metrics:
        return {
            'executive_summary': 'Load test completed but produced no metrics data.',
            'recommendations': ['Check the target URL is reachable and responding to requests.'],
            'metrics_assessment': {},
            'ai_unavailable': True,
        }

    # --- Build deterministic assessments ---
    p95 = metrics.get('http_req_duration_p95', 0)
    p99 = metrics.get('http_req_duration_p99', 0)
    avg = metrics.get('http_req_duration_avg', 0)
    error_rate = metrics.get('http_req_failed_rate', 0)
    rps = metrics.get('http_reqs_per_second', 0)
    total_reqs = metrics.get('total_requests', 0)
    vus_max = metrics.get('vus_max', 0)

    scenario_name = config.get('scenario', 'ramp')
    target_vus = config.get('virtual_users', 0)
    duration = config.get('duration_seconds', 0)
    target_urls = config.get('target_urls', [])
    primary_target = target_urls[0] if target_urls else 'unknown'

    assessments = {}

    # Latency assessment
    latency_rating = _severity_for_metric(p95, [
        (100, 'excellent'), (200, 'good'), (500, 'acceptable'),
        (1000, 'degraded'), (2000, 'poor'), (5000, 'critical'),
    ])
    assessments['latency'] = {
        'rating': latency_rating,
        'p95': p95,
        'p99': p99,
        'avg': avg,
        'detail': f'p95 latency is {_format_duration(p95)} ({latency_rating})',
    }

    # Error rate assessment
    error_rating = _severity_for_metric(error_rate * 100, [
        (0.1, 'excellent'), (1, 'good'), (5, 'acceptable'),
        (10, 'degraded'), (50, 'critical'),
    ])
    assessments['errors'] = {
        'rating': error_rating,
        'rate': error_rate,
        'detail': f'Error rate is {error_rate * 100:.2f}% ({error_rating})',
    }

    # Throughput assessment
    assessments['throughput'] = {
        'rps': rps,
        'total': total_reqs,
        'detail': f'{rps:.1f} requests/second sustained ({total_reqs} total)',
    }

    # Breaking point
    if breaking_point_vus is not None:
        assessments['breaking_point'] = {
            'vus': breaking_point_vus,
            'detail': (
                f'Performance degradation detected at ~{breaking_point_vus} concurrent users. '
                f'Target was {target_vus} VUs.'
            ),
        }

    # --- Generate recommendations ---
    recommendations = []

    if error_rate > 0.05:
        recommendations.append(
            f'High error rate ({error_rate * 100:.1f}%). Investigate server logs for '
            f'5xx errors and check connection/thread pool limits.'
        )
    if p95 > 1000:
        recommendations.append(
            f'p95 latency exceeds 1 second ({_format_duration(p95)}). Consider profiling '
            f'slow endpoints, adding caching, or optimizing database queries.'
        )
    if p99 > p95 * 3 and p95 > 100:
        recommendations.append(
            f'Large p99/p95 gap ({_format_duration(p99)} vs {_format_duration(p95)}) '
            f'indicates tail latency outliers. Check for GC pauses, lock contention, '
            f'or cold-cache scenarios.'
        )
    if breaking_point_vus is not None and breaking_point_vus < target_vus:
        recommendations.append(
            f'Breaking point ({breaking_point_vus} VUs) is below your target '
            f'({target_vus} VUs). Scale horizontally or vertically before production.'
        )
    if thresholds_passed is False:
        recommendations.append(
            'One or more performance thresholds failed. Review the threshold '
            'configuration and address the underlying bottleneck.'
        )
    if not recommendations:
        recommendations.append(
            'Performance looks healthy under the tested load. Consider testing '
            'with higher concurrency or longer duration to find the ceiling.'
        )

    # --- Build executive summary ---
    summary_parts = [
        f'Load test completed against {primary_target} using a {scenario_name} '
        f'scenario with {target_vus} virtual users over {duration} seconds.',
    ]
    summary_parts.append(
        f'The target sustained {rps:.1f} requests/second with a p95 latency of '
        f'{_format_duration(p95)} and a {error_rate * 100:.2f}% error rate.'
    )
    if breaking_point_vus is not None:
        summary_parts.append(
            f'Performance degradation was detected at approximately {breaking_point_vus} '
            f'concurrent users.'
        )
    if thresholds_passed is True:
        summary_parts.append('All defined performance thresholds passed.')
    elif thresholds_passed is False:
        summary_parts.append('One or more performance thresholds were exceeded.')

    deterministic_summary = ' '.join(summary_parts)

    # Try AI-enhanced summary
    ai_summary = _try_ai_summary(metrics, config, breaking_point_vus, thresholds_passed)
    if ai_summary:
        executive_summary = ai_summary
        ai_unavailable = False
    else:
        executive_summary = deterministic_summary
        ai_unavailable = True

    return {
        'executive_summary': executive_summary,
        'recommendations': recommendations,
        'metrics_assessment': assessments,
        'ai_unavailable': ai_unavailable,
    }


def _try_ai_summary(
    metrics: dict,
    config: dict,
    breaking_point_vus: Optional[int],
    thresholds_passed: Optional[bool],
) -> Optional[str]:
    """Attempt to generate an AI-enhanced executive summary via Ollama or
    GitHub Models. Returns None if the AI backend is unreachable.

    The AI receives the deterministic metrics and produces prose — it never
    generates numbers (same principle as VAPT: AI describes, never scores).
    """
    try:
        from analysis.ollama_client import _call_llm
    except ImportError:
        return None

    prompt = f"""You are a performance engineer writing a load test report summary.
You will receive load test metrics. Write 3-5 sentences summarizing the results
for a technical audience (developers and DevOps). Focus on:
- Overall performance under load
- Key bottlenecks or concerns
- Whether the system is production-ready at this load level

Metrics:
- Scenario: {config.get('scenario')} with {config.get('virtual_users')} virtual users
- Duration: {config.get('duration_seconds')} seconds
- p95 latency: {metrics.get('http_req_duration_p95', 0):.1f}ms
- p99 latency: {metrics.get('http_req_duration_p99', 0):.1f}ms
- Average latency: {metrics.get('http_req_duration_avg', 0):.1f}ms
- RPS: {metrics.get('http_reqs_per_second', 0):.1f}
- Error rate: {metrics.get('http_req_failed_rate', 0) * 100:.2f}%
- Total requests: {metrics.get('total_requests', 0)}
- Breaking point: {f'{breaking_point_vus} VUs' if breaking_point_vus else 'not detected'}
- Thresholds: {'passed' if thresholds_passed else 'failed' if thresholds_passed is False else 'not configured'}

Write ONLY the summary paragraph, nothing else. No bullet points, no headers."""

    try:
        response = _call_llm(prompt, max_tokens=512, temperature=0.3)
        if response and len(response.strip()) > 50:
            return response.strip()
    except Exception as e:
        logger.warning("AI summary generation failed: %s", e)

    return None
