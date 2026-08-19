"""
Guards the Celery task registry against a failure mode that ships silently.

`include` is what the worker imports at boot. A task missing from it is absent
from the worker's registry - but the API can still enqueue it, because routers
import the task object directly to call .delay(). So the message is accepted,
reaches Redis, and the worker discards it with

    Received unregistered task of type 'tasks.<x>'
    KeyError: 'tasks.<x>'

The job then sits at 'queued' / 0% forever. Nothing surfaces to the operator:
no failed status, no error on the job, nothing in the API logs - only a line in
the worker's stdout. This is exactly how the load-test pipeline shipped: every
load test was accepted and none ever ran.

The first test below is the real guard - it derives the expected set from the
filesystem, so a new task module added tomorrow fails here until it is
registered, rather than failing silently in production.

Run with:
    cd backend && python3 -m pytest tests/test_celery_registry.py -v
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
from pathlib import Path

import pytest

from tasks.celery_app import app

TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks"


def _modules_defining_tasks() -> set[str]:
    """Every tasks/*.py that declares at least one @app.task, as a dotted name."""
    found = set()
    for path in sorted(TASKS_DIR.glob("*.py")):
        if path.name in ("__init__.py", "celery_app.py"):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        # Match the decorator at column 0 only: a bare '@app.task' inside a
        # docstring or comment about tasks should not count.
        if re.search(r"^@app\.task\b", source, re.MULTILINE):
            found.add(f"tasks.{path.stem}")
    return found


class TestIncludeCoversEveryTaskModule:
    def test_at_least_one_task_module_was_discovered(self):
        """Cheap sanity check: if the glob silently matched nothing, the real
        assertion below would pass vacuously and guard nothing."""
        assert len(_modules_defining_tasks()) >= 9

    def test_no_task_module_is_missing_from_include(self):
        configured = set(app.conf.include or [])
        missing = _modules_defining_tasks() - configured
        assert not missing, (
            "These modules define @app.task but are not in celery_app.py's "
            f"include list, so the worker will never register them: {sorted(missing)}. "
            "Jobs dispatching them are accepted and then silently discarded."
        )

    def test_include_lists_no_module_that_does_not_exist(self):
        """The inverse: a stale entry makes the worker crash on boot with
        ModuleNotFoundError, taking every pipeline down, not just one."""
        for dotted in app.conf.include or []:
            assert dotted.startswith("tasks."), dotted
            assert (TASKS_DIR / f"{dotted.split('.', 1)[1]}.py").is_file(), (
                f"celery_app.py includes '{dotted}' but that module does not exist"
            )


class TestDispatchedTasksAreRegistered:
    """Tasks the API sends by name are the ones that break loudly for users
    when unregistered, so they get an explicit assertion each."""

    @pytest.mark.parametrize("task_name", [
        "tasks.scan_orchestrator.scan_orchestrator",
        "tasks.loadtest_orchestrator.loadtest_orchestrator",
    ])
    def test_task_is_in_the_registry(self, task_name):
        # Importing the include list is what populates app.tasks.
        for dotted in app.conf.include or []:
            __import__(dotted)
        assert task_name in app.tasks, (
            f"'{task_name}' is dispatched with .delay() but is not registered. "
            "The worker would log 'Received unregistered task' and discard it."
        )

    def test_loadtest_orchestrator_name_matches_what_the_router_dispatches(self):
        """The decorator's explicit name= and the import path must agree. If
        they drift, .delay() enqueues one name and the worker registers another
        - the same silent discard, with nothing obviously wrong in either file."""
        from tasks.loadtest_orchestrator import loadtest_orchestrator
        assert loadtest_orchestrator.name == "tasks.loadtest_orchestrator.loadtest_orchestrator"

    def test_k6_runner_name_matches(self):
        from tasks.k6_runner import run_k6_loadtest
        assert run_k6_loadtest.name == "tasks.k6_runner.run_k6_loadtest"
