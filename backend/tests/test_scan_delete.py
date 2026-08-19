"""
DELETE /api/scan/{id} and POST /api/scans/delete tests.

Covers the two rules that make deletion safe:
  1. only a job nothing is still writing to may be deleted (terminal statuses)
  2. deletion is owner-scoped in hosted mode, via the same
     get_owned_scan_or_404 gate every other scan endpoint uses

Run with:
    cd backend && python3 -m pytest tests/test_scan_delete.py -v
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from routers.scan import delete_scan, bulk_delete_scans, _delete_blocked_reason
from models import ScanStatus
from schemas import ScanBulkDeleteRequest


def _fake_scan(status=ScanStatus.complete, scan_type="full", scan_id=None):
    scan = MagicMock()
    scan.id = scan_id or uuid.uuid4()
    scan.status = status
    scan.scan_type = scan_type
    return scan


TERMINAL = [ScanStatus.complete, ScanStatus.failed, ScanStatus.cancelled]
IN_FLIGHT = [
    ScanStatus.queued,
    ScanStatus.running,
    ScanStatus.analysing,
    ScanStatus.awaiting_user_decision,
]


class TestDeleteGuard:
    @pytest.mark.parametrize("status", TERMINAL)
    def test_terminal_statuses_are_deletable(self, status):
        assert _delete_blocked_reason(_fake_scan(status=status)) is None

    @pytest.mark.parametrize("status", IN_FLIGHT)
    def test_in_flight_statuses_are_blocked(self, status):
        reason = _delete_blocked_reason(_fake_scan(status=status))
        assert reason is not None
        # The message must name the actual status - "cannot delete" alone
        # leaves the operator with no idea what to do next.
        assert status.value in reason

    def test_awaiting_decision_is_blocked(self):
        """A scan parked for an operator decision is NOT finished: the
        orchestrator is still holding it, waiting for an answer. Deleting it
        would strand that task against a row that no longer exists."""
        assert _delete_blocked_reason(
            _fake_scan(status=ScanStatus.awaiting_user_decision)
        ) is not None


class TestDeleteScan:
    def test_deletes_terminal_scan_and_commits(self):
        scan = _fake_scan(status=ScanStatus.complete)
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404", return_value=scan):
            result = delete_scan(scan.id, MagicMock(), db)
        assert result is None
        db.delete.assert_called_once_with(scan)
        db.commit.assert_called_once()

    def test_running_scan_is_409_and_not_deleted(self):
        scan = _fake_scan(status=ScanStatus.running)
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404", return_value=scan):
            with pytest.raises(HTTPException) as exc:
                delete_scan(scan.id, MagicMock(), db)
        assert exc.value.status_code == 409
        db.delete.assert_not_called()
        db.commit.assert_not_called()

    def test_missing_scan_propagates_404(self):
        """Ownership/existence is delegated entirely to get_owned_scan_or_404,
        so a foreign scan in hosted mode surfaces as the same 404 as a missing
        one - deletion must never confirm another user's scan exists."""
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404",
                   side_effect=HTTPException(status_code=404, detail="Scan not found")):
            with pytest.raises(HTTPException) as exc:
                delete_scan(uuid.uuid4(), MagicMock(), db)
        assert exc.value.status_code == 404
        db.delete.assert_not_called()

    def test_load_test_deletes_the_same_way(self):
        """A load test is a Scan row with scan_type='loadtest'; the ORM cascade
        takes its load_tests row with it. Nothing about deletion is type-specific."""
        scan = _fake_scan(status=ScanStatus.complete, scan_type="loadtest")
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404", return_value=scan):
            delete_scan(scan.id, MagicMock(), db)
        db.delete.assert_called_once_with(scan)


class TestBulkDelete:
    def test_deletes_all_terminal_ids_in_one_commit(self):
        scans = [_fake_scan(status=ScanStatus.complete) for _ in range(3)]
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404", side_effect=scans):
            res = bulk_delete_scans(
                ScanBulkDeleteRequest(job_ids=[s.id for s in scans]), MagicMock(), db
            )
        assert set(res.deleted) == {s.id for s in scans}
        assert res.skipped == []
        assert db.delete.call_count == 3
        # One commit for the batch, not one per row.
        db.commit.assert_called_once()

    def test_partial_success_keeps_running_jobs_and_reports_why(self):
        """The whole point of the per-id contract: one running scan in the
        selection must not block deleting the others."""
        ok_a = _fake_scan(status=ScanStatus.complete)
        busy = _fake_scan(status=ScanStatus.running)
        ok_b = _fake_scan(status=ScanStatus.failed)
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404", side_effect=[ok_a, busy, ok_b]):
            res = bulk_delete_scans(
                ScanBulkDeleteRequest(job_ids=[ok_a.id, busy.id, ok_b.id]), MagicMock(), db
            )
        assert set(res.deleted) == {ok_a.id, ok_b.id}
        assert [s.job_id for s in res.skipped] == [busy.id]
        assert "running" in res.skipped[0].reason
        assert db.delete.call_count == 2

    def test_unknown_id_is_skipped_not_fatal(self):
        ok = _fake_scan(status=ScanStatus.complete)
        missing_id = uuid.uuid4()
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404",
                   side_effect=[ok, HTTPException(status_code=404, detail="Scan not found")]):
            res = bulk_delete_scans(
                ScanBulkDeleteRequest(job_ids=[ok.id, missing_id]), MagicMock(), db
            )
        assert res.deleted == [ok.id]
        assert [s.job_id for s in res.skipped] == [missing_id]
        assert res.skipped[0].reason == "Not found."

    def test_401_propagates_instead_of_being_swallowed_per_id(self):
        """A missing session is a request-level failure. Reporting it as 100
        individually 'not found' rows would turn an auth error into a silent
        no-op."""
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404",
                   side_effect=HTTPException(status_code=401, detail="Authentication required.")):
            with pytest.raises(HTTPException) as exc:
                bulk_delete_scans(
                    ScanBulkDeleteRequest(job_ids=[uuid.uuid4()]), MagicMock(), db
                )
        assert exc.value.status_code == 401

    def test_duplicate_ids_are_collapsed(self):
        """Without de-duplication the same id would be looked up twice and
        reported once as deleted and once as missing."""
        scan = _fake_scan(status=ScanStatus.complete)
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404", return_value=scan):
            res = bulk_delete_scans(
                ScanBulkDeleteRequest(job_ids=[scan.id, scan.id]), MagicMock(), db
            )
        assert res.deleted == [scan.id]
        assert res.skipped == []
        db.delete.assert_called_once()

    def test_nothing_deletable_makes_no_commit(self):
        busy = _fake_scan(status=ScanStatus.running)
        db = MagicMock()
        with patch("routers.scan.get_owned_scan_or_404", return_value=busy):
            res = bulk_delete_scans(
                ScanBulkDeleteRequest(job_ids=[busy.id]), MagicMock(), db
            )
        assert res.deleted == []
        assert len(res.skipped) == 1
        db.commit.assert_not_called()


class TestBulkDeleteRequestValidation:
    def test_empty_list_is_rejected(self):
        with pytest.raises(Exception):
            ScanBulkDeleteRequest(job_ids=[])

    def test_batch_is_capped(self):
        """Capped at one page of the listing so a single request can never try
        to drop the whole table."""
        with pytest.raises(Exception):
            ScanBulkDeleteRequest(job_ids=[uuid.uuid4() for _ in range(101)])
