"""
k6 script generation and summary parsing.

The bug these were written for: _parse_k6_summary asks k6 for the 'p(99)' trend
stat, but the generated script never set summaryTrendStats, and k6's default set
is ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)'] - no p(99). k6 simply did not
export it, _metric_val fell through to its 0.0 default, and every completed run
reported a p99 of 0.00ms: a number below its own p95 and above nothing, visibly
impossible but not an error anywhere.

TestTrendStatsContract below is the real guard - it derives the required stats
from what the parser reads, so the two cannot drift apart again.

Run with:
    cd backend && python3 -m pytest tests/test_k6_runner.py -v
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
import tempfile

import pytest

from tasks.k6_runner import _build_k6_script, _parse_k6_summary, _K6_TREND_STATS

SCENARIOS = ['ramp', 'constant', 'spike', 'soak', 'stress', 'spider']


def _config(**over):
    base = {
        'target_urls': ['https://example.com/'],
        'scenario': 'ramp',
        'virtual_users': 50,
        'duration_seconds': 30,
        'http_method': 'GET',
    }
    base.update(over)
    return base


class TestScriptGeneration:
    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_every_scenario_renders_completely(self, scenario):
        """No unsubstituted placeholder may survive into the script - k6 would
        fail to parse it and the whole run dies at startup."""
        script = _build_k6_script(_config(scenario=scenario))
        leftovers = re.findall(r"\{[a-z_]+\}", script)
        assert not leftovers, f"unrendered placeholders in {scenario}: {leftovers}"
        assert "export const options" in script
        assert "export default function" in script

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_every_scenario_sets_trend_stats(self, scenario):
        script = _build_k6_script(_config(scenario=scenario))
        assert "summaryTrendStats" in script, (
            f"{scenario} does not set summaryTrendStats, so k6 falls back to its "
            "default set and any percentile outside it exports as absent"
        )

    def test_thresholds_reach_the_script(self):
        script = _build_k6_script(_config(thresholds={
            'http_req_duration_p95': 500, 'http_req_failed_rate': 0.01}))
        assert "p(95)<500" in script
        assert "rate<0.01" in script

    def test_no_thresholds_still_renders(self):
        script = _build_k6_script(_config(thresholds={}))
        assert "thresholds: {}" in script


class TestTrendStatsContract:
    """The parser and the script must agree on which stats exist. This is the
    guard that would have caught the p99 bug."""

    def _stats_the_parser_reads(self) -> set[str]:
        """Every literal stat key _parse_k6_summary asks http_req_duration for."""
        src = open(
            os.path.join(os.path.dirname(__file__), '..', 'tasks', 'k6_runner.py'),
            encoding='utf-8').read()
        return set(re.findall(r"_metric_val\('http_req_duration',\s*'([^']+)'", src))

    def test_parser_reads_something(self):
        assert len(self._stats_the_parser_reads()) >= 6

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_script_requests_every_stat_the_parser_reads(self, scenario):
        script = _build_k6_script(_config(scenario=scenario))
        trend_line = next(l for l in script.splitlines() if 'summaryTrendStats' in l)
        for stat in self._stats_the_parser_reads():
            assert f"'{stat}'" in trend_line, (
                f"_parse_k6_summary reads '{stat}' from http_req_duration but the "
                f"{scenario} script does not ask k6 to export it - it will silently "
                f"parse as 0.0"
            )

    def test_p99_specifically_is_requested(self):
        """Named explicitly because this is the one that shipped broken."""
        assert "p(99)" in _K6_TREND_STATS


class TestParseSummary:
    def _write(self, payload) -> str:
        fd, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f)
        return path

    def _summary(self, **duration_vals):
        vals = {'avg': 11.0, 'min': 7.0, 'med': 10.0, 'max': 74.0,
                'p(90)': 14.0, 'p(95)': 16.0, 'p(99)': 45.0}
        vals.update(duration_vals)
        return {'metrics': {
            'http_req_duration': {'values': vals},
            'http_reqs': {'values': {'rate': 748.3, 'count': 22470}},
            'http_req_failed': {'values': {'rate': 0.0}},
            'data_received': {'values': {'count': 730000000}},
            'data_sent': {'values': {'count': 1000000}},
            'vus_max': {'values': {'max': 50}},
            'iterations': {'values': {'count': 7490}},
        }}

    def test_percentiles_are_read_through(self):
        path = self._write(self._summary())
        try:
            m = _parse_k6_summary(path)
        finally:
            os.unlink(path)
        assert m['http_req_duration_p50'] == 10.0   # k6 calls this 'med'
        assert m['http_req_duration_p90'] == 14.0
        assert m['http_req_duration_p95'] == 16.0
        assert m['http_req_duration_p99'] == 45.0

    def test_percentiles_are_monotonic_on_real_shaped_input(self):
        """p50 <= p90 <= p95 <= p99 <= max. The shipped bug produced p99=0,
        breaking this ordering - the cheapest possible smoke test for it."""
        path = self._write(self._summary())
        try:
            m = _parse_k6_summary(path)
        finally:
            os.unlink(path)
        ordered = [m['http_req_duration_p50'], m['http_req_duration_p90'],
                   m['http_req_duration_p95'], m['http_req_duration_p99'],
                   m['http_req_duration_max']]
        assert ordered == sorted(ordered), f"percentiles out of order: {ordered}"

    def test_missing_stat_still_parses_but_yields_zero(self):
        """Documents the failure mode: a stat k6 did not export is not an error,
        it is a silent 0.0. That is exactly why the contract test above exists."""
        s = self._summary()
        del s['metrics']['http_req_duration']['values']['p(99)']
        path = self._write(s)
        try:
            m = _parse_k6_summary(path)
        finally:
            os.unlink(path)
        assert m['http_req_duration_p99'] == 0.0

    def test_counters_and_conversions(self):
        path = self._write(self._summary())
        try:
            m = _parse_k6_summary(path)
        finally:
            os.unlink(path)
        assert m['total_requests'] == 22470
        assert m['iterations'] == 7490
        assert m['vus_max'] == 50
        assert m['total_data_received_mb'] == pytest.approx(696.2, abs=1.0)

    def test_missing_file_is_none_not_an_exception(self):
        assert _parse_k6_summary('/nonexistent/k6-summary.json') is None

    def test_malformed_json_is_none_not_an_exception(self):
        fd, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w') as f:
            f.write('{not json')
        try:
            assert _parse_k6_summary(path) is None
        finally:
            os.unlink(path)
