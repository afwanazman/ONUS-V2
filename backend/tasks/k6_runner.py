"""k6 load test runner — the Celery task that generates a k6 JavaScript scenario
from the user's config (LoadTest model), executes it via subprocess, and parses
the structured JSON summary output into the ONUS metrics schema.

k6 is chosen because it's a single Go binary (easy to Dockerize), outputs
structured JSON natively (--out json + --summary-export), and supports
ramping-vus/constant-arrival-rate/spike/soak scenarios out of the box.

This module follows the same patterns as the VAPT scanning tasks:
  - subprocess with controlled timeout
  - temp-file cleanup in finally blocks
  - build_module_result-style envelope return
  - scaled_timeout for configurable patience
"""
import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Dict, List, Optional

from tasks.celery_app import app
from tasks.base_task import scaled_timeout, get_tool_version

logger = logging.getLogger(__name__)

# k6 subprocess timeout: user's configured duration + generous headroom for
# ramp-up/ramp-down phases, plus the scaled multiplier.
_K6_OVERHEAD_SECONDS = 120  # ramp phases + k6 startup/teardown


def _build_k6_script(config: dict) -> str:
    """Generate a k6 JavaScript test script from the LoadTest config dict.

    Returns a complete k6 script string. The script is written to a temp file
    and passed to `k6 run`. Scenarios are built dynamically based on the
    user's chosen scenario type.
    """
    target_urls = config.get('target_urls', [])
    primary_url = target_urls[0] if target_urls else 'http://localhost'
    scenario = config.get('scenario', 'ramp')
    vus = config.get('virtual_users', 50)
    duration = config.get('duration_seconds', 30)
    method = config.get('http_method', 'GET').upper()
    headers = config.get('headers_config') or {}
    body = config.get('request_body', '')
    thresholds = config.get('thresholds') or {}
    ramp_stages = config.get('ramp_stages')

    # Build k6 thresholds object
    k6_thresholds = {}
    if thresholds.get('http_req_duration_p95'):
        k6_thresholds['http_req_duration'] = [
            f"p(95)<{thresholds['http_req_duration_p95']}"
        ]
    if thresholds.get('http_req_failed_rate') is not None:
        k6_thresholds['http_req_failed'] = [
            f"rate<{thresholds['http_req_failed_rate']}"
        ]

    # Handle auth injection
    auth_config = config.get('auth')
    if auth_config:
        try:
            from tasks.auth_login import resolve_login_type, fetch_json_auth_token, auth_header_from
            ltype = resolve_login_type(auth_config)
            if ltype == 'json':
                token = fetch_json_auth_token(auth_config)
                if token:
                    h_name, h_val = auth_header_from(auth_config, token)
                    headers[h_name] = h_val
        except Exception as e:
            logger.error("Failed to fetch auth token for k6 load test: %s", e)

    thresholds_js = json.dumps(k6_thresholds)
    headers_js = json.dumps(headers) if headers else '{}'
    
    if scenario == 'spider':
        ramp_up = max(5, duration // 3)
        hold = max(5, duration // 3)
        ramp_down = max(5, duration - ramp_up - hold)
        options_js = f"""
  scenarios: {{
    spider: {{
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        {{ duration: '{ramp_up}s', target: {vus} }},
        {{ duration: '{hold}s', target: {vus} }},
        {{ duration: '{ramp_down}s', target: 0 }},
      ],
    }},
  }},"""
        script = f"""
import http from 'k6/http';
import {{ check, sleep }} from 'k6';
import {{ parseHTML }} from 'k6/html';
import {{ Rate }} from 'k6/metrics';

const errorRate = new Rate('errors');

export const options = {{
{options_js}
  thresholds: {thresholds_js},
}};

const headers = {headers_js};
const BASE_URL = '{primary_url}';

export default function () {{
  const res = http.get(BASE_URL, {{ headers: headers }});
  
  check(res, {{
    'status is 2xx': (r) => r.status >= 200 && r.status < 300,
  }});
  
  errorRate.add(res.status >= 400);

  const doc = parseHTML(res.body);
  const links = doc.find('a[href]').toArray();
  let validLinks = [];
  links.forEach((a) => {{
    const href = a.attr('href');
    if (href && (href.startsWith('/') || href.startsWith(BASE_URL))) {{
      validLinks.push(href);
    }}
  }});

  if (validLinks.length > 0) {{
    const nextLink = validLinks[Math.floor(Math.random() * validLinks.length)];
    let targetUrl = nextLink;
    if (targetUrl.startsWith('/')) {{
      targetUrl = BASE_URL + targetUrl;
    }}
    // Optionally respect method, but standard spidering uses GET.
    const nextRes = http.get(targetUrl, {{ headers: headers }});
    errorRate.add(nextRes.status >= 400);
  }}
  
  sleep(1);
}}
"""
        return script

    # Build scenario options based on type
    if scenario == 'constant':
        options_js = f"""
  scenarios: {{
    constant_load: {{
      executor: 'constant-vus',
      vus: {vus},
      duration: '{duration}s',
    }},
  }},"""
    elif scenario == 'spike':
        options_js = f"""
  scenarios: {{
    spike: {{
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        {{ duration: '5s', target: 0 }},
        {{ duration: '5s', target: {vus} }},
        {{ duration: '{max(5, duration - 20)}s', target: {vus} }},
        {{ duration: '5s', target: 0 }},
      ],
    }},
  }},"""
    elif scenario == 'soak':
        # Soak: moderate load sustained for the full duration
        soak_vus = max(1, vus // 2)
        options_js = f"""
  scenarios: {{
    soak: {{
      executor: 'constant-vus',
      vus: {soak_vus},
      duration: '{duration}s',
    }},
  }},"""
    elif scenario == 'stress':
        # Stress: progressively increase beyond expected capacity
        options_js = f"""
  scenarios: {{
    stress: {{
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        {{ duration: '{max(5, duration // 4)}s', target: {vus // 2} }},
        {{ duration: '{max(5, duration // 4)}s', target: {vus} }},
        {{ duration: '{max(5, duration // 4)}s', target: {int(vus * 1.5)} }},
        {{ duration: '{max(5, duration // 4)}s', target: 0 }},
      ],
    }},
  }},"""
    else:  # 'ramp' (default)
        if ramp_stages:
            stages_js = json.dumps([
                {'duration': s.get('duration', '10s'), 'target': s.get('target', vus)}
                for s in ramp_stages
            ])
            options_js = f"""
  scenarios: {{
    ramp: {{
      executor: 'ramping-vus',
      startVUs: 0,
      stages: {stages_js},
    }},
  }},"""
        else:
            # Default ramp: 0 → vus over 1/3 duration, hold for 1/3, ramp down
            ramp_up = max(5, duration // 3)
            hold = max(5, duration // 3)
            ramp_down = max(5, duration - ramp_up - hold)
            options_js = f"""
  scenarios: {{
    ramp: {{
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        {{ duration: '{ramp_up}s', target: {vus} }},
        {{ duration: '{hold}s', target: {vus} }},
        {{ duration: '{ramp_down}s', target: 0 }},
      ],
    }},
  }},"""

    # Build the request call based on HTTP method
    if method in ('POST', 'PUT', 'PATCH') and body:
        body_js = json.dumps(body)
        request_call = f"http.{method.lower()}(url, {body_js}, {{ headers: headers }})"
    else:
        request_call = f"http.{method.lower()}(url, {{ headers: headers }})"

    # Multi-URL support: round-robin across target URLs
    if len(target_urls) > 1:
        urls_js = json.dumps(target_urls)
        url_selection = f"""
  const urls = {urls_js};
  const url = urls[__ITER % urls.length];"""
    else:
        url_selection = f"\n  const url = '{primary_url}';"

    script = f"""
import http from 'k6/http';
import {{ check, sleep }} from 'k6';
import {{ Rate, Trend }} from 'k6/metrics';

const errorRate = new Rate('errors');

export const options = {{
{options_js}
  thresholds: {thresholds_js},
}};

const headers = {headers_js};

export default function () {{
{url_selection}

  const res = {request_call};

  check(res, {{
    'status is 2xx': (r) => r.status >= 200 && r.status < 300,
    'response time < 500ms': (r) => r.timings.duration < 500,
  }});

  errorRate.add(res.status >= 400);

  sleep(0.1);
}}
"""
    return script


def _parse_k6_summary(summary_path: str) -> Optional[dict]:
    """Parse the k6 --summary-export JSON file into a structured metrics dict."""
    try:
        with open(summary_path, 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to parse k6 summary: %s", e)
        return None

    metrics = data.get('metrics', {})

    def _metric_val(name: str, stat: str = 'avg', default: float = 0.0) -> float:
        m = metrics.get(name, {})
        if isinstance(m, dict):
            vals = m.get('values', m)
            return round(float(vals.get(stat, default)), 2)
        return default

    return {
        'http_req_duration_avg': _metric_val('http_req_duration', 'avg'),
        'http_req_duration_min': _metric_val('http_req_duration', 'min'),
        'http_req_duration_max': _metric_val('http_req_duration', 'max'),
        'http_req_duration_p50': _metric_val('http_req_duration', 'med'),
        'http_req_duration_p90': _metric_val('http_req_duration', 'p(90)'),
        'http_req_duration_p95': _metric_val('http_req_duration', 'p(95)'),
        'http_req_duration_p99': _metric_val('http_req_duration', 'p(99)'),
        'http_reqs_per_second': _metric_val('http_reqs', 'rate'),
        'http_req_failed_rate': _metric_val('http_req_failed', 'rate'),
        'total_requests': int(_metric_val('http_reqs', 'count', 0)),
        'total_data_received_mb': round(_metric_val('data_received', 'count', 0) / (1024 * 1024), 2),
        'total_data_sent_mb': round(_metric_val('data_sent', 'count', 0) / (1024 * 1024), 2),
        'vus_max': int(_metric_val('vus_max', 'max', 0) or _metric_val('vus_max', 'value', 0)),
        'iterations': int(_metric_val('iterations', 'count', 0)),
    }


def _parse_k6_json_output(json_output_path: str) -> List[dict]:
    """Parse the k6 --out json output file into a per-second timeseries.

    k6 --out json writes one JSON object per line: metric data points with
    timestamps. We bucket these into per-second aggregates for charting.
    """
    buckets: Dict[int, dict] = {}
    start_time = None

    try:
        with open(json_output_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get('type') != 'Point':
                    continue

                data = entry.get('data', {})
                ts = data.get('time', '')
                metric = entry.get('metric', '')
                value = data.get('value', 0)

                # Parse ISO timestamp to epoch second
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    epoch = int(dt.timestamp())
                except (ValueError, AttributeError):
                    continue

                if start_time is None:
                    start_time = epoch

                t = epoch - start_time
                if t < 0:
                    continue

                if t not in buckets:
                    buckets[t] = {
                        't': t, 'rps': 0, 'latency_samples': [],
                        'errors': 0, 'vus': 0
                    }

                bucket = buckets[t]

                if metric == 'http_reqs':
                    bucket['rps'] += 1
                elif metric == 'http_req_duration':
                    bucket['latency_samples'].append(value)
                elif metric == 'http_req_failed' and value == 1:
                    bucket['errors'] += 1
                elif metric == 'vus':
                    bucket['vus'] = max(bucket['vus'], int(value))

    except FileNotFoundError:
        logger.warning("k6 JSON output file not found: %s", json_output_path)
        return []

    # Convert buckets to timeseries points
    timeseries = []
    for t in sorted(buckets.keys()):
        b = buckets[t]
        samples = b['latency_samples']
        if samples:
            samples.sort()
            p95_idx = min(int(len(samples) * 0.95), len(samples) - 1)
            p95 = round(samples[p95_idx], 2)
            avg = round(sum(samples) / len(samples), 2)
        else:
            p95 = 0.0
            avg = 0.0

        timeseries.append({
            't': t,
            'rps': b['rps'],
            'latency_p95': p95,
            'latency_avg': avg,
            'errors': b['errors'],
            'vus': b['vus'],
        })

    return timeseries


# Celery soft/hard limits: user duration + overhead, scaled.
_LT_SOFT_LIMIT_BASE = 600   # 10min base for generous headroom
_LT_HARD_LIMIT_BASE = 660


@app.task(bind=True, name='tasks.k6_runner.run_k6_loadtest',
          soft_time_limit=scaled_timeout(_LT_SOFT_LIMIT_BASE),
          time_limit=scaled_timeout(_LT_HARD_LIMIT_BASE))
def run_k6_loadtest(self, scan_id: str, config: dict) -> dict:
    """Execute a k6 load test and return structured results.

    `config` is the LoadTest model's config fields as a dict (target_urls,
    scenario, virtual_users, duration_seconds, etc.).

    Returns a dict matching the load test result envelope:
    {status, metrics, timeseries, k6_summary, tool_versions, duration_seconds, error}
    """
    start = time.time()
    script_path = None
    summary_path = None
    json_output_path = None

    try:
        # Generate k6 script
        script = _build_k6_script(config)

        # Write to temp files
        fd_script, script_path = tempfile.mkstemp(suffix='.js', prefix=f'k6_{scan_id}_')
        os.close(fd_script)
        with open(script_path, 'w') as f:
            f.write(script)

        fd_summary, summary_path = tempfile.mkstemp(suffix='.json', prefix=f'k6_summary_{scan_id}_')
        os.close(fd_summary)

        fd_json, json_output_path = tempfile.mkstemp(suffix='.json', prefix=f'k6_out_{scan_id}_')
        os.close(fd_json)

        # Calculate timeout: user duration + overhead
        user_duration = config.get('duration_seconds', 30)
        timeout = scaled_timeout(user_duration + _K6_OVERHEAD_SECONDS)

        # Run k6
        cmd = [
            'k6', 'run',
            '--out', f'json={json_output_path}',
            '--summary-export', summary_path,
            '--no-color',
            '--quiet',
            script_path,
        ]

        logger.info("k6_runner: starting load test for scan %s, scenario=%s, vus=%d, duration=%ds",
                     scan_id, config.get('scenario'), config.get('virtual_users'), user_duration)

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

        duration = round(time.time() - start, 2)

        if result.returncode != 0:
            stderr = (result.stderr or b'').decode(errors='ignore')[:1000]
            # k6 returns exit code 99 when thresholds fail — that's NOT an error,
            # it's a valid test result with failing thresholds.
            if result.returncode == 99:
                logger.info("k6_runner: thresholds failed for scan %s (exit 99)", scan_id)
            else:
                logger.error("k6_runner: k6 exited %d for scan %s: %s",
                             result.returncode, scan_id, stderr)
                return {
                    'status': 'failed',
                    'metrics': None,
                    'timeseries': [],
                    'k6_summary': None,
                    'tool_versions': {'k6': get_tool_version('k6', 'version')},
                    'duration_seconds': duration,
                    'error': f'k6 exit code {result.returncode}: {stderr[:200]}',
                }

        # Parse results
        metrics = _parse_k6_summary(summary_path)
        timeseries = _parse_k6_json_output(json_output_path)

        # Read raw k6 summary for storage
        k6_summary = None
        try:
            with open(summary_path, 'r') as f:
                k6_summary = json.load(f)
        except Exception:
            pass

        # Determine if thresholds passed (exit code 99 = thresholds failed)
        thresholds_passed = result.returncode == 0

        return {
            'status': 'success',
            'metrics': metrics,
            'timeseries': timeseries,
            'k6_summary': k6_summary,
            'thresholds_passed': thresholds_passed,
            'tool_versions': {'k6': get_tool_version('k6', 'version')},
            'duration_seconds': duration,
            'error': None,
        }

    except subprocess.TimeoutExpired:
        return {
            'status': 'timeout',
            'metrics': None,
            'timeseries': [],
            'k6_summary': None,
            'tool_versions': {'k6': get_tool_version('k6', 'version')},
            'duration_seconds': round(time.time() - start, 2),
            'error': 'k6 execution timed out',
        }
    except Exception as e:
        logger.exception("k6_runner: unexpected error for scan %s: %s", scan_id, e)
        return {
            'status': 'failed',
            'metrics': None,
            'timeseries': [],
            'k6_summary': None,
            'tool_versions': {'k6': get_tool_version('k6', 'version')},
            'duration_seconds': round(time.time() - start, 2),
            'error': str(e)[:500],
        }
    finally:
        # Temp-file cleanup
        for path in (script_path, summary_path, json_output_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
