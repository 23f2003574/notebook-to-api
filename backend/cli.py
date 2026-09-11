import argparse
import contextlib
import hmac
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

# ValidationError is the exception nbformat raises for a syntactically valid
# JSON file that is nonetheless missing required notebook keys (e.g. no
# "cells"). It's distinct from nbformat.reader.NotJSONError (already a
# ValueError subclass) and needs to be named explicitly to be treated as a
# clean, user-facing CLI error rather than a raw traceback -- see
# CLI_USER_FACING_ERRORS below.
from nbformat import ValidationError as NotebookValidationError

# Import the compiler function
from backend.compiler import (
    NOTEBOOK_TO_API_VERSION,
    compile_notebook,
    compiling_python_version,
    _filter_functions_by_name,
)
# Import inspector for analysis
from backend.inspector import (
    DEFAULT_DEV_API_KEY,
    classify_notebook_diff,
    diff_notebook_functions,
    diff_notebook_source,
    generate_curl_commands,
    generate_postman_collection,
    inspect_notebook,
    inspect_notebook_data,
    print_compile_summary,
    print_notebook_diff,
)
from backend.serve import serve_notebook, watch_notebook
from backend.observability.deployment_governance_doctor import (
    run_deployment_governance_doctor,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
)
from backend.observability.deployment_governance_audit_history_cli import (
    parse_governance_audit_timestamp,
    run_deployment_governance_audit_history,
)
from backend.observability.deployment_governance_check import (
    GovernanceIntegrityCheckPolicy,
)
from backend.observability.deployment_governance_check_cli import (
    run_deployment_governance_check,
)
from backend.observability.deployment_governance_audit_prune_cli import (
    run_deployment_governance_audit_prune,
)
from backend.observability.deployment_governance_audit_export_cli import (
    run_deployment_governance_audit_export,
)
from backend.observability.deployment_governance_audit_verify_cli import (
    run_deployment_governance_audit_verify,
)
from backend.observability.deployment_governance_audit_statistics_cli import (
    run_deployment_governance_audit_stats,
)
from backend.observability.deployment_governance_audit_replay_cli import (
    run_deployment_governance_audit_replay,
)
from backend.observability.deployment_governance_audit_replay_diff_cli import (
    run_deployment_governance_audit_diff,
)
from backend.observability.deployment_governance_audit_timeline_cli import (
    run_deployment_governance_audit_timeline,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_tail,
    run_deployment_governance_logging_list,
    run_deployment_governance_logging_clear,
    run_deployment_governance_logging_rotate,
    run_deployment_governance_logging_rotation_status,
    run_deployment_governance_logging_search,
    run_deployment_governance_logging_export_json,
    run_deployment_governance_logging_export_csv,
    run_deployment_governance_logging_export_ndjson,
    run_deployment_governance_logging_redaction_rules,
    run_deployment_governance_logging_redaction_test,
    run_deployment_governance_logging_context,
    run_deployment_governance_logging_trace,
    run_deployment_governance_logging_sampling_show,
    run_deployment_governance_logging_sampling_update,
    run_deployment_governance_logging_flush,
    run_deployment_governance_logging_pending,
    run_deployment_governance_logging_replay,
    run_deployment_governance_logging_replay_next,
    run_deployment_governance_logging_config_show,
    run_deployment_governance_logging_config_reload,
    run_deployment_governance_logging_bootstrap,
    run_deployment_governance_logging_health,
)
from backend.observability.deployment_governance_audit_session_cli import (
    run_deployment_governance_audit_session,
)
from backend.observability.deployment_governance_audit_bookmarks_cli import (
    run_deployment_governance_audit_bookmark_add,
    run_deployment_governance_audit_bookmark_delete,
    run_deployment_governance_audit_bookmark_list,
    run_deployment_governance_audit_bookmark_show,
)
from backend.observability.deployment_governance_audit_labels_cli import (
    run_deployment_governance_audit_label_add,
    run_deployment_governance_audit_label_list,
    run_deployment_governance_audit_label_remove,
    run_deployment_governance_audit_label_search,
    run_deployment_governance_audit_label_show,
)
from backend.observability.deployment_governance_audit_search_cli import (
    run_deployment_governance_audit_search,
)
from backend.observability.deployment_governance_audit_saved_queries_cli import (
    run_deployment_governance_audit_saved_query_delete,
    run_deployment_governance_audit_saved_query_list,
    run_deployment_governance_audit_saved_query_run,
    run_deployment_governance_audit_saved_query_save,
    run_deployment_governance_audit_saved_query_show,
)
from backend.observability.deployment_governance_audit_collections_cli import (
    run_deployment_governance_audit_collection_add,
    run_deployment_governance_audit_collection_create,
    run_deployment_governance_audit_collection_delete,
    run_deployment_governance_audit_collection_list,
    run_deployment_governance_audit_collection_remove,
    run_deployment_governance_audit_collection_show,
)
from backend.observability.deployment_governance_audit_reports_cli import (
    run_deployment_governance_audit_report_audits,
    run_deployment_governance_audit_report_collection,
)
from backend.observability.deployment_governance_audit_report_templates import (
    GovernanceIntegrityAuditReportSource,
)
from backend.observability.deployment_governance_audit_report_templates_cli import (
    run_deployment_governance_audit_report_template_create,
    run_deployment_governance_audit_report_template_delete,
    run_deployment_governance_audit_report_template_generate,
    run_deployment_governance_audit_report_template_list,
    run_deployment_governance_audit_report_template_show,
)
from backend.observability.deployment_governance_audit_report_schedule import (
    GovernanceIntegrityReportScheduleFrequency,
)
from backend.observability.deployment_governance_audit_report_schedule_cli import (
    run_deployment_governance_audit_report_schedule_create,
    run_deployment_governance_audit_report_schedule_delete,
    run_deployment_governance_audit_report_schedule_disable,
    run_deployment_governance_audit_report_schedule_enable,
    run_deployment_governance_audit_report_schedule_list,
    run_deployment_governance_audit_report_schedule_show,
)
from backend.observability.deployment_governance_audit_execution_queue_cli import (
    run_deployment_governance_audit_queue_clear,
    run_deployment_governance_audit_queue_delete,
    run_deployment_governance_audit_queue_enqueue,
    run_deployment_governance_audit_queue_enqueue_due,
    run_deployment_governance_audit_queue_list,
    run_deployment_governance_audit_queue_show,
)
from backend.observability.deployment_governance_audit_worker_cli import (
    run_deployment_governance_audit_worker_clear,
    run_deployment_governance_audit_worker_history,
    run_deployment_governance_audit_worker_run,
    run_deployment_governance_audit_worker_run_all,
    run_deployment_governance_audit_worker_show,
)
from backend.observability.deployment_governance_audit_retry_cli import (
    run_deployment_governance_audit_retry_clear,
    run_deployment_governance_audit_retry_history,
    run_deployment_governance_audit_retry_run,
    run_deployment_governance_audit_retry_show,
)
from backend.observability.deployment_governance_dead_letter_queue_cli import (
    run_deployment_governance_dead_letter_archive,
    run_deployment_governance_dead_letter_clear,
    run_deployment_governance_dead_letter_delete,
    run_deployment_governance_dead_letter_list,
    run_deployment_governance_dead_letter_show,
)
from backend.observability.deployment_governance_failure_policy import (
    GovernanceIntegrityFailureAction,
)
from backend.observability.deployment_governance_failure_policy_cli import (
    run_deployment_governance_failure_policy_create,
    run_deployment_governance_failure_policy_delete,
    run_deployment_governance_failure_policy_list,
    run_deployment_governance_failure_policy_show,
    run_deployment_governance_failure_policy_update,
)
from backend.observability.deployment_governance_execution_metrics_cli import (
    run_deployment_governance_execution_metrics,
    run_deployment_governance_execution_metrics_for_template,
)
from backend.observability.deployment_governance_execution_alerts_cli import (
    DEFAULT_MAXIMUM_AVERAGE_DURATION_MS,
    DEFAULT_MAXIMUM_FAILURE_RATE,
    DEFAULT_MINIMUM_SUCCESS_RATE,
    run_deployment_governance_execution_alerts,
    run_deployment_governance_execution_alerts_for_template,
)
from backend.observability.deployment_governance_notifications_cli import (
    run_deployment_governance_notifications_clear,
    run_deployment_governance_notifications_delete,
    run_deployment_governance_notifications_list,
    run_deployment_governance_notifications_queue,
    run_deployment_governance_notifications_show,
)
from backend.observability.deployment_governance_metrics_cli import (
    run_deployment_governance_metrics,
    run_deployment_governance_metrics_aggregate,
    run_deployment_governance_metrics_alerts,
    run_deployment_governance_metrics_alerts_clear,
    run_deployment_governance_metrics_bootstrap,
    run_deployment_governance_metrics_collector_collect,
    run_deployment_governance_metrics_collector_status,
    run_deployment_governance_metrics_config_reload,
    run_deployment_governance_metrics_config_show,
    run_deployment_governance_metrics_dashboard,
    run_deployment_governance_metrics_health,
    run_deployment_governance_metrics_export,
    run_deployment_governance_metrics_requests,
    run_deployment_governance_metrics_retention_run,
    run_deployment_governance_metrics_retention_status,
    run_deployment_governance_metrics_export_csv,
    run_deployment_governance_metrics_export_json,
    run_deployment_governance_metrics_history,
    run_deployment_governance_metrics_latest,
    run_deployment_governance_metrics_reload,
    run_deployment_governance_metrics_reset,
)
from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from backend.observability.deployment_governance_notification_channels_cli import (
    run_deployment_governance_notification_channel_create,
    run_deployment_governance_notification_channel_delete,
    run_deployment_governance_notification_channel_disable,
    run_deployment_governance_notification_channel_enable,
    run_deployment_governance_notification_channel_list,
    run_deployment_governance_notification_channel_show,
    run_deployment_governance_notification_channel_update,
)
from backend.observability.deployment_governance_notification_dispatcher_cli import (
    run_deployment_governance_notification_dispatch_clear,
    run_deployment_governance_notification_dispatch_delete,
    run_deployment_governance_notification_dispatch_list,
    run_deployment_governance_notification_dispatch_run,
    run_deployment_governance_notification_dispatch_show,
)
from backend.observability.deployment_governance_delivery_engine_cli import (
    run_deployment_governance_delivery_run,
    run_deployment_governance_delivery_run_all,
)
from backend.observability.deployment_governance_delivery_history_cli import (
    run_deployment_governance_delivery_history_clear,
    run_deployment_governance_delivery_history_list,
    run_deployment_governance_delivery_history_show,
)
from backend.observability.deployment_governance_execution_alerts import (
    GovernanceIntegrityAlertSeverity,
)
from backend.observability.deployment_governance_notification_preferences_cli import (
    run_deployment_governance_notification_preference_create,
    run_deployment_governance_notification_preference_delete,
    run_deployment_governance_notification_preference_list,
    run_deployment_governance_notification_preference_show,
    run_deployment_governance_notification_preference_update,
)
from backend.observability.deployment_governance_delivery_policies_cli import (
    run_deployment_governance_delivery_policy_create,
    run_deployment_governance_delivery_policy_delete,
    run_deployment_governance_delivery_policy_list,
    run_deployment_governance_delivery_policy_show,
    run_deployment_governance_delivery_policy_update,
)
from backend.observability.deployment_governance_provider_registry_cli import (
    run_deployment_governance_provider_capabilities,
    run_deployment_governance_provider_disable,
    run_deployment_governance_provider_enable,
    run_deployment_governance_provider_health,
    run_deployment_governance_provider_health_all,
    run_deployment_governance_provider_list,
    run_deployment_governance_provider_metadata,
    run_deployment_governance_provider_replace,
    run_deployment_governance_provider_show,
    run_deployment_governance_provider_validate,
)
from backend.observability.deployment_governance_provider_configuration_cli import (
    run_deployment_governance_provider_config_create,
    run_deployment_governance_provider_config_delete,
    run_deployment_governance_provider_config_list,
    run_deployment_governance_provider_config_show,
    run_deployment_governance_provider_config_update,
)
from backend.observability.deployment_governance_provider_secrets_cli import (
    run_deployment_governance_provider_secrets_create,
    run_deployment_governance_provider_secrets_delete,
    run_deployment_governance_provider_secrets_list,
    run_deployment_governance_provider_secrets_show,
    run_deployment_governance_provider_secrets_update,
)
from backend.observability.deployment_governance_provider_authentication_cli import (
    run_deployment_governance_provider_auth_show,
    run_deployment_governance_provider_auth_validate,
)
from backend.observability.deployment_governance_provider_requests_cli import (
    run_deployment_governance_provider_request_show,
    run_deployment_governance_provider_request_validate,
)
from backend.observability.deployment_governance_provider_responses_cli import (
    run_deployment_governance_provider_response_show,
    run_deployment_governance_provider_response_validate,
)
from backend.observability.deployment_governance_retry_orchestrator_cli import (
    run_deployment_governance_retries_evaluate,
    run_deployment_governance_retries_preview,
)
from backend.observability.deployment_governance_delivery_scheduler_cli import (
    run_deployment_governance_scheduler_cancel,
    run_deployment_governance_scheduler_pending,
    run_deployment_governance_scheduler_ready,
    run_deployment_governance_scheduler_show,
)
from backend.observability.deployment_governance_delivery_worker_cli import (
    run_deployment_governance_delivery_worker_run,
    run_deployment_governance_delivery_worker_summary,
)
# export_openapi_schema is imported lazily (see below) because it imports
# generated/app.py at module load time, which re-executes a previously
# compiled notebook's top-level code as a side effect (stray stdout output).
# Importing it eagerly here would leak that output into every CLI
# invocation, including unrelated commands like `governance doctor --json`.

# The core notebook-to-API commands (as opposed to the `governance`
# subcommand tree, which manages its own process exit codes and error
# reporting entirely internally -- every governance branch ends in its own
# sys.exit(exit_code)). Kept as a set so main() can route only these
# commands through _dispatch_core_command's shared error handling.
_CORE_COMMANDS = frozenset({
    "doctor",
    "compile", "inspect", "validate", "export-openapi", "export-sdk",
    "export-curl", "export-postman", "serve", "watch", "deploy", "diff", "upload", "import-notebooks", "import-url",
    "list", "info", "info-batch",
    "search-functions", "search-content", "find-duplicates", "resolve-duplicates", "storage",
    "download", "export-notebooks", "delete", "delete-batch", "rename", "copy",
    "copy-batch", "copy-many", "rename-many", "tags", "prune-versions", "prune-temp-files", "description", "source-url", "deploy-history",
    "clear-deploy-history", "compile-history", "clear-compile-history",
    "remote-compile", "remote-inspect", "remote-build",
    "versions", "remote-files", "remote-diff", "diff-notebooks", "remote-export", "remote-deploy",
    "status", "metrics", "remote-validate", "validate-all", "requirements-preview", "curl-preview",
    "remote-curl", "remote-postman", "app-preview", "readme-preview", "dockerfile-preview", "docker-compose-preview", "env-example-preview", "env-vars-preview",
    "postman-preview", "k8s-preview", "openapi-preview", "verify-webhook",
    "app-metrics", "app-call", "app-tasks", "app-status", "app-auth",
})

# Exception types raised by real, expected failure conditions in the core
# commands -- a missing/unreadable notebook file, an invalid --output
# package name, a malformed .ipynb, a compiled app that doesn't exist yet,
# a corrupt openapi.json, Docker not being installed, `docker build`/
# `docker push` exiting non-zero, or one of them running past
# DEPLOY_SUBPROCESS_TIMEOUT_SECONDS. Before this, none of the core
# commands caught any of these: a plain `notebook-to-api compile
# missing.ipynb` crashed with a raw multi-frame Python traceback (confirmed
# by running it) instead of a one-line, actionable error message -- the
# worst possible first impression for a CLI's most common failure mode.
CLI_USER_FACING_ERRORS = (
    OSError,  # covers FileNotFoundError, PermissionError, etc.
    ValueError,  # covers json.JSONDecodeError and nbformat's NotJSONError
    ModuleNotFoundError,
    RuntimeError,
    NotebookValidationError,
    subprocess.CalledProcessError,
    subprocess.TimeoutExpired,
)

# Same NOTEBOOK_API_DEPLOY_TIMEOUT_SECONDS env var POST /api/deploy already
# reads (see DEPLOY_SUBPROCESS_TIMEOUT_SECONDS in routes/upload.py), so one
# setting controls both surfaces consistently. Before this, `deploy`'s
# docker build/push subprocess.run calls had no timeout at all -- a hung
# build (e.g. a stuck base-image pull) blocked the CLI forever with no way
# to configure a limit, unlike /api/deploy.
DEPLOY_SUBPROCESS_TIMEOUT_SECONDS = int(
    os.getenv("NOTEBOOK_API_DEPLOY_TIMEOUT_SECONDS", "600")
)

# Same NOTEBOOK_API_DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS env var POST
# /api/deploy's own "smoke_test" already reads (see
# DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS in routes/upload.py), so one setting
# controls both surfaces consistently.
DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS = float(
    os.getenv("NOTEBOOK_API_DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS", "30")
)
_DEPLOY_SMOKE_TEST_POLL_INTERVAL_SECONDS = 0.5


def _fallback_openapi_export_path(openapi_path):
    """If `openapi_path` doesn't exist but a sibling OpenAPI export using
    the other extension does (e.g. --openapi generated/openapi.json is
    missing because the only export actually run was `export-openapi
    --format yaml`, which wrote generated/openapi.yaml right next to it),
    return that sibling's path instead of None.

    Before this, `export-sdk --openapi generated/openapi.json` (its own
    documented default) against a notebook only ever exported as yaml
    crashed with a bare FileNotFoundError -- "[Errno 2] No such file or
    directory: 'generated/openapi.json'" -- with nothing pointing at the
    export that actually exists one directory listing away. Falling back
    to it here doesn't make export-sdk succeed (it still only reads JSON
    schemas): generate_python_sdk/generate_typescript_sdk will read this
    returned path and _load_openapi_schema (exporters/sdk_generator.py)
    will still refuse it -- but now via the specific, actionable message
    it already writes for exactly this situation ("This looks like a YAML
    export ... re-export with --format json first"), the same message
    POST /api/export-sdk (routes/upload.py) was already fixed to reach.
    """
    path = Path(openapi_path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        alternate_suffixes = (".yaml", ".yml")
    elif suffix in (".yaml", ".yml"):
        alternate_suffixes = (".json",)
    else:
        return None

    for alt_suffix in alternate_suffixes:

        candidate = path.with_suffix(alt_suffix)

        if candidate.is_file():
            return str(candidate)

    return None


def _run_deploy_docker_command(args, cwd, capture_output=False):
    """Run a `docker ...` subprocess for the `deploy` command.

    Converts a missing Docker CLI into a friendlier RuntimeError, same as
    before. subprocess.TimeoutExpired is left to propagate as-is -- its
    default message (the command and configured timeout) is already
    clear, and it's caught the same way as every other expected failure
    here, via CLI_USER_FACING_ERRORS in main().

    capture_output defaults to False -- the human-readable `deploy` path
    (below) wants `docker build`/`docker push`'s own live progress output
    to reach the user's real terminal, same as always.

    `deploy --json` (below) passes capture_output=True instead: real
    Docker is always verbose on stdout ("Step 1/5 : FROM ...",
    "Successfully built ...", ...), and this subprocess's stdout is
    inherited directly from this CLI process's own OS-level stdout file
    descriptor regardless of what Python code around the call does --
    confirmed exploitable, reproduced directly: the `--json` branch below
    already wraps this call in `contextlib.redirect_stdout(io.StringIO())`
    to keep compile_notebook's/print_compile_summary's/its own progress
    prints out of --json's stdout, and that comment claimed it also
    covered this subprocess -- but redirect_stdout only patches Python's
    own sys.stdout object, which a child process never reads from or
    writes to; it inherits the real fd 1 directly. A bare `subprocess.run`
    inside a redirect_stdout block writes straight through to the real
    terminal, unaffected, confirmed with a two-line reproduction. Every
    fake `docker` stub this file's own tests use writes only to a log
    file, never to stdout, so none of them exposed this: `deploy --json`
    against a *real* Docker build wrote its progress log directly to
    stdout, immediately followed by the JSON blob -- `json.loads(stdout)`
    on the combined output fails outright ("Expecting value: line 1
    column 1"). routes/upload.py's own `_run_docker_command`, the
    dashboard's equivalent of this exact function, already passes
    capture_output=True unconditionally (it always needs the output, to
    report a failed build/push's stderr back to the caller) -- this was
    the one place that operation was still done differently between the
    CLI and the dashboard.
    """
    try:

        return subprocess.run(
            args,
            cwd=str(cwd),
            check=True,
            timeout=DEPLOY_SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=capture_output,
            text=True if capture_output else None,
        )

    except FileNotFoundError as exc:

        raise RuntimeError(
            "Docker CLI not found. Install Docker and ensure `docker` is on PATH to use `deploy`."
        ) from exc


def _run_local_deploy_smoke_test(tag, cwd):
    """`deploy --smoke-test`'s own local counterpart to
    _run_deploy_smoke_test (backend/routes/upload.py) -- actually runs
    the just-built `tag` image in a real, throwaway container and polls
    its own GET /health until it responds (or
    DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS elapses), catching a class of
    failure `deploy`'s own successful `docker build` alone can't: a
    system library the base image is missing, a `pip install` that
    behaves differently inside the container's own environment, or a
    Dockerfile bug that only manifests once the image actually runs.

    Returns {"passed": bool, "status_code": int | None, "detail": str |
    None} -- never raises, the identical "diagnostic, not gating"
    contract _run_deploy_smoke_test/_run_local_compile_smoke_test already
    establish: a failed smoke test does not mean the build (or an
    already-requested push) failed or should be skipped.

    Unlike _run_deploy_smoke_test, this can't rely on `docker`/`httpx`
    already being imported at module scope the way the dashboard process
    can -- imported here instead, the same deferred-import convention
    every other command in this file already uses for a dependency (or,
    for `time`, a subprocess invocation) not every invocation needs.
    """
    import httpx

    try:

        run_result = subprocess.run(
            ["docker", "run", "-d", "--rm", "-p", "127.0.0.1::8000", tag],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=DEPLOY_SUBPROCESS_TIMEOUT_SECONDS,
        )

    except FileNotFoundError:

        return {
            "passed": False,
            "status_code": None,
            "detail": "Docker CLI not found.",
        }

    except subprocess.TimeoutExpired:

        return {
            "passed": False,
            "status_code": None,
            "detail": (
                "`docker run` did not finish within "
                f"{DEPLOY_SUBPROCESS_TIMEOUT_SECONDS} seconds."
            ),
        }

    if run_result.returncode != 0:

        return {
            "passed": False,
            "status_code": None,
            "detail": f"Docker run failed: {run_result.stderr.strip()}",
        }

    container_id = run_result.stdout.strip()

    try:

        port_result = subprocess.run(
            ["docker", "port", container_id, "8000/tcp"],
            capture_output=True,
            text=True,
            timeout=DEPLOY_SUBPROCESS_TIMEOUT_SECONDS,
        )

        if port_result.returncode != 0:

            return {
                "passed": False,
                "status_code": None,
                "detail": (
                    "Could not determine the container's own port: "
                    f"{port_result.stderr.strip()}"
                ),
            }

        host_port = port_result.stdout.strip().splitlines()[0]

        deadline = time.monotonic() + DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS
        last_status_code = None
        last_error = None

        while time.monotonic() < deadline:

            try:

                response = httpx.get(f"http://{host_port}/health", timeout=2)

            except httpx.HTTPError as e:

                last_error = str(e)

            else:

                last_status_code = response.status_code

                if response.status_code == 200:

                    return {
                        "passed": True,
                        "status_code": 200,
                        "detail": None,
                    }

            time.sleep(_DEPLOY_SMOKE_TEST_POLL_INTERVAL_SECONDS)

        logs_result = subprocess.run(
            ["docker", "logs", container_id],
            capture_output=True,
            text=True,
            timeout=DEPLOY_SUBPROCESS_TIMEOUT_SECONDS,
        )
        container_logs = (logs_result.stdout + logs_result.stderr).strip()

        if last_status_code is not None:
            reason = f"GET /health last responded {last_status_code}"
        elif last_error is not None:
            reason = f"GET /health never responded: {last_error}"
        else:
            reason = "GET /health was never reached"

        return {
            "passed": False,
            "status_code": last_status_code,
            "detail": (
                f"{reason} within {DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS}s. "
                f"Container logs:\n{container_logs}"
            ),
        }

    finally:

        subprocess.run(
            ["docker", "stop", container_id],
            capture_output=True,
            text=True,
            timeout=DEPLOY_SUBPROCESS_TIMEOUT_SECONDS,
        )


def _add_function_selection_arguments(parser):
    """Add --only/--exclude to `parser` -- shared by the `compile` and
    `deploy` subparsers below, so their help text and dest names can't
    drift apart from each other.
    """
    parser.add_argument(
        "--only",
        default=None,
        help=(
            "Comma-separated function names to compile as endpoints, "
            "excluding every other function the notebook defines (they "
            "stay callable from the ones that are compiled, just without "
            "an endpoint of their own). Mutually exclusive with --exclude."
        )
    )
    parser.add_argument(
        "--exclude",
        default=None,
        help=(
            "Comma-separated function names to exclude from becoming "
            "endpoints; every other function the notebook defines is "
            "compiled normally. Mutually exclusive with --only."
        )
    )


def _add_debounce_argument(parser):
    """Add --debounce to `parser` -- shared by the `serve` and `watch`
    subparsers below, so their help text and dest names can't drift apart
    from each other, the same way _add_function_selection_arguments
    already shares --only/--exclude between them.
    """
    parser.add_argument(
        "--debounce",
        type=float,
        default=1.0,
        dest="debounce_seconds",
        metavar="SECONDS",
        help=(
            "Seconds to wait after the last recompile before triggering "
            "another one (default: 1.0), passed straight through to "
            "NotebookChangeHandler (backend/serve.py) -- see its own "
            "docstring for why this is configurable. Must be zero or "
            "positive."
        )
    )


def _add_on_change_argument(parser):
    """Add --on-change to `parser` -- shared by the `serve` and `watch`
    subparsers below, the same way _add_debounce_argument already shares
    --debounce between them.
    """
    parser.add_argument(
        "--on-change",
        default=None,
        dest="on_change",
        metavar="COMMAND",
        help=(
            "Shell command to run (via run_on_change_hook, backend/"
            "serve.py) after the initial compile and after every "
            "subsequent successful recompile -- e.g. `--on-change "
            "\"pytest -x\"` to re-run a test suite against the freshly "
            "compiled app on every save, without leaving this command "
            "running in one terminal and re-running that command by hand "
            "in another after each one. Its own stdout/stderr are "
            "printed directly to this terminal; a non-zero exit is "
            "reported, never treated as this command's own failure -- "
            "the live server/watch session keeps running regardless. "
            "Not run at all if a recompile itself fails."
        )
    )


def _add_version_id_argument(parser, endpoint):
    """Add --version-id to `parser` -- shared by `remote-validate`,
    `requirements-preview`, `app-preview`, and `curl-preview` below, so
    their help text and dest names can't drift apart from each other, the
    same way _add_function_selection_arguments already shares
    --only/--exclude and _add_debounce_argument already shares --debounce.

    `endpoint` names the dashboard route this particular parser's own
    "version_id" body field actually goes to (e.g. "/api/validate"), for
    that flag's own help text.
    """
    parser.add_argument(
        "--version-id",
        default=None,
        dest="version_id",
        help=(
            "Run against one of this notebook's own previously "
            "snapshotted versions (as reported by `versions list`) "
            f"instead of its current content, via {endpoint}'s own "
            '"version_id" body field.'
        )
    )


def _add_callback_url_argument(parser):
    """Add --callback-url to `parser` -- shared by `export-curl`,
    `export-postman`, `remote-curl`, `remote-postman`, `curl-preview`, and
    `postman-preview` below, so their help text and dest names can't drift
    apart from each other, the same way _add_version_id_argument already
    shares --version-id across several of these same subparsers.

    Previews a background function's own ?callback_url= webhook option
    (see _deliver_task_webhook, generator/api_generator.py) in the
    generated curl command/Postman request -- before this, none of these
    five commands had any way to demonstrate it at all, even though the
    generated Python/TypeScript SDK clients' own background-task methods
    already accept a matching callback_url/callbackUrl keyword argument
    (see generate_python_sdk/generate_typescript_sdk, backend/exporters/
    sdk_generator.py). generate_curl_commands/generate_postman_collection
    (backend/inspector.py) reject anything other than an http:// or
    https:// URL with the identical ValueError the generated app itself
    raises for a real ?callback_url= at request time -- surfaced here the
    same way every other CLI_USER_FACING_ERRORS-caught ValueError already
    is, a clean "Error: ..." on stderr rather than a traceback.
    """
    parser.add_argument(
        "--callback-url",
        default=None,
        dest="callback_url",
        metavar="URL",
        help=(
            "Preview a background function's own ?callback_url= webhook "
            "delivery option instead of only ever documenting GET "
            "/tasks/{task_id} polling -- must be an http:// or https:// "
            "URL. Has no effect on a synchronous function's own command, "
            "matching the generated app's own identical restriction."
        )
    )


def _parse_comma_separated_names(value):
    """Parse a `--only`/`--exclude` argparse value ("add,subtract", or
    None) into a list of names, or None if nothing was given.

    Whitespace around each name is stripped and empty entries (a
    trailing comma, "a,,b") are dropped, rather than passing an empty
    string through as a "function name" _filter_functions_by_name would
    then correctly, but confusingly, reject as unknown.
    """
    if not value:
        return None

    names = [name.strip() for name in value.split(",") if name.strip()]

    return names or None


def _parse_import_url_headers(header_args):
    """Parse `import-url`'s own repeatable --header "NAME:VALUE" values
    into the dict POST /api/notebooks/import-url's own "headers" body
    field expects.

    Split on the *first* ":" only, so a header value that itself contains
    a colon (an "Authorization: Bearer <token>" value never has one, but
    a URL-shaped value in some other header could) isn't truncated.
    Leading/trailing whitespace around both the name and value is
    stripped -- "--header \"Authorization: Bearer x\"" (a space after the
    colon, the natural way to type one) must produce the value "Bearer x",
    not " Bearer x".
    """
    headers = {}

    for header_arg in header_args:

        name, separator, value = header_arg.partition(":")

        if not separator:
            raise ValueError(
                f"--header value {header_arg!r} must be in NAME:VALUE form"
            )

        headers[name.strip()] = value.strip()

    return headers


def _run_local_compile_smoke_test(package_name, output_dir):
    """`compile --smoke-test`'s own local counterpart to
    _run_compile_smoke_test (backend/routes/upload.py) -- actually
    imports the just-compiled "<package_name>.app" and calls its own GET
    /health in-process, to catch a class of failure nothing else at
    compile time can: every check `compile`/`inspect`/`validate` already
    perform (the reserved-name-collision check, inspect_notebook_data's
    own AST-level parsing) is purely static -- none of them actually
    execute a single line of the generated Python source. A bug in
    generate_fastapi_code itself can still write a syntactically-broken
    app.py that every one of those static checks passes cleanly, only to
    fail the moment anything -- `deploy`'s own `docker build`, or a real
    `uvicorn <package>.app:app` -- actually tries to run it. POST
    /api/compile's own "smoke_test" already closes this same gap for a
    notebook compiled on a running dashboard; this closes it for a local
    `compile` too.

    Unlike _run_compile_smoke_test, which can rely on GENERATED_DIR
    always being a fixed, cwd-relative directory of the one dashboard
    process it runs in, `compile --output` can point anywhere -- so
    `package_name` (output_dir's own basename) is only importable once
    output_dir's own *parent* directory is on sys.path, which this
    temporarily adds (removed again afterward, whether the import
    succeeds or not, so a failed smoke test doesn't leave this process's
    own sys.path permanently altered).

    No module-cache eviction is needed here the way POST /api/compile's
    own long-lived dashboard process needs it (see
    _evict_compiled_app_from_module_cache, backend/routes/upload.py):
    every `compile` invocation is its own fresh Python process with an
    empty sys.modules, so "<package_name>.app" can never already be
    imported from a previous, now-stale compile the way a dashboard's
    own repeated POST /api/compile calls could leave behind.

    Returns {"passed": bool, "status_code": int | None, "detail": str |
    None} -- never raises. A failed smoke test does not mean the compile
    itself failed: every file compile_notebook already wrote is still on
    disk exactly as it was -- this is a diagnostic on top of an
    already-successful compile, not a retroactive verdict on it.
    """
    import importlib

    from fastapi.testclient import TestClient

    parent_dir = str(Path(output_dir).resolve().parent)
    path_already_present = parent_dir in sys.path

    if not path_already_present:
        sys.path.insert(0, parent_dir)

    try:
        module = importlib.import_module(f"{package_name}.app")
        app = module.app

    except Exception as e:

        return {
            "passed": False,
            "status_code": None,
            "detail": f"Compiled app failed to import: {e}",
        }

    finally:

        if not path_already_present:
            sys.path.remove(parent_dir)

    try:

        response = TestClient(app).get("/health")

    except Exception as e:

        return {
            "passed": False,
            "status_code": None,
            "detail": f"Compiled app raised handling GET /health: {e}",
        }

    return {
        "passed": response.status_code == 200,
        "status_code": response.status_code,
        "detail": None if response.status_code == 200 else response.text,
    }


def _parse_notebook_version_pair(value):
    """Parse one `versions restore-batch` positional argument
    ("filename:version_id") into a {"filename", "version_id"} entry
    matching POST /api/notebooks/versions/restore-batch's own "entries"
    shape.

    A plain argparse.ArgumentTypeError (rather than a raw ValueError) so
    argparse itself reports a clean "invalid _parse_notebook_version_pair
    value" usage error for a malformed pair instead of a bare traceback --
    the same reasoning every other core command's own input validation
    already fails fast for. Splits on the *last* ":" so a version_id
    containing no colon of its own (every version_id this dashboard has
    ever generated, per _snapshot_current_notebook_version in
    routes/upload.py) round-trips even if a filename somehow did.
    """
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            f"'{value}' must be in filename:version_id form"
        )

    filename, _, version_id = value.rpartition(":")

    if not filename or not version_id:
        raise argparse.ArgumentTypeError(
            f"'{value}' must be in filename:version_id form"
        )

    return {"filename": filename, "version_id": version_id}


def _parse_notebook_copy_pair(value):
    """Parse one `copy-many` positional argument
    ("filename:new_filename") into a {"filename", "new_filename"} entry
    matching POST /api/notebooks/copy-batch's own "entries" shape -- the
    exact same "filename:value" pair convention
    _parse_notebook_version_pair already established for `versions
    restore-batch`, just paired with a destination filename instead of a
    version_id.
    """
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            f"'{value}' must be in filename:new_filename form"
        )

    filename, _, new_filename = value.rpartition(":")

    if not filename or not new_filename:
        raise argparse.ArgumentTypeError(
            f"'{value}' must be in filename:new_filename form"
        )

    return {"filename": filename, "new_filename": new_filename}


def _parse_version_copy_pair(value):
    """Parse one `versions copy-batch` positional argument
    ("version_id:new_filename") into a {"version_id", "new_filename"}
    entry matching POST /api/notebooks/{filename}/versions/copy-batch's
    own "entries" shape -- the same "value:value" pair convention
    _parse_notebook_copy_pair already established for `copy-many`, just
    paired with a version_id instead of a source filename.
    """
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            f"'{value}' must be in version_id:new_filename form"
        )

    version_id, _, new_filename = value.rpartition(":")

    if not version_id or not new_filename:
        raise argparse.ArgumentTypeError(
            f"'{value}' must be in version_id:new_filename form"
        )

    return {"version_id": version_id, "new_filename": new_filename}


def _matched_notebooks_summary(data, args, shown_count):
    """Format the trailing "N notebook(s) matched" summary line shared by
    `search-functions` and `search-content` below, accounting for
    --limit/--offset the same "how many of the total am I actually
    looking at" way `list`'s own identical --limit/--offset handling
    already does for GET /api/notebooks.
    """
    notebook_count = data.get("notebook_count", shown_count)

    if args.limit is not None and (args.offset + shown_count < notebook_count):
        return (
            f"Showing {shown_count} of {notebook_count} notebook(s) "
            f"(offset {args.offset})."
        )

    return f"{notebook_count} notebook(s) matched."


def _extract_dashboard_error_detail(response):
    """The most useful message extractable from a non-2xx `response` from
    any of this file's own dashboard-facing commands (upload, list,
    download) -- the dashboard's own {"detail": "..."} body (every
    HTTPException any of its routes raises already has this shape) if
    present, else the raw response text as a fallback for a non-JSON
    response (e.g. a reverse proxy's own HTML error page in front of a
    dashboard that isn't actually reachable behind it).
    """
    try:
        body = response.json()
    except ValueError:
        return response.text

    if isinstance(body, dict) and "detail" in body:
        return body["detail"]

    return response.text


def _dashboard_connection_error(exc, dashboard_url):
    """Translate an httpx transport-level failure reaching `dashboard_url`
    into the same clean, actionable RuntimeError every other core
    command's own expected failure modes already get via
    CLI_USER_FACING_ERRORS -- instead of a raw httpx traceback (connection
    refused, DNS failure, a timeout, ...) that gives no hint the actual
    problem is simply that no dashboard is listening there at all.
    """
    return RuntimeError(
        f"Could not reach the dashboard at {dashboard_url}: {exc}. Is it "
        "running? (see `python -m backend.dashboard`)"
    )


def _parse_prometheus_text_metrics(text):
    """Parse GET /api/metrics/prometheus' own Prometheus text exposition
    format (backend/routes/upload.py's dashboard_metrics_prometheus) into
    a flat {metric_name: value} dict, for the `metrics` command's own
    `--json` flag.

    Every real line that endpoint ever emits is either a "# HELP ..."/"#
    TYPE ..." comment line or a bare "metric_name value" data line (no
    labels -- none of this dashboard's own metrics carry any) -- so this
    only ever needs to skip the former and split the latter on its last
    space, the same shape every metric line dashboard_metrics_prometheus
    itself builds with an f-string ending in "{value}\\n".

    Each value is parsed as a float (Prometheus' own exposition format
    has no separate integer type -- every value, including a gauge like
    "notebook_to_api_dashboard_notebooks_total", is written as plain
    decimal text) and narrowed back to an int when it has no fractional
    part, so a caller scripting off e.g. "notebooks_total" gets back the
    same int GET /api/health's own JSON fields already use rather than a
    surprising "3.0".  A line that doesn't parse as "name value" (there
    are none today, but a future metric could in principle add labels)
    is skipped rather than raising, so one unparseable line can't take
    down this command's own `--json` output for every other metric on
    the same scrape.
    """
    metrics = {}

    for line in text.splitlines():

        line = line.strip()

        if not line or line.startswith("#"):
            continue

        name, _, raw_value = line.rpartition(" ")

        if not name:
            continue

        try:
            value = float(raw_value)
        except ValueError:
            continue

        metrics[name] = int(value) if value.is_integer() else value

    return metrics


def _filename_from_content_disposition(response, default):
    """The filename GET /api/download's own Content-Disposition header
    reports (routes/upload.py sets it to f'attachment; filename="{name}.zip"',
    named after GENERATED_DIR's own basename), or `default` if the header
    is missing/unparseable.

    `remote-build`'s own --output default reuses whatever name the
    dashboard itself considers this build to be (e.g. "generated.zip")
    rather than a name this CLI invents independently, so a file saved
    without --output still says what it actually is if ever looked at
    again later, out of context.
    """
    header = response.headers.get("content-disposition", "")

    marker = 'filename="'
    start = header.find(marker)

    if start == -1:
        return default

    start += len(marker)
    end = header.find('"', start)

    if end == -1:
        return default

    return header[start:end] or default


def _default_dashboard_url():
    """The CLI's own default --dashboard-url, for every dashboard-facing
    subcommand that doesn't get an explicit one.

    Reads NOTEBOOK_API_DASHBOARD_URL if set, falling back to the same
    "http://localhost:8001" literal every dashboard-facing subparser
    already defaulted to -- the same "already independently configurable
    via its own NOTEBOOK_API_* environment variable" convention
    dashboard_host()/dashboard_port() (backend/dashboard.py) already
    establish for the dashboard server's own bind address, just for this
    CLI's own client-side counterpart of it.

    Before this, every one of this CLI's ~30 dashboard-facing commands
    (upload, list, download, remote-diff, deploy, ...) defaulted its own
    --dashboard-url to that hardcoded literal, with no way to point a
    whole scripted workflow -- a CI pipeline running several of them
    against a shared staging dashboard, say, or simply a developer whose
    dashboard doesn't run on the default port -- at a different one
    without repeating an explicit --dashboard-url on every single
    invocation, or wrapping every call in a shell alias/function that
    does it by hand.

    An explicit --dashboard-url on any one call still always wins over
    this: argparse's own `default=` is only ever consulted when the flag
    itself is omitted, so this changes nothing for a script that already
    passes --dashboard-url explicitly.
    """
    return os.getenv("NOTEBOOK_API_DASHBOARD_URL", "http://localhost:8001")


def _default_app_api_key():
    """The CLI's own default --api-key, for every subcommand that talks
    to a *deployed* compiled app directly (app-call, every app-tasks
    subcommand) and doesn't get an explicit one.

    Reads NOTEBOOK_API_KEY if set -- the exact same environment variable
    a real deployment's own running app already reads to decide which
    key(s) it accepts (see verify_api_key, api_generator.py) -- falling
    back to DEFAULT_DEV_API_KEY exactly as every one of these subcommands
    already did before this existed, the same "already independently
    configurable via its own NOTEBOOK_API_* environment variable, this
    CLI's own default just never picked it up" gap
    _default_dashboard_url's own docstring already closed once for
    --dashboard-url/$NOTEBOOK_API_DASHBOARD_URL.

    Before this, an operator who'd set NOTEBOOK_API_KEY to a real,
    non-default value to configure the app itself (via `serve`, `docker
    compose up`, or a real deploy) still had to pass a matching, easy-to-
    typo --api-key on every single app-call/app-tasks invocation against
    it -- there was no way for this CLI to infer it from the exact same
    environment variable that already determines what the app expects,
    even though every one of these commands' own --api-key help text
    already told an operator to "pass the same value configured via
    NOTEBOOK_API_KEY" by hand.

    An explicit --api-key on any one call still always wins over this,
    the identical "default= is only ever consulted when the flag itself
    is omitted" guarantee _default_dashboard_url already gives.
    """
    return os.getenv("NOTEBOOK_API_KEY", DEFAULT_DEV_API_KEY)


def _add_dashboard_url_and_timeout_arguments(parser, default_timeout=30.0):
    """Add --dashboard-url and --timeout to `parser` -- shared by every
    dashboard-facing subparser below (upload, list, download), so their
    help text, defaults, and dest names can't drift apart from each
    other.
    """
    parser.add_argument(
        "--dashboard-url",
        default=_default_dashboard_url(),
        dest="dashboard_url",
        help=(
            "Base URL of the running dashboard API (default: "
            "http://localhost:8001, matching dashboard_port()'s own "
            "default in backend/dashboard.py -- or "
            "$NOTEBOOK_API_DASHBOARD_URL if set)."
        )
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=default_timeout,
        help=(
            "Seconds to wait for the dashboard to respond before giving "
            f"up (default: {default_timeout:g})."
        )
    )


def _add_app_host_port_arguments(parser, default_timeout=10.0):
    """Add --host/--port/--api-key/--timeout to `parser` -- shared by
    every subcommand that talks to a *deployed* compiled app directly
    (app-metrics, app-call, app-tasks' own subcommands below), the same
    "one shared helper, no drift" reasoning
    _add_dashboard_url_and_timeout_arguments already gives for its own
    identical role fronting this dashboard instead.
    """
    parser.add_argument(
        "--host",
        default="localhost",
        help=(
            "Host the compiled app is actually reachable at (default: "
            "localhost) -- where `serve`/`docker compose up`/a real "
            "deploy is actually listening, not the dashboard that built "
            "it."
        )
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the compiled app is actually reachable at (default: 8000, matching `serve`'s own default)."
    )
    parser.add_argument(
        "--api-key",
        default=_default_app_api_key(),
        dest="api_key",
        help=(
            "Value sent as the X-API-Key header (default: $NOTEBOOK_API_KEY "
            "if set -- the same variable a real deployment's own app "
            "already reads to decide which key(s) it accepts -- else the "
            "generated app's own default dev key)."
        )
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=default_timeout,
        help=(
            "Seconds to wait for the app to respond before giving up "
            f"(default: {default_timeout:g})."
        )
    )


def _completion_command_tree(parser):
    """Recursively extract `parser`'s own command tree -- every option
    string (--flag) it declares, plus, for each subcommand a nested
    subparsers group of its own defines, that subcommand's own tree in
    turn -- as {"options": [...], "subcommands": {name: subtree}}.

    Walked once per `completion` invocation directly off the real,
    fully-built argparse.ArgumentParser main() constructs (not a hand-
    maintained mirror of it), so a shell completion script generated
    here can never drift from what this CLI's own subcommands actually
    are -- the identical "can't drift from the real thing" guarantee
    GENERATED_APP_ENV_VARS (api_generator.py) already gives GET
    /api/env-vars-preview.
    """
    options = sorted(
        opt
        for action in parser._actions
        for opt in action.option_strings
    )
    subcommands = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                subcommands[name] = _completion_command_tree(subparser)
    return {"options": options, "subcommands": subcommands}


def _flatten_completion_tree(tree, prefix=()):
    """Flatten a _completion_command_tree result into a flat
    {path_tuple: sorted_completion_words} mapping, one entry per node
    (including the root, prefix=()) -- so a shell completion script can
    look up "what's legal after these already-typed subcommand words" by
    a single dict/case lookup on the path, without re-walking the tree
    at completion time (which would mean embedding the tree-walking
    logic itself, not just its result, into every generated shell
    script).
    """
    entries = {
        prefix: sorted(tree["options"] + list(tree["subcommands"].keys()))
    }
    for name, subtree in tree["subcommands"].items():
        entries.update(_flatten_completion_tree(subtree, prefix + (name,)))
    return entries


def _generate_bash_completion(parser):
    """A bash completion script for `parser`, offering this CLI's own
    subcommands and flags at every depth (`app-tasks <TAB>` -> `list get
    delete retry redeliver-webhook ...`, `governance audits <TAB>` ->
    `... logs prune export verify ...`, and so on) -- this CLI has no
    shell completion of any kind before this, across ~70 top-level
    subcommands and several more levels of nesting under versions/tags/
    app-tasks/governance/audits/logs alone, so using anything past the
    handful of commands memorized by heart means reaching for --help
    over and over. Self-contained (no dependency on the separate
    `bash-completion` package's own `_init_completion`) -- reads
    COMP_WORDS/COMP_CWORD directly, the same two bash-builtin completion
    variables `_init_completion` itself would ultimately read anyway.
    """
    entries = _flatten_completion_tree(_completion_command_tree(parser))

    lines = [
        "# Bash completion for notebook-to-api.",
        "# Generated by `notebook-to-api completion bash` -- regenerate any",
        "# time this CLI's own commands change, rather than hand-editing.",
        "#",
        "# Install (pick one):",
        "#   notebook-to-api completion bash | sudo tee /etc/bash_completion.d/notebook-to-api",
        "#   notebook-to-api completion bash >> ~/.bashrc",
        "_notebook_to_api_complete() {",
        '    local cur="${COMP_WORDS[COMP_CWORD]}"',
        '    local path="" i word',
        "    for ((i = 1; i < COMP_CWORD; i++)); do",
        '        word="${COMP_WORDS[i]}"',
        "        case \"$word\" in",
        "            -*) continue ;;",
        "        esac",
        '        path="${path:+$path }$word"',
        "    done",
        "    local opts",
        '    case "$path" in',
    ]
    for path in sorted(entries):
        lines.append(f'        "{" ".join(path)}") opts="{" ".join(entries[path])}" ;;')
    lines.append('        *) opts="" ;;')
    lines.append("    esac")
    lines.append('    COMPREPLY=( $(compgen -W "$opts" -- "$cur") )')
    lines.append("}")
    lines.append("complete -F _notebook_to_api_complete notebook-to-api")
    return "\n".join(lines) + "\n"


def _generate_zsh_completion(parser):
    """Mirrors _generate_bash_completion for zsh: the identical "path of
    already-typed subcommand words -> legal next words" case statement,
    driven by zsh's own $words/$CURRENT completion-system variables
    (1-indexed, with $words[1] always the command name itself) instead
    of bash's $COMP_WORDS/$COMP_CWORD, and compadd instead of
    $COMPREPLY.
    """
    entries = _flatten_completion_tree(_completion_command_tree(parser))

    lines = [
        "#compdef notebook-to-api",
        "# Zsh completion for notebook-to-api.",
        "# Generated by `notebook-to-api completion zsh` -- regenerate any",
        "# time this CLI's own commands change, rather than hand-editing.",
        "#",
        "# Install: notebook-to-api completion zsh > \"${fpath[1]}/_notebook_to_api\"",
        "# (then start a new shell, or `compinit` again).",
        "_notebook_to_api() {",
        '    local path="" i word',
        "    for ((i = 2; i < CURRENT; i++)); do",
        '        word="${words[i]}"',
        "        case \"$word\" in",
        "            -*) continue ;;",
        "        esac",
        '        path="${path:+$path }$word"',
        "    done",
        "    local -a opts",
        '    case "$path" in',
    ]
    for path in sorted(entries):
        words = " ".join(entries[path])
        lines.append(f'        "{" ".join(path)}") opts=({words}) ;;')
    lines.append("        *) opts=() ;;")
    lines.append("    esac")
    lines.append("    compadd -a opts")
    lines.append("}")
    lines.append("")
    lines.append('_notebook_to_api "$@"')
    return "\n".join(lines) + "\n"


def _generate_fish_completion(parser):
    """Mirrors _generate_bash_completion for fish: fish has no
    $COMP_WORDS/$COMP_CWORD equivalent, so the already-typed path is
    instead computed by a small helper function
    (__notebook_to_api_path, via `commandline -opc`) that every one of
    this command's own `complete -n ...` condition lines below calls to
    decide whether it applies to what's currently typed -- fish's own
    documented pattern for subcommand-aware completion (the same one
    `__fish_seen_subcommand_from`-based completions for git/docker/etc.
    already use), rather than fish-specific `-n
    '__fish_seen_subcommand_from ...'` conditions, which only ever test
    "was this word typed *somewhere*", not "is this the exact path so
    far" -- insufficient once two different subcommands can share a
    child name (e.g. both `versions` and `app-tasks` have their own
    `list`).
    """
    entries = _flatten_completion_tree(_completion_command_tree(parser))

    lines = [
        "# Fish completion for notebook-to-api.",
        "# Generated by `notebook-to-api completion fish` -- regenerate any",
        "# time this CLI's own commands change, rather than hand-editing.",
        "#",
        "# Install: notebook-to-api completion fish > ~/.config/fish/completions/notebook-to-api.fish",
        "function __notebook_to_api_path",
        "    set -l cmd (commandline -opc)",
        "    set -l path",
        "    for word in $cmd[2..-1]",
        "        switch $word",
        "            case '-*'",
        "                continue",
        "        end",
        "        set path $path $word",
        "    end",
        '    echo "$path"',
        "end",
        "",
        "complete -c notebook-to-api -f",
    ]
    for path in sorted(entries):
        # The condition itself must be double-quoted (fish permits a
        # single-quoted literal nested inside a double-quoted one, the
        # same as most POSIX shells) since every subcommand name is a
        # plain hyphenated identifier with no quote/space of its own to
        # escape -- the -a value below is the one that actually needs
        # single quotes, so fish treats its whole space-separated word
        # list as one argument rather than splitting it into several
        # positional arguments to `complete` itself.
        condition = f"test (__notebook_to_api_path) = '{' '.join(path)}'"
        words = " ".join(entries[path])
        lines.append(
            f'complete -c notebook-to-api -n "{condition}" -a \'{words}\''
        )
    return "\n".join(lines) + "\n"


# Python packages this tool needs for specific commands but never
# imports at *this file's own* module scope, so `doctor` (below) can
# actually check for them and still get a chance to report a missing one
# before that command runs. nbformat and watchdog, by contrast, are both
# already imported unconditionally at this file's own top -- "from
# nbformat import ValidationError" above, and `from backend.serve import
# serve_notebook, watch_notebook` (backend/serve.py itself does "from
# watchdog.observers import Observer" at its own top) -- so a machine
# missing either already fails with a plain ModuleNotFoundError the
# instant `notebook-to-api` (this `doctor` command included) is invoked
# at all; checking for them here would be dead code that could never
# actually run. Each entry is (the name `importlib.util.find_spec` looks
# up, the name `pip install` / requirements.txt actually uses -- they
# differ for python-multipart, whose importable module is "multipart",
# and the commands that would actually break without it.
_OPTIONAL_RUNTIME_PACKAGES = [
    (
        "fastapi", "fastapi",
        "`serve`/`watch` (their own uvicorn subprocess imports the "
        "generated app, which imports fastapi) and the dashboard "
        "(`python -m backend.dashboard`)",
    ),
    (
        "uvicorn", "uvicorn",
        "`serve`/`watch` (both shell out to `python -m uvicorn` to "
        "actually run the compiled app) and the dashboard",
    ),
    (
        "multipart", "python-multipart",
        "the dashboard's own multipart file upload (`upload`'s server "
        "side, POST /api/upload)",
    ),
    (
        "httpx", "httpx",
        "every command that talks to a dashboard or a deployed app "
        "directly (`upload`, `list`, `download`, every `remote-*` "
        "command, `app-call`, and every `app-status`/`app-metrics`/"
        "`app-auth`/`app-tasks` subcommand)",
    ),
]


def _dispatch_core_command(args):
    """Run one of the core notebook-to-API commands.

    Split out from main() so every one of its expected failure modes can be
    caught in a single place (see CLI_USER_FACING_ERRORS in main()) instead
    of needing its own try/except at each of the six call sites.
    """
    if args.command == "doctor":

        checks = []

        checks.append({
            "name": "python_version",
            "status": "info",
            "detail": f"Python {compiling_python_version()}",
        })

        docker_path = shutil.which("docker")

        if docker_path is None:

            checks.append({
                "name": "docker_cli",
                "status": "warn",
                "detail": (
                    "Docker CLI not found on PATH -- `deploy` (which "
                    "shells out to a local `docker build`/`docker push`) "
                    "will not work until Docker is installed. Every "
                    "other command here works fine without it."
                ),
            })

        else:

            checks.append({
                "name": "docker_cli",
                "status": "ok",
                "detail": f"Docker CLI found at {docker_path}",
            })

            # `docker info` (rather than a lighter `docker --version`,
            # which only confirms the CLI itself is installed, not that
            # it can actually talk to anything) is what `deploy`'s own
            # `docker build` would need to succeed -- a CLI present but
            # unable to reach its daemon (not started, permission denied
            # on its socket, ...) is exactly as unusable for `deploy` as
            # the CLI being missing outright, just discovered later and
            # less clearly (a raw `docker build` error) without this.
            try:

                subprocess.run(
                    ["docker", "info"],
                    capture_output=True, timeout=10, check=True,
                )

            except subprocess.CalledProcessError:

                checks.append({
                    "name": "docker_daemon",
                    "status": "warn",
                    "detail": (
                        "Docker CLI found, but the Docker daemon isn't "
                        "reachable (`docker info` failed) -- `deploy` "
                        "will fail until it's running."
                    ),
                })

            except subprocess.TimeoutExpired:

                checks.append({
                    "name": "docker_daemon",
                    "status": "warn",
                    "detail": (
                        "`docker info` timed out -- the Docker daemon "
                        "may not be running."
                    ),
                })

            else:

                checks.append({
                    "name": "docker_daemon",
                    "status": "ok",
                    "detail": "Docker daemon is reachable.",
                })

        # find_spec (not an actual import) is deliberate: importing
        # fastapi/uvicorn/httpx here just to check they exist would pull
        # each one fully into memory for every single `doctor` run, the
        # exact cost the lazy "import httpx" convention inside individual
        # command handlers above already exists to avoid paying for every
        # `notebook-to-api` invocation.
        for import_name, requirement_name, needed_by in _OPTIONAL_RUNTIME_PACKAGES:

            if importlib.util.find_spec(import_name) is None:

                checks.append({
                    "name": f"package_{requirement_name.replace('-', '_')}",
                    "status": "warn",
                    "detail": (
                        f"Python package '{requirement_name}' is not "
                        f"installed (pip install {requirement_name}) -- "
                        f"needed by {needed_by}. Every other command "
                        "here works fine without it."
                    ),
                })

            else:

                checks.append({
                    "name": f"package_{requirement_name.replace('-', '_')}",
                    "status": "ok",
                    "detail": f"Python package '{requirement_name}' is installed.",
                })

        # The nearest already-existing ancestor of --output (--output
        # itself, if it's already there) is what actually needs to be
        # writable: `compile`/`serve`/`deploy` all create --output via
        # Path.mkdir(parents=True, exist_ok=True) on demand, so a
        # not-yet-existing --output is only ever a real problem if
        # nothing above it in the path can be written to either.
        output_dir = Path(args.output).resolve()
        existing_ancestor = next(
            (
                candidate for candidate in [output_dir, *output_dir.parents]
                if candidate.exists()
            ),
            Path("/"),
        )

        if os.access(existing_ancestor, os.W_OK):

            checks.append({
                "name": "output_directory_writable",
                "status": "ok",
                "detail": f"{existing_ancestor} is writable.",
            })

        else:

            checks.append({
                "name": "output_directory_writable",
                "status": "fail",
                "detail": (
                    f"{existing_ancestor} is not writable -- "
                    "`compile`/`serve`/`deploy` will fail to write there."
                ),
            })

        has_failure = any(check["status"] == "fail" for check in checks)

        if args.json_output:
            print(json.dumps({"checks": checks, "ok": not has_failure}, indent=2))
        else:

            status_symbols = {"ok": "✓", "warn": "⚠", "fail": "✗", "info": "•"}

            for check in checks:
                print(f"{status_symbols[check['status']]} {check['detail']}")

            print(
                "\nAll checks passed." if not has_failure
                else "\nSome checks failed -- see above."
            )

        if has_failure:
            sys.exit(1)

    elif args.command == "compile":
        from backend.compiler import package_name_for_output_dir

        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)
        if args.json_output:
            # compile_notebook (backend/compiler.py) unconditionally prints
            # its own progress lines ("Starting compilation for: ...",
            # "Runtime module generated.", ...) -- meant for the
            # human-readable path below, but mixed into --json's stdout
            # they'd break any script trying to json.loads() it. inspect
            # --json has no equivalent problem since inspect_notebook_data
            # prints nothing on its own; compile actually writes output,
            # so its own writer functions' prints need suppressing here to
            # give --json the same "stdout is valid JSON, full stop"
            # guarantee.
            with contextlib.redirect_stdout(io.StringIO()):
                compile_notebook(
                    notebook_path=args.notebook, output_dir=str(output_dir),
                    only=only, exclude=exclude,
                )
            data = inspect_notebook_data(notebook_path=args.notebook, output_dir=str(output_dir))
            # inspect_notebook_data re-parses the notebook fresh, with no
            # idea --only/--exclude just restricted which functions the
            # compile above actually turned into endpoints -- without
            # this, `compile --json --only add` would still list every
            # *other* function the notebook defines under "functions"/
            # "endpoints", claiming endpoints exist for functions the
            # compiled app doesn't actually have.
            if only or exclude:
                data["functions"] = _filter_functions_by_name(data["functions"], only, exclude)
                kept_names = {func["name"] for func in data["functions"]}
                data["endpoints"] = [
                    endpoint for endpoint in data["endpoints"]
                    if endpoint["path"].lstrip("/") in kept_names
                ]

            smoke_test = None

            if args.smoke_test:
                smoke_test = _run_local_compile_smoke_test(
                    package_name_for_output_dir(str(output_dir)), str(output_dir),
                )
                data["smoke_test"] = smoke_test

            print(json.dumps(data, indent=2))
        else:
            compile_notebook(
                notebook_path=args.notebook, output_dir=str(output_dir),
                only=only, exclude=exclude,
            )
            print("\nCompilation finished. FastAPI app is ready in", output_dir)
            print_compile_summary(args.notebook, output_dir, only=only, exclude=exclude)

            smoke_test = None

            if args.smoke_test:

                smoke_test = _run_local_compile_smoke_test(
                    package_name_for_output_dir(str(output_dir)), str(output_dir),
                )

                if smoke_test["passed"]:
                    print("\nSmoke test: passed (GET /health responded 200)")
                else:
                    print(f"\nSmoke test: FAILED -- {smoke_test['detail']}")

        if smoke_test is not None and not smoke_test["passed"]:
            sys.exit(1)
    elif args.command == "inspect":
        # Deliberately does NOT create output_dir the way "compile" above
        # does -- `inspect` is documented as a read-only "preview what
        # compiling this notebook will do" step (see its own --help), but
        # this branch used to unconditionally `mkdir(parents=True,
        # exist_ok=True)` before ever reading anything. Confirmed
        # exploitable: `inspect nb.ipynb --output some/nested/path`
        # against a notebook that had never been compiled created the
        # entire "some/nested/path" directory tree on disk, purely as a
        # side effect of what a user reasonably expects to be a
        # side-effect-free command -- and, unlike "compile", `inspect`
        # never writes anything else there either, so nothing depended on
        # that directory existing. inspect_notebook/inspect_notebook_data's
        # own "Generated Files" listing (_list_generated_files, backend/
        # inspector.py) already handles a missing output_dir gracefully
        # (its own `if generated_path.is_dir()` check), the same way GET
        # /api/generated (routes/upload.py) already relies on for a
        # notebook that's never been compiled -- POST /api/inspect
        # (routes/upload.py) has no equivalent mkdir either, so this also
        # brings the CLI in line with the dashboard's own read-only
        # /api/inspect endpoint instead of the two disagreeing about
        # whether inspecting a notebook touches the filesystem.
        output_dir = Path(args.output)
        if args.json_output:
            data = inspect_notebook_data(notebook_path=args.notebook, output_dir=str(output_dir))
            print(json.dumps(data, indent=2))
        else:
            inspect_notebook(notebook_path=args.notebook, output_dir=str(output_dir))
    elif args.command == "validate":
        # Reuses inspect_notebook_data's own reserved_name_conflicts/
        # skipped_functions checks (backend/inspector.py) -- the tool
        # already computes exactly what would go wrong at compile time,
        # `inspect` just never turned that into a pass/fail verdict a CI
        # step could act on. Unlike "compile" or "inspect --output", this
        # never touches the filesystem: no output_dir is created or read,
        # only the notebook itself.
        data = inspect_notebook_data(notebook_path=args.notebook)

        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)

        reserved_name_conflicts = data["reserved_name_conflicts"]
        skipped_functions = data["skipped_functions"]
        duplicate_functions = data["duplicate_functions"]

        # Narrows "reserved_name_conflicts" to just the names --only/
        # --exclude would still actually let through to compile, the
        # same way compile_notebook_to_api itself already applies
        # only/exclude *before* generate_fastapi_code's own reserved-name
        # check (see _filter_functions_by_name, backend/compiler.py) --
        # without this, `validate --exclude health_check` on a notebook
        # whose only conflict is "health_check" still reported "fail",
        # even though the compile it's meant to predict would succeed
        # cleanly. "skipped_functions" is deliberately left unfiltered,
        # the same choice `compile --json`'s own identical field already
        # makes just above: a skipped function was never a candidate to
        # become an endpoint in the first place, whether or not
        # only/exclude names it. Raises the identical ValueError
        # _filter_functions_by_name itself raises for both-given or an
        # unrecognized function name, caught by this function's own
        # caller (see CLI_USER_FACING_ERRORS in main()) as a clean
        # one-line error.
        if only or exclude:
            kept_names = {
                func["name"]
                for func in _filter_functions_by_name(data["functions"], only, exclude)
            }
            reserved_name_conflicts = [
                name for name in reserved_name_conflicts if name in kept_names
            ]

        has_blocking_issues = bool(reserved_name_conflicts) or (
            args.strict and (bool(skipped_functions) or bool(duplicate_functions))
        )
        has_warnings = (
            bool(skipped_functions) or bool(duplicate_functions)
        ) and not has_blocking_issues

        if has_blocking_issues:
            status = "fail"
        elif has_warnings:
            status = "warn"
        else:
            status = "pass"

        if args.json_output:
            print(json.dumps(
                {
                    "status": status,
                    "notebook": args.notebook,
                    "reserved_name_conflicts": reserved_name_conflicts,
                    "skipped_functions": skipped_functions,
                    "duplicate_functions": duplicate_functions,
                },
                indent=2,
            ))
        else:
            print(f"Validating {args.notebook}")

            if reserved_name_conflicts:
                print("\n✗ Reserved name conflicts (compilation will fail):")
                for name in reserved_name_conflicts:
                    print(f"  - {name}")

            if duplicate_functions:
                marker = "✗" if args.strict else "⚠"
                print(f"\n{marker} Duplicate functions (redefined; only the last definition is compiled):")
                for name in duplicate_functions:
                    print(f"  - {name}")

            if skipped_functions:
                marker = "✗" if args.strict else "⚠"
                print(f"\n{marker} Skipped functions (no endpoint will be generated):")
                for skipped in skipped_functions:
                    print(f"  - {skipped['name']}: {skipped['reason']}")

            if status == "pass":
                print("\n✓ No issues found.")
            elif status == "warn":
                print("\nWarnings found, but this notebook would still compile cleanly.")
            else:
                print("\nValidation failed.")

        if status == "fail":
            sys.exit(2)
        elif status == "warn":
            sys.exit(1)
    elif args.command == "export-openapi":
        from backend.exporters.openapi_exporter import export_openapi_schema
        from backend.compiler import package_name_for_output_dir
        # Defaults next to the app --app-dir actually points at, not a
        # literal "generated/..." regardless of --app-dir. Before this, an
        # `export-openapi --app-dir built` (no explicit --output) with the
        # app compiled anywhere other than the default "generated" wrote
        # the schema to "generated/openapi.json" -- a directory unrelated
        # to, and possibly not even containing, the app it was just
        # exported from.
        default_output = (
            os.path.join(args.app_dir, "openapi.yaml") if args.format == "yaml"
            else os.path.join(args.app_dir, "openapi.json")
        )
        output = args.output or default_output
        package_name = package_name_for_output_dir(args.app_dir)

        if args.json_output:
            # export_openapi_schema unconditionally prints its own
            # "OpenAPI schema written to ..." progress line -- meant for
            # the human-readable path below, but mixed into --json's
            # stdout it would break a script trying to json.loads() it.
            # Same suppression `compile --json`/`deploy --json` already
            # apply to their own writer functions' prints, for the same
            # reason.
            with contextlib.redirect_stdout(io.StringIO()):
                export_openapi_schema(output, package_name, format=args.format)

            with open(output, "r", encoding="utf-8") as f:
                content = f.read()

            # Same {"status", "format", "path", "schema"/"content"} shape
            # POST /api/export-openapi (routes/upload.py) already returns
            # for the same operation, so a script driving either surface
            # can parse both the same way.
            response = {"status": "success", "format": args.format, "path": output}

            if args.format == "json":
                response["schema"] = json.loads(content)
            else:
                response["content"] = content

            print(json.dumps(response, indent=2))
        else:
            export_openapi_schema(output, package_name, format=args.format)
    elif args.command == "export-sdk":
        # --app-dir mirrors export-openapi's own --app-dir-derived default
        # (see its own comment above): before this, --openapi's default
        # was a flat "generated/openapi.json" literal with no --app-dir
        # concept at all, so `export-sdk` after `compile nb.ipynb --output
        # built` + `export-openapi --app-dir built` (which correctly wrote
        # built/openapi.json) either crashed looking for a nonexistent
        # generated/openapi.json, or -- worse, if an unrelated notebook
        # had ever been compiled into the default "generated" dir and
        # exported there too -- silently generated an SDK client for that
        # stale, unrelated schema instead, with no error or warning at
        # all. Confirmed reproduced: compiling two different notebooks
        # into "built" and "generated" respectively, exporting both
        # schemas, then running `export-sdk` with no args produced a
        # client exposing the "generated" notebook's endpoints, not the
        # one actually just built and exported via --app-dir built. An
        # explicit --openapi still always wins, exactly as before --
        # --app-dir only changes the default when --openapi isn't given.
        openapi_path = args.openapi or os.path.join(args.app_dir, "openapi.json")

        # Same fix as export-openapi just above, for the same reason:
        # defaults next to --openapi's own directory, not a literal
        # "generated/sdk/..." regardless of where --openapi actually
        # points.
        openapi_dir = os.path.dirname(openapi_path)

        if not os.path.isfile(openapi_path):
            fallback_path = _fallback_openapi_export_path(openapi_path)
            if fallback_path:
                openapi_path = fallback_path

        if args.language == "typescript":
            from backend.exporters.sdk_generator import generate_typescript_sdk as generate_sdk
            output = args.output or os.path.join(openapi_dir, "sdk", "typescript_client.ts")
        else:
            from backend.exporters.sdk_generator import generate_python_sdk as generate_sdk
            output = args.output or os.path.join(openapi_dir, "sdk", "python_client.py")

        if args.json_output:
            # Same reasoning as export-openapi --json above: generate_sdk
            # unconditionally prints its own "<language> SDK generated at
            # ..." progress line, which must not leak into --json's
            # stdout.
            with contextlib.redirect_stdout(io.StringIO()):
                generate_sdk(openapi_path, output)

            with open(output, "r", encoding="utf-8") as f:
                code = f.read()

            # Same {"status", "language", "path", "code"} shape POST
            # /api/export-sdk (routes/upload.py) already returns for the
            # same operation.
            print(json.dumps(
                {
                    "status": "success",
                    "language": args.language,
                    "path": output,
                    "code": code,
                },
                indent=2,
            ))
        else:
            generate_sdk(openapi_path, output)
    elif args.command == "verify-webhook":

        if args.body_file == "-":
            body = sys.stdin.buffer.read()
        else:
            with open(args.body_file, "rb") as f:
                body = f.read()

        secret = args.secret or os.getenv("NOTEBOOK_API_WEBHOOK_SECRET")

        if not secret:
            raise RuntimeError(
                "No secret given -- pass --secret, or set "
                "$NOTEBOOK_API_WEBHOOK_SECRET."
            )

        # Byte for byte the same verification generate_python_sdk's own
        # module-level verify_webhook_signature already gives an SDK-
        # based receiver (see its own docstring, exporters/sdk_generator.py)
        # -- returns False rather than raising for a missing, empty, or
        # malformed header, the same "this request can't be trusted"
        # verdict as a present-but-wrong signature.
        valid = False
        if args.signature and "=" in args.signature:
            algorithm, _, signature = args.signature.partition("=")
            if algorithm == "sha256":
                expected_signature = hmac.new(
                    secret.encode("utf-8"), body, "sha256"
                ).hexdigest()
                valid = hmac.compare_digest(expected_signature, signature)

        if args.json_output:
            print(json.dumps({"valid": valid}, indent=2))
        else:
            print("Signature valid" if valid else "Signature INVALID")

        if not valid:
            sys.exit(1)
    elif args.command == "export-curl":
        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)
        commands = generate_curl_commands(
            args.notebook, host=args.host, port=args.port, api_key=args.api_key,
            only=only, exclude=exclude, callback_url=args.callback_url,
        )

        script_content = (
            "#!/bin/sh\n"
            f"# Generated by notebook-to-api export-curl from {args.notebook}.\n"
            f'# Uses the API key "{args.api_key}" via the X-API-Key header --\n'
            "# pass --api-key here to match whatever NOTEBOOK_API_KEY is set\n"
            "# to on the server, if it's not the default.\n\n"
            + "\n\n".join(commands)
            + "\n"
        )

        output = args.output or "requests.sh"

        with open(output, "w", encoding="utf-8") as f:
            f.write(script_content)

        # Best-effort: makes the script directly runnable (`./requests.sh`)
        # on a POSIX shell without an extra `chmod +x` step -- a no-op on
        # a filesystem/platform where executable bits don't apply.
        try:
            os.chmod(output, os.stat(output).st_mode | 0o111)
        except OSError:
            pass

        if args.json_output:
            print(json.dumps(
                {"status": "success", "path": output, "commands": commands},
                indent=2,
            ))
        else:
            print(f"\ncURL script written to: {output} ({len(commands)} request(s))")
    elif args.command == "export-postman":
        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)
        collection = generate_postman_collection(
            args.notebook, host=args.host, port=args.port, api_key=args.api_key,
            only=only, exclude=exclude, collection_name=args.collection_name,
            callback_url=args.callback_url,
        )

        output = args.output or "postman_collection.json"

        with open(output, "w", encoding="utf-8") as f:
            json.dump(collection, f, indent=2)
            f.write("\n")

        if args.json_output:
            print(json.dumps(
                {"status": "success", "path": output, "collection": collection},
                indent=2,
            ))
        else:
            print(
                f"\nPostman collection written to: {output} "
                f"({len(collection['item'])} request(s))"
            )
    elif args.command == "serve":
        if args.debounce_seconds < 0:
            raise ValueError("--debounce must be zero or positive")
        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)
        serve_notebook(
            args.notebook, args.output, args.port, args.host,
            only=only, exclude=exclude, debounce_seconds=args.debounce_seconds,
            on_change=args.on_change,
        )
    elif args.command == "watch":
        if args.debounce_seconds < 0:
            raise ValueError("--debounce must be zero or positive")
        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)
        watch_notebook(
            args.notebook, args.output, only=only, exclude=exclude,
            debounce_seconds=args.debounce_seconds, on_change=args.on_change,
        )
    elif args.command == "deploy":
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)
        tag = args.tag or f"{output_dir.name.lower()}:latest"
        # `docker build`'s own default target platform is whatever the
        # local Docker daemon's host architecture is -- correct for a
        # plain `docker run` on that same machine, but not for the common
        # case of building on one architecture (e.g. Apple Silicon) for a
        # deploy target that runs another (almost every cloud PaaS is
        # linux/amd64). Without a way to override it, that mismatch
        # either silently produces an image that won't run on the target
        # at all, or requires falling back to the CLI-less `docker build
        # --platform ...` by hand, bypassing this tool's own compile step
        # entirely.
        build_args = ["docker", "build", "-t", tag]
        if args.platform:
            build_args += ["--platform", args.platform]
        # Same reasoning as --platform above: without a way to force
        # `docker build --no-cache`, an operator debugging a suspected
        # stale cached layer (requirements.txt changed but a pinned
        # version's wheel was silently re-published, a floating base
        # image tag moved without a local re-pull, ...) had no way to
        # rule it out through this command at all -- only by dropping to
        # a shell and running `docker build --no-cache` by hand in
        # --output, bypassing this tool's own compile step.
        if args.no_cache:
            build_args.append("--no-cache")
        build_args.append(".")
        if args.json_output:
            # Suppresses every progress print along this path
            # (compile_notebook's own prints, print_compile_summary's, and
            # this command's own "Building Docker image..."/"built
            # successfully"/"Pushing..." lines below) the same way
            # `compile --json` already suppresses compile_notebook's --
            # mixing any of that free text into --json's stdout would
            # break a script trying to json.loads() it. POST /api/deploy
            # (routes/upload.py) already returns exactly this
            # {"status", "tag", "pushed"} shape for the same operation;
            # matched here rather than inventing a different one so a
            # script driving either surface can parse both the same way.
            #
            # redirect_stdout above only covers this process's own
            # print() calls, though -- it has no effect on a subprocess's
            # inherited stdout file descriptor (see
            # _run_deploy_docker_command's own docstring for the
            # confirmed-reproduced failure this caused). capture_output=True
            # here is what actually keeps `docker build`/`docker push`'s
            # own progress output off of --json's stdout, the same way
            # routes/upload.py's `_run_docker_command` already captures it
            # unconditionally for the identical operation.
            smoke_test_result = None

            with contextlib.redirect_stdout(io.StringIO()):
                compile_notebook(
                    notebook_path=args.notebook, output_dir=str(output_dir),
                    only=only, exclude=exclude,
                )
                dockerfile_path = output_dir / "Dockerfile"
                if not dockerfile_path.is_file():
                    raise FileNotFoundError(f"Dockerfile not found at {dockerfile_path}. Ensure the compiler generated it.")

                if args.dry_run:
                    # Compiling (above) already validated the notebook and
                    # produced a Dockerfile -- everything --dry-run exists
                    # to check -- without ever invoking `docker build`/
                    # `docker push` at all. "pushed" reports what a real
                    # run *would* do with --push, not that anything
                    # actually happened. --smoke-test is ignored here too,
                    # the same reasoning: there is no built image yet to
                    # actually run.
                    pushed = args.push
                else:
                    _run_deploy_docker_command(build_args, output_dir, capture_output=True)
                    # See the non-JSON branch below (and POST /api/deploy,
                    # routes/upload.py) for why this regenerates
                    # --output/kubernetes.yaml with the tag actually just
                    # built, rather than leaving it at the default
                    # "{package_name}:latest" a plain `compile` bakes in.
                    from backend.compiler import package_name_for_output_dir
                    from backend.generator.api_generator import (
                        GENERATED_APP_ENV_VARS,
                    )
                    from backend.generator.kubernetes_generator import (
                        generate_kubernetes_manifest,
                    )
                    generate_kubernetes_manifest(
                        str(output_dir / "kubernetes.yaml"),
                        package_name_for_output_dir(str(output_dir)),
                        GENERATED_APP_ENV_VARS,
                        image=tag,
                    )
                    if args.smoke_test:
                        smoke_test_result = _run_local_deploy_smoke_test(tag, output_dir)
                    pushed = False
                    if args.push:
                        _run_deploy_docker_command(
                            ["docker", "push", tag], output_dir, capture_output=True
                        )
                        pushed = True

            result = {"status": "success", "tag": tag, "pushed": pushed}
            if args.dry_run:
                result["dry_run"] = True
            else:
                result["kubernetes_manifest_image"] = tag
            if smoke_test_result is not None:
                result["smoke_test"] = smoke_test_result
            print(json.dumps(result, indent=2))

            if smoke_test_result is not None and not smoke_test_result["passed"]:
                sys.exit(1)
        else:
            compile_notebook(
                notebook_path=args.notebook, output_dir=str(output_dir),
                only=only, exclude=exclude,
            )
            print_compile_summary(args.notebook, output_dir, only=only, exclude=exclude)
            # Build Docker image
            dockerfile_path = output_dir / "Dockerfile"
            if not dockerfile_path.is_file():
                raise FileNotFoundError(f"Dockerfile not found at {dockerfile_path}. Ensure the compiler generated it.")

            if args.dry_run:
                print(f"Would build Docker image '{tag}' from {output_dir}.")
                if args.push:
                    print(f"Would push Docker image '{tag}'.")
            else:
                print(f"Building Docker image '{tag}' from {output_dir} …")
                _run_deploy_docker_command(build_args, output_dir)
                print(f"Docker image '{tag}' built successfully.")

                # kubernetes.yaml (written above by compile_notebook, with
                # the default "{package_name}:latest" image) would
                # otherwise silently disagree with the tag actually just
                # built the moment --tag overrides that default -- the
                # same drift POST /api/deploy's own regeneration
                # (routes/upload.py) closes for the dashboard-driven path.
                from backend.compiler import package_name_for_output_dir
                from backend.generator.api_generator import (
                    GENERATED_APP_ENV_VARS,
                )
                from backend.generator.kubernetes_generator import (
                    generate_kubernetes_manifest,
                )
                generate_kubernetes_manifest(
                    str(output_dir / "kubernetes.yaml"),
                    package_name_for_output_dir(str(output_dir)),
                    GENERATED_APP_ENV_VARS,
                    image=tag,
                )
                print(f"kubernetes.yaml updated to reference '{tag}'.")

                smoke_test_result = None
                if args.smoke_test:
                    print("Running smoke test …")
                    smoke_test_result = _run_local_deploy_smoke_test(tag, output_dir)
                    if smoke_test_result["passed"]:
                        print("Smoke test: passed (GET /health responded 200)")
                    else:
                        print(f"Smoke test: FAILED -- {smoke_test_result['detail']}")

                if args.push:
                    print(f"Pushing Docker image '{tag}' …")
                    _run_deploy_docker_command(["docker", "push", tag], output_dir)
                    print(f"Docker image '{tag}' pushed successfully.")

                if smoke_test_result is not None and not smoke_test_result["passed"]:
                    sys.exit(1)
    elif args.command == "diff":
        diff = diff_notebook_functions(args.old_notebook, args.new_notebook)
        diff.update(classify_notebook_diff(diff))
        if args.content:
            diff["content_diff"] = diff_notebook_source(
                args.old_notebook, args.new_notebook
            )
        if args.json_output:
            print(json.dumps(diff, indent=2))
        else:
            print_notebook_diff(diff)
            if args.content and diff["content_diff"]:
                print("\n" + "\n".join(diff["content_diff"]))
        if args.fail_on_breaking and not diff["compatible"]:
            sys.exit(1)
    elif args.command == "upload":
        # Imported here, not at module scope, the same deferred-import
        # convention export-openapi/export-sdk's own dynamic imports
        # already use elsewhere in this file -- httpx is only ever needed
        # for this one command, not for every `notebook-to-api` invocation
        # (including `--help`).
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.expected_sha256 and len(args.notebook) > 1:

            raise ValueError(
                "--expected-sha256 can only be used when uploading a "
                "single notebook."
            )

        if len(args.notebook) == 1:
            # Single-file path, unchanged from before `upload` accepted
            # more than one notebook: still hits POST /api/upload
            # directly, not /api/upload/batch, so a script relying on
            # this command's original single-file output/exit-code shape
            # keeps working exactly as it did.
            notebook_path = args.notebook[0]

            params = {"overwrite": args.overwrite}
            if args.tags:
                params["tags"] = args.tags
            if args.description is not None:
                params["description"] = args.description
            if args.expected_sha256:
                params["expected_sha256"] = args.expected_sha256
            if args.dry_run:
                params["dry_run"] = True

            try:

                with open(notebook_path, "rb") as f:

                    response = httpx.post(
                        f"{dashboard_url}/api/upload",
                        params=params,
                        files={
                            "file": (
                                os.path.basename(notebook_path), f,
                                "application/json",
                            )
                        },
                        timeout=args.timeout,
                    )

            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the upload ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                verb = "Would upload" if data.get("dry_run") else "Uploaded"
                print(f"{verb} '{data.get('filename', notebook_path)}' to {dashboard_url}")
                print(f"  path: {data.get('path')}")
                print(f"  overwritten: {data.get('overwritten')}")
                print(f"  sha256: {data.get('sha256')}")
                if data.get("was_currently_compiled"):
                    print("  note: this was the notebook backing the currently compiled app.")

        else:
            # Multiple notebooks: POST /api/upload/batch instead of one
            # POST /api/upload per file -- besides the round-trip saving,
            # it means one bad file (invalid content, a 409 collision, an
            # oversized file, ...) doesn't stop the rest from uploading,
            # unlike a plain shell loop over single-file `upload` calls,
            # which stops at the first non-zero exit.
            opened_files = []

            try:

                for notebook_path in args.notebook:
                    f = open(notebook_path, "rb")
                    opened_files.append(f)

                files_payload = [
                    (
                        "files",
                        (os.path.basename(notebook_path), f, "application/json"),
                    )
                    for notebook_path, f in zip(args.notebook, opened_files)
                ]

                batch_params = {"overwrite": args.overwrite}
                if args.tags:
                    batch_params["tags"] = args.tags
                if args.description is not None:
                    batch_params["description"] = args.description
                if args.dry_run:
                    batch_params["dry_run"] = True

                try:
                    response = httpx.post(
                        f"{dashboard_url}/api/upload/batch",
                        params=batch_params,
                        files=files_payload,
                        timeout=args.timeout,
                    )
                except httpx.HTTPError as exc:
                    raise _dashboard_connection_error(exc, dashboard_url)

            finally:
                for f in opened_files:
                    f.close()

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the batch upload ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                verb = "Would upload" if data.get("dry_run") else "Uploaded"

                for result in data.get("results", []):

                    if result.get("status") == "success":
                        print(
                            f"{verb} '{result.get('filename')}' "
                            f"(overwritten: {result.get('overwritten')})"
                        )
                        if result.get("was_currently_compiled"):
                            print("  note: this was the notebook backing the currently compiled app.")
                    else:
                        print(f"Failed '{result.get('filename')}': {result.get('detail')}")

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed."
                )

            # A non-zero failed_count is a real, actionable outcome for a
            # script driving this command -- e.g. a CI step seeding
            # several notebooks that needs to know at least one didn't
            # land, not just that the batch request itself was handled
            # (which POST /api/upload/batch always reports as HTTP 200,
            # per-file failures included).
            if data.get("failed_count", 0) > 0:
                sys.exit(1)
    elif args.command == "import-notebooks":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {"overwrite": args.overwrite}

        if args.tags:
            params["tags"] = args.tags
        if args.description is not None:
            params["description"] = args.description
        if args.dry_run:
            params["dry_run"] = True
        if args.expected_sha256:
            params["expected_sha256"] = args.expected_sha256

        try:

            with open(args.zip_path, "rb") as f:

                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/import",
                    params=params,
                    files={
                        "file": (
                            os.path.basename(args.zip_path), f, "application/zip",
                        )
                    },
                    timeout=args.timeout,
                )

        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the import ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would import" if data.get("dry_run") else "Imported"

            for result in data.get("results", []):

                if result.get("status") == "success":

                    restored_version_count = result.get("restored_version_count", 0)
                    versions_suffix = (
                        f", restored {restored_version_count} version(s)"
                        if restored_version_count else ""
                    )
                    print(
                        f"{verb} '{result.get('filename')}' "
                        f"(overwritten: {result.get('overwritten')}"
                        f"{versions_suffix})"
                    )
                else:
                    print(f"Failed '{result.get('filename')}': {result.get('detail')}")

            print(
                f"\n{data.get('succeeded_count', 0)} succeeded, "
                f"{data.get('failed_count', 0)} failed."
            )

        if data.get("failed_count", 0) > 0:
            sys.exit(1)
    elif args.command == "import-url":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        body = {"url": args.url, "overwrite": args.overwrite}

        if args.filename:
            body["filename"] = args.filename
        if args.tags:
            body["tags"] = args.tags
        if args.description is not None:
            body["description"] = args.description
        if args.expected_sha256:
            body["expected_sha256"] = args.expected_sha256
        if args.dry_run:
            body["dry_run"] = True
        if args.headers:
            body["headers"] = _parse_import_url_headers(args.headers)

        try:
            response = httpx.post(
                f"{dashboard_url}/api/notebooks/import-url",
                json=body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the import ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would import" if data.get("dry_run") else "Imported"

            print(
                f"{verb} '{data.get('filename')}' from {data.get('source_url')}"
            )
            print(f"  path: {data.get('path')}")
            print(f"  overwritten: {data.get('overwritten')}")
            print(f"  sha256: {data.get('sha256')}")
    elif args.command == "list":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {"sort": args.sort, "order": args.order, "offset": args.offset}
        if args.search:
            params["search"] = args.search
        if args.tag:
            params["tag"] = args.tag
        if args.description_search:
            params["description_search"] = args.description_search
        if args.regex:
            params["regex"] = "true"
        if args.sha256:
            params["sha256"] = args.sha256
        if args.modified_after:
            params["modified_after"] = args.modified_after
        if args.modified_before:
            params["modified_before"] = args.modified_before
        if args.limit is not None:
            params["limit"] = args.limit
        if args.format == "csv":
            params["format"] = "csv"
        if args.checksums:
            params["checksums"] = "true"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/notebooks", params=params, timeout=args.timeout
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.format == "csv":
            # The response is CSV, not JSON -- printed as-is (redirect
            # stdout to a file to save it) rather than run through the
            # JSON/human-readable branches below, which both assume a
            # parseable JSON body.
            print(response.text, end="")
            return

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            notebooks = data.get("notebooks", [])

            if not notebooks:
                print("No notebooks found.")
            else:
                for notebook in notebooks:

                    markers = []

                    if notebook.get("currently_compiled"):
                        if notebook.get("compiled_version_id"):
                            markers.append(
                                f"currently compiled from version "
                                f"'{notebook['compiled_version_id']}'"
                            )
                        else:
                            markers.append("currently compiled")

                    if notebook.get("tags"):
                        markers.append(f"tags: {', '.join(notebook['tags'])}")

                    if args.checksums:
                        markers.append(f"sha256:{notebook.get('sha256')}")

                    suffix = f"  [{'; '.join(markers)}]" if markers else ""

                    print(
                        f"{notebook['filename']}  "
                        f"({notebook['size_bytes']} bytes){suffix}"
                    )

            total_count = data.get("total_count", len(notebooks))

            # "notebooks" can be a strict subset of "total_count" once
            # --limit/--offset are in play -- without this, a caller
            # paging through a large list had no way to tell "10
            # notebook(s) total" apart from "here are all 10" vs. "here
            # are 10 of many more", short of separately re-reading
            # --limit/--offset back off the JSON response themselves.
            if args.limit is not None and (args.offset + len(notebooks) < total_count):
                print(
                    f"\nShowing {len(notebooks)} of {total_count} "
                    f"notebook(s) (offset {args.offset})."
                )
            else:
                print(f"\n{total_count} notebook(s) total.")
    elif args.command == "info":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            response = httpx.get(
                f"{dashboard_url}/api/notebooks/{args.filename}/info",
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            print(f"{data['filename']}  ({data['size_bytes']} bytes)")
            print(f"  modified: {data['modified_at']}")
            print(f"  tags: {', '.join(data['tags']) if data['tags'] else '(none)'}")

            if data.get("source_url"):
                print(f"  source url: {data['source_url']}")

            print(f"  currently compiled: {data['currently_compiled']}")

            if data['currently_compiled']:
                print(f"  compiled at: {data.get('compiled_at')}")
                print(
                    "  changed since compile: "
                    f"{data.get('notebook_changed_since_compile')}"
                )
    elif args.command == "info-batch":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            response = httpx.post(
                f"{dashboard_url}/api/notebooks/info-batch",
                json={"filenames": args.filename},
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            for result in data.get("results", []):

                if result["status"] == "success":
                    tags = result.get("tags") or []
                    print(
                        f"{result['filename']}  ({result['size_bytes']} bytes) "
                        f"tags: {', '.join(tags) if tags else '(none)'}"
                    )
                else:
                    print(f"Failed '{result['filename']}': {result['detail']}")

            print(
                f"\n{data.get('succeeded_count', 0)} succeeded, "
                f"{data.get('failed_count', 0)} failed"
            )
    elif args.command == "search-functions":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {"search": args.search, "offset": args.offset}
        if args.tag:
            params["tag"] = args.tag
        if args.sha256:
            params["sha256"] = args.sha256
        if args.modified_after:
            params["modified_after"] = args.modified_after
        if args.modified_before:
            params["modified_before"] = args.modified_before
        if args.regex:
            params["regex"] = True
        if args.limit is not None:
            params["limit"] = args.limit
        if args.format == "csv":
            params["format"] = "csv"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/functions",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.format == "csv":
            # The response is CSV, not JSON -- printed as-is (redirect
            # stdout to a file to save it) rather than run through the
            # JSON/human-readable branches below, which both assume a
            # parseable JSON body.
            print(response.text, end="")
            return

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            matches = data.get("matches", [])

            if not matches:
                print(f"No notebooks define a function matching '{args.search}'.")
            else:
                for match in matches:
                    function_names = ", ".join(
                        func["name"] for func in match["functions"]
                    )
                    print(f"{match['filename']}: {function_names}")

                print(f"\n{_matched_notebooks_summary(data, args, len(matches))}")
    elif args.command == "search-content":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {"search": args.search, "offset": args.offset}
        if args.tag:
            params["tag"] = args.tag
        if args.sha256:
            params["sha256"] = args.sha256
        if args.modified_after:
            params["modified_after"] = args.modified_after
        if args.modified_before:
            params["modified_before"] = args.modified_before
        if args.regex:
            params["regex"] = True
        if args.limit is not None:
            params["limit"] = args.limit
        if args.format == "csv":
            params["format"] = "csv"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/notebooks/search-content",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.format == "csv":
            # The response is CSV, not JSON -- printed as-is (redirect
            # stdout to a file to save it) rather than run through the
            # JSON/human-readable branches below, which both assume a
            # parseable JSON body.
            print(response.text, end="")
            return

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            matches = data.get("matches", [])

            if not matches:
                print(f"No notebooks have a code cell matching '{args.search}'.")
            else:
                for match in matches:
                    print(f"{match['filename']}:")
                    for cell_match in match["matches"]:
                        print(f"  [{cell_match['cell_index']}] {cell_match['snippet']}")

                print(f"\n{_matched_notebooks_summary(data, args, len(matches))}")
    elif args.command == "find-duplicates":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {}

        if args.tag:
            params["tag"] = args.tag
        if args.sha256:
            params["sha256"] = args.sha256
        if args.modified_after:
            params["modified_after"] = args.modified_after
        if args.modified_before:
            params["modified_before"] = args.modified_before
        if args.limit is not None:
            params["limit"] = args.limit
        if args.offset:
            params["offset"] = args.offset
        if args.format == "csv":
            params["format"] = "csv"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/notebooks/duplicates",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.format == "csv":
            # The response is CSV, not JSON -- printed as-is (redirect
            # stdout to a file to save it) rather than run through the
            # JSON/human-readable branches below, which both assume a
            # parseable JSON body.
            print(response.text, end="")
            return

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            duplicate_groups = data.get("duplicate_groups", [])

            if not duplicate_groups:
                print(f"No duplicate notebooks found on {dashboard_url}.")
            else:
                for group in duplicate_groups:
                    print(f"{group['sha256']}: {', '.join(group['filenames'])}")

                print(
                    f"\n{data.get('group_count', len(duplicate_groups))} "
                    f"duplicate group(s), "
                    f"{data.get('duplicate_notebook_count', 0)} notebook(s) total"
                )

    elif args.command == "resolve-duplicates":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        keep = {}

        for raw_entry in args.keep or []:

            if "=" not in raw_entry:
                raise RuntimeError(
                    f"Invalid --keep '{raw_entry}' -- expected "
                    "SHA256=FILENAME."
                )

            sha256, _, keep_filename = raw_entry.partition("=")
            keep[sha256] = keep_filename

        if not args.dry_run and not args.yes:
            # POST /api/notebooks/duplicates/resolve (routes/upload.py)
            # has no confirmation step of its own, is irreversible, and
            # deletes every duplicate but one across every group found on
            # this dashboard -- the same reasoning `delete-batch`/
            # `prune-versions` already prompt for. Not asked at all under
            # --dry-run, which never deletes anything.
            answer = input(
                f"Permanently delete every duplicate notebook (keeping "
                f"one per group) on {dashboard_url}? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return

        resolve_body = {"keep": keep}

        if args.tag:
            resolve_body["tag"] = args.tag
        if args.sha256:
            resolve_body["sha256"] = args.sha256
        if args.dry_run:
            resolve_body["dry_run"] = True

        try:
            response = httpx.post(
                f"{dashboard_url}/api/notebooks/duplicates/resolve",
                json=resolve_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            results = data.get("results", [])
            verb = "would delete" if data.get("dry_run") else "deleted"

            if not results:
                print(f"No duplicate notebooks found on {dashboard_url}.")
            else:

                for result in results:

                    if result["status"] == "success":

                        deleted_names = [
                            entry["filename"] for entry in result["deleted_filenames"]
                        ]
                        print(
                            f"Kept {result['kept_filename']}, {verb} "
                            f"{', '.join(deleted_names) if deleted_names else '(nothing)'}"
                        )
                    else:
                        print(f"Failed to resolve group {result['sha256']}: {result['detail']}")

                summary = (
                    f"\n{data.get('succeeded_count', 0)} group(s) "
                    f"{'previewed' if data.get('dry_run') else 'resolved'}, "
                    f"{data.get('failed_count', 0)} failed"
                )
                print(summary)

    elif args.command == "storage":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {"offset": args.offset}
        if args.tag:
            params["tag"] = args.tag
        if args.limit is not None:
            params["limit"] = args.limit
        if args.format == "csv":
            params["format"] = "csv"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/notebooks/storage",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.format == "csv":
            # The response is CSV, not JSON -- printed as-is (redirect
            # stdout to a file to save it) rather than run through the
            # JSON/human-readable branches below, which both assume a
            # parseable JSON body.
            print(response.text, end="")
            return

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            notebooks = data.get("notebooks", [])

            if not notebooks:
                print(f"No notebooks uploaded to {dashboard_url}.")
            else:

                for entry in notebooks:
                    print(
                        f"{entry['filename']}: {entry['total_bytes']} bytes "
                        f"({entry['notebook_bytes']} notebook + "
                        f"{entry['version_bytes']} bytes across "
                        f"{entry['version_count']} version(s))"
                    )

                notebook_count = data.get("notebook_count", len(notebooks))

                # "notebooks" can be a strict subset of "notebook_count"
                # once --limit/--offset are in play -- the same "how many
                # of the total am I actually looking at" gap `list`'s own
                # identical --limit/--offset handling already closes for
                # GET /api/notebooks. Every running total below still
                # covers "notebook_count"-many notebooks regardless, so
                # this is an extra line, not a replacement for it.
                if args.limit is not None and (args.offset + len(notebooks) < notebook_count):
                    print(
                        f"\nShowing {len(notebooks)} of {notebook_count} "
                        f"notebook(s) (offset {args.offset})."
                    )

                print(
                    f"\n{notebook_count} "
                    f"notebook(s), {data.get('total_bytes', 0)} bytes total "
                    f"({data.get('total_notebook_bytes', 0)} notebooks + "
                    f"{data.get('total_version_bytes', 0)} across "
                    f"{data.get('total_version_count', 0)} version(s)) "
                    f"on {dashboard_url}"
                )

            # Printed regardless of whether "notebooks" is empty -- the
            # catalog-wide cap (and how close it is) is just as relevant
            # to an operator looking at an empty catalog as a full one.
            max_notebooks = data.get("max_notebooks")

            if max_notebooks:
                print(
                    f"Catalog cap: {max_notebooks} notebook(s), "
                    f"{data.get('notebooks_remaining')} remaining"
                )

            # Mirrors the "Catalog cap" line above for
            # max_total_storage_bytes/storage_bytes_remaining -- the whole-
            # catalog byte budget (distinct from max_notebooks' own notebook
            # *count* cap), previously reported by GET /api/notebooks/storage
            # itself but silently dropped on the way to this command's own
            # human-readable output (still visible via `storage --json`
            # ever since it was added).
            max_total_storage_bytes = data.get("max_total_storage_bytes")

            if max_total_storage_bytes:
                print(
                    f"Storage cap: {max_total_storage_bytes} bytes, "
                    f"{data.get('storage_bytes_remaining')} remaining"
                )

    elif args.command == "download":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            response = httpx.get(
                f"{dashboard_url}/api/notebooks/{args.filename}",
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        sha256 = response.headers.get("x-content-sha256")

        if args.expected_sha256 and args.expected_sha256 != sha256:

            raise RuntimeError(
                f"Downloaded content's sha256 ({sha256}) does not match "
                f"the expected value ({args.expected_sha256}) -- nothing "
                "was written to disk."
            )

        output_path = args.output or args.filename

        with open(output_path, "wb") as f:
            f.write(response.content)

        if args.json_output:
            print(json.dumps(
                {
                    "status": "success",
                    "filename": args.filename,
                    "path": output_path,
                    "size_bytes": len(response.content),
                    "sha256": sha256,
                },
                indent=2,
            ))
        else:
            print(
                f"Downloaded '{args.filename}' from {dashboard_url} to "
                f"{output_path} ({len(response.content)} bytes)"
            )
            if sha256:
                print(f"  sha256: {sha256}")
    elif args.command == "export-notebooks":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.filename and args.tag:
            raise RuntimeError("Pass either a filename or --tag, not both.")

        params = {}
        if args.filename:
            params["filenames"] = ",".join(args.filename)
        if args.tag:
            params["tag"] = args.tag
        if args.include_versions:
            params["include_versions"] = True

        try:
            response = httpx.get(
                f"{dashboard_url}/api/notebooks/export",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        bundle_sha256 = response.headers.get("X-Bundle-SHA256")

        if args.expected_sha256 and args.expected_sha256 != bundle_sha256:

            raise RuntimeError(
                f"Exported bundle's sha256 ({bundle_sha256}) does not "
                f"match the expected value ({args.expected_sha256}) -- "
                "nothing was written to disk."
            )

        output_path = args.output or _filename_from_content_disposition(
            response, "notebooks_export.zip"
        )

        with open(output_path, "wb") as f:
            f.write(response.content)

        if args.json_output:
            print(json.dumps(
                {
                    "status": "success",
                    "path": output_path,
                    "size_bytes": len(response.content),
                    "bundle_sha256": bundle_sha256,
                },
                indent=2,
            ))
        else:
            print(
                f"Exported notebooks from {dashboard_url} to "
                f"{output_path} ({len(response.content)} bytes)"
            )
            if bundle_sha256:
                print(f"  bundle sha256: {bundle_sha256}")
    elif args.command == "delete":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.all and args.filename:
            raise RuntimeError("Pass either a filename or --all, not both.")

        if not args.all and not args.filename:
            raise RuntimeError(
                "Pass a filename to delete, or --all to delete every "
                "uploaded notebook."
            )

        if args.tag and not args.all:
            raise RuntimeError("--tag only applies together with --all.")

        if args.sha256 and not args.all:
            raise RuntimeError("--sha256 only applies together with --all.")

        if args.all:
            # DELETE /api/notebooks requires its own ?confirm=true before
            # it does anything (routes/upload.py) -- always passed here
            # once this prompt (or --yes) has already confirmed the same
            # thing on this side, so the two confirmation steps never
            # double-prompt a caller who already said yes once. Neither
            # the prompt nor "confirm" is sent at all under --dry-run,
            # which never deletes anything -- the endpoint's own "dry_run"
            # bypasses its "confirm" requirement for the identical reason.
            if not args.dry_run and not args.yes:
                target_parts = []
                if args.tag:
                    target_parts.append(f"tagged '{args.tag}'")
                if args.sha256:
                    target_parts.append(f"with sha256 '{args.sha256}'")
                target = (
                    f"every notebook {' and '.join(target_parts)}" if target_parts
                    else "ALL uploaded notebooks"
                )
                answer = input(f"Delete {target} on {dashboard_url}? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            params = {}
            if args.dry_run:
                params["dry_run"] = "true"
            else:
                params["confirm"] = "true"
            if args.tag:
                params["tag"] = args.tag
            if args.sha256:
                params["sha256"] = args.sha256

            try:
                response = httpx.delete(
                    f"{dashboard_url}/api/notebooks",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would delete" if data.get("dry_run") else "Deleted"
                deleted_filenames = data.get("deleted_filenames", [])

                if not deleted_filenames:
                    print(f"No notebooks to delete on {dashboard_url}.")
                else:

                    for filename in deleted_filenames:
                        print(f"{verb} '{filename}'")

                    print(
                        f"\n{data.get('deleted_count', len(deleted_filenames))} "
                        f"notebook(s) {'would be ' if data.get('dry_run') else ''}"
                        f"deleted from {dashboard_url}"
                    )

                if data.get("currently_compiled_notebook_deleted"):
                    print("  note: this included the notebook backing the currently compiled app.")

        else:
            # Single-filename path, unchanged from before `delete`
            # accepted --all: still hits DELETE
            # /api/notebooks/{filename} directly, not DELETE
            # /api/notebooks, so a script relying on this command's
            # original single-file output/exit-code shape keeps working
            # exactly as it did.
            if not args.dry_run and not args.yes:
                # DELETE /api/notebooks/{filename} (routes/upload.py) has
                # no confirmation step of its own and is irreversible --
                # unlike `upload --overwrite`/`rename --overwrite`,
                # there's no non-destructive default to fall back to
                # here, so this asks on the terminal instead. --yes skips
                # the prompt for scripting/automation; --dry-run skips it
                # too, since nothing irreversible happens under it.
                answer = input(f"Delete '{args.filename}' from {dashboard_url}? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            params = {"dry_run": "true"} if args.dry_run else {}

            try:
                response = httpx.delete(
                    f"{dashboard_url}/api/notebooks/{args.filename}",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would delete" if data.get("dry_run") else "Deleted"

                print(f"{verb} '{data.get('filename', args.filename)}' from {dashboard_url}")
                if data.get("was_currently_compiled"):
                    print("  note: this was the notebook backing the currently compiled app.")
    elif args.command == "delete-batch":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if not args.dry_run and not args.yes:
            # POST /api/notebooks/delete-batch (routes/upload.py) has no
            # confirmation step of its own and is irreversible -- the same
            # reasoning `delete`'s own single-filename/--all paths already
            # prompt for. Not asked at all under --dry-run, which never
            # deletes anything.
            filenames_display = ", ".join(args.filename)
            answer = input(
                f"Delete {filenames_display} from {dashboard_url}? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return

        delete_batch_body = {"filenames": args.filename}
        if args.dry_run:
            delete_batch_body["dry_run"] = True

        try:
            response = httpx.post(
                f"{dashboard_url}/api/notebooks/delete-batch",
                json=delete_batch_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would delete" if data.get("dry_run") else "Deleted"

            for result in data.get("results", []):

                if result["status"] == "success":
                    print(f"{verb} '{result['filename']}'")
                    if result.get("was_currently_compiled"):
                        print("  note: this was the notebook backing the currently compiled app.")
                else:
                    print(f"Failed to delete {result['filename']}: {result['detail']}")

            print(
                f"\n{data.get('succeeded_count', 0)} succeeded, "
                f"{data.get('failed_count', 0)} failed"
            )
    elif args.command == "prune-versions":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if not args.dry_run and not args.yes:
            # DELETE /api/notebooks/versions (routes/upload.py) has no
            # confirmation step of its own, is irreversible, and affects
            # every notebook's own version history at once -- the same
            # reasoning `delete-batch`'s own confirmation already applies.
            # Not asked at all under --dry-run, which never deletes
            # anything.
            scope_parts = []
            if args.tag:
                scope_parts.append(f"tagged {args.tag!r}")
            if args.sha256:
                scope_parts.append(f"with sha256 {args.sha256!r}")
            scope = (
                f"every notebook {' and '.join(scope_parts)}" if scope_parts
                else "every notebook"
            )
            answer = input(
                f"Permanently discard every version older than "
                f"{args.older_than_days} day(s) across {scope} on "
                f"{dashboard_url}? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return

        params = {"older_than_days": args.older_than_days}
        if args.tag:
            params["tag"] = args.tag
        if args.sha256:
            params["sha256"] = args.sha256
        if args.dry_run:
            params["dry_run"] = True

        try:
            response = httpx.delete(
                f"{dashboard_url}/api/notebooks/versions",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            results = data.get("results", [])
            verb = "would discard" if data.get("dry_run") else "discarded"

            if not results:
                print(
                    f"No versions on {dashboard_url} older than "
                    f"{args.older_than_days} day(s)."
                )
            else:

                for result in results:
                    print(
                        f"{result['filename']}: {verb} "
                        f"{result['deleted_count']} version(s)"
                    )

                print(
                    f"\n{data.get('total_deleted_count', 0)} version(s) "
                    f"{verb} across "
                    f"{data.get('notebook_count_affected', len(results))} "
                    f"notebook(s) on {dashboard_url}"
                )

    elif args.command == "prune-temp-files":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if not args.dry_run and not args.yes:
            # DELETE /api/upload/temp-files (routes/upload.py) has no
            # confirmation step of its own and is irreversible -- the
            # same reasoning `prune-versions`'s own confirmation already
            # applies. Not asked at all under --dry-run, which never
            # deletes anything.
            answer = input(
                f"Permanently remove orphaned upload temp files on "
                f"{dashboard_url}? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return

        params = {}
        if args.older_than_seconds is not None:
            params["older_than_seconds"] = args.older_than_seconds
        if args.dry_run:
            params["dry_run"] = True

        try:
            response = httpx.delete(
                f"{dashboard_url}/api/upload/temp-files",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            deleted_files = data.get("deleted_files", [])
            verb = "Would remove" if data.get("dry_run") else "Removed"

            if not deleted_files:
                print(f"No orphaned upload temp files found on {dashboard_url}.")
            else:

                for entry in deleted_files:
                    print(
                        f"{verb} '{entry['filename']}' "
                        f"({entry['size_bytes']} bytes, "
                        f"{entry['age_seconds']}s old)"
                    )

                print(
                    f"\n{data.get('deleted_count', 0)} file(s), "
                    f"{data.get('reclaimed_bytes', 0)} byte(s) "
                    f"{'would be reclaimed' if data.get('dry_run') else 'reclaimed'}"
                )

    elif args.command == "rename":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        rename_body = {"new_filename": args.new_filename, "overwrite": args.overwrite}
        if args.dry_run:
            rename_body["dry_run"] = True

        try:
            response = httpx.patch(
                f"{dashboard_url}/api/notebooks/{args.filename}",
                json=rename_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would rename" if data.get("dry_run") else "Renamed"

            print(
                f"{verb} '{data.get('filename', args.filename)}' to "
                f"'{data.get('new_filename', args.new_filename)}' on {dashboard_url}"
            )
            if data.get("was_currently_compiled"):
                print("  note: this was the notebook backing the currently compiled app.")
    elif args.command == "rename-many":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        entries = [
            {**entry, "overwrite": args.overwrite} for entry in args.entry
        ]

        body = {"entries": entries}
        if args.dry_run:
            body["dry_run"] = True

        try:
            response = httpx.post(
                f"{dashboard_url}/api/notebooks/rename-batch",
                json=body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would rename" if data.get("dry_run") else "Renamed"

            for result in data.get("results", []):

                if result["status"] == "success":
                    print(f"{verb} '{result['filename']}' to '{result['new_filename']}'")
                    if result.get("was_currently_compiled"):
                        print("  note: this was the notebook backing the currently compiled app.")
                else:
                    print(
                        f"Failed to rename '{result['filename']}' to "
                        f"'{result['new_filename']}': {result['detail']}"
                    )

            print(
                f"\n{data.get('succeeded_count', 0)} succeeded, "
                f"{data.get('failed_count', 0)} failed"
            )
    elif args.command == "copy":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        copy_body = {"new_filename": args.new_filename, "overwrite": args.overwrite}
        if args.tags:
            copy_body["tags"] = _parse_comma_separated_names(args.tags)
        if args.description is not None:
            copy_body["description"] = args.description
        if args.dry_run:
            copy_body["dry_run"] = True

        try:
            response = httpx.post(
                f"{dashboard_url}/api/notebooks/{args.filename}/copy",
                json=copy_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would copy" if data.get("dry_run") else "Copied"

            print(
                f"{verb} '{data.get('filename', args.filename)}' to "
                f"'{data.get('new_filename', args.new_filename)}' on {dashboard_url}"
            )
    elif args.command == "copy-batch":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        body = {
            "new_filenames": args.new_filename,
            "overwrite": args.overwrite,
        }
        if args.tags:
            body["tags"] = _parse_comma_separated_names(args.tags)
        if args.description is not None:
            body["description"] = args.description
        if args.dry_run:
            body["dry_run"] = True

        try:
            response = httpx.post(
                f"{dashboard_url}/api/notebooks/{args.filename}/copy-batch",
                json=body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would copy" if data.get("dry_run") else "Copied"

            for result in data.get("results", []):

                if result["status"] == "success":
                    print(f"{verb} '{args.filename}' to '{result['new_filename']}'")
                else:
                    print(f"Failed to copy to '{result['new_filename']}': {result['detail']}")

            print(
                f"\n{data.get('succeeded_count', 0)} succeeded, "
                f"{data.get('failed_count', 0)} failed"
            )
    elif args.command == "copy-many":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        entries = [
            {**entry, "overwrite": args.overwrite} for entry in args.entry
        ]

        if args.tags:
            tags = _parse_comma_separated_names(args.tags)
            for entry in entries:
                entry["tags"] = tags

        if args.description is not None:
            for entry in entries:
                entry["description"] = args.description

        body = {"entries": entries}
        if args.dry_run:
            body["dry_run"] = True

        try:
            response = httpx.post(
                f"{dashboard_url}/api/notebooks/copy-batch",
                json=body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would copy" if data.get("dry_run") else "Copied"

            for result in data.get("results", []):

                if result["status"] == "success":
                    print(f"{verb} '{result['filename']}' to '{result['new_filename']}'")
                else:
                    print(
                        f"Failed to copy '{result['filename']}' to "
                        f"'{result['new_filename']}': {result['detail']}"
                    )

            print(
                f"\n{data.get('succeeded_count', 0)} succeeded, "
                f"{data.get('failed_count', 0)} failed"
            )
    elif args.command == "tags":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.tags_command == "get":

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}/tags",
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                tags = data.get("tags", [])
                print(f"{args.filename}: {', '.join(tags) if tags else '(no tags)'}")

        elif args.tags_command == "list":

            params = {"format": "csv"} if args.format == "csv" else {}

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/tags",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            if args.format == "csv":
                # The response is CSV, not JSON -- printed as-is, the
                # same "redirect it to a file" convention `find-
                # duplicates --format csv` already establishes.
                print(response.text, end="")
                return

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                tags = data.get("tags", [])

                if not tags:
                    print("No tags in use on any notebook.")
                else:
                    for tag_entry in tags:
                        plural = "" if tag_entry["notebook_count"] == 1 else "s"
                        print(
                            f"{tag_entry['tag']}  "
                            f"({tag_entry['notebook_count']} notebook{plural})"
                        )

        elif args.tags_command == "delete":

            if not args.dry_run and not args.yes:
                # DELETE /api/tags/{tag} (routes/upload.py) has no
                # confirmation step of its own and affects every notebook
                # carrying the tag at once -- the same reasoning
                # `delete`'s own single-filename path and `versions
                # delete` already prompt for. Not asked at all under
                # --dry-run, which never removes anything.
                answer = input(
                    f"Remove tag '{args.tag}' from every notebook on "
                    f"{dashboard_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            params = {"dry_run": True} if args.dry_run else {}

            try:
                response = httpx.delete(
                    f"{dashboard_url}/api/tags/{args.tag}",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                affected_notebooks = data.get("affected_notebooks", [])
                verb = "Would remove" if data.get("dry_run") else "Removed"

                if not affected_notebooks:
                    print(f"No notebooks on {dashboard_url} carry tag '{args.tag}'.")
                else:
                    for filename in affected_notebooks:
                        print(f"{verb} '{args.tag}' from {filename}")

                    print(
                        f"\n{data.get('notebook_count', len(affected_notebooks))} "
                        f"notebook(s) updated on {dashboard_url}"
                    )

        elif args.tags_command == "apply":

            apply_body = {"filenames": args.filename}
            if args.dry_run:
                apply_body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/tags/{args.tag}/apply",
                    json=apply_body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would tag" if data.get("dry_run") else "Tagged"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        print(f"{verb} {result['filename']} with '{args.tag}'")
                    else:
                        print(f"Failed to tag {result['filename']}: {result['detail']}")

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

        elif args.tags_command == "remove":

            remove_body = {"filenames": args.filename}
            if args.dry_run:
                remove_body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/tags/{args.tag}/remove",
                    json=remove_body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would remove" if data.get("dry_run") else "Removed"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        print(f"{verb} '{args.tag}' from {result['filename']}")
                    else:
                        print(f"Failed to update {result['filename']}: {result['detail']}")

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

        elif args.tags_command == "rename":

            if not args.dry_run and not args.yes:
                # PATCH /api/tags/{tag} (routes/upload.py) has no
                # confirmation step of its own and affects every notebook
                # carrying the tag at once -- the same reasoning `tags
                # delete` already prompts for. Not asked at all under
                # --dry-run, which never renames anything.
                answer = input(
                    f"Rename tag '{args.tag}' to '{args.new_tag}' on every "
                    f"notebook on {dashboard_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            body = {"new_tag": args.new_tag}
            if args.dry_run:
                body["dry_run"] = True

            try:
                response = httpx.patch(
                    f"{dashboard_url}/api/tags/{args.tag}",
                    json=body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                affected_notebooks = data.get("affected_notebooks", [])
                verb = "Would rename" if data.get("dry_run") else "Renamed"

                if not affected_notebooks:
                    print(f"No notebooks on {dashboard_url} carry tag '{args.tag}'.")
                else:
                    for filename in affected_notebooks:
                        print(f"{verb} '{args.tag}' to '{args.new_tag}' on {filename}")

                    print(
                        f"\n{data.get('notebook_count', len(affected_notebooks))} "
                        f"notebook(s) updated on {dashboard_url}"
                    )

        elif args.tags_command == "set":

            set_tags_body = {"tags": args.tag}
            if args.dry_run:
                set_tags_body["dry_run"] = True

            try:
                response = httpx.put(
                    f"{dashboard_url}/api/notebooks/{args.filename}/tags",
                    json=set_tags_body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                tags = data.get("tags", [])
                verb = "would be set to" if data.get("dry_run") else "set to"
                print(f"{args.filename} tags {verb}: {', '.join(tags) if tags else '(none)'}")

        else:  # args.tags_command == "set-batch"

            entries = []

            for raw_entry in args.entry:

                if "=" not in raw_entry:
                    raise RuntimeError(
                        f"Invalid --entry '{raw_entry}' -- expected "
                        "FILENAME=TAG1,TAG2,... (an empty right-hand side "
                        "clears that notebook's tags)."
                    )

                filename, _, tags_str = raw_entry.partition("=")

                entries.append({
                    "filename": filename,
                    "tags": _parse_comma_separated_names(tags_str) or [],
                })

            body = {"entries": entries}
            if args.dry_run:
                body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/tags-batch",
                    json=body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "would be set to" if data.get("dry_run") else "set to"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        tags = result.get("tags", [])
                        print(
                            f"{result['filename']} tags {verb}: "
                            f"{', '.join(tags) if tags else '(none)'}"
                        )
                    else:
                        print(f"Failed to set tags for {result['filename']}: {result['detail']}")

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

    elif args.command == "description":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.description_command == "get":

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}/description",
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                description = data.get("description", "")
                print(f"{args.filename}: {description if description else '(no description)'}")

        elif args.description_command == "set":

            set_description_body = {"description": args.description}
            if args.dry_run:
                set_description_body["dry_run"] = True

            try:
                response = httpx.put(
                    f"{dashboard_url}/api/notebooks/{args.filename}/description",
                    json=set_description_body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                description = data.get("description", "")
                verb = "would be set to" if data.get("dry_run") else "set to"
                print(
                    f"{args.filename} description {verb}: "
                    f"{description if description else '(cleared)'}"
                )

        else:  # args.description_command == "set-batch"

            entries = []

            for raw_entry in args.entry:

                if "=" not in raw_entry:
                    raise RuntimeError(
                        f"Invalid --entry '{raw_entry}' -- expected "
                        "FILENAME=DESCRIPTION (an empty right-hand side "
                        "clears that notebook's description)."
                    )

                filename, _, description = raw_entry.partition("=")

                entries.append({"filename": filename, "description": description})

            body = {"entries": entries}
            if args.dry_run:
                body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/description-batch",
                    json=body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "would be set to" if data.get("dry_run") else "set to"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        description = result.get("description", "")
                        print(
                            f"{result['filename']} description {verb}: "
                            f"{description if description else '(cleared)'}"
                        )
                    else:
                        print(
                            f"Failed to set description for {result['filename']}: "
                            f"{result['detail']}"
                        )

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

    elif args.command == "source-url":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.source_url_command == "get":

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}/source-url",
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                source_url = data.get("source_url")
                print(f"{args.filename}: {source_url if source_url else '(no source url)'}")

        elif args.source_url_command == "set":

            set_source_url_body = {"source_url": args.source_url}
            if args.dry_run:
                set_source_url_body["dry_run"] = True

            try:
                response = httpx.put(
                    f"{dashboard_url}/api/notebooks/{args.filename}/source-url",
                    json=set_source_url_body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                source_url = data.get("source_url")
                verb = "would be set to" if data.get("dry_run") else "set to"
                print(
                    f"{args.filename} source url {verb}: "
                    f"{source_url if source_url else '(cleared)'}"
                )

        else:  # args.source_url_command == "set-batch"

            entries = []

            for raw_entry in args.entry:

                if "=" not in raw_entry:
                    raise RuntimeError(
                        f"Invalid --entry '{raw_entry}' -- expected "
                        "FILENAME=SOURCE_URL (an empty right-hand side "
                        "clears that notebook's recorded source_url)."
                    )

                filename, _, source_url = raw_entry.partition("=")

                entries.append({"filename": filename, "source_url": source_url})

            body = {"entries": entries}
            if args.dry_run:
                body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/source-url-batch",
                    json=body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "would be set to" if data.get("dry_run") else "set to"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        source_url = result.get("source_url")
                        print(
                            f"{result['filename']} source url {verb}: "
                            f"{source_url if source_url else '(cleared)'}"
                        )
                    else:
                        print(
                            f"Failed to set source url for {result['filename']}: "
                            f"{result['detail']}"
                        )

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

    elif args.command == "remote-compile":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)

        request_body = {"notebook_path": args.filename}

        # Omitted entirely (rather than sent as null) when unset, so a
        # dashboard's POST /api/compile sees exactly the same request
        # shape it did before only/exclude/version_id existed for callers
        # that never pass any of them.
        if only:
            request_body["only"] = only
        if exclude:
            request_body["exclude"] = exclude
        if args.version_id:
            request_body["version_id"] = args.version_id
        if args.smoke_test:
            request_body["smoke_test"] = True

        try:
            response = httpx.post(
                f"{dashboard_url}/api/compile",
                json=request_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the compile ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            target = (
                f"'{data.get('notebook', args.filename)}' "
                f"version '{data['version_id']}'"
                if data.get("version_id")
                else f"'{data.get('notebook', args.filename)}'"
            )
            print(f"Compiled {target} on {dashboard_url}")

            endpoints = data.get("endpoints", [])

            if endpoints:
                print(f"\n{len(endpoints)} endpoint(s):")
                for endpoint in endpoints:
                    marker = "  [background]" if endpoint.get("is_async") else ""
                    print(f"  {endpoint['method']} {endpoint['path']}{marker}")

            skipped_functions = data.get("skipped_functions", [])

            if skipped_functions:
                print(f"\n{len(skipped_functions)} skipped function(s):")
                for skipped in skipped_functions:
                    print(f"  - {skipped['name']}: {skipped['reason']}")

            dependencies = data.get("dependencies", [])

            if dependencies:
                print(f"\nDependencies: {', '.join(dependencies)}")

            smoke_test = data.get("smoke_test")

            if smoke_test is not None:
                if smoke_test["passed"]:
                    print("\nSmoke test: passed (GET /health responded 200)")
                else:
                    print(
                        "\nSmoke test: FAILED -- "
                        f"{smoke_test.get('detail')}"
                    )

        if data.get("smoke_test") is not None and not data["smoke_test"]["passed"]:
            sys.exit(1)
    elif args.command == "remote-inspect":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            if args.version_id:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}"
                    f"/versions/{args.version_id}/inspect",
                    timeout=args.timeout,
                )
            else:
                response = httpx.post(
                    f"{dashboard_url}/api/inspect",
                    json={"notebook_path": args.filename},
                    timeout=args.timeout,
                )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )
            print(f"Inspecting {target} on {dashboard_url}")

            reserved_name_conflicts = data.get("reserved_name_conflicts", [])

            if reserved_name_conflicts:
                print("\n✗ Reserved name conflicts (compilation will fail):")
                for name in reserved_name_conflicts:
                    print(f"  - {name}")

            duplicate_functions = data.get("duplicate_functions", [])

            if duplicate_functions:
                print("\n⚠ Duplicate functions (redefined; only the last definition is compiled):")
                for name in duplicate_functions:
                    print(f"  - {name}")

            private_functions = data.get("private_functions", [])

            if private_functions:
                print("\nPrivate functions (never exposed as an endpoint):")
                for name in private_functions:
                    print(f"  - {name}")

            excluded_imports = data.get("excluded_imports", [])

            if excluded_imports:
                print("\nExcluded imports (opted out of requirements.txt):")
                for name in excluded_imports:
                    print(f"  - {name}")

            functions_without_docstrings = data.get("functions_without_docstrings", [])

            if functions_without_docstrings:
                print("\nFunctions without a docstring (will get a generic OpenAPI description):")
                for name in functions_without_docstrings:
                    print(f"  - {name}")

            skipped_functions = data.get("skipped_functions", [])

            if skipped_functions:
                print("\n⚠ Skipped functions (no endpoint will be generated):")
                for skipped in skipped_functions:
                    print(f"  - {skipped['name']}: {skipped['reason']}")

            endpoints = data.get("endpoints", [])

            if endpoints:
                print(f"\n{len(endpoints)} endpoint(s):")
                for endpoint in endpoints:
                    marker = "  [background]" if endpoint.get("is_async") else ""
                    print(f"  {endpoint['method']} {endpoint['path']}{marker}")

            dependencies = data.get("dependencies", [])

            if dependencies:
                print(f"\nDependencies: {', '.join(dependencies)}")

            generated_files = data.get("generated_files", [])

            if generated_files:
                print(f"\nGenerated files: {', '.join(sorted(generated_files))}")
    elif args.command == "remote-validate":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        validate_body = {"notebook_path": args.filename, "strict": args.strict}
        if args.version_id:
            validate_body["version_id"] = args.version_id
        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)
        if only:
            validate_body["only"] = only
        if exclude:
            validate_body["exclude"] = exclude

        try:
            response = httpx.post(
                f"{dashboard_url}/api/validate",
                json=validate_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        status = data.get("status")
        reserved_name_conflicts = data.get("reserved_name_conflicts", [])
        skipped_functions = data.get("skipped_functions", [])
        duplicate_functions = data.get("duplicate_functions", [])

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )
            print(f"Validating {target} on {dashboard_url}")

            if reserved_name_conflicts:
                print("\n✗ Reserved name conflicts (compilation will fail):")
                for name in reserved_name_conflicts:
                    print(f"  - {name}")

            if duplicate_functions:
                marker = "✗" if args.strict else "⚠"
                print(f"\n{marker} Duplicate functions (redefined; only the last definition is compiled):")
                for name in duplicate_functions:
                    print(f"  - {name}")

            if skipped_functions:
                marker = "✗" if args.strict else "⚠"
                print(f"\n{marker} Skipped functions (no endpoint will be generated):")
                for skipped in skipped_functions:
                    print(f"  - {skipped['name']}: {skipped['reason']}")

            if status == "pass":
                print("\n✓ No issues found.")
            elif status == "warn":
                print("\nWarnings found, but this notebook would still compile cleanly.")
            else:
                print("\nValidation failed.")

        if status == "fail":
            sys.exit(2)
        elif status == "warn":
            sys.exit(1)
    elif args.command == "validate-all":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {"strict": args.strict, "offset": args.offset}
        if args.tag:
            params["tag"] = args.tag
        if args.sha256:
            params["sha256"] = args.sha256
        if args.modified_after:
            params["modified_after"] = args.modified_after
        if args.modified_before:
            params["modified_before"] = args.modified_before
        if args.limit is not None:
            params["limit"] = args.limit
        if args.format == "csv":
            params["format"] = "csv"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/validate-all",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.format == "csv":
            # The response is CSV, not JSON -- printed as-is, the same
            # "redirect it to a file" convention `find-duplicates
            # --format csv` already establishes. Exits 0 unconditionally:
            # this mode is for archiving/reporting, not CI gating -- use
            # the default JSON/human mode (whose own pass_count/
            # warn_count/fail_count drive the exit code below) for that.
            print(response.text, end="")
            return

        data = response.json()
        results = data.get("results", [])

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            if not results:
                print(f"No notebooks to validate on {dashboard_url}.")
            else:
                for result in results:

                    marker = {"pass": "✓", "warn": "⚠", "fail": "✗"}[result["status"]]
                    print(f"{marker} {result['filename']}: {result['status']}")

                    if result.get("detail"):
                        print(f"    {result['detail']}")

                    for name in result.get("reserved_name_conflicts", []):
                        print(f"    reserved name conflict: {name}")

                    for name in result.get("duplicate_functions", []):
                        print(f"    duplicate function: {name}")

                    for skipped in result.get("skipped_functions", []):
                        print(f"    skipped: {skipped['name']}: {skipped['reason']}")

                result_count = data.get("result_count", len(results))

                # "results" can be a strict subset of "result_count" once
                # --limit/--offset are in play -- the same "how many of
                # the total am I actually looking at" gap `list`'s own
                # identical --limit/--offset handling already closes for
                # GET /api/notebooks. pass_count/warn_count/fail_count
                # below still cover "result_count"-many notebooks
                # regardless.
                if args.limit is not None and (args.offset + len(results) < result_count):
                    print(
                        f"\nShowing {len(results)} of {result_count} "
                        f"result(s) (offset {args.offset})."
                    )

                print(
                    f"\n{data.get('pass_count', 0)} passed, "
                    f"{data.get('warn_count', 0)} warned, "
                    f"{data.get('fail_count', 0)} failed"
                )

        if data.get("fail_count", 0) > 0:
            sys.exit(2)
        elif data.get("warn_count", 0) > 0:
            sys.exit(1)
    elif args.command == "requirements-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        requirements_preview_body = {"notebook_path": args.filename}
        if args.version_id:
            requirements_preview_body["version_id"] = args.version_id

        try:
            response = httpx.post(
                f"{dashboard_url}/api/requirements-preview",
                json=requirements_preview_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            requirements = data.get("requirements", [])
            explicit_requirements = set(data.get("explicit_requirements", []))
            excluded_imports = data.get("excluded_imports", [])

            target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )
            print(f"requirements.txt preview for {target} on {dashboard_url}:\n")

            for dep in requirements:
                suffix = "  (explicit)" if dep in explicit_requirements else ""
                print(f"  {dep}{suffix}")

            if excluded_imports:
                print("\nExcluded imports (opted out of requirements.txt):")
                for name in excluded_imports:
                    print(f"  {name}")
    elif args.command == "app-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)

        app_preview_body = {
            "notebook_path": args.filename,
            "only": only,
            "exclude": exclude,
        }
        if args.version_id:
            app_preview_body["version_id"] = args.version_id

        try:
            response = httpx.post(
                f"{dashboard_url}/api/app-preview",
                json=app_preview_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )
            print(
                f"app.py preview for {target} on {dashboard_url} "
                f"(package '{data.get('package_name')}'):\n"
            )
            print(data.get("app_code", ""))

    elif args.command == "readme-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)

        readme_preview_body = {
            "notebook_path": args.filename,
            "only": only,
            "exclude": exclude,
        }
        if args.version_id:
            readme_preview_body["version_id"] = args.version_id

        try:
            response = httpx.post(
                f"{dashboard_url}/api/readme-preview",
                json=readme_preview_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )
            print(
                f"README.md preview for {target} on {dashboard_url} "
                f"(package '{data.get('package_name')}'):\n"
            )
            print(data.get("readme", ""))

    elif args.command == "openapi-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)

        openapi_preview_body = {
            "notebook_path": args.filename,
            "only": only,
            "exclude": exclude,
        }
        if args.version_id:
            openapi_preview_body["version_id"] = args.version_id

        try:
            response = httpx.post(
                f"{dashboard_url}/api/openapi-preview",
                json=openapi_preview_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )
            print(
                f"OpenAPI schema preview for {target} on {dashboard_url} "
                f"(package '{data.get('package_name')}'):\n"
            )
            print(json.dumps(data.get("schema", {}), indent=2))

    elif args.command == "curl-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        curl_preview_body = {
            "notebook_path": args.filename,
            "host": args.host,
            "port": args.port,
            "api_key": args.api_key,
        }
        if args.version_id:
            curl_preview_body["version_id"] = args.version_id
        if args.callback_url:
            curl_preview_body["callback_url"] = args.callback_url

        try:
            response = httpx.post(
                f"{dashboard_url}/api/curl-preview",
                json=curl_preview_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            commands = data.get("commands", [])

            target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )
            print(f"curl preview for {target} on {dashboard_url}:\n")

            if not commands:
                print("No endpoints would be generated for this notebook.")
            else:
                print("\n\n".join(commands))
    elif args.command == "postman-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        only = _parse_comma_separated_names(args.only)
        exclude = _parse_comma_separated_names(args.exclude)

        postman_preview_body = {
            "notebook_path": args.filename,
            "host": args.host,
            "port": args.port,
            "api_key": args.api_key,
            "only": only,
            "exclude": exclude,
            "collection_name": args.collection_name,
        }
        if args.version_id:
            postman_preview_body["version_id"] = args.version_id
        if args.callback_url:
            postman_preview_body["callback_url"] = args.callback_url

        try:
            response = httpx.post(
                f"{dashboard_url}/api/postman-preview",
                json=postman_preview_body,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            collection = data.get("collection", {})
            items = collection.get("item", [])

            target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )
            print(f"Postman collection preview for {target} on {dashboard_url}:\n")

            if not items:
                print("No endpoints would be generated for this notebook.")
            else:
                for item in items:
                    print(f"- {item.get('name')}")
                print(
                    f"\n{len(items)} request(s) total. Pass --json to get "
                    "the full collection document."
                )
    elif args.command == "dockerfile-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {}
        if args.filename:
            params["notebook_path"] = args.filename
        if args.version_id:
            params["version_id"] = args.version_id

        try:
            response = httpx.get(
                f"{dashboard_url}/api/dockerfile-preview",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            target = (
                f"'{data['notebook']}' on {dashboard_url}"
                if data.get("notebook") else dashboard_url
            )
            print(
                f"Dockerfile preview for {target} "
                f"(package '{data.get('package_name')}', "
                f"Python {data.get('compiling_python_version')}):\n"
            )
            print(data.get("dockerfile", ""))
            print(".dockerignore:\n")
            print(data.get("dockerignore", ""))
    elif args.command == "docker-compose-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            response = httpx.get(
                f"{dashboard_url}/api/docker-compose-preview",
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            print(
                f"docker-compose.yml preview for {dashboard_url} "
                f"(package '{data.get('package_name')}'):\n"
            )
            print(data.get("docker_compose", ""))
    elif args.command == "k8s-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {}
        if args.image:
            params["image"] = args.image

        try:
            response = httpx.get(
                f"{dashboard_url}/api/k8s-preview",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            print(
                f"kubernetes.yaml preview for {dashboard_url} "
                f"(package '{data.get('package_name')}', "
                f"image '{data.get('image')}'):\n"
            )
            print(data.get("kubernetes_manifest", ""))
    elif args.command == "env-example-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            response = httpx.get(
                f"{dashboard_url}/api/env-example-preview",
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            print(f".env.example preview for {dashboard_url}:\n")
            print(data.get("env_example", ""))
    elif args.command == "env-vars-preview":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            response = httpx.get(
                f"{dashboard_url}/api/env-vars-preview",
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            print(f"Environment variables recognized by a compiled app on {dashboard_url}:\n")
            for env_var in data.get("environment_variables", []):
                print(f"{env_var['name']} (default: {env_var['default']!r})")
                print(f"  {env_var['description']}\n")
    elif args.command == "remote-build":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            response = httpx.get(
                f"{dashboard_url}/api/download", timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        bundle_sha256 = response.headers.get("X-Bundle-SHA256")

        if args.expected_sha256 and args.expected_sha256 != bundle_sha256:

            raise RuntimeError(
                f"Downloaded bundle's sha256 ({bundle_sha256}) does not "
                f"match the expected value ({args.expected_sha256}) -- "
                "nothing was written to disk."
            )

        output_path = args.output or _filename_from_content_disposition(
            response, "generated.zip"
        )

        with open(output_path, "wb") as f:
            f.write(response.content)

        # "X-Notebook-Changed-Since-Compile" (added alongside this same
        # warning, not a separate change) reports the identical staleness
        # POST /api/deploy already checks before building -- without
        # reading it back here, a caller had no way to tell the zip they
        # just downloaded no longer matches the source notebook's current
        # content short of a separate GET /api/notebooks call to check
        # the currently-compiled entry's own "notebook_changed_since_compile"
        # field themselves.
        is_stale = response.headers.get("X-Notebook-Changed-Since-Compile") == "true"

        if args.json_output:
            print(json.dumps(
                {
                    "status": "success",
                    "path": output_path,
                    "size_bytes": len(response.content),
                    "notebook_changed_since_compile": is_stale,
                    "bundle_sha256": bundle_sha256,
                },
                indent=2,
            ))
        else:
            print(
                f"Downloaded the compiled app from {dashboard_url} to "
                f"{output_path} ({len(response.content)} bytes)"
            )
            if bundle_sha256:
                print(f"  bundle sha256: {bundle_sha256}")
            if is_stale:
                print(
                    "  warning: the source notebook has changed since "
                    "this app was compiled -- run `remote-compile` again "
                    "to refresh it."
                )
    elif args.command == "versions":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.versions_command == "list":

            params = {"offset": args.offset}
            if args.limit is not None:
                params["limit"] = args.limit
            if args.format == "csv":
                params["format"] = "csv"
            if args.saved_after:
                params["saved_after"] = args.saved_after
            if args.saved_before:
                params["saved_before"] = args.saved_before
            if args.checksums:
                params["checksums"] = "true"
            if args.notes:
                params["notes"] = "true"

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            if args.format == "csv":
                # The response is CSV, not JSON -- printed as-is, the
                # same "redirect it to a file" convention `find-
                # duplicates --format csv` already establishes.
                print(response.text, end="")
                return

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                versions = data.get("versions", [])

                if not versions:
                    print(f"No saved versions for '{args.filename}'.")
                else:
                    for version in versions:
                        checksum_note = (
                            f"  sha256:{version['sha256']}" if args.checksums else ""
                        )
                        # Matches `versions note-get`'s own "(no note)"
                        # placeholder for a version that's never had one
                        # set -- rather than printing a bare, easy-to-miss
                        # empty string after the version's own summary.
                        note_suffix = (
                            f"  note: {version.get('note') or '(no note)'}"
                            if args.notes else ""
                        )
                        print(
                            f"{version['version_id']}  "
                            f"({version['size_bytes']} bytes, "
                            f"saved {version['saved_at']}){checksum_note}"
                            f"{note_suffix}"
                        )

                    total_count = data.get("total_count")
                    if total_count is not None and total_count != len(versions):
                        print(f"\n{len(versions)} of {total_count} total version(s) shown")

        elif args.versions_command == "get":

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/{args.version_id}",
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            sha256 = response.headers.get("x-content-sha256")

            if args.expected_sha256 and args.expected_sha256 != sha256:

                raise RuntimeError(
                    f"Downloaded content's sha256 ({sha256}) does not "
                    f"match the expected value ({args.expected_sha256}) "
                    "-- nothing was written to disk."
                )

            output_path = args.output or args.version_id

            with open(output_path, "wb") as f:
                f.write(response.content)

            if args.json_output:
                print(json.dumps(
                    {
                        "status": "success",
                        "filename": args.filename,
                        "version_id": args.version_id,
                        "path": output_path,
                        "size_bytes": len(response.content),
                        "sha256": sha256,
                    },
                    indent=2,
                ))
            else:
                print(
                    f"Downloaded version '{args.version_id}' of "
                    f"'{args.filename}' from {dashboard_url} to "
                    f"{output_path} ({len(response.content)} bytes)"
                )
                if sha256:
                    print(f"  sha256: {sha256}")

        elif args.versions_command == "note-get":

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/{args.version_id}/note",
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                note = data.get("note", "")
                print(
                    f"{args.filename} version '{args.version_id}': "
                    f"{note if note else '(no note)'}"
                )

        elif args.versions_command == "note-set":

            try:
                response = httpx.put(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/{args.version_id}/note",
                    json={"note": args.note},
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                note = data.get("note", "")
                print(
                    f"{args.filename} version '{args.version_id}' note set "
                    f"to: {note if note else '(cleared)'}"
                )

        elif args.versions_command == "note-batch":

            entries = []

            for raw_entry in args.entry:

                if "=" not in raw_entry:
                    raise RuntimeError(
                        f"Invalid --entry '{raw_entry}' -- expected "
                        "VERSION_ID=NOTE (an empty right-hand side clears "
                        "that version's note)."
                    )

                version_id, _, note = raw_entry.partition("=")

                entries.append({"version_id": version_id, "note": note})

            body = {"entries": entries}
            if args.dry_run:
                body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/note-batch",
                    json=body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "would be set to" if data.get("dry_run") else "set to"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        note = result.get("note", "")
                        print(
                            f"{args.filename} version '{result['version_id']}' "
                            f"note {verb}: {note if note else '(cleared)'}"
                        )
                    else:
                        print(
                            f"Failed to set note for version "
                            f"'{result['version_id']}': {result['detail']}"
                        )

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

        elif args.versions_command == "inspect":

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}"
                    f"/versions/{args.version_id}/inspect",
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                print(
                    f"Inspecting version '{args.version_id}' of "
                    f"'{args.filename}' on {dashboard_url}"
                )

                reserved_name_conflicts = data.get("reserved_name_conflicts", [])

                if reserved_name_conflicts:
                    print("\n✗ Reserved name conflicts (compilation will fail):")
                    for name in reserved_name_conflicts:
                        print(f"  - {name}")

                endpoints = data.get("endpoints", [])

                if endpoints:
                    print(f"\n{len(endpoints)} endpoint(s):")
                    for endpoint in endpoints:
                        marker = "  [background]" if endpoint.get("is_async") else ""
                        print(f"  {endpoint['method']} {endpoint['path']}{marker}")

                skipped_functions = data.get("skipped_functions", [])

                if skipped_functions:
                    print(f"\n{len(skipped_functions)} skipped function(s):")
                    for skipped in skipped_functions:
                        print(f"  - {skipped['name']}: {skipped['reason']}")

                dependencies = data.get("dependencies", [])

                if dependencies:
                    print(f"\nDependencies: {', '.join(dependencies)}")

        elif args.versions_command == "export":

            params = {}
            if args.version_ids:
                params["version_ids"] = ",".join(args.version_ids)

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/export",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            bundle_sha256 = response.headers.get("X-Bundle-SHA256")

            if args.expected_sha256 and args.expected_sha256 != bundle_sha256:

                raise RuntimeError(
                    f"Exported bundle's sha256 ({bundle_sha256}) does not "
                    f"match the expected value ({args.expected_sha256}) -- "
                    "nothing was written to disk."
                )

            output_path = args.output or _filename_from_content_disposition(
                response, f"{args.filename}.versions.zip"
            )

            with open(output_path, "wb") as f:
                f.write(response.content)

            if args.json_output:
                print(json.dumps(
                    {
                        "status": "success",
                        "filename": args.filename,
                        "path": output_path,
                        "size_bytes": len(response.content),
                        "bundle_sha256": bundle_sha256,
                    },
                    indent=2,
                ))
            else:
                print(
                    f"Exported '{args.filename}' and its version history "
                    f"from {dashboard_url} to {output_path} "
                    f"({len(response.content)} bytes)"
                )
                if bundle_sha256:
                    print(f"  bundle sha256: {bundle_sha256}")

        elif args.versions_command == "import":

            params = {"overwrite": args.overwrite}
            if args.expected_sha256:
                params["expected_sha256"] = args.expected_sha256

            try:

                with open(args.zip_path, "rb") as f:

                    response = httpx.post(
                        f"{dashboard_url}/api/notebooks/{args.filename}/versions/import",
                        params=params,
                        files={
                            "file": (
                                os.path.basename(args.zip_path), f, "application/zip",
                            )
                        },
                        timeout=args.timeout,
                    )

            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the import ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                print(
                    f"Restored '{data.get('filename', args.filename)}' "
                    f"(overwritten: {data.get('overwritten')}) with "
                    f"{data.get('imported_version_count', 0)} version(s) "
                    f"on {dashboard_url}"
                )

        elif args.versions_command == "copy":

            versions_copy_body = {
                "new_filename": args.new_filename,
                "overwrite": args.overwrite,
            }
            if args.tags:
                versions_copy_body["tags"] = _parse_comma_separated_names(args.tags)
            if args.description is not None:
                versions_copy_body["description"] = args.description
            if args.dry_run:
                versions_copy_body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/{args.filename}"
                    f"/versions/{args.version_id}/copy",
                    json=versions_copy_body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would copy" if data.get("dry_run") else "Copied"

                print(
                    f"{verb} version '{args.version_id}' of '{args.filename}' "
                    f"to '{data.get('new_filename', args.new_filename)}' "
                    f"on {dashboard_url}"
                )

        elif args.versions_command == "copy-batch":

            entries = [
                {**entry, "overwrite": args.overwrite} for entry in args.entry
            ]

            body = {"entries": entries}
            if args.dry_run:
                body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/copy-batch",
                    json=body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would copy" if data.get("dry_run") else "Copied"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        print(
                            f"{verb} version '{result['version_id']}' to "
                            f"'{result['new_filename']}'"
                        )
                    else:
                        print(
                            f"Failed to copy version '{result['version_id']}' "
                            f"to '{result['new_filename']}': {result['detail']}"
                        )

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

        elif args.versions_command == "restore":

            params = {"dry_run": "true"} if args.dry_run else {}

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/{args.version_id}/restore",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would restore" if data.get("dry_run") else "Restored"

                print(
                    f"{verb} '{data.get('filename', args.filename)}' to "
                    f"version '{data.get('restored_version_id', args.version_id)}' "
                    f"on {dashboard_url}"
                )
                if data.get("was_currently_compiled"):
                    print("  note: this was the notebook backing the currently compiled app.")

        elif args.versions_command == "restore-batch":

            body = {"entries": args.entry}
            if args.dry_run:
                body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/versions/restore-batch",
                    json=body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would restore" if data.get("dry_run") else "Restored"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        print(
                            f"{verb} '{result['filename']}' to version "
                            f"'{result['version_id']}'"
                        )
                        if result.get("was_currently_compiled"):
                            print("  note: this was the notebook backing the currently compiled app.")
                    else:
                        print(
                            f"Failed to restore '{result['filename']}' to "
                            f"version '{result['version_id']}': {result['detail']}"
                        )

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

        elif args.versions_command == "diff":
            # See `remote-diff` above for why these are imported here
            # rather than at module scope.
            import tempfile

            def _fetch_version_content(version_id):
                """GET one version_id's raw content (or, for version_id
                None, the notebook's own current live content) --
                shared so the "old" and "against"/current sides below go
                through identical request/error handling.
                """
                url = (
                    f"{dashboard_url}/api/notebooks/{args.filename}"
                    if version_id is None
                    else f"{dashboard_url}/api/notebooks/{args.filename}/versions/{version_id}"
                )

                try:
                    fetch_response = httpx.get(url, timeout=args.timeout)
                except httpx.HTTPError as exc:
                    raise _dashboard_connection_error(exc, dashboard_url)

                if fetch_response.status_code >= 400:

                    raise RuntimeError(
                        f"Dashboard rejected the request "
                        f"({fetch_response.status_code}): "
                        f"{_extract_dashboard_error_detail(fetch_response)}"
                    )

                return fetch_response.content

            old_content = _fetch_version_content(args.version_id)
            new_content = _fetch_version_content(args.against)

            # Both sides downloaded to real temp files, not held in memory
            # and parsed some other way, so diff_notebook_functions can
            # reuse its own existing load_notebook(path)-based pipeline
            # unchanged -- the same reasoning `remote-diff` above already
            # applies to its own single downloaded side.
            old_fd, old_path = tempfile.mkstemp(suffix=".ipynb")
            new_fd, new_path = tempfile.mkstemp(suffix=".ipynb")

            try:

                with os.fdopen(old_fd, "wb") as f:
                    f.write(old_content)

                with os.fdopen(new_fd, "wb") as f:
                    f.write(new_content)

                diff = diff_notebook_functions(old_path, new_path)
                diff.update(classify_notebook_diff(diff))

                if args.content:
                    # Same old_label/new_label convention GET
                    # .../versions/{version_id}/diff's own "content"
                    # already uses server-side (routes/upload.py) --
                    # old_path/new_path are meaningless local temp files,
                    # not something a reader of this diff's header would
                    # recognize.
                    diff["content_diff"] = diff_notebook_source(
                        old_path, new_path,
                        old_label=f"version '{args.version_id}'",
                        new_label=(
                            f"version '{args.against}'" if args.against
                            else f"the current live content of '{args.filename}'"
                        ),
                    )

            finally:
                os.remove(old_path)
                os.remove(new_path)

            if args.json_output:
                print(json.dumps(diff, indent=2))
            else:

                against_label = (
                    f"version '{args.against}'" if args.against
                    else f"'{args.filename}''s current live content"
                )

                print(
                    f"Comparing version '{args.version_id}' of "
                    f"'{args.filename}' against {against_label} on "
                    f"{dashboard_url}"
                )
                print_notebook_diff(diff)

                if args.content and diff["content_diff"]:
                    print("\n" + "\n".join(diff["content_diff"]))

            if args.fail_on_breaking and not diff["compatible"]:
                sys.exit(1)

        elif args.versions_command == "compare":

            compare_params = {}
            if args.against:
                compare_params["against"] = args.against
            if args.content:
                compare_params["content"] = "true"

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/notebooks/{args.filename}"
                    f"/versions/{args.version_id}/diff",
                    params=compare_params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                against_label = (
                    f"version '{args.against}'" if args.against
                    else f"'{args.filename}''s current live content"
                )

                print(
                    f"Comparing version '{args.version_id}' of "
                    f"'{args.filename}' against {against_label} on "
                    f"{dashboard_url}"
                )
                print_notebook_diff(data)

                if args.content and data.get("content_diff"):
                    print("\n" + "\n".join(data["content_diff"]))

            if args.fail_on_breaking and not data.get("compatible", True):
                sys.exit(1)

        elif args.versions_command == "delete":

            if not args.dry_run and not args.yes:
                # DELETE /api/notebooks/{filename}/versions/{version_id}
                # (routes/upload.py) has no confirmation step of its own
                # and is irreversible -- the same reasoning `delete`'s own
                # single-filename path above already applies. Not asked
                # at all under --dry-run, which never discards anything.
                answer = input(
                    f"Permanently delete version '{args.version_id}' of "
                    f"'{args.filename}' from {dashboard_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            params = {"dry_run": "true"} if args.dry_run else {}

            try:
                response = httpx.delete(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/{args.version_id}",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would delete" if data.get("dry_run") else "Deleted"

                print(
                    f"{verb} version "
                    f"'{data.get('deleted_version_id', args.version_id)}' of "
                    f"'{data.get('filename', args.filename)}' on {dashboard_url}"
                )

        elif args.versions_command == "delete-batch":

            if not args.dry_run and not args.yes:
                # POST /api/notebooks/{filename}/versions/delete-batch
                # (routes/upload.py) has no confirmation step of its own
                # and is irreversible -- the same reasoning `versions
                # delete` above already applies. Not asked at all under
                # --dry-run, which never deletes anything.
                version_ids_display = ", ".join(args.version_id)
                answer = input(
                    f"Permanently delete version(s) {version_ids_display} "
                    f"of '{args.filename}' from {dashboard_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            body = {"version_ids": args.version_id}
            if args.dry_run:
                body["dry_run"] = True

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions/delete-batch",
                    json=body,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                verb = "Would delete" if data.get("dry_run") else "Deleted"

                for result in data.get("results", []):

                    if result["status"] == "success":
                        print(f"{verb} version '{result['version_id']}'")
                    else:
                        print(
                            f"Failed to delete version '{result['version_id']}': "
                            f"{result['detail']}"
                        )

                print(
                    f"\n{data.get('succeeded_count', 0)} succeeded, "
                    f"{data.get('failed_count', 0)} failed"
                )

        else:  # args.versions_command == "clear"

            age_clause = (
                f" older than {args.older_than_days} day(s)" if args.older_than_days
                else ""
            )

            if not args.dry_run and not args.yes:
                # DELETE /api/notebooks/{filename}/versions
                # (routes/upload.py) has no confirmation step of its own
                # and is irreversible -- the same reasoning `versions
                # delete` above already applies. Not asked at all under
                # --dry-run, which never deletes anything.
                answer = input(
                    f"Permanently delete every version{age_clause} of "
                    f"'{args.filename}' from {dashboard_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            params = {"dry_run": True} if args.dry_run else {}
            if args.older_than_days:
                params["older_than_days"] = args.older_than_days

            try:
                response = httpx.delete(
                    f"{dashboard_url}/api/notebooks/{args.filename}/versions",
                    params=params,
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                deleted_count = data.get("deleted_count", 0)

                if not deleted_count:
                    print(f"'{args.filename}' has no version history on {dashboard_url}.")
                else:
                    verb = "Would delete" if data.get("dry_run") else "Deleted"
                    print(
                        f"{verb} {deleted_count} version(s) of "
                        f"'{data.get('filename', args.filename)}' on {dashboard_url}"
                    )
    elif args.command == "remote-files":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.remote_files_command == "list":

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/generated",
                    params={"checksums": "true"} if args.checksums else {},
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:

                file_details = data.get("file_details", [])

                if not file_details:
                    print("No compiled app found on the dashboard.")
                else:
                    for entry in file_details:
                        checksum_note = (
                            f"  sha256:{entry['sha256']}" if args.checksums else ""
                        )
                        print(
                            f"{entry['filename']}  "
                            f"({entry['size_bytes']} bytes, "
                            f"modified {entry['modified_at']}){checksum_note}"
                        )

                    if args.checksums:
                        print(f"\nBundle sha256: {data.get('bundle_sha256')}")

                    source = data.get("source_notebook_filename")

                    if source:
                        exists_note = (
                            "" if data.get("source_notebook_exists")
                            else "  [no longer uploaded]"
                        )
                        version_note = (
                            f" (version '{data['compiled_version_id']}')"
                            if data.get("compiled_version_id") else ""
                        )
                        print(f"\nCompiled from: {source}{version_note}{exists_note}")

                    if data.get("generated_files_modified_since_compile"):
                        print(
                            "\nwarning: the compiled output itself has been "
                            "modified since the last compile (app.py, "
                            "requirements.txt, Dockerfile, ... no longer "
                            "match what that compile actually produced)."
                        )

        elif args.remote_files_command == "get":

            try:
                response = httpx.get(
                    f"{dashboard_url}/api/generated/{args.filename}",
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()
            content = data.get("content", "")
            sha256 = data.get("sha256")

            if args.expected_sha256 and args.expected_sha256 != sha256:

                raise RuntimeError(
                    f"Fetched content's sha256 ({sha256}) does not match "
                    f"the expected value ({args.expected_sha256}) -- "
                    "nothing was written to disk."
                )

            if args.output:

                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(content)

            if args.json_output:
                print(json.dumps(data, indent=2))
            elif args.output:
                print(f"Saved '{args.filename}' from {dashboard_url} to {args.output}")
                if sha256:
                    print(f"  sha256: {sha256}")
            else:
                print(content, end="" if content.endswith("\n") else "\n")

        else:  # args.remote_files_command == "delete"

            if not args.yes:
                # DELETE /api/generated (routes/upload.py) has no
                # confirmation step of its own and is irreversible -- the
                # same reasoning as `delete`'s own confirmation prompt for
                # an uploaded notebook.
                answer = input(
                    f"Delete the compiled app on {dashboard_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            try:
                response = httpx.delete(
                    f"{dashboard_url}/api/generated", timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                print(f"Deleted the compiled app on {dashboard_url}.")
    elif args.command == "remote-diff":
        # See `upload` above for why these are imported here rather than
        # at module scope.
        import httpx
        import tempfile

        dashboard_url = args.dashboard_url.rstrip("/")
        local_notebook_path = args.notebook or args.filename

        remote_url = (
            f"{dashboard_url}/api/notebooks/{args.filename}"
            f"/versions/{args.version_id}" if args.version_id
            else f"{dashboard_url}/api/notebooks/{args.filename}"
        )

        try:
            response = httpx.get(remote_url, timeout=args.timeout)
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        # Same content-integrity check `remote-curl`/`remote-postman`
        # (and `download`/`versions get`/`remote-files get`) already
        # perform before writing/using anything -- applied here before
        # diff_notebook_functions ever reads the downloaded bytes.
        sha256 = response.headers.get("x-content-sha256")

        if args.expected_sha256 and args.expected_sha256 != sha256:

            raise RuntimeError(
                f"Downloaded content's sha256 ({sha256}) does not match "
                f"the expected value ({args.expected_sha256}) -- nothing "
                "was diffed."
            )

        # Downloaded to a real temp file, not held in memory and parsed
        # some other way, so diff_notebook_functions can reuse its own
        # existing load_notebook(path)-based pipeline unchanged -- the
        # same one `diff` itself already runs on two local paths -- with
        # no separate "diff bytes in memory" code path to keep in sync
        # with it.
        remote_notebook_fd, remote_notebook_path = tempfile.mkstemp(suffix=".ipynb")

        try:

            with os.fdopen(remote_notebook_fd, "wb") as f:
                f.write(response.content)

            # The dashboard's own copy of `filename` is "old", the local
            # file is "new" -- diff_notebook_functions' own "added"/
            # "removed"/"changed" then read the same direction `upload
            # --overwrite` would move the world in: what an overwrite is
            # about to change on the dashboard, not the reverse.
            diff = diff_notebook_functions(remote_notebook_path, local_notebook_path)
            diff.update(classify_notebook_diff(diff))

            remote_target = (
                f"'{args.filename}' version '{args.version_id}'" if args.version_id
                else f"'{args.filename}'"
            )

            if args.content:
                # Labeled, not left to diff_notebook_source's own
                # "default to the path itself" fallback -- unlike `diff`'s
                # own two caller-given paths, remote_notebook_path is a
                # meaningless local temp file, not something a reader of
                # this diff's header would recognize.
                diff["content_diff"] = diff_notebook_source(
                    remote_notebook_path, local_notebook_path,
                    old_label=f"{remote_target} on {dashboard_url}",
                    new_label=local_notebook_path,
                )

        finally:
            os.remove(remote_notebook_path)

        if args.json_output:
            print(json.dumps(diff, indent=2))
        else:
            print(
                f"Comparing local '{local_notebook_path}' against "
                f"{remote_target} on {dashboard_url}"
            )
            print_notebook_diff(diff)
            if args.content and diff["content_diff"]:
                print("\n" + "\n".join(diff["content_diff"]))
        if args.fail_on_breaking and not diff["compatible"]:
            sys.exit(1)
    elif args.command == "diff-notebooks":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {"old": args.old, "new": args.new}
        if args.old_version:
            params["old_version"] = args.old_version
        if args.new_version:
            params["new_version"] = args.new_version
        if args.content:
            params["content"] = "true"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/notebooks/diff",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            old_label = (
                f"'{args.old}' version '{args.old_version}'" if args.old_version
                else f"'{args.old}'"
            )
            new_label = (
                f"'{args.new}' version '{args.new_version}'" if args.new_version
                else f"'{args.new}'"
            )
            print(f"Comparing {old_label} against {new_label} on {dashboard_url}")
            print_notebook_diff(data)

            if args.content and data.get("content_diff"):
                print("\n" + "\n".join(data["content_diff"]))

        if args.fail_on_breaking and not data.get("compatible", True):
            sys.exit(1)
    elif args.command == "remote-curl":
        # See `upload` above for why these are imported here rather than
        # at module scope.
        import httpx
        import tempfile

        dashboard_url = args.dashboard_url.rstrip("/")

        # Fetching a specific version's own raw bytes (GET
        # .../versions/{version_id}) instead of the notebook's current
        # content (GET /api/notebooks/{filename}) is the only difference
        # --version-id makes here -- everything below (the temp file,
        # generate_curl_commands, the written script) is otherwise
        # identical regardless of which one was actually fetched.
        fetch_url = (
            f"{dashboard_url}/api/notebooks/{args.filename}/versions/{args.version_id}"
            if args.version_id
            else f"{dashboard_url}/api/notebooks/{args.filename}"
        )

        try:
            response = httpx.get(fetch_url, timeout=args.timeout)
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        # Same content-integrity check `download`/`versions get`/
        # `remote-files get` already perform before writing anything --
        # applied here before generate_curl_commands ever reads the
        # downloaded bytes, not just before the script is written, so a
        # mismatch is caught even under --json (which never touches disk
        # on its own but still runs the notebook through the parser).
        sha256 = response.headers.get("x-content-sha256")

        if args.expected_sha256 and args.expected_sha256 != sha256:

            raise RuntimeError(
                f"Downloaded content's sha256 ({sha256}) does not match "
                f"the expected value ({args.expected_sha256}) -- nothing "
                "was written."
            )

        # Downloaded to a real temp file, not held in memory and parsed
        # some other way, so generate_curl_commands can reuse its own
        # existing load_notebook(path)-based pipeline (via
        # inspect_notebook_data) unchanged -- the same "fetch to a temp
        # file" approach `remote-diff` above already applies to
        # diff_notebook_functions.
        remote_notebook_fd, remote_notebook_path = tempfile.mkstemp(suffix=".ipynb")

        try:

            with os.fdopen(remote_notebook_fd, "wb") as f:
                f.write(response.content)

            only = _parse_comma_separated_names(args.only)
            exclude = _parse_comma_separated_names(args.exclude)

            commands = generate_curl_commands(
                remote_notebook_path, host=args.host, port=args.port,
                api_key=args.api_key, only=only, exclude=exclude,
                callback_url=args.callback_url,
            )

        finally:
            os.remove(remote_notebook_path)

        source_label = (
            f"'{args.filename}' version '{args.version_id}'" if args.version_id
            else f"'{args.filename}'"
        )

        script_content = (
            "#!/bin/sh\n"
            f"# Generated by notebook-to-api remote-curl from "
            f"{source_label} on {dashboard_url}.\n"
            f'# Uses the API key "{args.api_key}" via the X-API-Key header --\n'
            "# pass --api-key here to match whatever NOTEBOOK_API_KEY is set\n"
            "# to on the server actually running the compiled app, if it's\n"
            "# not the default.\n\n"
            + "\n\n".join(commands)
            + "\n"
        )

        output = args.output or "requests.sh"

        with open(output, "w", encoding="utf-8") as f:
            f.write(script_content)

        # Best-effort: makes the script directly runnable (`./requests.sh`)
        # on a POSIX shell without an extra `chmod +x` step -- the same
        # convenience `export-curl` already applies to its own output.
        try:
            os.chmod(output, os.stat(output).st_mode | 0o111)
        except OSError:
            pass

        if args.json_output:
            print(json.dumps(
                {"status": "success", "path": output, "commands": commands},
                indent=2,
            ))
        else:
            print(
                f"cURL script for {source_label} on {dashboard_url} "
                f"written to: {output} ({len(commands)} request(s))"
            )
    elif args.command == "remote-postman":
        # See `upload` above for why these are imported here rather than
        # at module scope.
        import httpx
        import tempfile

        dashboard_url = args.dashboard_url.rstrip("/")

        # Same reasoning as `remote-curl` above: fetching a specific
        # version's own raw bytes (GET .../versions/{version_id}) instead
        # of the notebook's current content (GET /api/notebooks/{filename})
        # is the only difference --version-id makes here.
        fetch_url = (
            f"{dashboard_url}/api/notebooks/{args.filename}/versions/{args.version_id}"
            if args.version_id
            else f"{dashboard_url}/api/notebooks/{args.filename}"
        )

        try:
            response = httpx.get(fetch_url, timeout=args.timeout)
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        # Same content-integrity check `remote-curl` above (and
        # `download`/`versions get`/`remote-files get`) already perform
        # before writing anything -- applied here before
        # generate_postman_collection ever reads the downloaded bytes.
        sha256 = response.headers.get("x-content-sha256")

        if args.expected_sha256 and args.expected_sha256 != sha256:

            raise RuntimeError(
                f"Downloaded content's sha256 ({sha256}) does not match "
                f"the expected value ({args.expected_sha256}) -- nothing "
                "was written."
            )

        # Downloaded to a real temp file, not held in memory and parsed
        # some other way, so generate_postman_collection can reuse its
        # own existing load_notebook(path)-based pipeline (via
        # inspect_notebook_data) unchanged -- the same "fetch to a temp
        # file" approach `remote-curl` above already applies to
        # generate_curl_commands.
        remote_notebook_fd, remote_notebook_path = tempfile.mkstemp(suffix=".ipynb")

        try:

            with os.fdopen(remote_notebook_fd, "wb") as f:
                f.write(response.content)

            only = _parse_comma_separated_names(args.only)
            exclude = _parse_comma_separated_names(args.exclude)

            collection = generate_postman_collection(
                remote_notebook_path, host=args.host, port=args.port,
                api_key=args.api_key, only=only, exclude=exclude,
                collection_name=args.collection_name,
                callback_url=args.callback_url,
            )

        finally:
            os.remove(remote_notebook_path)

        source_label = (
            f"'{args.filename}' version '{args.version_id}'" if args.version_id
            else f"'{args.filename}'"
        )

        output = args.output or "postman_collection.json"

        with open(output, "w", encoding="utf-8") as f:
            json.dump(collection, f, indent=2)
            f.write("\n")

        if args.json_output:
            print(json.dumps(
                {"status": "success", "path": output, "collection": collection},
                indent=2,
            ))
        else:
            print(
                f"\nPostman collection for {source_label} on {dashboard_url} "
                f"written to: {output} ({len(collection['item'])} request(s))"
            )
    elif args.command == "remote-export":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if args.remote_export_command == "openapi":

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/export-openapi",
                    json={"format": args.format},
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()

            # POST /api/export-openapi (routes/upload.py) returns the
            # parsed schema under "schema" for format=json, but the raw
            # text under "content" for format=yaml -- json.dumps'ing the
            # already-parsed "schema" reconstitutes an equivalent JSON
            # document rather than requiring a caller to know which key
            # holds it for which format.
            content = (
                json.dumps(data["schema"], indent=2) if args.format == "json"
                else data.get("content", "")
            )

            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(content)

            if args.json_output:
                print(json.dumps(data, indent=2))
            elif args.output:
                print(
                    f"Saved the OpenAPI {args.format} export from "
                    f"{dashboard_url} to {args.output}"
                )
            else:
                print(content)

        else:  # args.remote_export_command == "sdk"

            try:
                response = httpx.post(
                    f"{dashboard_url}/api/export-sdk",
                    json={"language": args.language},
                    timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise _dashboard_connection_error(exc, dashboard_url)

            if response.status_code >= 400:

                raise RuntimeError(
                    f"Dashboard rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            data = response.json()
            code = data.get("code", "")

            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(code)

            if args.json_output:
                print(json.dumps(data, indent=2))
            elif args.output:
                print(
                    f"Saved the {args.language} SDK client from "
                    f"{dashboard_url} to {args.output}"
                )
            else:
                print(code)
    elif args.command == "remote-deploy":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        body = {"push": args.push, "force": args.force}

        if args.tag:
            body["tag"] = args.tag

        if args.platform:
            body["platform"] = args.platform

        if args.no_cache:
            body["no_cache"] = True

        if args.dry_run:
            body["dry_run"] = True

        if args.smoke_test:
            body["smoke_test"] = True

        try:
            response = httpx.post(
                f"{dashboard_url}/api/deploy", json=body, timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the deploy ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            verb = "Would build" if data.get("dry_run") else "Built"
            print(f"{verb} image '{data.get('tag')}' on {dashboard_url}")

            if data.get("pushed"):
                print(
                    "Would push to the registry." if data.get("dry_run")
                    else "Pushed to the registry."
                )

            smoke_test = data.get("smoke_test")

            if smoke_test is not None:
                if smoke_test["passed"]:
                    print("\nSmoke test: passed (GET /health responded 200)")
                else:
                    print(f"\nSmoke test: FAILED -- {smoke_test.get('detail')}")

        if data.get("smoke_test") is not None and not data["smoke_test"]["passed"]:
            sys.exit(1)
    elif args.command == "deploy-history":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {}

        if args.source_notebook_filename:
            params["source_notebook_filename"] = args.source_notebook_filename
        if args.source_notebook_sha256:
            params["source_notebook_sha256"] = args.source_notebook_sha256
        if args.platform:
            params["platform"] = args.platform
        if args.tag:
            params["tag"] = args.tag
        if args.pushed is not None:
            params["pushed"] = args.pushed
        if args.deployed_after:
            params["deployed_after"] = args.deployed_after
        if args.deployed_before:
            params["deployed_before"] = args.deployed_before
        if args.limit is not None:
            params["limit"] = args.limit
        if args.offset:
            params["offset"] = args.offset
        if args.format == "csv":
            params["format"] = "csv"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/deploy/history",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.format == "csv":
            # The response is CSV, not JSON -- printed as-is (redirect
            # stdout to a file to save it) rather than run through the
            # JSON/human-readable branches below, which both assume a
            # parseable JSON body.
            print(response.text, end="")
            return

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            entries = data.get("entries", [])

            if not entries:
                print(f"No deploys recorded on {dashboard_url} yet.")
            else:

                for entry in entries:

                    push_note = "pushed" if entry.get("pushed") else "not pushed"
                    source = entry.get("source_notebook_filename") or "(unknown source)"

                    print(
                        f"{entry.get('deployed_at')}  {entry.get('tag')}  "
                        f"({push_note}, from {source})"
                    )

                print(f"\n{data.get('entry_count', len(entries))} deploy(s) on {dashboard_url}")

    elif args.command == "clear-deploy-history":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if not args.dry_run and not args.yes:
            # DELETE /api/deploy/history (routes/upload.py) has no
            # confirmation step of its own and is irreversible -- the
            # same reasoning `prune-versions`/`tags delete` already
            # prompt for. Not asked at all under --dry-run, which never
            # deletes anything.
            target_parts = []
            if args.source_notebook_filename:
                target_parts.append(repr(args.source_notebook_filename))
            if args.source_notebook_sha256:
                target_parts.append(f"sha256 {args.source_notebook_sha256!r}")
            target = (
                f"the deploy history for {' and '.join(target_parts)} on "
                f"{dashboard_url}" if target_parts
                else f"the entire deploy history on {dashboard_url}"
            )
            answer = input(f"Permanently discard {target}? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return

        params = {}
        if args.source_notebook_filename:
            params["source_notebook_filename"] = args.source_notebook_filename
        if args.source_notebook_sha256:
            params["source_notebook_sha256"] = args.source_notebook_sha256
        if args.older_than_days is not None:
            params["older_than_days"] = args.older_than_days
        if args.deployed_after:
            params["deployed_after"] = args.deployed_after
        if args.deployed_before:
            params["deployed_before"] = args.deployed_before
        if args.dry_run:
            params["dry_run"] = True

        try:
            response = httpx.delete(
                f"{dashboard_url}/api/deploy/history",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            verb = "Would discard" if data.get("dry_run") else "Discarded"
            print(
                f"{verb} {data.get('deleted_count', 0)} deploy history "
                f"entr(y/ies) on {dashboard_url}"
            )

    elif args.command == "compile-history":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        params = {}

        if args.notebook_filename:
            params["notebook_filename"] = args.notebook_filename
        if args.source_notebook_sha256:
            params["source_notebook_sha256"] = args.source_notebook_sha256
        if args.compiled_after:
            params["compiled_after"] = args.compiled_after
        if args.compiled_before:
            params["compiled_before"] = args.compiled_before
        if args.limit is not None:
            params["limit"] = args.limit
        if args.offset:
            params["offset"] = args.offset
        if args.format == "csv":
            params["format"] = "csv"

        try:
            response = httpx.get(
                f"{dashboard_url}/api/compile/history",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.format == "csv":
            # The response is CSV, not JSON -- printed as-is (redirect
            # stdout to a file to save it) rather than run through the
            # JSON/human-readable branches below, which both assume a
            # parseable JSON body.
            print(response.text, end="")
            return

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:

            entries = data.get("entries", [])

            if not entries:
                print(f"No compiles recorded on {dashboard_url} yet.")
            else:

                for entry in entries:

                    endpoint_count = entry.get("endpoint_count", 0)
                    notebook = entry.get("notebook_filename") or "(unknown notebook)"

                    print(
                        f"{entry.get('compiled_at')}  {notebook}  "
                        f"({endpoint_count} endpoint(s))"
                    )

                print(f"\n{data.get('entry_count', len(entries))} compile(s) on {dashboard_url}")

    elif args.command == "clear-compile-history":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        if not args.dry_run and not args.yes:
            # DELETE /api/compile/history (routes/upload.py) has no
            # confirmation step of its own and is irreversible -- the
            # same reasoning `clear-deploy-history` already prompts for.
            # Not asked at all under --dry-run, which never deletes
            # anything.
            target_parts = []
            if args.notebook_filename:
                target_parts.append(repr(args.notebook_filename))
            if args.source_notebook_sha256:
                target_parts.append(f"sha256 {args.source_notebook_sha256!r}")
            target = (
                f"the compile history for {' and '.join(target_parts)} on "
                f"{dashboard_url}" if target_parts
                else f"the entire compile history on {dashboard_url}"
            )
            answer = input(f"Permanently discard {target}? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return

        params = {}
        if args.notebook_filename:
            params["notebook_filename"] = args.notebook_filename
        if args.source_notebook_sha256:
            params["source_notebook_sha256"] = args.source_notebook_sha256
        if args.older_than_days is not None:
            params["older_than_days"] = args.older_than_days
        if args.compiled_after:
            params["compiled_after"] = args.compiled_after
        if args.compiled_before:
            params["compiled_before"] = args.compiled_before
        if args.dry_run:
            params["dry_run"] = True

        try:
            response = httpx.delete(
                f"{dashboard_url}/api/compile/history",
                params=params,
                timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        data = response.json()

        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            verb = "Would discard" if data.get("dry_run") else "Discarded"
            print(
                f"{verb} {data.get('deleted_count', 0)} compile history "
                f"entr(y/ies) on {dashboard_url}"
            )

    elif args.command == "status":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        health_params = {"check_writable": "true"} if args.check_writable else {}

        try:
            health_response = httpx.get(
                f"{dashboard_url}/api/health", params=health_params, timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if health_response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({health_response.status_code}): "
                f"{_extract_dashboard_error_detail(health_response)}"
            )

        try:
            config_response = httpx.get(
                f"{dashboard_url}/api/config", timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if config_response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({config_response.status_code}): "
                f"{_extract_dashboard_error_detail(config_response)}"
            )

        health = health_response.json()
        config = config_response.json()

        if args.json_output:
            print(json.dumps({"health": health, "config": config}, indent=2))
        else:

            print(f"Dashboard at {dashboard_url}: {health.get('status')}")

            dashboard_version = health.get("version")

            if dashboard_version is not None:

                if dashboard_version == NOTEBOOK_TO_API_VERSION:
                    print(f"  version: {dashboard_version} (matches this CLI)")
                else:
                    print(
                        f"  version: {dashboard_version} "
                        f"(this CLI is {NOTEBOOK_TO_API_VERSION} -- mismatched)"
                    )

            if health.get("compiled_app_present"):
                version_note = (
                    f" (version '{health['compiled_version_id']}')"
                    if health.get("compiled_version_id") else ""
                )
                print(
                    f"  compiled app present, last compiled at "
                    f"{health.get('compiled_at')}{version_note}"
                )
                if health.get("generated_files_modified_since_compile"):
                    print(
                        "  warning: the compiled output itself has been "
                        "modified since the last compile (app.py, "
                        "requirements.txt, Dockerfile, ... no longer "
                        "match what that compile actually produced)."
                    )
            else:
                print("  no compiled app yet")

            if "upload_dir_writable" in health:
                writable = "yes" if health["upload_dir_writable"] else "NO"
                print(f"  upload directory writable: {writable}")

            if "generated_dir_writable" in health:
                writable = "yes" if health["generated_dir_writable"] else "NO"
                print(f"  generated directory writable: {writable}")

            print("\nConfigured limits:")
            print(f"  max upload size: {config.get('max_upload_bytes')} bytes")
            print(f"  max batch upload files: {config.get('max_batch_upload_files')}")
            max_notebooks = config.get('max_notebooks')
            print(f"  max notebooks: {max_notebooks if max_notebooks else 'unlimited'}")
            max_total_storage_bytes = config.get('max_total_storage_bytes')
            print(
                "  max total storage: "
                f"{f'{max_total_storage_bytes} bytes' if max_total_storage_bytes else 'unlimited'}"
            )
            print(f"  max notebook versions kept: {config.get('max_notebook_versions')}")
            print(f"  max tag length: {config.get('max_tag_length')}")
            print(f"  max tags per notebook: {config.get('max_tags_per_notebook')}")
            print(f"  max description length: {config.get('max_description_length')}")
            print(f"  max source url length: {config.get('max_source_url_length')}")
            print(f"  max search regex length: {config.get('max_search_regex_length')}")
            print(f"  max deploy history entries: {config.get('max_deploy_history_entries')}")
            print(f"  max compile history entries: {config.get('max_compile_history_entries')}")
            print(f"  deploy subprocess timeout: {config.get('deploy_subprocess_timeout_seconds')}s")
            print(f"  URL import timeout: {config.get('url_import_timeout_seconds')}s")
            print(f"  stale upload temp file threshold: {config.get('stale_upload_temp_file_seconds')}s")
            print(f"  notebook sort keys: {', '.join(config.get('notebook_sort_keys', []))}")
            print(f"  notebook sort orders: {', '.join(config.get('notebook_sort_orders', []))}")
            print(f"  allowed origins: {', '.join(config.get('allowed_origins', []))}")
            dashboard_rate_limit = config.get('dashboard_rate_limit_per_minute')
            print(
                "  dashboard rate limit: "
                f"{f'{dashboard_rate_limit} requests/minute per client' if dashboard_rate_limit else 'disabled'}"
            )
            print(f"\nCompiling Python version: {config.get('compiling_python_version')}")

    elif args.command == "metrics":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        dashboard_url = args.dashboard_url.rstrip("/")

        try:
            response = httpx.get(
                f"{dashboard_url}/api/metrics/prometheus", timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise _dashboard_connection_error(exc, dashboard_url)

        if response.status_code >= 400:

            raise RuntimeError(
                f"Dashboard rejected the request ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        if args.json_output:
            print(json.dumps(_parse_prometheus_text_metrics(response.text), indent=2))
        else:
            print(response.text, end="" if response.text.endswith("\n") else "\n")
    elif args.command == "app-status":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        app_url = f"http://{args.host}:{args.port}"

        def _app_get(path):
            try:
                response = httpx.get(f"{app_url}{path}", timeout=args.timeout)
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Could not reach the compiled app at {app_url}: "
                    f"{exc}. Is it running? (see `serve`, or `docker "
                    "compose up`)"
                )

            if response.status_code >= 400:
                raise RuntimeError(
                    f"App rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            return response.json()

        # Wrapped in its own function -- rather than inlined once, as it
        # was before --watch existed -- purely so --watch (below) can
        # call the exact same fetch-and-report logic on every poll
        # without duplicating it: a second, hand-copied print block here
        # could silently drift from this one over time the same way this
        # project's own generators (README/curl/Postman) already
        # confirmed happens to a duplicated "what does this app actually
        # do" description if it's ever allowed to.
        def _fetch_and_report_status():

            health = _app_get("/health")
            ready = _app_get("/ready")
            info = _app_get("/info")
            config = _app_get("/config")

            if args.json_output:
                result = {
                    "health": health, "ready": ready,
                    "info": info, "config": config,
                }
                if args.watch:
                    # One compact object per line (NDJSON), not
                    # indent=2's multi-line pretty-print -- a --watch
                    # consumer needs to tell where one poll's own JSON
                    # ends and the next begins by line alone, the same
                    # reason app-tasks list --json's own single-object
                    # response is never split across lines either.
                    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    print(json.dumps(result))
                else:
                    print(json.dumps(result, indent=2))
                return

            if args.watch:
                print(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S')} ---")

            print(f"Compiled app at {app_url}: {health.get('status')}")
            print(
                f"  ready: {ready.get('status')} "
                f"({ready.get('tasks_registered')} task(s) registered)"
            )
            print(
                f"  service: {info.get('service')} v{info.get('version')}"
            )
            print(
                f"  endpoints: {info.get('endpoint_count')} "
                f"({info.get('background_endpoint_count')} background)"
            )
            sha256 = info.get("source_notebook_sha256")
            if sha256:
                print(f"  source notebook sha256: {sha256}")

            print("\nConfigured limits:")
            print(f"  max request body: {config.get('max_request_body_bytes')} bytes")
            print(f"  task TTL: {config.get('task_ttl_seconds')}s")
            print(f"  max pending tasks: {config.get('max_pending_tasks')}")
            task_timeout = config.get('task_execution_timeout_seconds')
            print(f"  task execution timeout: {f'{task_timeout}s' if task_timeout else 'disabled'}")
            print(f"  webhook timeout: {config.get('webhook_timeout_seconds')}s")
            print(
                "  webhook signing: "
                f"{'enabled' if config.get('webhook_signing_enabled') else 'disabled'}"
            )
            print(f"  webhook max retries: {config.get('webhook_max_retries')}")
            rate_limit = config.get('rate_limit_per_minute')
            print(
                "  rate limit: "
                f"{f'{rate_limit} requests/minute per key' if rate_limit else 'disabled'}"
            )
            print(f"  allowed origins: {', '.join(config.get('allowed_origins', []))}")
            print(
                "  docs: "
                f"{'disabled' if config.get('disable_docs') else 'enabled'}"
            )

        if not args.watch:
            _fetch_and_report_status()
        else:
            try:
                while True:
                    _fetch_and_report_status()
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nStopped watching.")
    elif args.command == "app-auth":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        app_url = f"http://{args.host}:{args.port}"

        def _app_get(path, headers=None):
            try:
                response = httpx.get(
                    f"{app_url}{path}", headers=headers, timeout=args.timeout
                )
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Could not reach the compiled app at {app_url}: "
                    f"{exc}. Is it running? (see `serve`, or `docker "
                    "compose up`)"
                )

            # A 401 from GET /auth/validate specifically means "this
            # --api-key isn't valid" -- exactly the thing this command
            # exists to report, not a failure of the command itself, so
            # it's handled by the caller below rather than raised here
            # like every other unexpected non-2xx already is.
            if response.status_code == 401 and path == "/auth/validate":
                return response

            if response.status_code >= 400:
                raise RuntimeError(
                    f"App rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            return response

        # Wrapped in its own function, the same "so --watch can call the
        # exact same fetch-and-report logic on every poll" reasoning
        # app-status/app-metrics/app-tasks list's own identical functions
        # already establish.
        def _fetch_and_report_auth():

            status = _app_get("/auth/status").json()
            info = _app_get("/auth/info").json()

            validate_response = _app_get(
                "/auth/validate", headers={"X-API-Key": args.api_key}
            )
            if validate_response.status_code == 401:
                validate = {
                    "authenticated": False,
                    "detail": _extract_dashboard_error_detail(validate_response),
                }
            else:
                validate = validate_response.json()

            if args.json_output:
                result = {"status": status, "info": info, "validate": validate}
                if args.watch:
                    # One compact object per line (NDJSON), the same
                    # reason app-status --json --watch's own identical
                    # combined result is never split across lines either.
                    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    print(json.dumps(result))
                else:
                    print(json.dumps(result, indent=2))
                return

            if args.watch:
                print(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S')} ---")

            print(
                f"Authentication at {app_url}: "
                f"{status.get('authentication')} "
                f"(api key configured: {status.get('api_key_configured')})"
            )
            print(f"  header: {info.get('header')}")
            print(f"  environment variable: {info.get('environment_variable')}")
            rate_limit = info.get("rate_limit_per_minute")
            print(
                "  rate limiting: "
                f"{f'enabled ({rate_limit} requests/minute per key)' if rate_limit else 'disabled'}"
            )
            print(f"  configured keys: {info.get('configured_keys')}")
            print(f"  protected endpoints: {info.get('protected_endpoints')}")
            if validate.get("authenticated"):
                print("  --api-key: valid")
            else:
                print(f"  --api-key: INVALID ({validate.get('detail')})")

        if not args.watch:
            _fetch_and_report_auth()
        else:
            try:
                while True:
                    _fetch_and_report_auth()
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nStopped watching.")
    elif args.command == "app-metrics":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        app_url = f"http://{args.host}:{args.port}"

        # See app-status' own identical _fetch_and_report_status just
        # above for why this is a function rather than inlined once --
        # --watch below needs to call the exact same fetch-and-report
        # logic on every poll without a second, hand-copied, driftable
        # print block.
        def _fetch_and_report_metrics():

            try:
                response = httpx.get(
                    f"{app_url}/metrics/prometheus", timeout=args.timeout,
                )
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Could not reach the compiled app at {app_url}: "
                    f"{exc}. Is it running? (see `serve`, or `docker "
                    "compose up`)"
                )

            if response.status_code >= 400:

                raise RuntimeError(
                    f"App rejected the request ({response.status_code}): "
                    f"{response.text}"
                )

            if args.json_output:
                result = _parse_prometheus_text_metrics(response.text)
                if args.watch:
                    # See app-status --watch --json's own identical
                    # one-object-per-line reasoning above.
                    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    print(json.dumps(result))
                else:
                    print(json.dumps(result, indent=2))
            else:
                if args.watch:
                    print(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S')} ---")
                print(response.text, end="" if response.text.endswith("\n") else "\n")

        if not args.watch:
            _fetch_and_report_metrics()
        else:
            try:
                while True:
                    _fetch_and_report_metrics()
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("\nStopped watching.")
    elif args.command == "app-call":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        # Validated before any other work here -- the identical
        # "callback_url must be an http:// or https:// URL" check
        # generate_curl_commands/generate_postman_collection
        # (backend/inspector.py) already apply before generating a single
        # preview command, applied here before this command reads the
        # notebook or reaches the app at all, so a typo'd scheme fails
        # fast and clean rather than as a 400 from the app itself deep
        # into this call.
        if (
            args.callback_url is not None
            and urlsplit(args.callback_url).scheme not in ("http", "https")
        ):
            raise ValueError("--callback-url must be an http:// or https:// URL")

        # Read locally, purely to learn args.function's own
        # example_payload/background-ness -- the notebook itself is never
        # uploaded or compiled by this command, the same "read-only local
        # preview source" role it already plays for curl-preview/
        # export-curl's own identical example_payload default.
        data = inspect_notebook_data(notebook_path=args.notebook)

        functions_by_name = {func["name"]: func for func in data["functions"]}

        if args.function not in functions_by_name:
            raise RuntimeError(
                f"'{args.function}' is not a function {args.notebook} "
                "would compile -- available: "
                f"{', '.join(sorted(functions_by_name)) or '(none)'}"
            )

        if args.function in data["reserved_name_conflicts"]:
            raise RuntimeError(
                f"'{args.function}' collides with a name this app's own "
                "infrastructure already reserves -- it would never "
                "actually compile into a real endpoint. See `inspect "
                f"{args.notebook}` for details."
            )

        endpoint = next(
            (
                ep for ep in data["endpoints"]
                if ep["path"] == f"/{args.function}"
            ),
            None,
        )
        is_background = bool(endpoint and endpoint["is_async"])

        if args.data is not None:
            try:
                payload = json.loads(args.data)
            except ValueError as exc:
                raise RuntimeError(f"--data is not valid JSON: {exc}")
        else:
            payload = functions_by_name[args.function].get("example_payload", {})

        app_url = f"http://{args.host}:{args.port}"
        headers = {"X-API-Key": args.api_key}

        call_params = {}

        if args.callback_url is not None:
            if is_background:
                call_params["callback_url"] = args.callback_url
            else:
                # The generated app never even reads ?callback_url= for a
                # synchronous endpoint (see generate_fastapi_code's own
                # async-vs-sync code paths, api_generator.py) -- the
                # identical restriction generate_curl_commands/
                # generate_postman_collection already apply by simply
                # never attaching it to a synchronous function's own
                # command. A warning (not a hard error) here, since
                # --data/--wait alike are otherwise still perfectly valid
                # against a synchronous function -- only this one flag is
                # a no-op for it.
                print(
                    f"Warning: --callback-url given but '{args.function}' "
                    "is a synchronous function -- the app never reads "
                    "this parameter for one, so it will be ignored.",
                    file=sys.stderr,
                )

        try:
            response = httpx.post(
                f"{app_url}/{args.function}", json=payload, headers=headers,
                params=call_params, timeout=args.timeout,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Could not reach the compiled app at {app_url}: {exc}. Is "
                "it running? (see `serve`, or `docker compose up`)"
            )

        if response.status_code >= 400:

            raise RuntimeError(
                f"App rejected the call ({response.status_code}): "
                f"{_extract_dashboard_error_detail(response)}"
            )

        result = response.json()

        if is_background and args.wait:

            task_id = result.get("task_id")
            deadline = time.time() + args.wait_timeout

            while True:

                try:
                    task_response = httpx.get(
                        f"{app_url}/tasks/{task_id}", headers=headers,
                        timeout=args.timeout,
                    )
                except httpx.HTTPError as exc:
                    raise RuntimeError(
                        f"Could not reach the compiled app at {app_url}: "
                        f"{exc}."
                    )

                if task_response.status_code >= 400:
                    raise RuntimeError(
                        f"App rejected the request "
                        f"({task_response.status_code}): "
                        f"{_extract_dashboard_error_detail(task_response)}"
                    )

                result = task_response.json()

                if result.get("status") != "processing":
                    break

                if time.time() >= deadline:
                    raise RuntimeError(
                        f"Task {task_id} did not complete within "
                        f"{args.wait_timeout}s"
                    )

                time.sleep(args.poll_interval)

        if args.json_output:
            print(json.dumps(result, indent=2))
        elif is_background and not args.wait:
            print(f"Task submitted: {result.get('task_id')}")
            print(
                "(pass --wait to poll for the real result, or check "
                f"GET /tasks/{result.get('task_id')} yourself)"
            )
        elif is_background:
            if result.get("status") == "completed":
                print(f"Task completed: {result.get('result')!r}")
            else:
                print(f"Task {result.get('status')}: {result.get('error')}")
        else:
            print(f"Result: {result.get('result')!r}")
    elif args.command == "app-tasks":
        # See `upload` above for why this is imported here rather than at
        # module scope.
        import httpx

        app_url = f"http://{args.host}:{args.port}"
        headers = {"X-API-Key": args.api_key}

        def _app_request(method, path, **kwargs):
            try:
                response = httpx.request(
                    method, f"{app_url}{path}", headers=headers,
                    timeout=args.timeout, **kwargs,
                )
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Could not reach the compiled app at {app_url}: "
                    f"{exc}. Is it running? (see `serve`, or `docker "
                    "compose up`)"
                )

            if response.status_code >= 400:
                raise RuntimeError(
                    f"App rejected the request ({response.status_code}): "
                    f"{_extract_dashboard_error_detail(response)}"
                )

            return response.json()

        # Shared by both `get` (a single fetch) and `wait` (the identical
        # fetch, repeated until the task leaves "processing") -- so the
        # two can never drift on what a task record's own summary looks
        # like.
        def _print_task_record(task_id, data, json_output):
            if json_output:
                print(json.dumps(data, indent=2))
            else:
                print(f"{task_id}: {data.get('status')}")
                if "result" in data:
                    print(f"  result: {data['result']!r}")
                if "error" in data:
                    print(f"  error: {data['error']}")
                webhook = data.get("webhook")
                if webhook is not None:
                    print(
                        "  webhook: "
                        f"{'delivered' if webhook.get('delivered') else 'FAILED'}"
                        f" ({webhook.get('attempts')} attempt(s))"
                    )

        if args.app_tasks_command == "list":

            params = {}
            if args.status:
                params["status"] = args.status
            if args.webhook_delivery_failed is not None:
                params["webhook_delivery_failed"] = args.webhook_delivery_failed
            if args.limit is not None:
                params["limit"] = args.limit
            if args.offset is not None:
                params["offset"] = args.offset

            # Wrapped in its own function -- the same "so --watch can
            # call the exact same fetch-and-report logic on every poll
            # without a second, hand-copied, driftable print block"
            # reasoning app-status/app-metrics' own identical functions
            # already establish (see _fetch_and_report_status above).
            def _fetch_and_report_tasks():

                data = _app_request("GET", "/tasks", params=params)

                if args.json_output:
                    if args.watch:
                        # One compact object per line (NDJSON) -- see
                        # app-status --watch --json's own identical
                        # reasoning above.
                        result = dict(data)
                        result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                        print(json.dumps(result))
                    else:
                        print(json.dumps(data, indent=2))
                    return

                if args.watch:
                    print(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S')} ---")

                tasks = data.get("tasks", {})

                if not tasks:
                    print("No matching tasks.")
                else:
                    for task_id, task in tasks.items():
                        webhook_note = ""
                        webhook = task.get("webhook")
                        if webhook is not None:
                            webhook_note = (
                                "  webhook: delivered" if webhook.get("delivered")
                                else "  webhook: FAILED"
                            )
                        print(f"{task_id}  ({task.get('status')}){webhook_note}")

                print(
                    f"\n{data.get('matching_tasks', 0)} matching task(s) "
                    f"({data.get('webhook_delivery_failed_tasks', 0)} with "
                    "a failed webhook delivery)"
                )

            if not args.watch:
                _fetch_and_report_tasks()
            else:
                try:
                    while True:
                        _fetch_and_report_tasks()
                        time.sleep(args.interval)
                except KeyboardInterrupt:
                    print("\nStopped watching.")

        elif args.app_tasks_command == "get":

            data = _app_request("GET", f"/tasks/{args.task_id}")
            _print_task_record(args.task_id, data, args.json_output)

        elif args.app_tasks_command == "wait":

            deadline = time.time() + args.wait_timeout

            while True:

                data = _app_request("GET", f"/tasks/{args.task_id}")

                if data.get("status") != "processing":
                    break

                if time.time() >= deadline:
                    raise RuntimeError(
                        f"Task {args.task_id} did not complete within "
                        f"{args.wait_timeout}s"
                    )

                time.sleep(args.poll_interval)

            _print_task_record(args.task_id, data, args.json_output)

        elif args.app_tasks_command == "delete":

            data = _app_request("DELETE", f"/tasks/{args.task_id}")

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                print(f"Deleted task {args.task_id} (was {data.get('status')}).")

        elif args.app_tasks_command == "retry":

            data = _app_request("POST", f"/tasks/{args.task_id}/retry")

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                print(
                    f"Retrying task {args.task_id} as new task "
                    f"{data.get('task_id')} ({data.get('status')})."
                )

        elif args.app_tasks_command == "redeliver-webhook":

            data = _app_request("POST", f"/tasks/{args.task_id}/redeliver-webhook")

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                webhook = data.get("webhook", {})
                verb = "delivered" if webhook.get("delivered") else "FAILED"
                print(
                    f"Redelivery {verb} for task {args.task_id} "
                    f"(attempt {data.get('webhook_redelivery_count')})."
                )

        elif args.app_tasks_command == "redeliver-failed":

            # Paginated (GET /tasks caps a single response at limit=1000)
            # so a deployment with more than one page of currently-failed
            # tasks still gets every one of them redelivered, not silently
            # just the first 1000.
            task_ids = []
            offset = 0

            while True:

                page = _app_request(
                    "GET", "/tasks",
                    params={
                        "webhook_delivery_failed": "true",
                        "limit": 1000,
                        "offset": offset,
                    },
                )
                page_task_ids = list(page.get("tasks", {}).keys())

                if not page_task_ids:
                    break

                task_ids.extend(page_task_ids)
                offset += len(page_task_ids)

                if offset >= page.get("matching_tasks", offset):
                    break

            results = []
            succeeded_count = 0
            failed_count = 0

            for task_id in task_ids:

                if args.dry_run:
                    results.append({"task_id": task_id, "status": "would_redeliver"})
                    continue

                # One bad task_id (e.g. deleted between the listing above
                # and this redelivery -- a real race, not hypothetical,
                # the identical window every *-batch endpoint's own
                # per-entry try/except already guards against) must not
                # abort the whole run: every other matching task still
                # gets its own redelivery attempt.
                try:
                    data = _app_request(
                        "POST", f"/tasks/{task_id}/redeliver-webhook"
                    )
                except RuntimeError as exc:
                    results.append({
                        "task_id": task_id, "status": "error",
                        "detail": str(exc),
                    })
                    failed_count += 1
                    continue

                webhook = data.get("webhook", {})

                if webhook.get("delivered"):
                    results.append({"task_id": task_id, "status": "delivered"})
                    succeeded_count += 1
                else:
                    results.append({
                        "task_id": task_id, "status": "failed",
                        "detail": webhook.get("error"),
                    })
                    failed_count += 1

            if args.dry_run:
                succeeded_count = len(results)
                failed_count = 0

            if args.json_output:
                print(json.dumps({
                    "dry_run": args.dry_run,
                    "results": results,
                    "succeeded_count": succeeded_count,
                    "failed_count": failed_count,
                }, indent=2))
            elif not results:
                print("No tasks currently have a failed webhook delivery.")
            else:

                for result in results:

                    if args.dry_run:
                        print(f"Would redeliver: {result['task_id']}")
                    elif result["status"] == "delivered":
                        print(f"Redelivered: {result['task_id']}")
                    else:
                        print(
                            f"Still failed: {result['task_id']} -- "
                            f"{result.get('detail')}"
                        )

                if not args.dry_run:
                    print(f"\n{succeeded_count} succeeded, {failed_count} still failed")

        elif args.app_tasks_command == "purge-completed":

            # DELETE /tasks/completed has no confirmation step of its own
            # and is irreversible -- the same reasoning
            # clear-deploy-history/prune-versions/tags-delete already
            # prompt for.
            if not args.yes:
                answer = input(
                    f"Permanently delete every completed task on {app_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            data = _app_request("DELETE", "/tasks/completed")

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                print(
                    f"Deleted {data.get('deleted', 0)} completed task(s) "
                    f"({data.get('remaining_tasks', 0)} remaining)."
                )

        elif args.app_tasks_command == "purge-failed":

            if not args.yes:
                answer = input(
                    f"Permanently delete every failed task on {app_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            data = _app_request("DELETE", "/tasks/failed")

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                print(
                    f"Deleted {data.get('deleted', 0)} failed task(s) "
                    f"({data.get('remaining_tasks', 0)} remaining)."
                )

        elif args.app_tasks_command == "cleanup":

            if not args.yes:
                answer = input(
                    "Permanently delete every completed and failed task "
                    f"on {app_url}? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            data = _app_request("POST", "/tasks/cleanup")

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                print(
                    f"Deleted {data.get('completed_deleted', 0)} completed "
                    f"and {data.get('failed_deleted', 0)} failed task(s) "
                    f"({data.get('remaining_tasks', 0)} remaining)."
                )

        elif args.app_tasks_command == "reset":

            # Unlike purge-completed/purge-failed/cleanup above, POST
            # /tasks/reset drops every task regardless of status --
            # including one still processing -- so this warns about that
            # explicitly in the prompt itself, not just in --help.
            if not args.yes:
                answer = input(
                    f"Permanently delete EVERY task on {app_url}, "
                    "including any still processing? [y/N] "
                )
                if answer.strip().lower() not in ("y", "yes"):
                    print("Aborted.")
                    return

            data = _app_request("POST", "/tasks/reset")

            if args.json_output:
                print(json.dumps(data, indent=2))
            else:
                print(f"Deleted {data.get('deleted_tasks', 0)} task(s).")


def main():
    parser = argparse.ArgumentParser(
        prog="notebook-to-api",
        description="Compile Jupyter notebooks into FastAPI services and optionally build Docker images."
    )
    # NOTEBOOK_TO_API_VERSION (backend/compiler.py) is also what
    # dashboard.py's own FastAPI(version=...) and GET / already report --
    # before this, there was no way to ask the CLI itself which version
    # it was, short of reading its own source, the single most standard
    # expectation any well-behaved CLI tool already meets. action="version"
    # prints and exits immediately when given, before subparsers' own
    # required=True below is ever enforced -- `notebook-to-api --version`
    # works with no subcommand, exactly like `notebook-to-api --help`
    # already does.
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"notebook-to-api {NOTEBOOK_TO_API_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # compile command
    compile_parser = subparsers.add_parser("compile", help="Compile a notebook to FastAPI app.")
    compile_parser.add_argument("notebook", help="Path to the notebook file.")
    compile_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where the FastAPI app and assets will be written."
    )
    compile_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON (functions, dependencies, "
            "generated_files, endpoints, skipped_functions) instead of the "
            "human-readable summary, for scripting/automation -- the same "
            "shape `inspect --json` already returns, reflecting the app "
            "this compile just produced. Also includes \"smoke_test\" "
            "when --smoke-test is given."
        )
    )
    compile_parser.add_argument(
        "--smoke-test",
        action="store_true",
        dest="smoke_test",
        help=(
            "After compiling, actually import the compiled app in this "
            "process and call its own GET /health -- catches a class of "
            "failure (a codegen bug producing syntactically broken "
            "Python, say) no purely static check can. The same "
            "\"smoke_test\" `remote-compile --smoke-test` already "
            "performs against a notebook compiled on a running "
            "dashboard, applied here to a local compile instead. This "
            "command exits 1 if the smoke test fails, even though the "
            "compile itself still succeeded and every file it wrote is "
            "still on disk."
        )
    )
    _add_function_selection_arguments(compile_parser)

    # doctor command -- a local, read-only pre-flight check of this
    # machine's own environment, distinct from `governance doctor` below
    # (which audits this dashboard's own recorded governance history, not
    # the machine `notebook-to-api` itself is running on). Before this,
    # every one of the things it checks was only ever discovered
    # reactively, mid-command: `deploy`'s own docker build already
    # converts a missing Docker CLI into a clean RuntimeError (see
    # _run_deploy_docker_command), but only once an operator had already
    # gotten as far as compiling a real notebook and starting a build --
    # there was no single command to sanity-check a fresh machine's setup
    # (or diagnose "why does deploy keep failing") up front, without
    # needing a real notebook on hand at all.
    doctor_parser = subparsers.add_parser(
        "doctor",
        help=(
            "Check this machine's own local environment (Python version, "
            "Docker availability, required Python packages, output "
            "directory permissions) -- not this dashboard's own "
            "`governance doctor`."
        )
    )
    doctor_parser.add_argument(
        "--output",
        default="generated",
        help=(
            "Directory `compile`/`serve`/`deploy` would write into by "
            "default -- checked for write permission the same way "
            "`compile --output` itself would need. Only the directory "
            "itself (or its nearest already-existing ancestor, if it "
            "doesn't exist yet) is checked; nothing is actually created "
            "or written."
        )
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON ({\"checks\": [{\"name\", "
            "\"status\", \"detail\"}, ...], \"ok\"}) instead of a "
            "human-readable summary, for scripting/automation (e.g. a CI "
            "step gating on Docker actually being available before "
            "attempting `deploy`)."
        )
    )

    # inspect command (show analysis report)
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a notebook and display analysis report.")
    inspect_parser.add_argument("notebook", help="Path to the notebook file.")
    inspect_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where compilation artifacts would be placed (used to list generated files)."
    )
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON (functions, dependencies, "
            "generated_files) instead of the human-readable report, for "
            "scripting/automation."
        )
    )

    # validate command (CI-friendly exit-code gate on inspect's own
    # compile-time-issue checks, without writing any output)
    validate_parser = subparsers.add_parser(
        "validate",
        help="Check whether a notebook would compile cleanly, without writing any output -- exits non-zero on issues, for CI."
    )
    validate_parser.add_argument("notebook", help="Path to the notebook file.")
    validate_parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Also fail (exit 2) when the notebook has skipped functions "
            "(no endpoint will be generated for them) or duplicate "
            "functions (a name defined more than once -- only the last "
            "definition is compiled). By default these are reported as "
            "a non-fatal warning (exit 1): compiling still succeeds for "
            "every other function, unlike a reserved name conflict, "
            "which always fails compilation outright and always exits 2."
        )
    )
    _add_function_selection_arguments(validate_parser)
    validate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON ({\"status\": \"pass\"|\"warn\"|"
            "\"fail\", \"notebook\", \"reserved_name_conflicts\", "
            "\"skipped_functions\", \"duplicate_functions\"}) instead of "
            "the human-readable report, for scripting/automation."
        )
    )

    # openapi export command
    openapi_parser = subparsers.add_parser(
        "export-openapi", help="Export OpenAPI schema from generated FastAPI app."
    )
    openapi_parser.add_argument(
        "--app-dir",
        default="generated",
        help="Directory the app was compiled into (the --output used with `compile`)."
    )
    openapi_parser.add_argument(
        "--format",
        choices=["json", "yaml"],
        default="json",
        help="Output format for the exported schema. Default: json."
    )
    openapi_parser.add_argument(
        "--output",
        default=None,
        help=(
            "Path to write the OpenAPI schema. Defaults to "
            "<app-dir>/openapi.json for --format json, or "
            "<app-dir>/openapi.yaml for --format yaml."
        )
    )
    openapi_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"format\", \"path\", \"schema\"/\"content\"}) "
            "instead of only writing the schema file, for "
            "scripting/automation -- the same shape POST /api/export-openapi "
            "already returns for the same operation."
        )
    )

    # SDK export command
    sdk_parser = subparsers.add_parser(
        "export-sdk", help="Generate an SDK client from an exported OpenAPI schema."
    )
    sdk_parser.add_argument(
        "--app-dir",
        default="generated",
        help=(
            "Directory the app was compiled into (the --output used with "
            "`compile`) -- used to locate the exported OpenAPI schema "
            "when --openapi isn't given (<app-dir>/openapi.json), the same "
            "convention `export-openapi --app-dir` already uses for its "
            "own default --output."
        )
    )
    sdk_parser.add_argument(
        "--openapi",
        default=None,
        help=(
            "Path to the OpenAPI JSON file to generate the client from "
            "(see export-openapi). Defaults to <app-dir>/openapi.json."
        )
    )
    sdk_parser.add_argument(
        "--language",
        choices=["python", "typescript"],
        default="python",
        help="Target language for the generated SDK client (default: python)."
    )
    sdk_parser.add_argument(
        "--output",
        default=None,
        help=(
            "Path to write the generated SDK client. Defaults to "
            "<openapi-dir>/sdk/python_client.py for --language python, or "
            "<openapi-dir>/sdk/typescript_client.ts for --language "
            "typescript, where <openapi-dir> is --openapi's own directory."
        )
    )
    sdk_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"language\", \"path\", \"code\"}) instead of "
            "only writing the client file, for scripting/automation -- the "
            "same shape POST /api/export-sdk already returns for the same "
            "operation."
        )
    )

    # verify-webhook command (locally verify a captured task webhook's own
    # X-Webhook-Signature against a shared secret -- reimplements, byte
    # for byte, the exact verification generate_python_sdk's own module-
    # level verify_webhook_signature already gives an SDK-based receiver,
    # for a caller with no code of their own to run it from at all: a
    # shell debugging session, a CI step, a receiver written in a
    # language this project has no generated SDK for)
    verify_webhook_parser = subparsers.add_parser(
        "verify-webhook",
        help=(
            "Verify a captured task webhook's own X-Webhook-Signature "
            "against a shared secret, entirely locally -- no compiled "
            "app or dashboard involved."
        )
    )
    verify_webhook_parser.add_argument(
        "--body-file",
        required=True,
        dest="body_file",
        help=(
            "Path to the exact, untouched raw bytes of the received "
            "webhook request body -- re-serializing a parsed JSON object "
            "before verifying can reorder keys or change whitespace, "
            "producing a body that no longer matches what was actually "
            "signed, even though the payload itself is semantically "
            "unchanged. Pass '-' to read the body from stdin instead."
        )
    )
    verify_webhook_parser.add_argument(
        "--signature",
        required=True,
        help=(
            "The received X-Webhook-Signature header value verbatim "
            "(e.g. \"sha256=abcdef...\")."
        )
    )
    verify_webhook_parser.add_argument(
        "--secret",
        default=None,
        help=(
            "The compiled app's own NOTEBOOK_API_WEBHOOK_SECRET. "
            "Defaults to the identically-named $NOTEBOOK_API_WEBHOOK_SECRET "
            "environment variable if not given -- passing the real secret "
            "as a bare command-line argument leaves it readable in shell "
            "history and process listings, the same reason "
            "$NOTEBOOK_API_DASHBOARD_URL is already an accepted alternative "
            "to --dashboard-url elsewhere in this CLI."
        )
    )
    verify_webhook_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result ({\"valid\"}) instead of "
            "a human-readable one-line verdict, for scripting/automation. "
            "Either way, this command's own exit code already reflects "
            "the verdict (0 valid, 1 invalid) for a plain shell "
            "`if notebook-to-api verify-webhook ...; then` check with no "
            "need to parse either output form at all."
        )
    )

    # curl export command (ready-to-run test requests, no compile needed)
    curl_parser = subparsers.add_parser(
        "export-curl",
        help="Generate a shell script of curl commands for a notebook's would-be endpoints."
    )
    curl_parser.add_argument("notebook", help="Path to the notebook file.")
    curl_parser.add_argument(
        "--host",
        default="localhost",
        help="Host the generated commands target (default: localhost)."
    )
    curl_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the generated commands target (default: 8000, matching `serve`'s own default)."
    )
    curl_parser.add_argument(
        "--api-key",
        default=DEFAULT_DEV_API_KEY,
        dest="api_key",
        help=(
            "Value sent as the X-API-Key header (default: the generated "
            "app's own default dev key, used when NOTEBOOK_API_KEY isn't "
            "set on the server). Pass the same value configured via "
            "NOTEBOOK_API_KEY if it's been changed."
        )
    )
    curl_parser.add_argument(
        "--output",
        default=None,
        help="Path to write the generated shell script to. Default: requests.sh"
    )
    _add_function_selection_arguments(curl_parser)
    _add_callback_url_argument(curl_parser)
    curl_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"path\", \"commands\"}) instead of only "
            "writing the script file, for scripting/automation."
        )
    )

    # export-postman command (generate a Postman Collection v2.1.0 for a
    # notebook's would-be endpoints -- the same coverage export-curl
    # already gives a terminal, for the far larger share of API consumers
    # who reach for Postman instead of raw curl)
    postman_parser = subparsers.add_parser(
        "export-postman",
        help="Generate a Postman Collection v2.1.0 for a notebook's would-be endpoints."
    )
    postman_parser.add_argument("notebook", help="Path to the notebook file.")
    postman_parser.add_argument(
        "--host",
        default="localhost",
        help="Host the generated requests target (default: localhost)."
    )
    postman_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the generated requests target (default: 8000, matching `serve`'s own default)."
    )
    postman_parser.add_argument(
        "--api-key",
        default=DEFAULT_DEV_API_KEY,
        dest="api_key",
        help=(
            "Value sent as the X-API-Key header, and as the collection's "
            "own \"api_key\" variable (default: the generated app's own "
            "default dev key, used when NOTEBOOK_API_KEY isn't set on the "
            "server). Pass the same value configured via NOTEBOOK_API_KEY "
            "if it's been changed."
        )
    )
    postman_parser.add_argument(
        "--collection-name",
        default=None,
        dest="collection_name",
        help=(
            "Name shown for the collection in Postman (default: the "
            "notebook's own filename, without its extension)."
        )
    )
    postman_parser.add_argument(
        "--output",
        default=None,
        help="Path to write the generated collection to. Default: postman_collection.json"
    )
    _add_function_selection_arguments(postman_parser)
    _add_callback_url_argument(postman_parser)
    postman_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"path\", \"collection\"}) instead of only "
            "writing the collection file, for scripting/automation."
        )
    )

    # serve command (live notebook server)
    serve_parser = subparsers.add_parser("serve", help="Serve notebook as live API with hot recompilation.")
    serve_parser.add_argument("notebook", help="Path to the notebook file.")
    serve_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where the FastAPI app will be written."
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to run the live API server on (default: 8000). Lets more than one notebook be served at once."
    )
    serve_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help=(
            "Interface the live API server binds to (default: 0.0.0.0, "
            "every interface). Set to 127.0.0.1 to only accept "
            "connections from this machine."
        )
    )
    _add_function_selection_arguments(serve_parser)
    _add_debounce_argument(serve_parser)
    _add_on_change_argument(serve_parser)

    # watch command (recompile on save, no live API server)
    watch_parser = subparsers.add_parser(
        "watch",
        help="Recompile a notebook on every save, without running a live API server."
    )
    watch_parser.add_argument("notebook", help="Path to the notebook file.")
    watch_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where the FastAPI app and assets will be written."
    )
    _add_function_selection_arguments(watch_parser)
    _add_debounce_argument(watch_parser)
    _add_on_change_argument(watch_parser)

    # deploy command (compile + build a Docker image)
    deploy_parser = subparsers.add_parser(
        "deploy", help="Compile a notebook and build a Docker image for the generated FastAPI app."
    )
    deploy_parser.add_argument("notebook", help="Path to the notebook file.")
    deploy_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where the FastAPI app and assets will be written."
    )
    deploy_parser.add_argument(
        "--tag",
        default=None,
        help="Docker image tag to build (default: <output-dir-basename>:latest)."
    )
    deploy_parser.add_argument(
        "--push",
        action="store_true",
        help=(
            "Push the built image with `docker push <tag>` after a successful "
            "build. The tag must already reference the target registry (e.g. "
            "--tag registry.example.com/myapp:v1); this does not modify or "
            "infer a registry, and assumes `docker login` has already been "
            "done for it."
        )
    )
    deploy_parser.add_argument(
        "--platform",
        default=None,
        help=(
            "Target platform to pass to `docker build --platform` (e.g. "
            "linux/amd64, linux/arm64). Defaults to the local Docker "
            "daemon's own default (its host architecture) -- set this "
            "when building on one architecture (e.g. Apple Silicon) for a "
            "deploy target that runs another, which almost every cloud "
            "PaaS does (linux/amd64)."
        )
    )
    deploy_parser.add_argument(
        "--no-cache",
        action="store_true",
        dest="no_cache",
        help=(
            "Pass `docker build --no-cache`, forcing a clean rebuild of "
            "every layer instead of reusing Docker's own cache -- e.g. "
            "to rule out a stale cached `pip install` layer after "
            "requirements.txt changed but a pinned version's wheel was "
            "silently re-published, or a floating base image tag moved "
            "without a local re-pull."
        )
    )
    deploy_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Compile the notebook and confirm a Dockerfile was produced, "
            "but stop there -- without actually running `docker build`/"
            "`docker push` -- mirroring POST /api/deploy's own "
            "\"dry_run\" body field."
        )
    )
    deploy_parser.add_argument(
        "--smoke-test",
        action="store_true",
        dest="smoke_test",
        help=(
            "After a successful build, actually run the image in a real, "
            "throwaway container and poll its own GET /health until it "
            "responds -- catches a class of failure a successful `docker "
            "build` alone can't (a missing system library, a `pip "
            "install` that behaves differently inside the container's "
            "own environment, a Dockerfile bug that only manifests once "
            "the image actually runs), the same way `compile "
            "--smoke-test` catches a codegen bug a successful compile "
            "alone can't. The same \"smoke_test\" POST /api/deploy "
            "itself performs. This command exits 1 if the smoke test "
            "fails, even though the image was still built (and, with "
            "--push, still pushed) successfully. Ignored under --dry-run."
        )
    )
    deploy_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result ({\"status\", \"tag\", "
            "\"pushed\"}) instead of human-readable progress output, for "
            "scripting/automation -- the same shape POST /api/deploy "
            "already returns for the same operation. Also includes "
            "\"smoke_test\" when --smoke-test is given."
        )
    )
    _add_function_selection_arguments(deploy_parser)

    # diff command (compare two notebooks' compiled API surface)
    diff_parser = subparsers.add_parser(
        "diff",
        help="Compare two notebooks' compiled API surface (added/removed/changed endpoints)."
    )
    diff_parser.add_argument("old_notebook", help="Path to the baseline notebook file.")
    diff_parser.add_argument("new_notebook", help="Path to the notebook file to compare against it.")
    diff_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON ({\"added\", \"removed\", "
            "\"changed\", \"unchanged\", \"compatible\", "
            "\"breaking_changes\"}) instead of the human-readable report, "
            "for scripting/automation."
        )
    )
    diff_parser.add_argument(
        "--fail-on-breaking",
        action="store_true",
        help=(
            "Exit with status 1 if classify_notebook_diff (backend/"
            "inspector.py) finds any breaking change between the two "
            "notebooks -- a removed endpoint, a removed or newly-required "
            "parameter, a parameter type change, or a return type change "
            "-- after printing the report. Purely additive changes (a new "
            "endpoint, a new parameter with a default) never trigger this."
        )
    )
    diff_parser.add_argument(
        "--content",
        action="store_true",
        help=(
            "Also print a line-level unified diff of both notebooks' own "
            "raw code cell source, via diff_notebook_source (backend/"
            "inspector.py) -- distinct from the structural added/removed/"
            "changed-signature report this command already prints, e.g. "
            "to actually see what changed in a function's own body, not "
            "just whether its signature did. Computed locally from "
            "old_notebook/new_notebook directly -- the same report "
            "`diff-notebooks --content` already offers for two notebooks "
            "already on a dashboard, just without needing either one "
            "uploaded first."
        )
    )

    # upload command (push a local notebook to a running dashboard)
    upload_parser = subparsers.add_parser(
        "upload",
        help=(
            "Upload one or more notebooks to a running dashboard "
            "instance's POST /api/upload (single file) or POST "
            "/api/upload/batch (multiple files)."
        )
    )
    upload_parser.add_argument(
        "notebook", nargs="+",
        help=(
            "Path to the notebook file to upload. Multiple paths use "
            "POST /api/upload/batch instead, so one bad file doesn't "
            "abort the rest -- unlike a plain shell loop of single-file "
            "`upload` invocations, which stops at the first non-zero "
            "exit."
        )
    )
    _add_dashboard_url_and_timeout_arguments(upload_parser)
    upload_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook of the same name on the "
            "dashboard, mirroring POST /api/upload's own ?overwrite=true "
            "-- without this, uploading onto an existing filename is "
            "rejected with a 409, exactly as it already is through that "
            "endpoint directly."
        )
    )
    upload_parser.add_argument(
        "--tags",
        help=(
            "Comma-separated tags to set on the uploaded notebook(s), via "
            "POST /api/upload's or POST /api/upload/batch's own ?tags= "
            "query param -- applied uniformly to every file when "
            "uploading more than one."
        )
    )
    upload_parser.add_argument(
        "--description",
        default=None,
        help=(
            "Description to set on the uploaded notebook(s), via POST "
            "/api/upload's or POST /api/upload/batch's own ?description= "
            "query param -- applied uniformly to every file when "
            "uploading more than one, instead of a separate `description "
            "set` round trip immediately after."
        )
    )
    upload_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        metavar="SHA256",
        help=(
            "Reject the upload with an error unless the uploaded "
            "content's own hash matches this value, via POST "
            "/api/upload's own ?expected_sha256= query param -- e.g. to "
            "catch a corrupted transfer or the wrong file before it lands "
            "on the dashboard. Only valid when uploading a single "
            "notebook."
        )
    )
    upload_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report what the upload(s) would do -- including a same-name "
            "collision without --overwrite, or an --expected-sha256 "
            "mismatch -- via POST /api/upload's or POST /api/upload/"
            "batch's own \"dry_run\" query param, without writing "
            "anything to UPLOAD_DIR. Neither --tags nor --description "
            "are applied under --dry-run either."
        )
    )
    upload_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response -- "
            "{\"status\", \"filename\", \"path\", \"overwritten\", "
            "\"sha256\", \"dry_run\"} for a single file, or {\"status\", "
            "\"dry_run\", \"results\", \"succeeded_count\", "
            "\"failed_count\"} for multiple -- instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # import-notebooks command (upload every .ipynb bundled inside a
    # single .zip archive, via POST /api/notebooks/import -- the
    # counterpart to `export-notebooks`' own GET /api/notebooks/export)
    import_notebooks_parser = subparsers.add_parser(
        "import-notebooks",
        help=(
            "Upload every .ipynb file bundled inside a single .zip "
            "archive to a running dashboard instance, via POST "
            "/api/notebooks/import."
        )
    )
    import_notebooks_parser.add_argument(
        "zip_path", help="Path to the local .zip archive to import."
    )
    _add_dashboard_url_and_timeout_arguments(import_notebooks_parser)
    import_notebooks_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook of the same name on the "
            "dashboard, mirroring POST /api/notebooks/import's own "
            "?overwrite=true -- without this, an entry whose name "
            "collides with an already-uploaded notebook is reported as "
            "its own failed result rather than aborting the rest of the "
            "import."
        )
    )
    import_notebooks_parser.add_argument(
        "--tags",
        help=(
            "Comma-separated tags to set on every successfully-imported "
            "notebook, via POST /api/notebooks/import's own ?tags= query "
            "param -- e.g. to re-apply the tag a matching `export-"
            "notebooks --tag ...` archive was originally selected by."
        )
    )
    import_notebooks_parser.add_argument(
        "--description",
        default=None,
        help=(
            "Description to set on every successfully-imported notebook, "
            "via POST /api/notebooks/import's own ?description= query "
            "param."
        )
    )
    import_notebooks_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which entries in the archive would be imported -- "
            "and which would fail or collide with an already-uploaded "
            "notebook -- via POST /api/notebooks/import's own \"dry_run\" "
            "query param, without importing anything."
        )
    )
    import_notebooks_parser.add_argument(
        "--expected-sha256",
        dest="expected_sha256",
        default=None,
        help=(
            "Reject the import with an error if the local .zip's own "
            "sha256 doesn't match, via POST /api/notebooks/import's own "
            "?expected_sha256= query param -- e.g. the \"X-Bundle-SHA256\" "
            "header a prior `export-notebooks` already reported for this "
            "same archive, so a corrupted or wrong local copy is caught "
            "before anything is written rather than silently restored."
        )
    )
    import_notebooks_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"results\": [{\"filename\", \"status\", ...}, ...], "
            "\"succeeded_count\", \"failed_count\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # import-url command (upload a notebook a running dashboard fetches
    # itself from a URL, via POST /api/notebooks/import-url -- distinct
    # from `upload`, which always sends a local file's own bytes)
    import_url_parser = subparsers.add_parser(
        "import-url",
        help=(
            "Upload a notebook fetched from a URL a running dashboard "
            "instance downloads itself, via POST "
            "/api/notebooks/import-url."
        )
    )
    import_url_parser.add_argument(
        "url",
        help=(
            "http(s) URL of the notebook to fetch and upload -- the "
            "dashboard itself performs this fetch, not this CLI, so the "
            "URL only needs to be reachable from wherever the dashboard "
            "is running."
        )
    )
    _add_dashboard_url_and_timeout_arguments(import_url_parser)
    import_url_parser.add_argument(
        "--filename",
        default=None,
        help=(
            "Name to save the fetched notebook as, via POST "
            "/api/notebooks/import-url's own \"filename\" body field -- "
            "omitted, it's derived from the URL's own last path segment."
        )
    )
    import_url_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook of the same name on the "
            "dashboard, mirroring POST /api/notebooks/import-url's own "
            "\"overwrite\": true -- without this, an existing filename "
            "collision is rejected with a 409, exactly as it already is "
            "for `upload`."
        )
    )
    import_url_parser.add_argument(
        "--tags",
        help=(
            "Comma-separated tags to set on the imported notebook, via "
            "POST /api/notebooks/import-url's own \"tags\" body field."
        )
    )
    import_url_parser.add_argument(
        "--description",
        default=None,
        help=(
            "Description to set on the imported notebook, via POST "
            "/api/notebooks/import-url's own \"description\" body field."
        )
    )
    import_url_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        metavar="SHA256",
        help=(
            "Reject the import with an error unless the fetched "
            "content's own hash matches this value, via POST "
            "/api/notebooks/import-url's own \"expected_sha256\" body "
            "field."
        )
    )
    import_url_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Fetch the URL and report what would happen -- without "
            "saving anything -- via POST /api/notebooks/import-url's own "
            "\"dry_run\" body field."
        )
    )
    import_url_parser.add_argument(
        "--header",
        action="append",
        dest="headers",
        metavar="NAME:VALUE",
        help=(
            "An HTTP header to send with the fetch, as NAME:VALUE (e.g. "
            "--header \"Authorization: Bearer <token>\") -- via POST "
            "/api/notebooks/import-url's own \"headers\" body field, "
            "letting a private GitHub raw URL or an internal artifact "
            "server's own API key actually be reached. Repeat to send "
            "more than one; only ever sent to the URL's own original "
            "host -- dropped on any redirect to a different one."
        )
    )
    import_url_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"filename\", \"path\", \"overwritten\", \"sha256\", "
            "\"dry_run\", \"source_url\"}) instead of a human-readable "
            "summary, for scripting/automation."
        )
    )

    # list command (browse notebooks already on a running dashboard)
    list_parser = subparsers.add_parser(
        "list",
        help="List notebooks already uploaded to a running dashboard instance's GET /api/notebooks."
    )
    _add_dashboard_url_and_timeout_arguments(list_parser)
    list_parser.add_argument(
        "--search",
        default=None,
        help="Case-insensitive filename substring filter, mirroring GET /api/notebooks' own ?search=."
    )
    list_parser.add_argument(
        "--tag",
        default=None,
        help="Only list notebooks carrying this exact tag, mirroring GET /api/notebooks' own ?tag=."
    )
    list_parser.add_argument(
        "--description-search",
        default=None,
        help=(
            "Case-insensitive description substring filter, mirroring "
            "GET /api/notebooks' own ?description_search=."
        )
    )
    list_parser.add_argument(
        "--regex",
        action="store_true",
        help=(
            "Treat --search and --description-search as case-insensitive "
            "Python regular expressions instead of plain substrings, via "
            "GET /api/notebooks' own ?regex=true -- the same \"regex\" "
            "`search-functions`/`search-content` already offer for their "
            "own single search field, applied here to whichever of "
            "--search/--description-search is actually given."
        )
    )
    list_parser.add_argument(
        "--sha256",
        default=None,
        help=(
            "Only list the notebook(s) whose exact current content "
            "hashes to this value, mirroring GET /api/notebooks' own "
            "?sha256= -- the same digest `find-duplicates` reports per "
            "group and `deploy-history --source-sha256`/`compile-history "
            "--source-sha256` already filter by, so a hash read back "
            "from either history command can be checked against what's "
            "still in the catalog today."
        )
    )
    list_parser.add_argument(
        "--modified-after",
        default=None,
        dest="modified_after",
        metavar="ISO_DATETIME",
        help=(
            "Only list notebooks modified on or after this ISO 8601 "
            "datetime (e.g. 2026-01-01T00:00:00+00:00), mirroring GET "
            "/api/notebooks' own ?modified_after= -- a value with no UTC "
            "offset is assumed to already be UTC. Composes with "
            "--modified-before to bound a window; rejected if later than "
            "--modified-before."
        )
    )
    list_parser.add_argument(
        "--modified-before",
        default=None,
        dest="modified_before",
        metavar="ISO_DATETIME",
        help=(
            "Only list notebooks modified on or before this ISO 8601 "
            "datetime, mirroring GET /api/notebooks' own "
            "?modified_before= -- the complementary bound to "
            "--modified-after."
        )
    )
    list_parser.add_argument(
        "--sort",
        choices=["name", "size", "modified"],
        default="name",
        help="Field to sort by, mirroring GET /api/notebooks' own ?sort= (default: name)."
    )
    list_parser.add_argument(
        "--order",
        choices=["asc", "desc"],
        default="asc",
        help="Sort direction, mirroring GET /api/notebooks' own ?order= (default: asc)."
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Maximum number of notebooks to return (after --search/--tag "
            "filtering and --sort/--order), mirroring GET /api/notebooks' "
            "own ?limit=. Default: no limit -- every matching notebook."
        )
    )
    list_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Number of matching notebooks to skip before --limit is "
            "applied, mirroring GET /api/notebooks' own ?offset= -- e.g. "
            "for paging through a large list. Default: 0."
        )
    )
    list_parser.add_argument(
        "--checksums",
        action="store_true",
        help=(
            "Also request each notebook's own sha256, via GET "
            "/api/notebooks' own \"checksums\" query param -- the same "
            "per-entry checksum `remote-files list`/`versions list` "
            "already offer for a compiled bundle's own files/a "
            "notebook's own snapshotted versions, applied here to the "
            "notebook catalog itself. Applies to the same already-"
            "filtered/sorted/paginated page --search/--tag/--sort/"
            "--order/--limit/--offset already narrow down to; off by "
            "default, since hashing every matching notebook is real "
            "work most `list` calls don't need."
        )
    )
    list_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help=(
            "Response format to request via GET /api/notebooks' own "
            "?format= query param, the same \"json\"/\"csv\" choice "
            "`storage`/`deploy-history`/`compile-history` already offer "
            "for their own listings. \"csv\" prints the dashboard's own "
            "per-notebook CSV response straight to stdout (redirect it "
            "to a file, e.g. `> notebooks.csv`) -- the same "
            "already-filtered/sorted/paginated notebooks the \"json\" "
            "response's own \"notebooks\" would list. Every --search/"
            "--tag/--sort/--order/--limit/--offset above still applies; "
            "--json is ignored under --format csv, since the response "
            "isn't JSON at all."
        )
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"notebooks\", \"total_count\", \"limit\", "
            "\"offset\"}) instead of a human-readable listing, for "
            "scripting/automation."
        )
    )

    # info command (a single notebook's own metadata, without listing/
    # filtering every notebook on the dashboard just to find it)
    info_parser = subparsers.add_parser(
        "info",
        help="Show one notebook's own metadata via a running dashboard instance's GET /api/notebooks/{filename}/info."
    )
    info_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(info_parser)
    info_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"filename\", \"size_bytes\", \"modified_at\", "
            "\"currently_compiled\", \"tags\", ...}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # info-batch command (show several named notebooks' own metadata at
    # once, via POST /api/notebooks/info-batch -- distinct from `info`
    # above, which only ever fetches one notebook's own metadata at a
    # time)
    info_batch_parser = subparsers.add_parser(
        "info-batch",
        help=(
            "Show several named notebooks' own metadata at once via a "
            "running dashboard instance's POST /api/notebooks/info-batch."
        )
    )
    info_batch_parser.add_argument(
        "filename", nargs="+",
        help="Filenames of the notebooks to look up, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(info_batch_parser)
    info_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"results\": [{\"filename\", \"status\", ...}, ...], "
            "\"succeeded_count\", \"failed_count\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # search-functions command (find which uploaded notebooks define a
    # matching function, across every notebook on the dashboard at once)
    search_functions_parser = subparsers.add_parser(
        "search-functions",
        help="Find which notebooks already on a running dashboard instance define a matching function, via its GET /api/functions."
    )
    search_functions_parser.add_argument(
        "search",
        help="Case-insensitive substring to match against every uploaded notebook's own function names."
    )
    search_functions_parser.add_argument(
        "--tag",
        default=None,
        help=(
            "Only scan notebooks carrying this exact tag, mirroring GET "
            "/api/functions' own ?tag=."
        )
    )
    search_functions_parser.add_argument(
        "--sha256",
        default=None,
        help=(
            "Only scan the notebook(s) whose exact content hashes to "
            "this, mirroring GET /api/functions' own ?sha256= -- as "
            "reported by `find-duplicates`, for scanning just one "
            "duplicate-content group's own copies (which may carry "
            "different --tag values, or none at all) rather than every "
            "notebook on the dashboard. Composes with --tag as an AND, "
            "exactly like GET /api/functions itself."
        )
    )
    search_functions_parser.add_argument(
        "--modified-after",
        default=None,
        dest="modified_after",
        metavar="ISO_DATETIME",
        help=(
            "Only scan notebooks modified on or after this ISO 8601 "
            "datetime, mirroring GET /api/functions' own "
            "?modified_after= -- the same absolute-date-range filter "
            "`list`'s own --modified-after already provides for the "
            "notebook catalog, applied here to which notebooks get "
            "scanned for a matching function. A value with no UTC offset "
            "is assumed to already be UTC. Composes with "
            "--modified-before to bound a window, and with --tag/--sha256 "
            "as an AND; rejected if later than --modified-before."
        )
    )
    search_functions_parser.add_argument(
        "--modified-before",
        default=None,
        dest="modified_before",
        metavar="ISO_DATETIME",
        help=(
            "Only scan notebooks modified on or before this ISO 8601 "
            "datetime, mirroring GET /api/functions' own "
            "?modified_before=."
        )
    )
    search_functions_parser.add_argument(
        "--regex",
        action="store_true",
        help=(
            "Treat `search` as a case-insensitive Python regular "
            "expression instead of a plain substring, via GET "
            "/api/functions' own ?regex=true -- e.g. to find every "
            "function name ending in `_v1`/`_v2` rather than one exact, "
            "already-known name."
        )
    )
    search_functions_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap how many matching notebooks are returned, via GET /api/functions' own ?limit=."
    )
    search_functions_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many matching notebooks before --limit is applied, via GET /api/functions' own ?offset=."
    )
    search_functions_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help=(
            "Response format to request via GET /api/functions' own "
            "?format= query param, the same \"json\"/\"csv\" choice "
            "`list`/`storage`/`deploy-history`/`compile-history` already "
            "offer for their own listings. \"csv\" prints the dashboard's "
            "own CSV response straight to stdout (redirect it to a file, "
            "e.g. `> functions.csv`) -- one row per matching function, "
            "flattened out of the \"json\" response's own per-notebook "
            "\"matches\". Every --tag/--sha256/--modified-after/"
            "--modified-before/--regex/--limit/--offset above still "
            "applies; --json is ignored under --format csv, since the "
            "response isn't JSON at all."
        )
    )
    _add_dashboard_url_and_timeout_arguments(search_functions_parser)
    search_functions_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"search\", \"matches\": [{\"filename\", \"functions\"}, ...], "
            "\"notebook_count\", \"limit\", \"offset\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # search-content command (find which uploaded notebooks have a code
    # cell whose raw source contains a matching substring, across every
    # notebook on the dashboard at once -- distinct from
    # `search-functions`, which only ever matches a function's own name,
    # not a cell's actual code)
    search_content_parser = subparsers.add_parser(
        "search-content",
        help="Find which notebooks already on a running dashboard instance have a matching code cell, via its GET /api/notebooks/search-content."
    )
    search_content_parser.add_argument(
        "search",
        help="Case-insensitive substring to match against every uploaded notebook's own code cell source."
    )
    search_content_parser.add_argument(
        "--tag",
        default=None,
        help=(
            "Only scan notebooks carrying this exact tag, mirroring GET "
            "/api/notebooks/search-content's own ?tag=."
        )
    )
    search_content_parser.add_argument(
        "--sha256",
        default=None,
        help=(
            "Only scan the notebook(s) whose exact content hashes to "
            "this, mirroring GET /api/notebooks/search-content's own "
            "?sha256= -- as reported by `find-duplicates`, for scanning "
            "just one duplicate-content group's own copies rather than "
            "every notebook on the dashboard. Composes with --tag as an "
            "AND, exactly like the endpoint itself."
        )
    )
    search_content_parser.add_argument(
        "--modified-after",
        default=None,
        dest="modified_after",
        metavar="ISO_DATETIME",
        help=(
            "Only scan notebooks modified on or after this ISO 8601 "
            "datetime, mirroring GET /api/notebooks/search-content's own "
            "?modified_after= -- the same absolute-date-range filter "
            "`search-functions --modified-after` just gained, applied "
            "here to which notebooks get scanned for a matching code "
            "cell. A value with no UTC offset is assumed to already be "
            "UTC. Composes with --modified-before to bound a window, and "
            "with --tag/--sha256 as an AND; rejected if later than "
            "--modified-before."
        )
    )
    search_content_parser.add_argument(
        "--modified-before",
        default=None,
        dest="modified_before",
        metavar="ISO_DATETIME",
        help=(
            "Only scan notebooks modified on or before this ISO 8601 "
            "datetime, mirroring GET /api/notebooks/search-content's own "
            "?modified_before=."
        )
    )
    search_content_parser.add_argument(
        "--regex",
        action="store_true",
        help=(
            "Treat `search` as a case-insensitive Python regular "
            "expression instead of a plain substring, via GET "
            "/api/notebooks/search-content's own ?regex=true -- e.g. to "
            "find every notebook calling `pd\\.read_csv\\(.*index_col=` "
            "rather than one exact, unchanging literal string."
        )
    )
    search_content_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Cap how many matching notebooks are returned, via GET "
            "/api/notebooks/search-content's own ?limit=."
        )
    )
    search_content_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Skip this many matching notebooks before --limit is "
            "applied, via GET /api/notebooks/search-content's own ?offset=."
        )
    )
    search_content_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help=(
            "Response format to request via GET "
            "/api/notebooks/search-content's own ?format= query param, "
            "the same \"json\"/\"csv\" choice `search-functions` already "
            "offers for its own catalog-wide search. \"csv\" prints the "
            "dashboard's own CSV response straight to stdout (redirect "
            "it to a file, e.g. `> search_content.csv`) -- one row per "
            "matching code cell, flattened out of the \"json\" "
            "response's own per-notebook \"matches\". Every --tag/"
            "--sha256/--modified-after/--modified-before/--regex/"
            "--limit/--offset above still applies; --json is ignored "
            "under --format csv, since the response isn't JSON at all."
        )
    )
    _add_dashboard_url_and_timeout_arguments(search_content_parser)
    search_content_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"search\", \"matches\": [{\"filename\", \"matches\": "
            "[{\"cell_index\", \"snippet\"}, ...]}, ...], "
            "\"notebook_count\", \"limit\", \"offset\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # find-duplicates command (group already-uploaded notebooks that are
    # byte-identical, across every notebook on the dashboard at once)
    find_duplicates_parser = subparsers.add_parser(
        "find-duplicates",
        help=(
            "Group already-uploaded notebooks on a running dashboard "
            "instance that are byte-identical, via GET "
            "/api/notebooks/duplicates."
        )
    )
    find_duplicates_parser.add_argument(
        "--tag",
        help=(
            "Only scan notebooks carrying this exact tag for duplicates, "
            "via GET /api/notebooks/duplicates' own ?tag= query param -- "
            "e.g. to find duplicates among only your \"production\" "
            "notebooks, without an untagged or differently-tagged "
            "byte-identical notebook pulling it into the same group."
        )
    )
    find_duplicates_parser.add_argument(
        "--sha256",
        help=(
            "Narrow the report to at most the one group matching this "
            "exact content hash, via GET /api/notebooks/duplicates' own "
            "?sha256= query param -- the same digest GET "
            "/api/notebooks?sha256= and this dashboard's history commands "
            "already match by."
        )
    )
    find_duplicates_parser.add_argument(
        "--modified-after",
        default=None,
        dest="modified_after",
        metavar="ISO_DATETIME",
        help=(
            "Only scan notebooks modified on or after this ISO 8601 "
            "datetime, mirroring GET /api/notebooks/duplicates' own "
            "?modified_after= -- the same absolute-date-range filter "
            "`search-functions`/`search-content` already have, applied "
            "here to which notebooks get scanned for duplicates. A value "
            "with no UTC offset is assumed to already be UTC. Composes "
            "with --modified-before to bound a window, and with "
            "--tag/--sha256 as an AND; rejected if later than "
            "--modified-before."
        )
    )
    find_duplicates_parser.add_argument(
        "--modified-before",
        default=None,
        dest="modified_before",
        metavar="ISO_DATETIME",
        help=(
            "Only scan notebooks modified on or before this ISO 8601 "
            "datetime, mirroring GET /api/notebooks/duplicates' own "
            "?modified_before=."
        )
    )
    find_duplicates_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap how many duplicate groups are returned, via GET /api/notebooks/duplicates' own ?limit=."
    )
    find_duplicates_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many duplicate groups before --limit is applied, via GET /api/notebooks/duplicates' own ?offset=."
    )
    _add_dashboard_url_and_timeout_arguments(find_duplicates_parser)
    find_duplicates_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help=(
            "Response format to request via GET /api/notebooks/duplicates' "
            "own ?format= query param, the same \"json\"/\"csv\" choice "
            "`list`/`search-functions`/`search-content`/`storage` already "
            "offer for their own listings. \"csv\" prints the dashboard's "
            "own \"sha256,filename,size_bytes\" CSV response (one row per "
            "filename within a group) straight to stdout (redirect it to a "
            "file, e.g. `> duplicates.csv`) -- the same already-filtered/"
            "paginated duplicate groups the \"json\" response's own "
            "\"duplicate_groups\" would list. Every --tag/--sha256/"
            "--modified-after/--modified-before/--limit/--offset above "
            "still applies; --json is ignored under --format csv, since "
            "the response isn't JSON at all."
        )
    )
    find_duplicates_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"duplicate_groups\": [{\"sha256\", \"filenames\", "
            "\"size_bytes\"}, ...], \"group_count\", "
            "\"duplicate_notebook_count\", \"limit\", \"offset\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    # resolve-duplicates command (delete every duplicate but one per
    # group found by `find-duplicates` above, via POST
    # /api/notebooks/duplicates/resolve -- distinct from `find-duplicates`,
    # which only ever reports them)
    resolve_duplicates_parser = subparsers.add_parser(
        "resolve-duplicates",
        help=(
            "Delete every byte-identical duplicate notebook on a running "
            "dashboard instance, keeping one filename per group, via "
            "POST /api/notebooks/duplicates/resolve."
        )
    )
    resolve_duplicates_parser.add_argument(
        "--keep",
        action="append",
        metavar="SHA256=FILENAME",
        help=(
            "Override which filename to keep for a specific duplicate "
            "group (its own \"sha256\", as reported by `find-duplicates`), "
            "instead of the alphabetically-first filename in that group. "
            "Repeat --keep once per group to override."
        )
    )
    resolve_duplicates_parser.add_argument(
        "--tag",
        help=(
            "Only resolve duplicate groups among notebooks carrying this "
            "exact tag, via POST /api/notebooks/duplicates/resolve's own "
            "\"tag\" body field -- the identical scoping `find-duplicates "
            "--tag` already applies to its own report, so resolving only "
            "ever deletes what a matching `find-duplicates --tag` call "
            "would have reported."
        )
    )
    resolve_duplicates_parser.add_argument(
        "--sha256",
        help=(
            "Only resolve the one duplicate group matching this exact "
            "content hash, via POST /api/notebooks/duplicates/resolve's "
            "own \"sha256\" body field -- the identical scoping "
            "`find-duplicates --sha256` already applies to its own "
            "report, without touching any other duplicate group elsewhere "
            "in the catalog (or, with --tag, elsewhere in that tag)."
        )
    )
    resolve_duplicates_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which groups would be resolved and which filenames "
            "would be deleted, via POST "
            "/api/notebooks/duplicates/resolve's own \"dry_run\" body "
            "field, without deleting anything. Skips the confirmation "
            "prompt --yes would otherwise require, since nothing "
            "irreversible happens."
        )
    )
    _add_dashboard_url_and_timeout_arguments(resolve_duplicates_parser)
    resolve_duplicates_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `resolve-duplicates` asks for a y/N confirmation on "
            "the terminal before sending the request -- POST "
            "/api/notebooks/duplicates/resolve itself has no confirmation "
            "step of its own, and is irreversible. Ignored under "
            "--dry-run, which never prompts."
        )
    )
    resolve_duplicates_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"results\": [{\"sha256\", \"status\", \"kept_filename\", "
            "\"deleted_filenames\"}, ...], \"succeeded_count\", "
            "\"failed_count\"}) instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    # storage command (report disk usage across every uploaded notebook,
    # each one's current content plus its own version history, via GET
    # /api/notebooks/storage)
    storage_parser = subparsers.add_parser(
        "storage",
        help=(
            "Report disk usage across every notebook already uploaded to "
            "a running dashboard instance, including their own version "
            "history, via GET /api/notebooks/storage."
        )
    )
    storage_parser.add_argument(
        "--tag",
        help=(
            "Only report disk usage for notebooks currently carrying "
            "this exact tag, via GET /api/notebooks/storage's own "
            "?tag= query param -- both the per-notebook list and every "
            "running total are scoped to it."
        )
    )
    storage_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Cap how many of the biggest-first notebooks are returned, "
            "via GET /api/notebooks/storage's own ?limit= query param -- "
            "e.g. --limit 10 for just the 10 biggest. Every running "
            "total is unaffected, and still covers every matching "
            "notebook regardless of --limit/--offset."
        )
    )
    storage_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Skip this many of the biggest-first notebooks before "
            "--limit is applied, via GET /api/notebooks/storage's own "
            "?offset= query param, for paging past the first --limit."
        )
    )
    storage_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help=(
            "Response format to request via GET /api/notebooks/storage's "
            "own ?format= query param, the same \"json\"/\"csv\" choice "
            "`deploy-history`/`compile-history` already offer for their "
            "own history logs. \"csv\" prints the dashboard's own "
            "per-notebook CSV response straight to stdout (redirect it "
            "to a file, e.g. `> notebook_storage.csv`) -- the same "
            "biggest-first \"notebooks\" rows the \"json\" response's own "
            "would list, just without the catalog-wide running totals, "
            "which aren't one more per-notebook row. Every --tag/--limit/"
            "--offset above still applies; --json is ignored under "
            "--format csv, since the response isn't JSON at all."
        )
    )
    _add_dashboard_url_and_timeout_arguments(storage_parser)
    storage_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"notebooks\": [{\"filename\", \"notebook_bytes\", "
            "\"version_bytes\", \"version_count\", \"total_bytes\"}, ...], "
            "\"notebook_count\", \"limit\", \"offset\", "
            "\"total_notebook_bytes\", \"total_version_bytes\", "
            "\"total_version_count\", \"total_bytes\", \"max_notebooks\", "
            "\"notebooks_remaining\", \"max_total_storage_bytes\", "
            "\"storage_bytes_remaining\"}) instead of a human-readable "
            "summary, for scripting/automation."
        )
    )

    # download command (pull a notebook already on a running dashboard)
    download_parser = subparsers.add_parser(
        "download",
        help="Download a notebook already on a running dashboard instance's GET /api/notebooks/{filename}."
    )
    download_parser.add_argument(
        "filename", help="Filename of the notebook to download, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(download_parser)
    download_parser.add_argument(
        "--output",
        default=None,
        help="Path to save the downloaded notebook to. Default: the notebook's own filename, in the current directory."
    )
    download_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        metavar="SHA256",
        help=(
            "Verify the downloaded content's own hash matches this "
            "value -- read from the response's own \"X-Content-SHA256\" "
            "header, the same hash GET /api/notebooks/{filename} itself "
            "now reports -- before writing anything to disk, exiting "
            "with an error on a mismatch instead. The download-side "
            "complement to `upload --expected-sha256`, which verifies "
            "integrity on the way in instead of the way back out."
        )
    )
    download_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"filename\", \"path\", \"size_bytes\", "
            "\"sha256\"}) instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    # export-notebooks command (download several uploaded notebooks --
    # or, with no filenames given, every one of them -- as a single zip,
    # via GET /api/notebooks/export -- distinct from `download`'s own
    # single-notebook GET /api/notebooks/{filename})
    export_notebooks_parser = subparsers.add_parser(
        "export-notebooks",
        help=(
            "Download several already-uploaded notebooks (or, with no "
            "filenames given, every one of them) as a single zip, via "
            "GET /api/notebooks/export."
        )
    )
    export_notebooks_parser.add_argument(
        "filename", nargs="*",
        help=(
            "Filenames of the notebooks to export, as reported by `list`. "
            "Omit to export every uploaded notebook."
        )
    )
    export_notebooks_parser.add_argument(
        "--tag",
        help=(
            "Export every notebook currently carrying this exact tag, "
            "via GET /api/notebooks/export's own ?tag= query param, "
            "instead of naming filenames directly. Can't be combined "
            "with an explicit filename."
        )
    )
    export_notebooks_parser.add_argument(
        "--include-versions",
        action="store_true",
        dest="include_versions",
        help=(
            "Also bundle each exported notebook's own snapshotted version "
            "history, via GET /api/notebooks/export's own "
            "?include_versions= query param -- each notebook's own "
            "snapshots land under versions/<filename>/<version_id> in the "
            "downloaded zip, the same per-notebook layout `versions "
            "export` already uses for one notebook at a time."
        )
    )
    _add_dashboard_url_and_timeout_arguments(export_notebooks_parser)
    export_notebooks_parser.add_argument(
        "--output",
        default=None,
        help="Path to save the downloaded zip to. Default: notebooks_export.zip."
    )
    export_notebooks_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        help=(
            "Verify the exported bundle's own sha256 matches this value "
            "-- checked against GET /api/notebooks/export's own "
            "\"X-Bundle-SHA256\" response header -- before writing it to "
            "disk. The same content-integrity check `remote-build` "
            "already performs for a compiled bundle, applied here to a "
            "notebook export instead."
        )
    )
    export_notebooks_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"path\", \"size_bytes\", \"bundle_sha256\"}) "
            "instead of a human-readable summary, for scripting/"
            "automation."
        )
    )

    # delete command (remove a notebook already on a running dashboard)
    delete_parser = subparsers.add_parser(
        "delete",
        help=(
            "Delete a notebook (or, with --all, every notebook) already "
            "on a running dashboard instance, via its DELETE "
            "/api/notebooks/{filename} or DELETE /api/notebooks."
        )
    )
    delete_parser.add_argument(
        "filename", nargs="?", default=None,
        help=(
            "Filename of the notebook to delete, as reported by `list`. "
            "Omit when passing --all."
        )
    )
    _add_dashboard_url_and_timeout_arguments(delete_parser)
    delete_parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Delete every notebook currently uploaded to the dashboard, "
            "via DELETE /api/notebooks?confirm=true, instead of one "
            "filename via DELETE /api/notebooks/{filename} -- e.g. to "
            "reset the uploads directory before a demo or between CI "
            "runs, without discovering and deleting each filename one at "
            "a time. Mutually exclusive with passing a filename."
        )
    )
    delete_parser.add_argument(
        "--tag",
        default=None,
        help=(
            "With --all, only delete notebooks currently carrying this "
            "exact tag, via DELETE /api/notebooks's own ?tag= query "
            "param, leaving every other notebook untouched. Without "
            "this, --all deletes every uploaded notebook. Ignored (and "
            "rejected) without --all."
        )
    )
    delete_parser.add_argument(
        "--sha256",
        default=None,
        help=(
            "With --all, only delete notebooks whose content hashes to "
            "this exact value, via DELETE /api/notebooks's own ?sha256= "
            "query param -- the same exact-content-match filter `list` "
            "and `find-duplicates` already support, letting a caller "
            "remove every copy of one specific content hash (e.g. one "
            "reported by `find-duplicates`) regardless of which "
            "filename(s) it currently sits under. Composes with --tag: "
            "with both given, a notebook must match both to be deleted. "
            "Ignored (and rejected) without --all."
        )
    )
    delete_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `delete` asks for a y/N confirmation on the terminal "
            "before sending the request -- neither DELETE "
            "/api/notebooks/{filename} nor DELETE /api/notebooks has a "
            "confirmation step of its own (beyond that endpoint's own "
            "required ?confirm=true, which this always passes for --all "
            "once the terminal prompt -- or --yes -- has already "
            "confirmed it), and both are irreversible."
        )
    )
    delete_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report what would be deleted (one filename, or, with --all, "
            "every matching one), via DELETE /api/notebooks/{filename} or "
            "DELETE /api/notebooks's own \"dry_run\" query param, without "
            "deleting anything -- the same preview `delete-batch` already "
            "offers for deleting several named notebooks at once. Skips "
            "the confirmation prompt --yes would otherwise require (and, "
            "with --all, the endpoint's own \"?confirm=true\" requirement "
            "too), since nothing irreversible happens."
        )
    )
    delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response -- "
            "{\"status\", \"dry_run\", \"filename\", "
            "\"was_currently_compiled\"} for a single filename, or "
            "{\"status\", \"dry_run\", \"deleted_count\", "
            "\"deleted_filenames\", \"currently_compiled_notebook_deleted\"} "
            "for --all -- instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    # delete-batch command (remove several named notebooks at once, via
    # POST /api/notebooks/delete-batch -- distinct from `delete`'s own
    # single-filename/--all pair above: neither removes an arbitrary
    # caller-chosen *set* of notebooks in one call without either
    # discovering and deleting each one individually or wiping every
    # uploaded notebook via --all)
    delete_batch_parser = subparsers.add_parser(
        "delete-batch",
        help=(
            "Delete several named notebooks at once, via POST "
            "/api/notebooks/delete-batch."
        )
    )
    delete_batch_parser.add_argument(
        "filename", nargs="+",
        help="Filenames of the notebooks to delete, as reported by `list`."
    )
    delete_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which of the given filenames would be deleted (and "
            "which would fail, e.g. a typo'd filename), via POST "
            "/api/notebooks/delete-batch's own \"dry_run\" body field, "
            "without deleting anything. Skips the confirmation prompt "
            "--yes would otherwise require, since nothing irreversible "
            "happens."
        )
    )
    _add_dashboard_url_and_timeout_arguments(delete_batch_parser)
    delete_batch_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `delete-batch` asks for a y/N confirmation on the "
            "terminal before sending the request -- POST "
            "/api/notebooks/delete-batch itself has no confirmation step "
            "of its own and is irreversible. Ignored under --dry-run, "
            "which never prompts."
        )
    )
    delete_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"results\": [{\"filename\", \"status\", ...}, ...], "
            "\"succeeded_count\", \"failed_count\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # prune-versions command (discard every notebook's own snapshotted
    # versions older than a given age, across the whole catalog at once,
    # via DELETE /api/notebooks/versions -- distinct from `versions
    # clear`, which discards a single notebook's entire version history
    # regardless of age)
    prune_versions_parser = subparsers.add_parser(
        "prune-versions",
        help=(
            "Discard every notebook's own snapshotted versions older "
            "than a given age, across every notebook already uploaded "
            "to a running dashboard instance, via DELETE "
            "/api/notebooks/versions."
        )
    )
    prune_versions_parser.add_argument(
        "--older-than-days",
        type=int,
        required=True,
        dest="older_than_days",
        help="Discard any version snapshot saved more than this many days ago."
    )
    prune_versions_parser.add_argument(
        "--tag",
        help=(
            "Only prune versions for notebooks currently carrying this "
            "exact tag, via DELETE /api/notebooks/versions's own ?tag= "
            "query param, leaving every other notebook's own version "
            "history untouched. Without this, every notebook is pruned."
        )
    )
    prune_versions_parser.add_argument(
        "--sha256",
        help=(
            "Only prune versions for the notebook(s) whose exact current "
            "content hashes to this, via DELETE /api/notebooks/versions's "
            "own ?sha256= -- as reported by `find-duplicates`, for "
            "reclaiming space from just one duplicate-content group's own "
            "copies (which may carry different --tag values, or none at "
            "all) rather than every notebook. Composes with --tag as an "
            "AND, exactly like the endpoint itself."
        )
    )
    prune_versions_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which notebooks and version snapshots would be "
            "discarded, via DELETE /api/notebooks/versions's own "
            "?dry_run= query param, without deleting anything. Skips the "
            "confirmation prompt --yes would otherwise require, since "
            "nothing irreversible happens."
        )
    )
    _add_dashboard_url_and_timeout_arguments(prune_versions_parser)
    prune_versions_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the prune without an interactive prompt. Without "
            "this, `prune-versions` asks for a y/N confirmation on the "
            "terminal before sending the request -- DELETE "
            "/api/notebooks/versions itself has no confirmation step of "
            "its own, is irreversible, and affects every notebook's own "
            "version history at once. Ignored under --dry-run, which "
            "never prompts."
        )
    )
    prune_versions_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"older_than_days\", \"results\": [{\"filename\", "
            "\"deleted_version_ids\", \"deleted_count\"}, ...], "
            "\"notebook_count_affected\", \"total_deleted_count\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    # prune-temp-files command (remove orphaned upload ".part" temp files
    # left behind by a crashed/interrupted upload, via DELETE
    # /api/upload/temp-files)
    prune_temp_files_parser = subparsers.add_parser(
        "prune-temp-files",
        help=(
            "Remove orphaned upload temp files left behind by a crashed "
            "or interrupted upload on a running dashboard instance, via "
            "DELETE /api/upload/temp-files."
        )
    )
    prune_temp_files_parser.add_argument(
        "--older-than-seconds",
        type=int,
        dest="older_than_seconds",
        help=(
            "Only remove a temp file whose last modification is older "
            "than this many seconds. Defaults to the dashboard's own "
            "configured NOTEBOOK_API_STALE_UPLOAD_TEMP_FILE_SECONDS "
            "(the same threshold its automatic sweep already uses) when "
            "omitted."
        )
    )
    prune_temp_files_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which temp files would be removed, via DELETE "
            "/api/upload/temp-files's own ?dry_run= query param, "
            "without deleting anything. Skips the confirmation prompt "
            "--yes would otherwise require, since nothing irreversible "
            "happens."
        )
    )
    _add_dashboard_url_and_timeout_arguments(prune_temp_files_parser)
    prune_temp_files_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the prune without an interactive prompt. Without "
            "this, `prune-temp-files` asks for a y/N confirmation on the "
            "terminal before sending the request -- DELETE "
            "/api/upload/temp-files itself has no confirmation step of "
            "its own and is irreversible. Ignored under --dry-run, which "
            "never prompts."
        )
    )
    prune_temp_files_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"older_than_seconds\", \"deleted_files\": "
            "[{\"filename\", \"size_bytes\", \"age_seconds\"}, ...], "
            "\"deleted_count\", \"reclaimed_bytes\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # rename command (rename a notebook already on a running dashboard)
    rename_parser = subparsers.add_parser(
        "rename",
        help="Rename a notebook already on a running dashboard instance's PATCH /api/notebooks/{filename}."
    )
    rename_parser.add_argument(
        "filename", help="Current filename of the notebook, as reported by `list`."
    )
    rename_parser.add_argument(
        "new_filename",
        help="New filename to rename the notebook to (must end in .ipynb)."
    )
    _add_dashboard_url_and_timeout_arguments(rename_parser)
    rename_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook already at new_filename, "
            "mirroring PATCH /api/notebooks/{filename}'s own "
            "\"overwrite\": true -- without this, renaming onto an "
            "existing filename is rejected with a 409, exactly as it "
            "already is through that endpoint directly."
        )
    )
    rename_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report what would be renamed (including the 409 a same-name "
            "collision without --overwrite would raise), via PATCH "
            "/api/notebooks/{filename}'s own \"dry_run\" body field, "
            "without renaming anything -- the same preview `rename-many` "
            "already offers for renaming several notebooks at once."
        )
    )
    rename_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"dry_run\", \"filename\", \"new_filename\", "
            "\"was_currently_compiled\"}) instead of a human-readable "
            "summary, for scripting/automation."
        )
    )

    # rename-many command (rename several different notebooks at once,
    # each to its own new name, via POST /api/notebooks/rename-batch --
    # distinct from `rename` above, which only ever renames one notebook
    # per call)
    rename_many_parser = subparsers.add_parser(
        "rename-many",
        help=(
            "Rename several different notebooks at once, each to its own "
            "new filename, via POST /api/notebooks/rename-batch."
        )
    )
    rename_many_parser.add_argument(
        "entry", nargs="+", type=_parse_notebook_copy_pair,
        help=(
            "One or more \"filename:new_filename\" pairs, e.g. "
            "a.ipynb:a2.ipynb b.ipynb:b2.ipynb."
        )
    )
    _add_dashboard_url_and_timeout_arguments(rename_many_parser)
    rename_many_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook at any new_filename that "
            "already exists, mirroring POST /api/notebooks/rename-batch's "
            "own per-entry \"overwrite\": true -- applies uniformly to "
            "every entry given here."
        )
    )
    rename_many_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which entries would be renamed (and which would "
            "fail, e.g. a same-name collision without --overwrite), via "
            "POST /api/notebooks/rename-batch's own \"dry_run\" body "
            "field, without renaming anything."
        )
    )
    rename_many_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"results\": [{\"filename\", \"new_filename\", "
            "\"status\", \"was_currently_compiled\", ...}, ...], "
            "\"succeeded_count\", \"failed_count\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # copy command (duplicate a notebook already on a running dashboard,
    # leaving the source notebook untouched -- unlike `rename`, which
    # moves the one notebook it operates on)
    copy_parser = subparsers.add_parser(
        "copy",
        help="Duplicate a notebook already on a running dashboard instance's POST /api/notebooks/{filename}/copy."
    )
    copy_parser.add_argument(
        "filename", help="Filename of the notebook to duplicate, as reported by `list`."
    )
    copy_parser.add_argument(
        "new_filename",
        help="Filename for the new copy (must end in .ipynb)."
    )
    _add_dashboard_url_and_timeout_arguments(copy_parser)
    copy_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook already at new_filename, "
            "mirroring POST /api/notebooks/{filename}/copy's own "
            "\"overwrite\": true -- without this, copying onto an "
            "existing filename is rejected with a 409, exactly as it "
            "already is through that endpoint directly."
        )
    )
    copy_parser.add_argument(
        "--tags",
        help=(
            "Comma-separated tags for the new copy, via POST "
            "/api/notebooks/{filename}/copy's own \"tags\" body field -- "
            "overrides inheriting the source notebook's own tags, which "
            "is what happens when this is omitted."
        )
    )
    copy_parser.add_argument(
        "--description",
        default=None,
        help=(
            "Description for the new copy, via POST "
            "/api/notebooks/{filename}/copy's own \"description\" body "
            "field -- overrides inheriting the source notebook's own "
            "description, which is what happens when this is omitted."
        )
    )
    copy_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report what would be copied (including the 409 a same-name "
            "collision without --overwrite would raise), via POST "
            "/api/notebooks/{filename}/copy's own \"dry_run\" body field, "
            "without copying anything -- the same preview `copy-batch` "
            "already offers for copying to several destinations at once."
        )
    )
    copy_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"dry_run\", \"filename\", \"new_filename\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    # copy-batch command (duplicate a notebook already on a running
    # dashboard under several new names at once, via its own POST
    # /api/notebooks/{filename}/copy-batch -- distinct from `copy` above,
    # which only ever creates one new copy per call)
    copy_batch_parser = subparsers.add_parser(
        "copy-batch",
        help=(
            "Duplicate a notebook already on a running dashboard instance "
            "under several new names at once, via its POST "
            "/api/notebooks/{filename}/copy-batch."
        )
    )
    copy_batch_parser.add_argument(
        "filename", help="Filename of the notebook to duplicate, as reported by `list`."
    )
    copy_batch_parser.add_argument(
        "new_filename", nargs="+",
        help="Filenames for the new copies (each must end in .ipynb)."
    )
    _add_dashboard_url_and_timeout_arguments(copy_batch_parser)
    copy_batch_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook at any new_filename that "
            "already exists, mirroring POST "
            "/api/notebooks/{filename}/copy-batch's own \"overwrite\": "
            "true -- applies uniformly to every destination, the same "
            "single flag `copy` itself takes for its own one destination."
        )
    )
    copy_batch_parser.add_argument(
        "--tags",
        help=(
            "Comma-separated tags applied uniformly to every new copy, "
            "via POST /api/notebooks/{filename}/copy-batch's own \"tags\" "
            "body field -- overrides inheriting the source notebook's "
            "own tags, which is what happens when this is omitted."
        )
    )
    copy_batch_parser.add_argument(
        "--description",
        default=None,
        help=(
            "Description applied uniformly to every new copy, via POST "
            "/api/notebooks/{filename}/copy-batch's own \"description\" "
            "body field -- overrides inheriting the source notebook's "
            "own description, which is what happens when this is "
            "omitted."
        )
    )
    copy_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which destinations would be copied to (and which "
            "would fail, e.g. a same-name collision without --overwrite), "
            "via POST /api/notebooks/{filename}/copy-batch's own "
            "\"dry_run\" body field, without copying anything."
        )
    )
    copy_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"filename\", \"results\": [{\"new_filename\", "
            "\"status\", ...}, ...], \"succeeded_count\", "
            "\"failed_count\"}) instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    # copy-many command (duplicate several *different* notebooks at once,
    # each into its own new filename, via POST
    # /api/notebooks/copy-batch -- the mirror shape `copy-batch` above
    # deliberately doesn't cover: one fixed source fanned out across
    # several destinations there, vs. several different sources each with
    # their own destination here)
    copy_many_parser = subparsers.add_parser(
        "copy-many",
        help=(
            "Duplicate several different notebooks at once, each into "
            "its own new filename, via POST /api/notebooks/copy-batch."
        )
    )
    copy_many_parser.add_argument(
        "entry", nargs="+", type=_parse_notebook_copy_pair,
        help=(
            "One or more \"filename:new_filename\" pairs, e.g. "
            "a.ipynb:a-copy.ipynb b.ipynb:b-copy.ipynb."
        )
    )
    _add_dashboard_url_and_timeout_arguments(copy_many_parser)
    copy_many_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook at any new_filename that "
            "already exists, mirroring POST /api/notebooks/copy-batch's "
            "own per-entry \"overwrite\": true -- applies uniformly to "
            "every entry given here."
        )
    )
    copy_many_parser.add_argument(
        "--tags",
        help=(
            "Comma-separated tags applied uniformly to every new copy in "
            "this batch, via POST /api/notebooks/copy-batch's own "
            "per-entry \"tags\" field -- overrides each entry's own "
            "inheriting its own source notebook's own tags, which is "
            "what happens when this is omitted. The endpoint itself "
            "supports a different \"tags\"/\"description\" per entry "
            "(each entry names its own independent source and "
            "destination, so its own tags/description is its own "
            "entry's own business) -- this applies one uniform value to "
            "every entry given here instead, the same uniform-value "
            "convention `copy-batch`'s own --tags already applies across "
            "its own several destinations of one fixed source. Tag each "
            "entry differently by running `copy` once per notebook "
            "instead."
        )
    )
    copy_many_parser.add_argument(
        "--description",
        default=None,
        help=(
            "Description applied uniformly to every new copy in this "
            "batch, via POST /api/notebooks/copy-batch's own per-entry "
            "\"description\" field -- overrides each entry's own "
            "inheriting its own source notebook's own description, "
            "which is what happens when this is omitted. See --tags "
            "above for why this is one uniform value rather than a "
            "per-entry one."
        )
    )
    copy_many_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which entries would be copied (and which would fail, "
            "e.g. a same-name collision without --overwrite), via POST "
            "/api/notebooks/copy-batch's own \"dry_run\" body field, "
            "without copying anything."
        )
    )
    copy_many_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"results\": [{\"filename\", \"new_filename\", "
            "\"status\", ...}, ...], \"succeeded_count\", "
            "\"failed_count\"}) instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    # tags command group (view/replace a notebook's tags on a running
    # dashboard, mirroring GET/PUT /api/notebooks/{filename}/tags)
    tags_parser = subparsers.add_parser(
        "tags",
        help="View or replace the tags on a notebook already on a running dashboard instance."
    )
    tags_subparsers = tags_parser.add_subparsers(dest="tags_command", required=True)

    tags_get_parser = tags_subparsers.add_parser(
        "get", help="Show a notebook's tags via GET /api/notebooks/{filename}/tags."
    )
    tags_get_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(tags_get_parser)
    tags_get_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"filename\", \"tags\"}) instead of a "
            "human-readable listing, for scripting/automation."
        )
    )

    tags_list_parser = tags_subparsers.add_parser(
        "list",
        help=(
            "List every distinct tag in use across all notebooks on a "
            "running dashboard instance, via GET /api/tags."
        )
    )
    _add_dashboard_url_and_timeout_arguments(tags_list_parser)
    tags_list_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        dest="format",
        help=(
            "Response format to request via GET /api/tags' own ?format= "
            "query param, the same \"json\"/\"csv\" choice "
            "`list`/`find-duplicates`/`storage` already offer for their "
            "own listings. \"csv\" prints the dashboard's own "
            "\"tag,notebook_count\" CSV response straight to stdout "
            "(redirect it to a file, e.g. `> tags.csv`); --json is "
            "ignored under --format csv, since the response isn't JSON "
            "at all."
        )
    )
    tags_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"tags\": [{\"tag\", \"notebook_count\"}, ...]}) "
            "instead of a human-readable listing, for scripting/automation."
        )
    )

    tags_delete_parser = tags_subparsers.add_parser(
        "delete",
        help=(
            "Remove a tag from every notebook that currently carries it, "
            "via DELETE /api/tags/{tag}."
        )
    )
    tags_delete_parser.add_argument(
        "tag", help="Tag to remove from every notebook that has it, as reported by `tags list`."
    )
    _add_dashboard_url_and_timeout_arguments(tags_delete_parser)
    tags_delete_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which notebooks would be affected, via DELETE "
            "/api/tags/{tag}'s own ?dry_run= query param, without "
            "removing the tag from any of them. Skips the confirmation "
            "prompt --yes would otherwise require, since nothing "
            "irreversible happens."
        )
    )
    tags_delete_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the removal without an interactive prompt. Without "
            "this, `tags delete` asks for a y/N confirmation on the "
            "terminal before sending the request -- DELETE /api/tags/{tag} "
            "itself has no confirmation step of its own, and affects "
            "every notebook carrying the tag at once. Ignored under "
            "--dry-run, which never prompts."
        )
    )
    tags_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"tag\", \"affected_notebooks\", "
            "\"notebook_count\"}) instead of a human-readable summary, "
            "for scripting/automation."
        )
    )

    tags_apply_parser = tags_subparsers.add_parser(
        "apply",
        help=(
            "Add a tag to several notebooks at once, merging it into each "
            "one's existing tags, via POST /api/tags/{tag}/apply."
        )
    )
    tags_apply_parser.add_argument(
        "tag", help="Tag to add to every named notebook."
    )
    tags_apply_parser.add_argument(
        "filename", nargs="+",
        help="Filenames of the notebooks to tag, as reported by `list`."
    )
    tags_apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which of the named notebooks would be tagged, via "
            "POST /api/tags/{tag}/apply's own \"dry_run\" body field, "
            "without tagging anything."
        )
    )
    _add_dashboard_url_and_timeout_arguments(tags_apply_parser)
    tags_apply_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"tag\", \"results\": [{\"filename\", \"status\", ...}, ...], "
            "\"succeeded_count\", \"failed_count\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    tags_remove_parser = tags_subparsers.add_parser(
        "remove",
        help=(
            "Remove a tag from several named notebooks at once, leaving "
            "each one's other tags untouched, via POST "
            "/api/tags/{tag}/remove."
        )
    )
    tags_remove_parser.add_argument(
        "tag", help="Tag to remove from every named notebook."
    )
    tags_remove_parser.add_argument(
        "filename", nargs="+",
        help="Filenames of the notebooks to untag, as reported by `list`."
    )
    tags_remove_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which of the named notebooks would be untagged, via "
            "POST /api/tags/{tag}/remove's own \"dry_run\" body field, "
            "without removing anything."
        )
    )
    _add_dashboard_url_and_timeout_arguments(tags_remove_parser)
    tags_remove_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"tag\", \"results\": [{\"filename\", \"status\", ...}, ...], "
            "\"succeeded_count\", \"failed_count\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    tags_rename_parser = tags_subparsers.add_parser(
        "rename",
        help=(
            "Rename a tag on every notebook that currently carries it, "
            "via PATCH /api/tags/{tag}."
        )
    )
    tags_rename_parser.add_argument(
        "tag", help="Tag to rename, as reported by `tags list`."
    )
    tags_rename_parser.add_argument(
        "new_tag", help="New name for the tag."
    )
    _add_dashboard_url_and_timeout_arguments(tags_rename_parser)
    tags_rename_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which notebooks would be affected, via PATCH "
            "/api/tags/{tag}'s own \"dry_run\" body field, without "
            "renaming the tag on any of them. Skips the confirmation "
            "prompt --yes would otherwise require, since nothing "
            "irreversible happens."
        )
    )
    tags_rename_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the rename without an interactive prompt. Without "
            "this, `tags rename` asks for a y/N confirmation on the "
            "terminal before sending the request -- PATCH /api/tags/{tag} "
            "itself has no confirmation step of its own and affects "
            "every notebook carrying the tag at once. Ignored under "
            "--dry-run, which never prompts."
        )
    )
    tags_rename_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"tag\", \"new_tag\", \"affected_notebooks\", "
            "\"notebook_count\"}) instead of a human-readable summary, "
            "for scripting/automation."
        )
    )

    tags_set_parser = tags_subparsers.add_parser(
        "set",
        help="Replace a notebook's full tag set via PUT /api/notebooks/{filename}/tags."
    )
    tags_set_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    tags_set_parser.add_argument(
        "tag", nargs="*",
        help=(
            "Tags to set, replacing the notebook's entire existing tag "
            "set -- the same replace-not-merge contract PUT "
            "/api/notebooks/{filename}/tags itself has. Omit to clear "
            "every tag."
        )
    )
    _add_dashboard_url_and_timeout_arguments(tags_set_parser)
    tags_set_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report the validated, normalized tag set a real call would "
            "record, via PUT /api/notebooks/{filename}/tags's own "
            "\"dry_run\" body field, without replacing the notebook's "
            "own existing tags -- the same preview `tags set-batch` "
            "already offers for replacing several notebooks' tags at "
            "once."
        )
    )
    tags_set_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"dry_run\", \"filename\", \"tags\"}) instead "
            "of a human-readable summary, for scripting/automation."
        )
    )

    # tags set-batch (replace the full tag set for several notebooks at
    # once, each getting its own explicit tags, via POST
    # /api/notebooks/tags-batch -- distinct from `tags set` above, which
    # only ever replaces one notebook's tags, and from `tags apply`/
    # `tags remove`, which add/remove a single *shared* tag across
    # several notebooks rather than replacing each one's own full set)
    tags_set_batch_parser = tags_subparsers.add_parser(
        "set-batch",
        help=(
            "Replace the full tag set for several notebooks at once, "
            "each getting its own explicit tags, via POST "
            "/api/notebooks/tags-batch."
        )
    )
    tags_set_batch_parser.add_argument(
        "--entry",
        action="append",
        required=True,
        metavar="FILENAME=TAG1,TAG2,...",
        help=(
            "One notebook's own new tag set, as FILENAME=TAG1,TAG2,... "
            "(an empty right-hand side clears that notebook's tags). "
            "Repeat --entry once per notebook."
        )
    )
    _add_dashboard_url_and_timeout_arguments(tags_set_batch_parser)
    tags_set_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report the resulting tag set each entry would get (and "
            "which would fail, e.g. an unknown filename), via POST "
            "/api/notebooks/tags-batch's own \"dry_run\" body field, "
            "without replacing a single notebook's own tags."
        )
    )
    tags_set_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"results\": [{\"filename\", \"status\", "
            "...}, ...], \"succeeded_count\", \"failed_count\"}) instead "
            "of a human-readable summary, for scripting/automation."
        )
    )

    # description command group (view or replace a notebook's own
    # freeform description, via GET/PUT /api/notebooks/{filename}
    # /description -- distinct from `tags`, which is for categorical
    # labels, not freeform text)
    description_parser = subparsers.add_parser(
        "description",
        help="View or replace the freeform description on a notebook already on a running dashboard instance."
    )
    description_subparsers = description_parser.add_subparsers(
        dest="description_command", required=True
    )

    description_get_parser = description_subparsers.add_parser(
        "get",
        help="Show a notebook's description via GET /api/notebooks/{filename}/description."
    )
    description_get_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(description_get_parser)
    description_get_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"filename\", \"description\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    description_set_parser = description_subparsers.add_parser(
        "set",
        help="Replace a notebook's description via PUT /api/notebooks/{filename}/description."
    )
    description_set_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    description_set_parser.add_argument(
        "description",
        help=(
            "New description, replacing the notebook's entire existing "
            "one -- the same replace-not-append contract PUT "
            "/api/notebooks/{filename}/description itself has. Pass an "
            "empty string to clear it."
        )
    )
    _add_dashboard_url_and_timeout_arguments(description_set_parser)
    description_set_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report the validated, normalized description a real call "
            "would record, via PUT /api/notebooks/{filename}"
            "/description's own \"dry_run\" body field, without "
            "replacing the notebook's own existing description -- the "
            "same preview `description set-batch` already offers for "
            "replacing several notebooks' descriptions at once."
        )
    )
    description_set_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"dry_run\", \"filename\", \"description\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    # description set-batch (replace the description for several
    # notebooks at once, each getting its own explicit description, via
    # POST /api/notebooks/description-batch -- distinct from
    # `description set` above, which only ever replaces one notebook's
    # description)
    description_set_batch_parser = description_subparsers.add_parser(
        "set-batch",
        help=(
            "Replace the description for several notebooks at once, each "
            "getting its own explicit description, via POST "
            "/api/notebooks/description-batch."
        )
    )
    description_set_batch_parser.add_argument(
        "--entry",
        action="append",
        required=True,
        metavar="FILENAME=DESCRIPTION",
        help=(
            "One notebook's own new description, as FILENAME=DESCRIPTION "
            "(an empty right-hand side clears that notebook's "
            "description). Repeat --entry once per notebook."
        )
    )
    _add_dashboard_url_and_timeout_arguments(description_set_batch_parser)
    description_set_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report the resulting description each entry would get (and "
            "which would fail, e.g. an unknown filename), via POST "
            "/api/notebooks/description-batch's own \"dry_run\" body "
            "field, without replacing a single notebook's own "
            "description."
        )
    )
    description_set_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"results\": [{\"filename\", \"status\", "
            "...}, ...], \"succeeded_count\", \"failed_count\"}) instead "
            "of a human-readable summary, for scripting/automation."
        )
    )

    # source-url command group (view or manually correct/clear a
    # notebook's own recorded provenance URL, via GET/PUT
    # /api/notebooks/{filename}/source-url -- normally set automatically
    # by `import-url`/`remote-compile`'s own POST /api/notebooks/import-url,
    # this exists for the same reason `tags`/`description` do: correcting
    # or clearing a value by hand without direct server access)
    source_url_parser = subparsers.add_parser(
        "source-url",
        help="View or manually set/clear the recorded provenance URL on a notebook already on a running dashboard instance."
    )
    source_url_subparsers = source_url_parser.add_subparsers(
        dest="source_url_command", required=True
    )

    source_url_get_parser = source_url_subparsers.add_parser(
        "get",
        help="Show a notebook's recorded source_url via GET /api/notebooks/{filename}/source-url."
    )
    source_url_get_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(source_url_get_parser)
    source_url_get_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"filename\", \"source_url\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    source_url_set_parser = source_url_subparsers.add_parser(
        "set",
        help="Set or clear a notebook's recorded source_url via PUT /api/notebooks/{filename}/source-url."
    )
    source_url_set_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    source_url_set_parser.add_argument(
        "source_url",
        nargs="?",
        default="",
        help=(
            "New provenance URL (http:// or https://), replacing the "
            "notebook's entire existing one. Omit (or pass an empty "
            "string) to clear it."
        )
    )
    _add_dashboard_url_and_timeout_arguments(source_url_set_parser)
    source_url_set_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report the validated source_url a real call would record, "
            "via PUT /api/notebooks/{filename}/source-url's own "
            "\"dry_run\" body field, without replacing the notebook's "
            "own existing recorded value."
        )
    )
    source_url_set_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"dry_run\", \"filename\", \"source_url\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    # source-url set-batch (set/clear the recorded source_url for several
    # notebooks at once, each getting its own explicit value, via POST
    # /api/notebooks/source-url-batch -- distinct from `source-url set`
    # above, which only ever replaces one notebook's own value)
    source_url_set_batch_parser = source_url_subparsers.add_parser(
        "set-batch",
        help=(
            "Set or clear the recorded source_url for several notebooks "
            "at once, each getting its own explicit value, via POST "
            "/api/notebooks/source-url-batch."
        )
    )
    source_url_set_batch_parser.add_argument(
        "--entry",
        action="append",
        required=True,
        metavar="FILENAME=SOURCE_URL",
        help=(
            "One notebook's own new source_url, as FILENAME=SOURCE_URL "
            "(an empty right-hand side clears that notebook's recorded "
            "source_url). Repeat --entry once per notebook."
        )
    )
    _add_dashboard_url_and_timeout_arguments(source_url_set_batch_parser)
    source_url_set_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report the resulting source_url each entry would get (and "
            "which would fail, e.g. an unknown filename), via POST "
            "/api/notebooks/source-url-batch's own \"dry_run\" body "
            "field, without replacing a single notebook's own recorded "
            "source_url."
        )
    )
    source_url_set_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"results\": [{\"filename\", \"status\", "
            "...}, ...], \"succeeded_count\", \"failed_count\"}) instead "
            "of a human-readable summary, for scripting/automation."
        )
    )

    # remote-compile command (compile a notebook already on a running
    # dashboard, via its own POST /api/compile -- not this CLI's own
    # local `compile`, which never touches a dashboard at all)
    remote_compile_parser = subparsers.add_parser(
        "remote-compile",
        help="Compile a notebook already uploaded to a running dashboard instance, via its POST /api/compile."
    )
    remote_compile_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(remote_compile_parser)
    _add_function_selection_arguments(remote_compile_parser)
    _add_version_id_argument(remote_compile_parser, "POST /api/compile")
    remote_compile_parser.add_argument(
        "--smoke-test",
        action="store_true",
        dest="smoke_test",
        help=(
            "After compiling, actually import the compiled app on the "
            "dashboard itself and call its own GET /health, via POST "
            "/api/compile's own \"smoke_test\" body field -- catches a "
            "class of failure (a codegen bug producing syntactically "
            "broken Python, say) no purely static check can. This "
            "command exits 1 if the smoke test fails, even though the "
            "compile itself still succeeded and every file it wrote is "
            "still on disk."
        )
    )
    remote_compile_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"notebook\", \"version_id\", \"functions\", "
            "\"endpoints\", \"skipped_functions\", \"dependencies\", "
            "\"generated_files\"}, plus \"smoke_test\" when --smoke-test "
            "is given) instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    # remote-inspect command (the full inspection report -- functions,
    # dependencies, would-be endpoints, reserved-name conflicts, skipped/
    # private functions, and already-generated files -- for a notebook
    # already uploaded to a running dashboard, via its own POST
    # /api/inspect -- not this CLI's own local `inspect`, which only ever
    # reads a notebook on disk, and unlike `remote-compile` -- whose own
    # response happens to carry nearly this same shape -- never performs
    # a real compile or touches GENERATED_DIR/the currently-compiled app
    # just to get it)
    remote_inspect_parser = subparsers.add_parser(
        "remote-inspect",
        help="Inspect a notebook already uploaded to a running dashboard instance and display its analysis report, via its POST /api/inspect."
    )
    remote_inspect_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    # Not _add_version_id_argument (its own help text assumes a POST
    # body field, the way remote-compile/remote-validate's own
    # "version_id" actually is) -- POST /api/inspect itself has no
    # "version_id" support at all (unlike /api/compile, /api/validate):
    # resolve_upload_path (which it uses to resolve "notebook_path")
    # deliberately rejects any value with a directory component, and a
    # version snapshot lives under UPLOAD_DIR/.versions/<filename>/, not
    # directly inside UPLOAD_DIR as a flat filename (see
    # inspect_notebook_version's own docstring, routes/upload.py). A
    # dedicated GET .../versions/{version_id}/inspect exists specifically
    # for this instead, with the id in the URL path, not a body field --
    # confirmed missing here even though remote-compile/remote-validate
    # already reach the identical capability for their own endpoints.
    remote_inspect_parser.add_argument(
        "--version-id",
        default=None,
        dest="version_id",
        help=(
            "Inspect one of this notebook's own previously snapshotted "
            "versions (as reported by `versions list`) instead of its "
            "current content, via GET .../notebooks/{filename}/versions/"
            "{version_id}/inspect instead of POST /api/inspect."
        )
    )
    _add_dashboard_url_and_timeout_arguments(remote_inspect_parser)
    remote_inspect_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"functions\", \"dependencies\", \"generated_files\", "
            "\"reserved_name_conflicts\", \"endpoints\", "
            "\"skipped_functions\", \"private_functions\", "
            "\"excluded_imports\", \"duplicate_functions\"}) instead of "
            "the human-readable report, for scripting/automation. Under "
            "--version-id, the response also carries \"filename\"/"
            "\"version_id\" fields naming which snapshot was actually "
            "inspected."
        )
    )

    # remote-validate command (CI-friendly exit-code gate on a notebook
    # already uploaded to a running dashboard, via its own POST
    # /api/validate -- not this CLI's own local `validate`, which never
    # touches a dashboard, and unlike `remote-compile` never mutates
    # GENERATED_DIR or the currently-compiled app just to ask a yes/no
    # question)
    remote_validate_parser = subparsers.add_parser(
        "remote-validate",
        help="Check whether a notebook already uploaded to a running dashboard instance would compile cleanly, via its POST /api/validate."
    )
    remote_validate_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(remote_validate_parser)
    _add_version_id_argument(remote_validate_parser, "POST /api/validate")
    remote_validate_parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Also fail (exit 2) when the notebook has skipped functions "
            "(no endpoint will be generated for them) or duplicate "
            "functions (a name defined more than once) -- the same "
            "--strict `validate` itself already accepts. By default "
            "these are reported as a non-fatal warning (exit 1)."
        )
    )
    _add_function_selection_arguments(remote_validate_parser)
    remote_validate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\": "
            "\"pass\"|\"warn\"|\"fail\", \"notebook\", \"version_id\", "
            "\"reserved_name_conflicts\", \"skipped_functions\", "
            "\"duplicate_functions\"}) instead of the human-readable "
            "report, for scripting/automation."
        )
    )

    # validate-all command (check every notebook already uploaded to a
    # running dashboard at once, via its own GET /api/validate-all --
    # distinct from `remote-validate`, which only ever checks one
    # already-uploaded notebook named on the command line)
    validate_all_parser = subparsers.add_parser(
        "validate-all",
        help=(
            "Check whether every notebook already uploaded to a running "
            "dashboard instance would compile cleanly, via its GET "
            "/api/validate-all."
        )
    )
    _add_dashboard_url_and_timeout_arguments(validate_all_parser)
    validate_all_parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Also fail a notebook (and this command's own exit code) when "
            "it has skipped functions -- the same --strict "
            "`validate`/`remote-validate` already accept. By default "
            "these are reported as a non-fatal warning."
        )
    )
    validate_all_parser.add_argument(
        "--tag",
        help=(
            "Only validate notebooks currently carrying this exact tag, "
            "via GET /api/validate-all's own ?tag= query param, the same "
            "exact-match filter `search-functions --tag`/`list --tag` "
            "already accept."
        )
    )
    validate_all_parser.add_argument(
        "--sha256",
        default=None,
        help=(
            "Only validate the notebook(s) whose exact content hashes to "
            "this, via GET /api/validate-all's own ?sha256= query param "
            "-- as reported by `find-duplicates`, for revalidating just "
            "one duplicate-content group's own copies (which may carry "
            "different --tag values, or none at all) rather than every "
            "notebook on the dashboard. Composes with --tag as an AND, "
            "exactly like the endpoint itself."
        )
    )
    validate_all_parser.add_argument(
        "--modified-after",
        default=None,
        dest="modified_after",
        metavar="ISO_DATETIME",
        help=(
            "Only validate notebooks modified on or after this ISO 8601 "
            "datetime, mirroring GET /api/validate-all's own "
            "?modified_after= -- the same absolute-date-range filter "
            "`search-functions`/`search-content`/`find-duplicates` "
            "already have, applied here to which notebooks get "
            "validated. A value with no UTC offset is assumed to "
            "already be UTC. Composes with --modified-before to bound a "
            "window, and with --tag/--sha256 as an AND; rejected if "
            "later than --modified-before."
        )
    )
    validate_all_parser.add_argument(
        "--modified-before",
        default=None,
        dest="modified_before",
        metavar="ISO_DATETIME",
        help=(
            "Only validate notebooks modified on or before this ISO "
            "8601 datetime, mirroring GET /api/validate-all's own "
            "?modified_before=."
        )
    )
    validate_all_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Cap how many results are returned, via GET "
            "/api/validate-all's own ?limit= query param. "
            "pass_count/warn_count/fail_count are unaffected, and still "
            "cover every matching notebook regardless of --limit/--offset."
        )
    )
    validate_all_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Skip this many results before --limit is applied, via GET "
            "/api/validate-all's own ?offset= query param, for paging "
            "past the first --limit."
        )
    )
    validate_all_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        dest="format",
        help=(
            "Response format to request via GET /api/validate-all's own "
            "?format= query param, the same \"json\"/\"csv\" choice "
            "`list`/`find-duplicates`/`storage` already offer for their "
            "own listings. \"csv\" prints the dashboard's own "
            "\"filename,status,reserved_name_conflicts,skipped_functions,"
            "detail\" CSV response straight to stdout (redirect it to a "
            "file, e.g. for a CI job's own audit trail); --json is "
            "ignored under --format csv, since the response isn't JSON "
            "at all -- and this command always exits 0 under --format "
            "csv, since it's meant for archiving/reporting, not CI "
            "gating (use the default JSON/human mode, whose own exit "
            "code already reflects pass_count/warn_count/fail_count, for "
            "that)."
        )
    )
    validate_all_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"results\": [{\"filename\", \"status\", ...}, ...], "
            "\"result_count\", \"limit\", \"offset\", \"pass_count\", "
            "\"warn_count\", \"fail_count\"}) instead of the "
            "human-readable report, for scripting/automation."
        )
    )

    # requirements-preview command (preview requirements.txt for a
    # notebook already uploaded to a running dashboard, via its own POST
    # /api/requirements-preview -- without actually compiling it)
    requirements_preview_parser = subparsers.add_parser(
        "requirements-preview",
        help=(
            "Preview the exact requirements.txt a compile of an "
            "already-uploaded notebook would produce, via its POST "
            "/api/requirements-preview -- without actually compiling it."
        )
    )
    requirements_preview_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(requirements_preview_parser)
    _add_version_id_argument(requirements_preview_parser, "POST /api/requirements-preview")
    requirements_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"notebook\", \"version_id\", \"requirements\", "
            "\"explicit_requirements\", \"excluded_imports\"}) instead "
            "of a human-readable listing, for scripting/automation."
        )
    )

    # app-preview command (preview the generated app.py source for a
    # notebook already uploaded to a running dashboard, via its own POST
    # /api/app-preview -- unlike `remote-compile`, this never writes
    # anything or touches GENERATED_DIR/whatever it currently serves)
    app_preview_parser = subparsers.add_parser(
        "app-preview",
        help=(
            "Preview the exact app.py source a compile of an "
            "already-uploaded notebook would produce, via its POST "
            "/api/app-preview -- without actually compiling it."
        )
    )
    app_preview_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(app_preview_parser)
    _add_function_selection_arguments(app_preview_parser)
    _add_version_id_argument(app_preview_parser, "POST /api/app-preview")
    app_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"notebook\", \"version_id\", \"package_name\", "
            "\"app_code\"}) instead of a human-readable preview, for "
            "scripting/automation."
        )
    )

    # readme-preview command (preview the generated README.md for a
    # notebook already uploaded to a running dashboard, via its own POST
    # /api/readme-preview -- README.md was the one real compile-produced
    # artifact with no preview of its own before this; mirrors
    # app-preview above exactly, just for README.md instead of app.py)
    readme_preview_parser = subparsers.add_parser(
        "readme-preview",
        help=(
            "Preview the exact README.md a compile of an "
            "already-uploaded notebook would produce, via its POST "
            "/api/readme-preview -- without actually compiling it."
        )
    )
    readme_preview_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(readme_preview_parser)
    _add_function_selection_arguments(readme_preview_parser)
    _add_version_id_argument(readme_preview_parser, "POST /api/readme-preview")
    readme_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"notebook\", \"version_id\", \"package_name\", "
            "\"readme\"}) instead of a human-readable preview, for "
            "scripting/automation."
        )
    )

    # curl-preview command (preview curl commands for a notebook already
    # uploaded to a running dashboard, via its own POST /api/curl-preview
    # -- distinct from `remote-curl`, which downloads the notebook first
    # and always writes a shell script to disk; this only ever prints the
    # commands, no download and no file written)
    curl_preview_parser = subparsers.add_parser(
        "curl-preview",
        help=(
            "Preview curl commands for a notebook already uploaded to a "
            "running dashboard instance, via its POST /api/curl-preview "
            "-- no download, no script file written."
        )
    )
    curl_preview_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(curl_preview_parser)
    curl_preview_parser.add_argument(
        "--host",
        default="localhost",
        help="Host the generated commands target (default: localhost)."
    )
    curl_preview_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the generated commands target (default: 8000, matching `serve`'s own default)."
    )
    curl_preview_parser.add_argument(
        "--api-key",
        default=DEFAULT_DEV_API_KEY,
        dest="api_key",
        help=(
            "Value sent as the X-API-Key header (default: the generated "
            "app's own default dev key, used when NOTEBOOK_API_KEY isn't "
            "set on the server). Pass the same value configured via "
            "NOTEBOOK_API_KEY if it's been changed."
        )
    )
    _add_version_id_argument(curl_preview_parser, "POST /api/curl-preview")
    _add_callback_url_argument(curl_preview_parser)
    curl_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"notebook\", \"version_id\", \"commands\"}) instead of a "
            "human-readable listing, for scripting/automation."
        )
    )

    # postman-preview command (preview a Postman Collection v2.1.0 for a
    # notebook already uploaded to a running dashboard instance, via its
    # POST /api/postman-preview -- no download, no local file written,
    # mirroring curl-preview above for Postman instead of curl)
    postman_preview_parser = subparsers.add_parser(
        "postman-preview",
        help=(
            "Preview a Postman Collection for a notebook already uploaded "
            "to a running dashboard instance, via its POST "
            "/api/postman-preview -- no download, no collection file "
            "written."
        )
    )
    postman_preview_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(postman_preview_parser)
    postman_preview_parser.add_argument(
        "--host",
        default="localhost",
        help="Host the generated requests target (default: localhost)."
    )
    postman_preview_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the generated requests target (default: 8000, matching `serve`'s own default)."
    )
    postman_preview_parser.add_argument(
        "--api-key",
        default=DEFAULT_DEV_API_KEY,
        dest="api_key",
        help=(
            "Value sent as the X-API-Key header, and as the collection's "
            "own \"api_key\" variable (default: the generated app's own "
            "default dev key, used when NOTEBOOK_API_KEY isn't set on the "
            "server). Pass the same value configured via NOTEBOOK_API_KEY "
            "if it's been changed."
        )
    )
    postman_preview_parser.add_argument(
        "--collection-name",
        default=None,
        dest="collection_name",
        help=(
            "Name shown for the collection in Postman (default: the "
            "notebook's own filename, without its extension)."
        )
    )
    _add_function_selection_arguments(postman_preview_parser)
    _add_version_id_argument(postman_preview_parser, "POST /api/postman-preview")
    _add_callback_url_argument(postman_preview_parser)
    postman_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"notebook\", \"version_id\", \"collection\"}) instead of a "
            "human-readable listing, for scripting/automation."
        )
    )

    # dockerfile-preview command (preview the Dockerfile/.dockerignore a
    # compile on a running dashboard would produce, via its own GET
    # /api/dockerfile-preview -- unlike requirements-preview/app-preview/
    # curl-preview, "filename" is optional: neither artifact varies by
    # notebook for the overwhelming majority of them, only by this
    # dashboard's own configured output directory name and compiling
    # interpreter -- except a notebook using its own "# notebook-to-api:
    # apt-requires" directive, which is exactly what passing "filename"
    # here is for)
    dockerfile_preview_parser = subparsers.add_parser(
        "dockerfile-preview",
        help=(
            "Preview the exact Dockerfile and .dockerignore a compile on "
            "a running dashboard would produce, via its GET "
            "/api/dockerfile-preview -- without compiling anything."
        )
    )
    dockerfile_preview_parser.add_argument(
        "filename",
        nargs="?",
        default=None,
        help=(
            "Filename of a notebook already uploaded to the dashboard, "
            "as reported by `list` -- only needed to reflect that "
            "notebook's own \"# notebook-to-api: apt-requires\" "
            "directives (see GET /api/dockerfile-preview's own "
            "\"notebook_path\" query parameter); omitted, the preview is "
            "identical for every notebook that uses no such directive, "
            "which is most of them."
        )
    )
    _add_dashboard_url_and_timeout_arguments(dockerfile_preview_parser)
    _add_version_id_argument(dockerfile_preview_parser, "GET /api/dockerfile-preview")
    dockerfile_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"package_name\", \"compiling_python_version\", "
            "\"dockerfile\", \"dockerignore\", \"notebook\", "
            "\"version_id\"}) instead of a human-readable preview, for "
            "scripting/automation."
        )
    )

    # docker-compose-preview command (preview the docker-compose.yml a
    # compile on a running dashboard would produce, via its own GET
    # /api/docker-compose-preview -- takes no notebook argument, the same
    # reason dockerfile-preview above doesn't: neither the Dockerfile nor
    # this varies by notebook)
    docker_compose_preview_parser = subparsers.add_parser(
        "docker-compose-preview",
        help=(
            "Preview the exact docker-compose.yml a compile on a running "
            "dashboard would produce, via its GET "
            "/api/docker-compose-preview -- without compiling anything."
        )
    )
    _add_dashboard_url_and_timeout_arguments(docker_compose_preview_parser)
    docker_compose_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"package_name\", \"docker_compose\"}) instead of a "
            "human-readable preview, for scripting/automation."
        )
    )

    # k8s-preview command (preview the kubernetes.yaml a compile on a
    # running dashboard would produce, via its own GET /api/k8s-preview --
    # takes no notebook argument, the same reason docker-compose-preview
    # above doesn't: neither varies by notebook)
    k8s_preview_parser = subparsers.add_parser(
        "k8s-preview",
        help=(
            "Preview the exact kubernetes.yaml a compile on a running "
            "dashboard would produce, via its GET /api/k8s-preview -- "
            "without compiling anything."
        )
    )
    _add_dashboard_url_and_timeout_arguments(k8s_preview_parser)
    k8s_preview_parser.add_argument(
        "--image",
        default=None,
        help=(
            "Container image reference for the previewed manifest's own "
            "\"image:\" field (default: the dashboard's own "
            "\"<package_name>:latest\"), via GET /api/k8s-preview's own "
            "\"image\" query param -- pass the same tag intended for "
            "`deploy --tag`/`remote-deploy --tag` to check what "
            "`kubectl apply -f` would actually reference before running "
            "that deploy for real."
        )
    )
    k8s_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"package_name\", \"image\", \"kubernetes_manifest\"}) "
            "instead of a human-readable preview, for scripting/"
            "automation."
        )
    )

    # env-example-preview command (preview the exact .env.example a
    # compile on a running dashboard would produce, via its own GET
    # /api/env-example-preview -- like docker-compose-preview above,
    # takes no notebook argument at all: it never varies by notebook,
    # only by generate_fastapi_code's own fixed GENERATED_APP_ENV_VARS)
    env_example_preview_parser = subparsers.add_parser(
        "env-example-preview",
        help=(
            "Preview the exact .env.example a compile on a running "
            "dashboard would produce, via its GET "
            "/api/env-example-preview -- without compiling anything."
        )
    )
    _add_dashboard_url_and_timeout_arguments(env_example_preview_parser)
    env_example_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"env_example\"}) instead of a human-readable preview, for "
            "scripting/automation."
        )
    )

    # env-vars-preview command (preview the environment variables a
    # compiled app on a running dashboard would recognize, via its own
    # GET /api/env-vars-preview -- like dockerfile-preview above, takes
    # no notebook argument at all: none of these vary by notebook, only
    # by generate_fastapi_code's own fixed GENERATED_APP_ENV_VARS)
    env_vars_preview_parser = subparsers.add_parser(
        "env-vars-preview",
        help=(
            "Preview the environment variables a compiled app on a "
            "running dashboard would recognize (name, default, and what "
            "it controls), via its GET /api/env-vars-preview -- without "
            "compiling anything."
        )
    )
    _add_dashboard_url_and_timeout_arguments(env_vars_preview_parser)
    env_vars_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"environment_variables\": [{\"name\", \"default\", "
            "\"description\"}, ...]}) instead of a human-readable "
            "preview, for scripting/automation."
        )
    )

    # openapi-preview command (preview the computed OpenAPI schema for a
    # notebook already uploaded to a running dashboard, via its own POST
    # /api/openapi-preview -- the one preview family member (Commit #4,
    # 5869449) that never got a CLI mirror when it was added, unlike
    # every other one of its siblings above; mirrors app-preview/
    # readme-preview's own CLI wiring exactly, just for the computed
    # schema dict instead of source text)
    openapi_preview_parser = subparsers.add_parser(
        "openapi-preview",
        help=(
            "Preview the exact OpenAPI schema a compile of an "
            "already-uploaded notebook would produce, via its POST "
            "/api/openapi-preview -- without actually compiling it."
        )
    )
    openapi_preview_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(openapi_preview_parser)
    _add_function_selection_arguments(openapi_preview_parser)
    _add_version_id_argument(openapi_preview_parser, "POST /api/openapi-preview")
    openapi_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"notebook\", \"version_id\", \"package_name\", "
            "\"schema\"}) instead of a human-readable preview, for "
            "scripting/automation."
        )
    )

    # remote-build command (download the app currently compiled on a
    # running dashboard, via its own GET /api/download)
    remote_build_parser = subparsers.add_parser(
        "remote-build",
        help="Download the app currently compiled on a running dashboard instance as a zip, via its GET /api/download."
    )
    _add_dashboard_url_and_timeout_arguments(remote_build_parser)
    remote_build_parser.add_argument(
        "--output",
        default=None,
        help=(
            "Path to save the downloaded zip to. Default: the filename "
            "GET /api/download itself reports via Content-Disposition "
            "(e.g. \"generated.zip\"), in the current directory."
        )
    )
    remote_build_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        help=(
            "Verify the downloaded zip's own sha256 matches this value "
            "-- checked against GET /api/download's own \"X-Bundle-"
            "SHA256\" response header, the same one GET /api/generated"
            "?checksums=true reports as \"bundle_sha256\" for the exact "
            "same file set -- before writing it to disk. The same "
            "content-integrity check `download` and `versions get` "
            "already perform, applied here to the whole compiled "
            "bundle instead of a single notebook."
        )
    )
    remote_build_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"path\", \"size_bytes\", "
            "\"notebook_changed_since_compile\", \"bundle_sha256\"}) "
            "instead of a human-readable summary, for scripting/"
            "automation."
        )
    )

    # versions command group (view/download/restore a notebook's
    # snapshotted previous versions on a running dashboard, mirroring
    # GET/GET/POST /api/notebooks/{filename}/versions[/{version_id}[/restore]])
    versions_parser = subparsers.add_parser(
        "versions",
        help="View, download, or restore a notebook's snapshotted previous versions on a running dashboard instance."
    )
    versions_subparsers = versions_parser.add_subparsers(
        dest="versions_command", required=True
    )

    versions_list_parser = versions_subparsers.add_parser(
        "list",
        help="List a notebook's snapshotted versions via GET /api/notebooks/{filename}/versions."
    )
    versions_list_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_list_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Cap how many of the newest-first versions are returned, via "
            "GET /api/notebooks/{filename}/versions's own ?limit= query "
            "param. Without this, every version is returned, as before."
        )
    )
    versions_list_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Skip this many of the newest-first versions before --limit "
            "is applied, via GET /api/notebooks/{filename}/versions's own "
            "?offset= query param, for paging through a long-lived "
            "notebook's own history."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_list_parser)
    versions_list_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        dest="format",
        help=(
            "Response format to request via GET "
            "/api/notebooks/{filename}/versions's own ?format= query "
            "param, the same \"json\"/\"csv\" choice `list`/`find-"
            "duplicates`/`storage` already offer for their own listings. "
            "\"csv\" prints the dashboard's own \"version_id,size_bytes,"
            "saved_at\" CSV response straight to stdout (redirect it to a "
            "file); --json is ignored under --format csv, since the "
            "response isn't JSON at all."
        )
    )
    versions_list_parser.add_argument(
        "--saved-after",
        default=None,
        dest="saved_after",
        metavar="ISO_DATETIME",
        help=(
            "Only show versions saved on or after this ISO 8601 datetime, "
            "via GET /api/notebooks/{filename}/versions's own "
            "?saved_after= query param -- a value with no UTC offset is "
            "assumed to already be UTC. Composes with --saved-before to "
            "bound a window."
        )
    )
    versions_list_parser.add_argument(
        "--saved-before",
        default=None,
        dest="saved_before",
        metavar="ISO_DATETIME",
        help=(
            "Only show versions saved on or before this ISO 8601 "
            "datetime, via GET /api/notebooks/{filename}/versions's own "
            "?saved_before= query param."
        )
    )
    versions_list_parser.add_argument(
        "--checksums",
        action="store_true",
        help=(
            "Also request each version's own sha256, via GET "
            "/api/notebooks/{filename}/versions's own \"checksums\" "
            "query param -- the same per-entry sha256 `remote-files "
            "list --checksums` already offers for a compiled bundle's "
            "own files, e.g. to spot a redundant overwrite that produced "
            "byte-identical content, or to verify a specific historical "
            "version against a known-good hash before `versions "
            "restore`. Adds a matching \"sha256\" column under --format "
            "csv; a plain `versions list` (without --checksums) keeps "
            "its previous column set unchanged."
        )
    )
    versions_list_parser.add_argument(
        "--notes",
        action="store_true",
        help=(
            "Also request each version's own note, via GET "
            "/api/notebooks/{filename}/versions's own \"notes\" query "
            "param -- the same per-entry note `versions note-get` already "
            "prints for a single known version_id, just for every version "
            "in this one already-paginated listing at once, without an "
            "N+1 `versions note-get` per entry. Adds a matching \"note\" "
            "column under --format csv; a plain `versions list` (without "
            "--notes) keeps its previous column set unchanged."
        )
    )
    versions_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"filename\", \"versions\", \"total_count\", "
            "\"limit\", \"offset\"}) instead of a human-readable listing, "
            "for scripting/automation."
        )
    )

    versions_get_parser = versions_subparsers.add_parser(
        "get",
        help="Download one of a notebook's snapshotted versions via GET /api/notebooks/{filename}/versions/{version_id}."
    )
    versions_get_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_get_parser.add_argument(
        "version_id",
        help="Version id to download, as reported by `versions list`."
    )
    _add_dashboard_url_and_timeout_arguments(versions_get_parser)
    versions_get_parser.add_argument(
        "--output",
        default=None,
        help="Path to save the downloaded version to. Default: the version_id itself, in the current directory."
    )
    versions_get_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        metavar="SHA256",
        help=(
            "Verify the downloaded content's own hash matches this "
            "value -- read from the response's own \"X-Content-SHA256\" "
            "header, the same hash GET /api/notebooks/{filename}/"
            "versions/{version_id} itself now reports -- before writing "
            "anything to disk, exiting with an error on a mismatch "
            "instead. The version-pinned equivalent of `download "
            "--expected-sha256`."
        )
    )
    versions_get_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"filename\", \"version_id\", \"path\", "
            "\"size_bytes\", \"sha256\"}) instead of a human-readable "
            "summary, for scripting/automation."
        )
    )

    # versions note-get / note-set (view or replace the freeform note
    # attached to one specific snapshotted version, via GET/PUT
    # /api/notebooks/{filename}/versions/{version_id}/note -- the
    # per-version counterpart to the top-level `description get`/
    # `description set` commands above, just scoped to one snapshot
    # instead of the whole notebook, and named "note-get"/"note-set"
    # rather than nesting a third subparsers level under `versions`)
    versions_note_get_parser = versions_subparsers.add_parser(
        "note-get",
        help=(
            "Show the note attached to one of a notebook's snapshotted "
            "versions via GET /api/notebooks/{filename}/versions/"
            "{version_id}/note."
        )
    )
    versions_note_get_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_note_get_parser.add_argument(
        "version_id",
        help="Version id whose note to show, as reported by `versions list`."
    )
    _add_dashboard_url_and_timeout_arguments(versions_note_get_parser)
    versions_note_get_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"filename\", \"version_id\", \"note\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    versions_note_set_parser = versions_subparsers.add_parser(
        "note-set",
        help=(
            "Replace the note attached to one of a notebook's snapshotted "
            "versions via PUT /api/notebooks/{filename}/versions/"
            "{version_id}/note."
        )
    )
    versions_note_set_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_note_set_parser.add_argument(
        "version_id",
        help="Version id whose note to replace, as reported by `versions list`."
    )
    versions_note_set_parser.add_argument(
        "note",
        help=(
            "New note, replacing the version's entire existing one -- "
            "the same replace-not-append contract PUT .../versions/"
            "{version_id}/note itself has. Pass an empty string to clear "
            "it."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_note_set_parser)
    versions_note_set_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"filename\", \"version_id\", \"note\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    # versions note-batch (set/clear the note attached to several of one
    # notebook's own snapshotted versions at once, each getting its own
    # explicit note, via POST /api/notebooks/{filename}/versions/
    # note-batch -- distinct from `note-set` above, which only ever
    # replaces one version's own note; mirrors `tags set-batch`'s own
    # --entry convention one level down, per-version instead of
    # per-notebook)
    versions_note_batch_parser = versions_subparsers.add_parser(
        "note-batch",
        help=(
            "Set (or clear) the note attached to several of a notebook's "
            "snapshotted versions at once, each getting its own explicit "
            "note, via POST /api/notebooks/{filename}/versions/note-batch."
        )
    )
    versions_note_batch_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_note_batch_parser.add_argument(
        "--entry",
        action="append",
        required=True,
        metavar="VERSION_ID=NOTE",
        help=(
            "One version's own new note, as VERSION_ID=NOTE (an empty "
            "right-hand side clears that version's note) -- VERSION_ID "
            "as reported by `versions list`. Only the first '=' splits "
            "VERSION_ID from NOTE, so NOTE itself may safely contain "
            "further '=' characters. Repeat --entry once per version."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_note_batch_parser)
    versions_note_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report the resulting note each entry would get (and which "
            "would fail, e.g. an unknown version_id), via POST "
            ".../note-batch's own \"dry_run\" body field, without "
            "changing a single version's own note."
        )
    )
    versions_note_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"filename\", \"results\": [{\"version_id\", "
            "\"status\", ...}, ...], \"succeeded_count\", "
            "\"failed_count\"}) instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    versions_inspect_parser = versions_subparsers.add_parser(
        "inspect",
        help=(
            "Inspect one of a notebook's snapshotted versions -- its "
            "functions, dependencies, and would-be endpoints -- via GET "
            "/api/notebooks/{filename}/versions/{version_id}/inspect."
        )
    )
    versions_inspect_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_inspect_parser.add_argument(
        "version_id",
        help="Version id to inspect, as reported by `versions list`."
    )
    _add_dashboard_url_and_timeout_arguments(versions_inspect_parser)
    versions_inspect_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"filename\", \"version_id\", \"functions\", "
            "\"dependencies\", \"generated_files\", "
            "\"reserved_name_conflicts\", \"endpoints\", "
            "\"skipped_functions\"}) instead of a human-readable summary, "
            "for scripting/automation."
        )
    )

    versions_export_parser = versions_subparsers.add_parser(
        "export",
        help=(
            "Download a notebook's current content together with its "
            "entire snapshotted version history as a single zip, via GET "
            "/api/notebooks/{filename}/versions/export."
        )
    )
    versions_export_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_export_parser.add_argument(
        "--version-id",
        nargs="+",
        default=None,
        dest="version_ids",
        metavar="VERSION_ID",
        help=(
            "Only bundle these specific version snapshots (as reported "
            "by `versions list`) instead of the notebook's entire "
            "history, via GET /api/notebooks/{filename}/versions/"
            "export's own \"version_ids\" query param -- the same "
            "caller-chosen-subset shape `export-notebooks`' own filename "
            "arguments already give for picking which notebooks go into "
            "a catalog-wide export, just applied here to which versions "
            "of one notebook. Omit to bundle every version, as before."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_export_parser)
    versions_export_parser.add_argument(
        "--output",
        default=None,
        help="Path to save the downloaded zip to. Default: the server's own suggested filename."
    )
    versions_export_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        help=(
            "Verify the exported bundle's own sha256 matches this value "
            "-- checked against GET /api/notebooks/{filename}/versions/"
            "export's own \"X-Bundle-SHA256\" response header -- before "
            "writing it to disk."
        )
    )
    versions_export_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result ({\"status\", "
            "\"filename\", \"path\", \"size_bytes\", \"bundle_sha256\"}) "
            "instead of a human-readable summary, for scripting/"
            "automation."
        )
    )

    # versions import (restore a notebook's current content together with
    # its entire snapshotted version history from a single zip, via POST
    # /api/notebooks/{filename}/versions/import -- the counterpart to
    # `versions export` above)
    versions_import_parser = versions_subparsers.add_parser(
        "import",
        help=(
            "Restore a notebook's current content together with its "
            "entire snapshotted version history from a single zip "
            "produced by `versions export`, via POST "
            "/api/notebooks/{filename}/versions/import."
        )
    )
    versions_import_parser.add_argument(
        "filename",
        help="Filename to restore the archive's current content to (need not match its original name)."
    )
    versions_import_parser.add_argument(
        "zip_path", help="Path to the local zip archive to import, as produced by `versions export`."
    )
    _add_dashboard_url_and_timeout_arguments(versions_import_parser)
    versions_import_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook already at `filename`, "
            "mirroring POST /api/notebooks/{filename}/versions/import's "
            "own ?overwrite=true -- without this, restoring onto a "
            "filename that already exists is rejected with a 409, "
            "exactly as overwriting it any other way already is."
        )
    )
    versions_import_parser.add_argument(
        "--expected-sha256",
        dest="expected_sha256",
        default=None,
        help=(
            "Reject the restore with an error if the local zip's own "
            "sha256 doesn't match, via POST /api/notebooks/{filename}"
            "/versions/import's own ?expected_sha256= query param -- "
            "e.g. the \"X-Bundle-SHA256\" header a prior `versions "
            "export` already reported for this same archive, so a "
            "corrupted or wrong local copy is caught before anything is "
            "written rather than silently restored."
        )
    )
    versions_import_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"filename\", \"overwritten\", \"imported_version_ids\", "
            "\"imported_version_count\"}) instead of a human-readable "
            "summary, for scripting/automation."
        )
    )

    versions_copy_parser = versions_subparsers.add_parser(
        "copy",
        help=(
            "Duplicate one of a notebook's snapshotted versions into a "
            "brand-new notebook, leaving the source notebook's current "
            "content and version history untouched, via POST "
            "/api/notebooks/{filename}/versions/{version_id}/copy."
        )
    )
    versions_copy_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_copy_parser.add_argument(
        "version_id",
        help="Version id to copy, as reported by `versions list`."
    )
    versions_copy_parser.add_argument(
        "new_filename",
        help="Filename for the new notebook (must end in .ipynb, and differ from filename)."
    )
    _add_dashboard_url_and_timeout_arguments(versions_copy_parser)
    versions_copy_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook already at new_filename, "
            "mirroring this endpoint's own \"overwrite\": true -- "
            "without this, copying onto an existing filename is rejected "
            "with a 409."
        )
    )
    versions_copy_parser.add_argument(
        "--tags",
        help=(
            "Comma-separated tags for the new copy, via this endpoint's "
            "own \"tags\" body field -- without this, the new copy "
            "starts untagged (it never inherits the source notebook's "
            "own current tags, since they describe its *current* "
            "content, not this snapshot)."
        )
    )
    versions_copy_parser.add_argument(
        "--description",
        default=None,
        help=(
            "Description for the new copy, via this endpoint's own "
            "\"description\" body field -- without this, the new copy "
            "starts undescribed, the same reasoning --tags above gives."
        )
    )
    versions_copy_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report what would be copied (including the 409 a same-name "
            "collision without --overwrite would raise), via this "
            "endpoint's own \"dry_run\" body field, without copying "
            "anything -- the same preview `versions copy-batch` already "
            "offers for copying several versions at once."
        )
    )
    versions_copy_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"filename\", \"version_id\", \"new_filename\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    # versions copy-batch (duplicate several of a notebook's own
    # snapshotted versions into brand-new notebooks at once, via POST
    # /api/notebooks/{filename}/versions/copy-batch -- the identical
    # "one fixed source, several destinations" shape `copy-batch` above
    # provides for a notebook's current content, just sourced from
    # several of its past snapshots instead)
    versions_copy_batch_parser = versions_subparsers.add_parser(
        "copy-batch",
        help=(
            "Duplicate several of a notebook's own snapshotted versions "
            "into brand-new notebooks at once, via POST "
            "/api/notebooks/{filename}/versions/copy-batch."
        )
    )
    versions_copy_batch_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_copy_batch_parser.add_argument(
        "entry", nargs="+", type=_parse_version_copy_pair,
        help=(
            "One or more \"version_id:new_filename\" pairs, as reported "
            "by `versions list`, e.g. "
            "20240101T000000000000_abcd1234.ipynb:a.ipynb."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_copy_batch_parser)
    versions_copy_batch_parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace an existing notebook at any new_filename that "
            "already exists, mirroring POST "
            "/api/notebooks/{filename}/versions/copy-batch's own "
            "per-entry \"overwrite\": true -- applies uniformly to every "
            "entry given here."
        )
    )
    versions_copy_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which entries would be copied (and which would "
            "fail, e.g. a same-name collision without --overwrite), via "
            "POST /api/notebooks/{filename}/versions/copy-batch's own "
            "\"dry_run\" body field, without copying anything."
        )
    )
    versions_copy_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"filename\", \"results\": [{\"version_id\", "
            "\"new_filename\", \"status\", ...}, ...], "
            "\"succeeded_count\", \"failed_count\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    versions_restore_parser = versions_subparsers.add_parser(
        "restore",
        help="Restore a notebook to one of its snapshotted versions via POST /api/notebooks/{filename}/versions/{version_id}/restore."
    )
    versions_restore_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_restore_parser.add_argument(
        "version_id",
        help="Version id to restore, as reported by `versions list`."
    )
    versions_restore_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Confirm the version can be restored -- filename and "
            "version_id both exist -- via POST "
            "/api/notebooks/{filename}/versions/{version_id}/restore's "
            "own \"dry_run\" query param, without actually restoring "
            "anything. The same preview `restore-batch` already offers "
            "for restoring several different notebooks at once."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_restore_parser)
    versions_restore_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"filename\", \"restored_version_id\", "
            "\"dry_run\"}) instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    # versions restore-batch (roll back several *different* notebooks at
    # once, each to its own version_id, via POST
    # /api/notebooks/versions/restore-batch -- distinct from `restore`
    # above, which only ever restores one notebook at a time)
    versions_restore_batch_parser = versions_subparsers.add_parser(
        "restore-batch",
        help=(
            "Roll back several different notebooks at once, each to its "
            "own snapshotted version, via POST "
            "/api/notebooks/versions/restore-batch."
        )
    )
    versions_restore_batch_parser.add_argument(
        "entry", nargs="+", type=_parse_notebook_version_pair,
        help=(
            "One or more \"filename:version_id\" pairs, as reported by "
            "`list`/`versions list`, e.g. "
            "a.ipynb:20240101T000000000000_abcd1234.ipynb."
        )
    )
    versions_restore_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which of the given entries would be restored, via "
            "POST /api/notebooks/versions/restore-batch's own \"dry_run\" "
            "body field, without restoring anything."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_restore_batch_parser)
    versions_restore_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"results\": [{\"filename\", \"version_id\", "
            "\"status\", ...}, ...], \"succeeded_count\", "
            "\"failed_count\"}) instead of a human-readable summary, for "
            "scripting/automation."
        )
    )

    versions_delete_parser = versions_subparsers.add_parser(
        "delete",
        help="Permanently discard one of a notebook's snapshotted versions via DELETE /api/notebooks/{filename}/versions/{version_id}."
    )
    versions_delete_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_delete_parser.add_argument(
        "version_id",
        help="Version id to permanently discard, as reported by `versions list`."
    )
    _add_dashboard_url_and_timeout_arguments(versions_delete_parser)
    versions_delete_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `versions delete` asks for a y/N confirmation on the "
            "terminal before sending the request -- DELETE "
            "/api/notebooks/{filename}/versions/{version_id} itself has "
            "no confirmation step of its own, and is irreversible."
        )
    )
    versions_delete_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Confirm the version exists and report what would be "
            "discarded, via DELETE "
            "/api/notebooks/{filename}/versions/{version_id}'s own "
            "\"dry_run\" query param, without discarding it -- the same "
            "preview `versions delete-batch` already offers for "
            "discarding several versions at once. Skips the confirmation "
            "prompt --yes would otherwise require, since nothing "
            "irreversible happens."
        )
    )
    versions_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"dry_run\", \"filename\", "
            "\"deleted_version_id\"}) instead of a human-readable "
            "summary, for scripting/automation."
        )
    )

    # versions delete-batch (discard a caller-chosen set of a notebook's
    # own snapshotted versions at once, via POST
    # /api/notebooks/{filename}/versions/delete-batch -- the middle
    # ground between `versions delete` above, which only ever discards
    # one named version_id, and `versions clear` below, which discards
    # every one)
    versions_delete_batch_parser = versions_subparsers.add_parser(
        "delete-batch",
        help=(
            "Permanently discard several of a notebook's own snapshotted "
            "versions at once, via POST "
            "/api/notebooks/{filename}/versions/delete-batch."
        )
    )
    versions_delete_batch_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_delete_batch_parser.add_argument(
        "version_id", nargs="+",
        help="Version id(s) to permanently discard, as reported by `versions list`."
    )
    versions_delete_batch_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which of the given version_id(s) would be discarded, "
            "via POST /api/notebooks/{filename}/versions/delete-batch's "
            "own \"dry_run\" body field, without deleting anything. Skips "
            "the confirmation prompt --yes would otherwise require, since "
            "nothing irreversible happens."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_delete_batch_parser)
    versions_delete_batch_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `versions delete-batch` asks for a y/N confirmation on "
            "the terminal before sending the request -- POST "
            "/api/notebooks/{filename}/versions/delete-batch itself has "
            "no confirmation step of its own, and is irreversible. "
            "Ignored under --dry-run, which never prompts."
        )
    )
    versions_delete_batch_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"filename\", \"results\": [{\"version_id\", \"status\", "
            "...}, ...], \"succeeded_count\", \"failed_count\"}) instead "
            "of a human-readable summary, for scripting/automation."
        )
    )

    # versions clear (discard every one of a notebook's snapshotted
    # versions at once, via DELETE /api/notebooks/{filename}/versions --
    # distinct from `versions delete` above, which only ever discards one
    # named version_id at a time)
    versions_clear_parser = versions_subparsers.add_parser(
        "clear",
        help=(
            "Permanently discard every one of a notebook's snapshotted "
            "versions at once, via DELETE "
            "/api/notebooks/{filename}/versions."
        )
    )
    versions_clear_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_clear_parser.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        dest="older_than_days",
        help=(
            "Only discard this notebook's own version snapshots saved "
            "more than this many days ago, via DELETE "
            "/api/notebooks/{filename}/versions's own \"older_than_days\" "
            "query param -- the same age-based cutoff `prune-versions` "
            "already applies catalog-wide, just scoped to this one "
            "notebook instead of every notebook. Without this, every "
            "version is discarded regardless of age, as before."
        )
    )
    versions_clear_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report which versions would be discarded, via DELETE "
            "/api/notebooks/{filename}/versions's own \"dry_run\" query "
            "param, without deleting anything. Skips the confirmation "
            "prompt --yes would otherwise require, since nothing "
            "irreversible happens."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_clear_parser)
    versions_clear_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `versions clear` asks for a y/N confirmation on the "
            "terminal before sending the request -- DELETE "
            "/api/notebooks/{filename}/versions itself has no "
            "confirmation step of its own, and is irreversible. Ignored "
            "under --dry-run, which never prompts."
        )
    )
    versions_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"filename\", \"deleted_version_ids\", \"deleted_count\"}) "
            "instead of a human-readable summary, for scripting/automation."
        )
    )

    # versions diff (compare a snapshotted version's compiled API surface
    # against the notebook's current live content -- or another
    # snapshotted version -- before deciding whether to `versions
    # restore` it. Reuses `diff`'s own diff_notebook_functions/
    # print_notebook_diff exactly as `remote-diff` already does, just
    # with both sides potentially coming from GET
    # /api/notebooks/{filename}/versions/{version_id} instead of one side
    # always being a local file)
    versions_diff_parser = versions_subparsers.add_parser(
        "diff",
        help=(
            "Compare a notebook's snapshotted version against its current "
            "live content (or another snapshotted version), via GET "
            "/api/notebooks/{filename}/versions/{version_id}."
        )
    )
    versions_diff_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_diff_parser.add_argument(
        "version_id",
        help=(
            "The (older) version id to use as the baseline, as reported "
            "by `versions list`."
        )
    )
    versions_diff_parser.add_argument(
        "--against",
        default=None,
        metavar="VERSION_ID",
        help=(
            "Another version id to compare `version_id` against, instead "
            "of the notebook's current live content (the default) -- "
            "e.g. to see what changed between two older snapshots without "
            "restoring either one first."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_diff_parser)
    versions_diff_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON ({\"added\", \"removed\", "
            "\"changed\", \"unchanged\", \"compatible\", "
            "\"breaking_changes\"}) instead of the human-readable report, "
            "for scripting/automation."
        )
    )
    versions_diff_parser.add_argument(
        "--content",
        action="store_true",
        help=(
            "Also print a line-level unified diff of both sides' own raw "
            "code cell source, via diff_notebook_source (backend/"
            "inspector.py) -- distinct from the structural added/removed/"
            "changed-signature report this command already prints. See "
            "`diff --content`'s own help for what this shows, and "
            "`versions compare --content` for the entirely-server-side "
            "equivalent that needs no local temp files at all."
        )
    )
    versions_diff_parser.add_argument(
        "--fail-on-breaking",
        action="store_true",
        help=(
            "Exit with status 1 if classify_notebook_diff finds any "
            "breaking change between the two sides -- e.g. to refuse a "
            "`versions restore` that would break an existing caller. See "
            "`diff --fail-on-breaking`'s own help for exactly what counts "
            "as breaking."
        )
    )

    # versions compare (the same version-vs-current/version-vs-version
    # comparison `versions diff` already computes, but entirely
    # server-side via GET /api/notebooks/{filename}/versions/{version_id}
    # /diff -- neither side downloaded to a local temp file first, the
    # same round trip `diff-notebooks` already avoids for two
    # independently-uploaded notebooks)
    versions_compare_parser = versions_subparsers.add_parser(
        "compare",
        help=(
            "Compare a notebook's snapshotted version against its current "
            "live content (or another snapshotted version) entirely "
            "server-side, via GET "
            "/api/notebooks/{filename}/versions/{version_id}/diff."
        )
    )
    versions_compare_parser.add_argument(
        "filename", help="Filename of the notebook, as reported by `list`."
    )
    versions_compare_parser.add_argument(
        "version_id",
        help=(
            "The (older) version id to use as the baseline, as reported "
            "by `versions list`."
        )
    )
    versions_compare_parser.add_argument(
        "--against",
        default=None,
        metavar="VERSION_ID",
        help=(
            "Another version id to compare `version_id` against, instead "
            "of the notebook's current live content (the default)."
        )
    )
    versions_compare_parser.add_argument(
        "--content",
        action="store_true",
        help=(
            "Also request a line-level unified diff of both sides' own "
            "raw code cell source, via GET .../versions/{version_id}"
            "/diff's own \"content\" query param -- distinct from the "
            "structural added/removed/changed-signature report this "
            "command already prints."
        )
    )
    _add_dashboard_url_and_timeout_arguments(versions_compare_parser)
    versions_compare_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON ({\"added\", \"removed\", "
            "\"changed\", \"unchanged\", \"compatible\", "
            "\"breaking_changes\"[, \"content_diff\"]}) instead of "
            "the human-readable report, for scripting/automation."
        )
    )
    versions_compare_parser.add_argument(
        "--fail-on-breaking",
        action="store_true",
        help=(
            "Exit with status 1 if GET .../versions/{version_id}/diff's "
            "own \"compatible\" field is false -- see `diff --fail-on-"
            "breaking`'s own help for exactly what counts as breaking."
        )
    )

    # remote-files command group (list/preview/delete the compiled app's
    # own files on a running dashboard, mirroring GET/GET/DELETE
    # /api/generated[/{filename}]) -- distinct from `remote-build`, which
    # fetches the whole compiled output as one zip, not a per-file
    # listing or preview, and can't delete it
    remote_files_parser = subparsers.add_parser(
        "remote-files",
        help="List, preview, or delete the compiled app's own files on a running dashboard instance."
    )
    remote_files_subparsers = remote_files_parser.add_subparsers(
        dest="remote_files_command", required=True
    )

    remote_files_list_parser = remote_files_subparsers.add_parser(
        "list",
        help="List the compiled app's files via GET /api/generated."
    )
    remote_files_list_parser.add_argument(
        "--checksums",
        action="store_true",
        help=(
            "Also request each file's own sha256 and the whole bundle's "
            "own summary sha256, via GET /api/generated's own "
            "\"checksums\" query param -- e.g. to verify a compiled "
            "bundle fetched earlier (via `remote-build`, or a series of "
            "`remote-files get`) still byte-for-byte matches what the "
            "dashboard currently has compiled, without re-fetching it."
        )
    )
    _add_dashboard_url_and_timeout_arguments(remote_files_list_parser)
    remote_files_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"generated_files\", \"file_details\", \"compiled_at\", "
            "\"compiled_version_id\", \"source_notebook_filename\", "
            "\"source_notebook_exists\", "
            "\"generated_files_modified_since_compile\"[, "
            "\"bundle_sha256\"]}) instead of a human-readable listing, "
            "for scripting/automation."
        )
    )

    remote_files_get_parser = remote_files_subparsers.add_parser(
        "get",
        help="Preview one compiled file's text content via GET /api/generated/{filename}."
    )
    remote_files_get_parser.add_argument(
        "filename",
        help=(
            "Name of the compiled file to preview (e.g. \"app.py\", "
            "\"requirements.txt\", \"runtime/notebook_module.py\"), as "
            "reported by `remote-files list`."
        )
    )
    _add_dashboard_url_and_timeout_arguments(remote_files_get_parser)
    remote_files_get_parser.add_argument(
        "--output",
        default=None,
        help="Path to save the file's content to. Default: print it to stdout instead of writing a file."
    )
    remote_files_get_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        help=(
            "Verify the fetched content's sha256 matches this value "
            "before printing it or writing --output -- checked against "
            "GET /api/generated/{filename}'s own \"sha256\" response "
            "field. The same content-integrity check `download` and "
            "`versions get` already perform before writing anything to "
            "disk, applied here to a compiled output file instead of a "
            "notebook."
        )
    )
    remote_files_get_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"filename\", \"content\", \"sha256\"}) "
            "instead of just the raw content, for scripting/automation."
        )
    )

    remote_files_delete_parser = remote_files_subparsers.add_parser(
        "delete",
        help="Delete the compiled app currently on a running dashboard instance via DELETE /api/generated."
    )
    _add_dashboard_url_and_timeout_arguments(remote_files_delete_parser)
    remote_files_delete_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `remote-files delete` asks for a y/N confirmation on "
            "the terminal before sending the request -- DELETE "
            "/api/generated itself has no confirmation step of its own, "
            "and is irreversible."
        )
    )
    remote_files_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"generated_dir\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # remote-diff command (compare a local notebook against a notebook
    # already uploaded to a running dashboard, reusing `diff`'s own
    # diff_notebook_functions/print_notebook_diff -- unlike `diff`
    # itself, one side of the comparison is fetched from a running
    # dashboard instead of both being local paths)
    remote_diff_parser = subparsers.add_parser(
        "remote-diff",
        help="Compare a local notebook against a notebook already uploaded to a running dashboard instance's compiled API surface."
    )
    remote_diff_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    remote_diff_parser.add_argument(
        "notebook", nargs="?", default=None,
        help=(
            "Path to the local notebook to compare it against. Default: "
            "a file named `filename` in the current directory -- the "
            "same path `download filename` (no --output) would save it "
            "to."
        )
    )
    # `diff-notebooks` (below) already has --old-version/--new-version,
    # since GET /api/notebooks/diff supports pinning either side to a
    # past snapshot -- but remote-diff (one local file against one
    # already-uploaded notebook) always fetched the dashboard side's own
    # *current* content via GET /api/notebooks/{filename}, with no way to
    # pin it to a version instead, even though the identical capability
    # already exists via GET /api/notebooks/{filename}/versions/
    # {version_id} (get_notebook_version, routes/upload.py) -- confirmed
    # missing here even though `remote-diff` is otherwise the one command
    # of the three diff commands (`diff`, `diff-notebooks`, `remote-diff`)
    # built specifically to compare against something already on a
    # dashboard. A caller wanting "does my local draft still match what
    # was actually deployed N versions ago" had no way to ask that
    # through this command at all -- only current content.
    remote_diff_parser.add_argument(
        "--version-id",
        default=None,
        dest="version_id",
        help=(
            "Compare against one of `filename`'s own previously "
            "snapshotted versions (as reported by `versions list`) "
            "instead of its current content, via GET .../notebooks/"
            "{filename}/versions/{version_id} instead of GET "
            "/api/notebooks/{filename}."
        )
    )
    _add_dashboard_url_and_timeout_arguments(remote_diff_parser)
    remote_diff_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON ({\"added\", \"removed\", "
            "\"changed\", \"unchanged\", \"compatible\", "
            "\"breaking_changes\"}) instead of the human-readable report, "
            "for scripting/automation."
        )
    )
    remote_diff_parser.add_argument(
        "--fail-on-breaking",
        action="store_true",
        help=(
            "Exit with status 1 if classify_notebook_diff finds any "
            "breaking change between the two notebooks -- e.g. to refuse "
            "`upload --overwrite` when the local file would break an "
            "existing caller of the already-uploaded notebook's compiled "
            "API. See `diff --fail-on-breaking`'s own help for exactly "
            "what counts as breaking."
        )
    )
    remote_diff_parser.add_argument(
        "--content",
        action="store_true",
        help=(
            "Also print a line-level unified diff of both sides' own raw "
            "code cell source, via diff_notebook_source (backend/"
            "inspector.py) -- distinct from the structural added/removed/"
            "changed-signature report this command already prints. See "
            "`diff --content`'s own help for what this shows."
        )
    )
    remote_diff_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        metavar="SHA256",
        help=(
            "Verify the dashboard side's own downloaded content hash "
            "matches this value -- read from the response's own "
            "\"X-Content-SHA256\" header, the same header GET "
            "/api/notebooks/{filename}[/versions/{version_id}] itself "
            "already reports -- before diffing anything, exiting with an "
            "error on a mismatch instead. The same content-integrity "
            "check `download`/`versions get`/`remote-files get`/"
            "`remote-curl`/`remote-postman` already perform for their own "
            "downloads, applied here to the dashboard-side notebook "
            "`remote-diff` itself fetches."
        )
    )

    # diff-notebooks command (compare two notebooks that are BOTH already
    # uploaded to a running dashboard, via its own GET /api/notebooks/diff
    # -- distinct from `diff` (two local files) and `remote-diff` (one
    # local file against one already-uploaded notebook): neither compares
    # two independently-uploaded notebooks without a caller downloading
    # both first)
    diff_notebooks_parser = subparsers.add_parser(
        "diff-notebooks",
        help=(
            "Compare two notebooks that are both already uploaded to a "
            "running dashboard instance's compiled API surface, via its "
            "GET /api/notebooks/diff -- no download of either one."
        )
    )
    diff_notebooks_parser.add_argument(
        "old", help="Filename of the baseline notebook, as reported by `list`."
    )
    diff_notebooks_parser.add_argument(
        "new", help="Filename of the notebook to compare it against, as reported by `list`."
    )
    diff_notebooks_parser.add_argument(
        "--old-version",
        default=None,
        metavar="VERSION_ID",
        help=(
            "Pin the baseline side to one of `old`'s own previously "
            "snapshotted versions (as reported by `versions list`), via "
            "GET /api/notebooks/diff's own \"old_version\" query param, "
            "instead of `old`'s current live content."
        )
    )
    diff_notebooks_parser.add_argument(
        "--new-version",
        default=None,
        metavar="VERSION_ID",
        help=(
            "Pin the comparison side to one of `new`'s own previously "
            "snapshotted versions, via GET /api/notebooks/diff's own "
            "\"new_version\" query param, instead of `new`'s current "
            "live content."
        )
    )
    diff_notebooks_parser.add_argument(
        "--content",
        action="store_true",
        help=(
            "Also request a line-level unified diff of both sides' own "
            "raw code cell source, via GET /api/notebooks/diff's own "
            "\"content\" query param -- distinct from the structural "
            "added/removed/changed-signature report this command already "
            "prints, e.g. to actually see what changed in a function's "
            "own body, not just whether its signature did."
        )
    )
    _add_dashboard_url_and_timeout_arguments(diff_notebooks_parser)
    diff_notebooks_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit machine-readable JSON ({\"status\", \"old\", \"new\", "
            "\"old_version\", \"new_version\", \"added\", \"removed\", "
            "\"changed\", \"unchanged\", \"compatible\", "
            "\"breaking_changes\"[, \"content_diff\"]) instead of "
            "the human-readable report, for scripting/automation."
        )
    )
    diff_notebooks_parser.add_argument(
        "--fail-on-breaking",
        action="store_true",
        help=(
            "Exit with status 1 if GET /api/notebooks/diff's own "
            "\"compatible\" field is false -- see `diff --fail-on-"
            "breaking`'s own help for exactly what counts as breaking."
        )
    )

    # remote-curl command (generate a ready-to-run curl script for a
    # notebook already uploaded to a running dashboard, reusing
    # `export-curl`'s own generate_curl_commands -- unlike `export-curl`
    # itself, the notebook is fetched from a running dashboard instead of
    # read from a local path, the same "fetch to a temp file, reuse the
    # existing local-path pipeline unchanged" approach `remote-diff`
    # above already applies to diff_notebook_functions)
    remote_curl_parser = subparsers.add_parser(
        "remote-curl",
        help="Generate a shell script of curl commands for a notebook already uploaded to a running dashboard instance."
    )
    remote_curl_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(remote_curl_parser)
    remote_curl_parser.add_argument(
        "--host",
        default="localhost",
        help="Host the generated commands target (default: localhost) -- where the compiled app will actually run, not the dashboard itself."
    )
    remote_curl_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the generated commands target (default: 8000, matching `serve`'s own default)."
    )
    remote_curl_parser.add_argument(
        "--api-key",
        default=DEFAULT_DEV_API_KEY,
        dest="api_key",
        help=(
            "Value sent as the X-API-Key header (default: the generated "
            "app's own default dev key, used when NOTEBOOK_API_KEY isn't "
            "set on the server). Pass the same value configured via "
            "NOTEBOOK_API_KEY if it's been changed."
        )
    )
    remote_curl_parser.add_argument(
        "--output",
        default=None,
        help="Path to write the generated shell script to. Default: requests.sh"
    )
    remote_curl_parser.add_argument(
        "--version-id",
        default=None,
        dest="version_id",
        help=(
            "Generate commands from one of this notebook's own previously "
            "snapshotted versions (as reported by `versions list`) instead "
            "of its current content -- downloaded via GET "
            "/api/notebooks/{filename}/versions/{version_id} instead of "
            "GET /api/notebooks/{filename}, the same source `curl-preview "
            "--version-id` already lets a caller preview without writing "
            "a runnable script file at all."
        )
    )
    remote_curl_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        metavar="SHA256",
        help=(
            "Verify the downloaded notebook content's own hash matches "
            "this value -- read from the response's own "
            "\"X-Content-SHA256\" header, the same header GET "
            "/api/notebooks/{filename}[/versions/{version_id}] itself "
            "already reports -- before generating anything from it or "
            "writing the script file, exiting with an error on a "
            "mismatch instead. The same content-integrity check "
            "`download`/`versions get`/`remote-files get` already "
            "perform for their own downloads, applied here to the "
            "notebook `remote-curl` itself fetches."
        )
    )
    _add_function_selection_arguments(remote_curl_parser)
    _add_callback_url_argument(remote_curl_parser)
    remote_curl_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"path\", \"commands\"}) instead of only "
            "writing the script file, for scripting/automation."
        )
    )

    # remote-postman command (download a notebook already uploaded to a
    # running dashboard -- current content, or a past snapshot via
    # --version-id -- and write a real, runnable Postman Collection file
    # from it, the same way remote-curl above writes a real requests.sh
    # instead of only ever previewing one. `postman-preview` already
    # covers "what would this collection look like" against a dashboard
    # notebook (including --version-id, via POST /api/postman-preview's
    # own "version_id" body field), but its own docstring says verbatim
    # it writes no collection file -- a caller who actually wanted the
    # file itself, to import into Postman or hand to a teammate, had to
    # fall back to `download` (or `versions get`) followed by a separate
    # `export-postman` against the downloaded copy. remote-curl already
    # closed this identical gap for curl scripts; this closes it for
    # Postman collections.
    remote_postman_parser = subparsers.add_parser(
        "remote-postman",
        help="Generate a Postman Collection for a notebook already uploaded to a running dashboard instance."
    )
    remote_postman_parser.add_argument(
        "filename",
        help="Filename of the notebook already uploaded to the dashboard, as reported by `list`."
    )
    _add_dashboard_url_and_timeout_arguments(remote_postman_parser)
    remote_postman_parser.add_argument(
        "--host",
        default="localhost",
        help="Host the generated requests target (default: localhost) -- where the compiled app will actually run, not the dashboard itself."
    )
    remote_postman_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the generated requests target (default: 8000, matching `serve`'s own default)."
    )
    remote_postman_parser.add_argument(
        "--api-key",
        default=DEFAULT_DEV_API_KEY,
        dest="api_key",
        help=(
            "Value sent as the X-API-Key header, and as the collection's "
            "own \"api_key\" variable (default: the generated app's own "
            "default dev key, used when NOTEBOOK_API_KEY isn't set on the "
            "server). Pass the same value configured via NOTEBOOK_API_KEY "
            "if it's been changed."
        )
    )
    remote_postman_parser.add_argument(
        "--collection-name",
        default=None,
        dest="collection_name",
        help=(
            "Name shown for the collection in Postman (default: the "
            "notebook's own filename, without its extension)."
        )
    )
    remote_postman_parser.add_argument(
        "--output",
        default=None,
        help="Path to write the generated collection to. Default: postman_collection.json"
    )
    remote_postman_parser.add_argument(
        "--version-id",
        default=None,
        dest="version_id",
        help=(
            "Generate the collection from one of this notebook's own "
            "previously snapshotted versions (as reported by `versions "
            "list`) instead of its current content -- downloaded via GET "
            "/api/notebooks/{filename}/versions/{version_id} instead of "
            "GET /api/notebooks/{filename}, the same source "
            "`postman-preview --version-id` already lets a caller preview "
            "without writing a collection file at all."
        )
    )
    remote_postman_parser.add_argument(
        "--expected-sha256",
        default=None,
        dest="expected_sha256",
        metavar="SHA256",
        help=(
            "Verify the downloaded notebook content's own hash matches "
            "this value -- read from the response's own "
            "\"X-Content-SHA256\" header, the same header GET "
            "/api/notebooks/{filename}[/versions/{version_id}] itself "
            "already reports -- before generating anything from it or "
            "writing the collection file, exiting with an error on a "
            "mismatch instead. The same content-integrity check "
            "`download`/`versions get`/`remote-files get` already "
            "perform for their own downloads, applied here to the "
            "notebook `remote-postman` itself fetches."
        )
    )
    _add_function_selection_arguments(remote_postman_parser)
    _add_callback_url_argument(remote_postman_parser)
    remote_postman_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable JSON result "
            "({\"status\", \"path\", \"collection\"}) instead of only "
            "writing the collection file, for scripting/automation."
        )
    )

    # remote-export command group (export the OpenAPI schema or an SDK
    # client from the app currently compiled on a running dashboard, via
    # its own POST /api/export-openapi and POST /api/export-sdk --
    # distinct from the CLI's own local `export-openapi`/`export-sdk`,
    # which only ever read a local --app-dir, never a dashboard)
    remote_export_parser = subparsers.add_parser(
        "remote-export",
        help="Export the OpenAPI schema or an SDK client from the app currently compiled on a running dashboard instance."
    )
    remote_export_subparsers = remote_export_parser.add_subparsers(
        dest="remote_export_command", required=True
    )

    remote_export_openapi_parser = remote_export_subparsers.add_parser(
        "openapi",
        help="Export the OpenAPI schema via POST /api/export-openapi."
    )
    remote_export_openapi_parser.add_argument(
        "--format",
        choices=["json", "yaml"],
        default="json",
        help="Output format for the exported schema. Default: json."
    )
    _add_dashboard_url_and_timeout_arguments(remote_export_openapi_parser)
    remote_export_openapi_parser.add_argument(
        "--output",
        default=None,
        help="Path to save the exported schema to. Default: print it to stdout instead of writing a file."
    )
    remote_export_openapi_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"format\", \"path\", \"schema\"/\"content\"}) "
            "instead of just the exported schema, for "
            "scripting/automation."
        )
    )

    remote_export_sdk_parser = remote_export_subparsers.add_parser(
        "sdk",
        help="Generate an SDK client from the exported OpenAPI schema via POST /api/export-sdk."
    )
    remote_export_sdk_parser.add_argument(
        "--language",
        choices=["python", "typescript"],
        default="python",
        help="Target language for the generated SDK client (default: python)."
    )
    _add_dashboard_url_and_timeout_arguments(remote_export_sdk_parser)
    remote_export_sdk_parser.add_argument(
        "--output",
        default=None,
        help="Path to save the generated SDK client to. Default: print it to stdout instead of writing a file."
    )
    remote_export_sdk_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"language\", \"path\", \"code\"}) instead of "
            "just the generated client, for scripting/automation."
        )
    )

    # remote-deploy command (build, and optionally push, a Docker image
    # from the app currently compiled on a running dashboard, via its
    # own POST /api/deploy -- distinct from the CLI's own local `deploy`,
    # which always recompiles from a local notebook path first and runs
    # `docker build`/`docker push` on *this* machine, not the dashboard's)
    remote_deploy_parser = subparsers.add_parser(
        "remote-deploy",
        help="Build (and optionally push) a Docker image from the app currently compiled on a running dashboard instance, via its POST /api/deploy."
    )
    remote_deploy_parser.add_argument(
        "--tag",
        default=None,
        help=(
            "Docker image tag to build (default: the dashboard's own "
            "<generated-dir-basename>:latest, the same default POST "
            "/api/deploy itself falls back to when \"tag\" is omitted)."
        )
    )
    remote_deploy_parser.add_argument(
        "--push",
        action="store_true",
        help=(
            "Push the built image with `docker push <tag>` after a "
            "successful build -- run on the dashboard's own host, not "
            "this machine, so the tag must reference a registry that "
            "host can already push to (`docker login` already done "
            "there), the same requirement the CLI's own local `deploy "
            "--push` has for this machine instead."
        )
    )
    remote_deploy_parser.add_argument(
        "--platform",
        default=None,
        help=(
            "Target platform to pass to `docker build --platform` on the "
            "dashboard's own host (e.g. linux/amd64, linux/arm64). "
            "Defaults to that host's own Docker daemon default."
        )
    )
    remote_deploy_parser.add_argument(
        "--no-cache",
        action="store_true",
        dest="no_cache",
        help=(
            "Pass `docker build --no-cache` on the dashboard's own host, "
            "via POST /api/deploy's own \"no_cache\" body field -- "
            "forcing a clean rebuild of every layer instead of reusing "
            "Docker's own cache there."
        )
    )
    remote_deploy_parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Deploy even if the currently-compiled app no longer matches "
            "its source notebook's current content -- POST /api/deploy "
            "rejects a stale build with 409 unless this is set, the same "
            "staleness check `remote-compile` (run again) already clears."
        )
    )
    remote_deploy_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Check whether this deploy would actually go through -- a "
            "compiled app exists and isn't stale (unless --force) -- via "
            "POST /api/deploy's own \"dry_run\" body field, without "
            "running `docker build`/`docker push` or recording a deploy "
            "history entry."
        )
    )
    remote_deploy_parser.add_argument(
        "--smoke-test",
        action="store_true",
        dest="smoke_test",
        help=(
            "After a successful build, have the dashboard actually run "
            "the image in a real, throwaway container on its own host "
            "and poll its own GET /health until it responds, via POST "
            "/api/deploy's own \"smoke_test\" body field -- the same "
            "\"smoke_test\" the local `deploy --smoke-test` performs, "
            "just against the dashboard's own build instead. This "
            "command exits 1 if the smoke test fails, even though the "
            "image was still built (and, with --push, still pushed) "
            "successfully. Ignored under --dry-run."
        )
    )
    # Docker builds routinely run well past this file's other commands'
    # own 30s default (see _add_dashboard_url_and_timeout_arguments) --
    # matched here to DEPLOY_SUBPROCESS_TIMEOUT_SECONDS's own 600s
    # default, the limit the dashboard's own `docker build`/`docker push`
    # subprocess is already bounded by server-side, so this command's
    # own --timeout isn't the first thing to time out a slow build.
    _add_dashboard_url_and_timeout_arguments(remote_deploy_parser, default_timeout=600.0)
    remote_deploy_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response "
            "({\"status\", \"tag\", \"pushed\"}) instead of "
            "human-readable progress output, for scripting/automation. "
            "Also includes \"smoke_test\" when --smoke-test is given."
        )
    )

    # deploy-history command (this dashboard's own past POST /api/deploy
    # invocations, via its GET /api/deploy/history -- distinct from
    # `remote-deploy`, which triggers a new one)
    deploy_history_parser = subparsers.add_parser(
        "deploy-history",
        help=(
            "Show a running dashboard instance's own past deploys, via "
            "its GET /api/deploy/history."
        )
    )
    deploy_history_parser.add_argument(
        "--source-notebook",
        dest="source_notebook_filename",
        help=(
            "Only show deploys whose compiled source was this exact "
            "notebook filename, via GET /api/deploy/history's own "
            "?source_notebook_filename= query param."
        )
    )
    deploy_history_parser.add_argument(
        "--source-sha256",
        dest="source_notebook_sha256",
        help=(
            "Only show deploys whose compiled source was this exact "
            "notebook content, via GET /api/deploy/history's own "
            "?source_notebook_sha256= query param -- matches the "
            "notebook's content hash at deploy time (the same digest "
            "GET /api/notebooks/duplicates groups uploads by) rather "
            "than its current filename, so it still finds a deploy "
            "after the notebook was later renamed or overwritten."
        )
    )
    deploy_history_parser.add_argument(
        "--platform",
        help=(
            "Only show deploys built for this exact --platform value, via "
            "GET /api/deploy/history's own ?platform= query param."
        )
    )
    deploy_history_parser.add_argument(
        "--tag",
        help=(
            "Only show deploys built under this exact Docker image tag "
            "(e.g. \"myapp:latest\"), via GET /api/deploy/history's own "
            "?tag= query param -- not a notebook's own category tag."
        )
    )
    pushed_group = deploy_history_parser.add_mutually_exclusive_group()
    pushed_group.add_argument(
        "--pushed-only",
        action="store_const",
        dest="pushed",
        const=True,
        help="Only show deploys that were pushed to a registry."
    )
    pushed_group.add_argument(
        "--not-pushed",
        action="store_const",
        dest="pushed",
        const=False,
        help="Only show deploys that were not pushed to a registry."
    )
    deploy_history_parser.add_argument(
        "--deployed-after",
        default=None,
        dest="deployed_after",
        metavar="ISO_DATETIME",
        help=(
            "Only show deploys on or after this ISO 8601 datetime, via "
            "GET /api/deploy/history's own ?deployed_after= -- a value "
            "with no UTC offset is assumed to already be UTC. Composes "
            "with --deployed-before to bound a window."
        )
    )
    deploy_history_parser.add_argument(
        "--deployed-before",
        default=None,
        dest="deployed_before",
        metavar="ISO_DATETIME",
        help=(
            "Only show deploys on or before this ISO 8601 datetime, via "
            "GET /api/deploy/history's own ?deployed_before=."
        )
    )
    deploy_history_parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Only show (at most) this many of the most recent matching "
            "deploys, via GET /api/deploy/history's own ?limit= query "
            "param."
        )
    )
    deploy_history_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Skip this many of the most recent matching deploys before "
            "--limit is applied, via GET /api/deploy/history's own "
            "?offset= query param -- e.g. for paging through a deploy "
            "history longer than one --limit-sized page."
        )
    )
    deploy_history_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help=(
            "Response format to request via GET /api/deploy/history's "
            "own ?format= query param. \"csv\" prints the dashboard's "
            "own CSV response straight to stdout -- redirect it to a "
            "file (e.g. `> deploy_history.csv`) to open this dashboard's "
            "deploy history in a spreadsheet, or feed it into an "
            "existing CSV-based reporting pipeline. Every filter/--limit/"
            "--offset above still applies; --json is ignored under "
            "--format csv, since the response isn't JSON at all."
        )
    )
    _add_dashboard_url_and_timeout_arguments(deploy_history_parser)
    deploy_history_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"entries\": [{\"deployed_at\", \"tag\", \"platform\", "
            "\"pushed\", \"source_notebook_filename\", "
            "\"source_notebook_sha256\"}, ...], \"entry_count\"}) instead "
            "of a human-readable summary, for scripting/automation."
        )
    )

    # clear-deploy-history command (discard a running dashboard's entire
    # deploy history log at once, via its DELETE /api/deploy/history --
    # distinct from `deploy-history` above, which only ever reads it)
    clear_deploy_history_parser = subparsers.add_parser(
        "clear-deploy-history",
        help=(
            "Permanently discard a running dashboard instance's entire "
            "deploy history log, via its DELETE /api/deploy/history."
        )
    )
    clear_deploy_history_parser.add_argument(
        "--source-notebook",
        dest="source_notebook_filename",
        help=(
            "Only discard deploy history entries whose compiled source "
            "was this exact notebook filename, via DELETE "
            "/api/deploy/history's own ?source_notebook_filename= query "
            "param, leaving every other notebook's own deploy history "
            "entries in place. Without this, the entire deploy history "
            "log is discarded."
        )
    )
    clear_deploy_history_parser.add_argument(
        "--sha256",
        dest="source_notebook_sha256",
        help=(
            "Only discard deploy history entries whose compiled source "
            "hashes to this exact value, via DELETE /api/deploy/history's "
            "own ?source_notebook_sha256= query param -- the same "
            "exact-content-match filter `list`/`find-duplicates` already "
            "support, reaching a notebook's deploy history by its actual "
            "content even if it's since been renamed or re-uploaded under "
            "a different filename (which --source-notebook, matching only "
            "the filename recorded at deploy time, can't). Composes with "
            "--source-notebook/--older-than-days: given more than one, "
            "only entries matching all of them are discarded."
        )
    )
    clear_deploy_history_parser.add_argument(
        "--older-than-days",
        type=int,
        dest="older_than_days",
        help=(
            "Only discard deploy history entries older than this many "
            "days, via DELETE /api/deploy/history's own "
            "?older_than_days= query param. Composes with "
            "--source-notebook/--sha256: given more than one, only "
            "entries matching all of them are discarded. Without this, "
            "age plays no part in what's discarded."
        )
    )
    clear_deploy_history_parser.add_argument(
        "--deployed-after",
        default=None,
        dest="deployed_after",
        metavar="ISO_DATETIME",
        help=(
            "Only discard deploy history entries on or after this ISO "
            "8601 datetime, via DELETE /api/deploy/history's own "
            "?deployed_after= -- the same absolute-date-range filter "
            "`deploy-history`'s own --deployed-after already provides "
            "for *listing* entries, applied here to purge a specific "
            "bounded window (e.g. a bad rollout) instead of only ever "
            "the relative, open-ended --older-than-days. A value with no "
            "UTC offset is assumed to already be UTC. Composes with "
            "--deployed-before to bound a window, and with "
            "--source-notebook/--sha256/--older-than-days as an AND."
        )
    )
    clear_deploy_history_parser.add_argument(
        "--deployed-before",
        default=None,
        dest="deployed_before",
        metavar="ISO_DATETIME",
        help=(
            "Only discard deploy history entries on or before this ISO "
            "8601 datetime, via DELETE /api/deploy/history's own "
            "?deployed_before=."
        )
    )
    clear_deploy_history_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report how many entries would be discarded, via DELETE "
            "/api/deploy/history's own ?dry_run= query param, without "
            "discarding anything. Skips the confirmation prompt --yes "
            "would otherwise require, since nothing irreversible "
            "happens."
        )
    )
    _add_dashboard_url_and_timeout_arguments(clear_deploy_history_parser)
    clear_deploy_history_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `clear-deploy-history` asks for a y/N confirmation on "
            "the terminal before sending the request -- DELETE "
            "/api/deploy/history itself has no confirmation step of its "
            "own, and is irreversible. Ignored under --dry-run, which "
            "never prompts."
        )
    )
    clear_deploy_history_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"deleted_count\"}) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # compile-history command (this dashboard's own past POST
    # /api/compile invocations, via its GET /api/compile/history --
    # distinct from `remote-compile`, which triggers a new one, and from
    # `deploy-history` above, which is this dashboard's *deploy* history
    # instead)
    compile_history_parser = subparsers.add_parser(
        "compile-history",
        help=(
            "Show a running dashboard instance's own past compiles, via "
            "its GET /api/compile/history."
        )
    )
    compile_history_parser.add_argument(
        "--notebook",
        dest="notebook_filename",
        help=(
            "Only show compiles of this exact notebook filename, via GET "
            "/api/compile/history's own ?notebook_filename= query param."
        )
    )
    compile_history_parser.add_argument(
        "--source-sha256",
        dest="source_notebook_sha256",
        help=(
            "Only show compiles of this exact notebook content, via GET "
            "/api/compile/history's own ?source_notebook_sha256= query "
            "param -- matches the notebook's content hash at compile "
            "time (the same digest GET /api/notebooks/duplicates groups "
            "uploads by) rather than its current filename, so it still "
            "finds a compile after the notebook was later renamed or "
            "overwritten."
        )
    )
    compile_history_parser.add_argument(
        "--compiled-after",
        default=None,
        dest="compiled_after",
        metavar="ISO_DATETIME",
        help=(
            "Only show compiles on or after this ISO 8601 datetime, via "
            "GET /api/compile/history's own ?compiled_after= -- a value "
            "with no UTC offset is assumed to already be UTC. Composes "
            "with --compiled-before to bound a window."
        )
    )
    compile_history_parser.add_argument(
        "--compiled-before",
        default=None,
        dest="compiled_before",
        metavar="ISO_DATETIME",
        help=(
            "Only show compiles on or before this ISO 8601 datetime, via "
            "GET /api/compile/history's own ?compiled_before=."
        )
    )
    compile_history_parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Only show (at most) this many of the most recent matching "
            "compiles, via GET /api/compile/history's own ?limit= query "
            "param."
        )
    )
    compile_history_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help=(
            "Skip this many of the most recent matching compiles before "
            "--limit is applied, via GET /api/compile/history's own "
            "?offset= query param -- e.g. for paging through a compile "
            "history longer than one --limit-sized page."
        )
    )
    compile_history_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help=(
            "Response format to request via GET /api/compile/history's "
            "own ?format= query param, the same \"json\"/\"csv\" choice "
            "GET /api/deploy/history's own ?format= already offers for "
            "its own history log. \"csv\" prints the dashboard's own CSV "
            "response straight to stdout (redirect it to a file, e.g. "
            "`> compile_history.csv`). Every filter/--limit/--offset "
            "above still applies; --json is ignored under --format csv, "
            "since the response isn't JSON at all."
        )
    )
    _add_dashboard_url_and_timeout_arguments(compile_history_parser)
    compile_history_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"entries\": [{\"compiled_at\", \"notebook_filename\", "
            "\"source_notebook_sha256\", \"only\", \"exclude\", "
            "\"endpoint_count\", \"dependency_count\", "
            "\"skipped_function_count\"}, ...], \"entry_count\"}) instead "
            "of a human-readable summary, for scripting/automation."
        )
    )

    # clear-compile-history command (discard a running dashboard's entire
    # compile history log at once, via its DELETE /api/compile/history --
    # distinct from `compile-history` above, which only ever reads it)
    clear_compile_history_parser = subparsers.add_parser(
        "clear-compile-history",
        help=(
            "Permanently discard a running dashboard instance's entire "
            "compile history log, via its DELETE /api/compile/history."
        )
    )
    clear_compile_history_parser.add_argument(
        "--notebook",
        dest="notebook_filename",
        help=(
            "Only discard compile history entries for this exact notebook "
            "filename, via DELETE /api/compile/history's own "
            "?notebook_filename= query param, leaving every other "
            "notebook's own compile history entries in place. Without "
            "this, the entire compile history log is discarded."
        )
    )
    clear_compile_history_parser.add_argument(
        "--sha256",
        dest="source_notebook_sha256",
        help=(
            "Only discard compile history entries whose compiled source "
            "hashes to this exact value, via DELETE /api/compile/history's "
            "own ?source_notebook_sha256= query param -- the same "
            "exact-content-match filter `list`/`find-duplicates` already "
            "support, reaching a notebook's compile history by its actual "
            "content even if it's since been renamed or re-uploaded under "
            "a different filename (which --notebook, matching only the "
            "filename recorded at compile time, can't). Composes with "
            "--notebook/--older-than-days: given more than one, only "
            "entries matching all of them are discarded."
        )
    )
    clear_compile_history_parser.add_argument(
        "--older-than-days",
        type=int,
        dest="older_than_days",
        help=(
            "Only discard compile history entries older than this many "
            "days, via DELETE /api/compile/history's own "
            "?older_than_days= query param. Composes with "
            "--notebook/--sha256: given more than one, only entries "
            "matching all of them are discarded. Without this, age plays "
            "no part in what's discarded."
        )
    )
    clear_compile_history_parser.add_argument(
        "--compiled-after",
        default=None,
        dest="compiled_after",
        metavar="ISO_DATETIME",
        help=(
            "Only discard compile history entries on or after this ISO "
            "8601 datetime, via DELETE /api/compile/history's own "
            "?compiled_after= -- the same absolute-date-range filter "
            "`compile-history`'s own --compiled-after already provides "
            "for *listing* entries, applied here to purge a specific "
            "bounded window instead of only ever the relative, "
            "open-ended --older-than-days. A value with no UTC offset is "
            "assumed to already be UTC. Composes with --compiled-before "
            "to bound a window, and with --notebook/--sha256/"
            "--older-than-days as an AND."
        )
    )
    clear_compile_history_parser.add_argument(
        "--compiled-before",
        default=None,
        dest="compiled_before",
        metavar="ISO_DATETIME",
        help=(
            "Only discard compile history entries on or before this ISO "
            "8601 datetime, via DELETE /api/compile/history's own "
            "?compiled_before=."
        )
    )
    clear_compile_history_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Report how many entries would be discarded, via DELETE "
            "/api/compile/history's own ?dry_run= query param, without "
            "discarding anything. Skips the confirmation prompt --yes "
            "would otherwise require, since nothing irreversible "
            "happens."
        )
    )
    _add_dashboard_url_and_timeout_arguments(clear_compile_history_parser)
    clear_compile_history_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `clear-compile-history` asks for a y/N confirmation on "
            "the terminal before sending the request -- DELETE "
            "/api/compile/history itself has no confirmation step of its "
            "own, and is irreversible. Ignored under --dry-run, which "
            "never prompts."
        )
    )
    clear_compile_history_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the dashboard's own JSON response ({\"status\", "
            "\"dry_run\", \"deleted_count\"}) instead of a human-readable summary, "
            "for scripting/automation."
        )
    )

    # status command (a running dashboard's own health and configured
    # limits, in one call, via its GET /api/health and GET /api/config --
    # every other `remote-*`/notebook-management command already assumes
    # a dashboard is reachable and configured a certain way; this is the
    # one command whose whole job is finding that out first, before
    # scripting a sequence of them against it)
    status_parser = subparsers.add_parser(
        "status",
        help="Show a running dashboard instance's health and configured limits, via its GET /api/health and GET /api/config."
    )
    _add_dashboard_url_and_timeout_arguments(status_parser)
    status_parser.add_argument(
        "--check-writable",
        action="store_true",
        dest="check_writable",
        help=(
            "Also probe UPLOAD_DIR/GENERATED_DIR for an actual write, "
            "via GET /api/health's own ?check_writable=true query param "
            "-- catches a data volume that's gone read-only, hit a disk "
            "quota, or had its permissions changed since the dashboard "
            "started, none of which a plain \"is the process up\" health "
            "check notices on its own. Off by default, since it's real "
            "extra work most callers of this command don't need."
        )
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a combined machine-readable JSON result "
            "({\"health\": <GET /api/health response>, \"config\": <GET "
            "/api/config response>}) instead of a human-readable "
            "summary, for scripting/automation."
        )
    )

    # metrics command
    metrics_parser = subparsers.add_parser(
        "metrics",
        help=(
            "Show a running dashboard instance's own scrape-shaped "
            "operational metrics, via its GET /api/metrics/prometheus."
        )
    )
    _add_dashboard_url_and_timeout_arguments(metrics_parser)
    metrics_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Parse GET /api/metrics/prometheus' own Prometheus text "
            "exposition format into a flat {metric_name: value} JSON "
            "object instead of printing the raw text, for scripting/"
            "automation that wants one field at a time without its own "
            "Prometheus text parser."
        )
    )

    # app-status command -- the direct-app-talk counterpart to `status`
    # above, the same way app-metrics below is `metrics`'s: `status`
    # already answers "is this dashboard healthy, and what's it
    # configured with" in one call, but nothing gave the identical answer
    # for the actual product this dashboard exists to produce -- a
    # deployed compiled app. An operator who just ran `serve`/`docker
    # compose up`/a real deploy, wanting to confirm it actually started
    # healthy and see what it's configured with (rate limits, webhook
    # signing, allowed origins, ...) before scripting calls against it,
    # had to hit GET /health/GET /ready/GET /info/GET /config by hand,
    # one curl per endpoint, and piece the picture together themselves.
    # No --api-key: GET /health/GET /ready/GET /info/GET /config are all
    # deliberately unauthenticated (see RESERVED_INFRASTRUCTURE_NAMES'
    # own health_check/readiness_check/service_info/service_config
    # entries, api_generator.py) so a load balancer/readiness probe can
    # reach them with no credential of its own -- the identical reasoning
    # app-metrics' own missing --api-key already documents for
    # GET /metrics/prometheus.
    app_status_parser = subparsers.add_parser(
        "app-status",
        help=(
            "Show a compiled app's own health, readiness, and configured "
            "limits, via its GET /health, GET /ready, GET /info, and GET "
            "/config. Distinct from `status` above, which shows this "
            "dashboard's own health/config instead."
        )
    )
    app_status_parser.add_argument(
        "--host",
        default="localhost",
        help=(
            "Host the compiled app is actually reachable at (default: "
            "localhost) -- the same convention `app-metrics`/`app-call` "
            "already use."
        )
    )
    app_status_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the compiled app is actually reachable at (default: 8000, matching `serve`'s own default)."
    )
    app_status_parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the app to respond before giving up (default: 10)."
    )
    app_status_parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Keep polling every --interval seconds (Ctrl+C to stop) "
            "instead of fetching once and exiting -- useful for watching "
            "a rollout, or a flapping health check, without wrapping "
            "this command in a separate `watch` utility of its own "
            "(unavailable on Windows, and not something this CLI could "
            "otherwise rely on being installed)."
        )
    )
    app_status_parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between polls under --watch (default: 2)."
    )
    app_status_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a combined machine-readable JSON result ({\"health\": "
            "<GET /health response>, \"ready\": <GET /ready response>, "
            "\"info\": <GET /info response>, \"config\": <GET /config "
            "response>}) instead of a human-readable summary, for "
            "scripting/automation -- the same combined shape `status "
            "--json` already returns for this dashboard's own health/"
            "config. Under --watch, one such object (plus a "
            "\"timestamp\") is printed per poll, one per line, so a "
            "consumer can stream-parse each snapshot as it arrives "
            "rather than waiting for this command to exit."
        )
    )

    # app-auth command -- the direct-app-talk counterpart to a compiled
    # app's own /auth/status, /auth/info, and /auth/validate, none of
    # which app-status above ever calls (it deliberately sticks to the
    # four *unauthenticated* infra routes -- see its own comment). The
    # generated Python/TypeScript SDK clients already expose auth_status/
    # auth_info/auth_validate as real methods (see generate_python_sdk's
    # own docstring, backend/exporters/sdk_generator.py: "Also always
    # includes health/ready/info/metrics/uptime/auth_status/auth_info/
    # auth_validate"), but this CLI's own direct-app family never got an
    # equivalent -- an operator wanting to confirm a --api-key actually
    # works against a given deployment (the single most common first
    # question when wiring up a new caller), or just see how many keys/
    # protected endpoints it's configured with, had no way to ask short
    # of a raw `curl -H "X-API-Key: ..."` by hand, or writing throwaway
    # code against a generated SDK client just to check one credential.
    app_auth_parser = subparsers.add_parser(
        "app-auth",
        help=(
            "Show a compiled app's own authentication status/config, and "
            "confirm --api-key is actually valid against it, via its GET "
            "/auth/status, GET /auth/info, and GET /auth/validate."
        )
    )
    _add_app_host_port_arguments(app_auth_parser)
    app_auth_parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Keep polling every --interval seconds (Ctrl+C to stop) "
            "instead of fetching once and exiting -- the same "
            "`app-status --watch` mirrors for this command's own "
            "endpoints, e.g. for watching a --api-key start working "
            "again right after a key-rotation redeploy."
        )
    )
    app_auth_parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between polls under --watch (default: 2)."
    )
    app_auth_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a combined machine-readable JSON result ({\"status\": "
            "<GET /auth/status response>, \"info\": <GET /auth/info "
            "response>, \"validate\": <GET /auth/validate response, or "
            "{\"authenticated\": false, \"detail\": ...} for an invalid "
            "--api-key>}) instead of a human-readable summary, for "
            "scripting/automation. Under --watch, one such object (plus "
            "a \"timestamp\") is printed per poll, one per line, the "
            "same NDJSON-style streaming `app-status --json --watch` "
            "already gives."
        )
    )

    # app-metrics command (the FIRST command anywhere in this CLI to
    # actually call a *deployed* compiled app's own runtime directly --
    # every other command here either talks to this dashboard (`metrics`
    # above, and every `remote-*` command) or only *generates* example
    # requests for a deployed app -- curl-preview/postman-preview/
    # remote-curl -- without ever sending one itself. `serve`/`watch`
    # start a compiled app locally and `deploy`/`remote-deploy` build and
    # push a real image, but nothing in this CLI could then actually ask
    # that running app anything at all -- an operator wanting to confirm
    # it's actually serving real traffic, or grab one metric for a shell
    # script, had to reach for a raw `curl` against a path this CLI
    # otherwise treats every other endpoint as its own business to front)
    app_metrics_parser = subparsers.add_parser(
        "app-metrics",
        help=(
            "Show a compiled app's own scrape-shaped operational metrics "
            "-- task/HTTP/webhook throughput -- via its GET "
            "/metrics/prometheus. Distinct from `metrics` above, which "
            "shows this dashboard's own metrics instead."
        )
    )
    app_metrics_parser.add_argument(
        "--host",
        default="localhost",
        help=(
            "Host the compiled app is actually reachable at (default: "
            "localhost) -- where `serve`/`docker compose up`/a real "
            "deploy is actually listening, not the dashboard that built "
            "it."
        )
    )
    app_metrics_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the compiled app is actually reachable at (default: 8000, matching `serve`'s own default)."
    )
    app_metrics_parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the app to respond before giving up (default: 10)."
    )
    app_metrics_parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Keep polling every --interval seconds (Ctrl+C to stop) "
            "instead of scraping once and exiting -- the same "
            "`app-status --watch` mirrors for this command's own "
            "GET /metrics/prometheus, for watching throughput change "
            "live rather than one static snapshot."
        )
    )
    app_metrics_parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between polls under --watch (default: 2)."
    )
    app_metrics_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Parse GET /metrics/prometheus' own Prometheus text exposition "
            "format into a flat {metric_name: value} JSON object instead "
            "of printing the raw text, the same parsing `metrics --json` "
            "already applies to this dashboard's own scrape -- a labeled "
            "line (e.g. a webhook outcome broken out by status) keeps its "
            "own \"{...}\" label suffix as part of the key verbatim, "
            "rather than being split apart into its own nested structure. "
            "Under --watch, one such object (plus a \"timestamp\") is "
            "printed per poll, one per line -- the same NDJSON-style "
            "streaming `app-status --json --watch` already gives."
        )
    )

    # app-call command -- the second command (after app-metrics above) to
    # actually call a *deployed* compiled app's own runtime directly, this
    # one to actually invoke one of its endpoints, not just read its
    # metrics. curl-preview/postman-preview/export-curl/export-postman/
    # remote-curl already build a ready-to-run curl command or Postman
    # request for exactly this -- correct JSON body shape (from the
    # notebook's own example_payload), the required X-API-Key header, the
    # right host/port -- but none of them, nor anything else in this CLI,
    # ever actually *sends* one. An operator who just ran `serve`/`docker
    # compose up` and wants to try a function once, or a CI smoke test
    # wanting to call one real endpoint and check its result, had to copy
    # a generated curl command out and run it by hand (or write their own
    # httpx call) even though this CLI already knows everything needed to
    # build that exact request itself.
    app_call_parser = subparsers.add_parser(
        "app-call",
        help=(
            "Call one of a notebook's own compiled endpoints on a "
            "deployed app directly (POST /<function>), rather than only "
            "previewing what that call would look like."
        )
    )
    app_call_parser.add_argument(
        "notebook",
        help=(
            "Path to the notebook `function` was compiled from -- read "
            "locally, the same as `curl-preview`/`export-curl`, purely to "
            "learn `function`'s own example_payload (when --data isn't "
            "given) and whether it's a background function; never "
            "uploaded or compiled itself."
        )
    )
    app_call_parser.add_argument(
        "function",
        help="Name of the notebook function to call, as reported by `inspect`."
    )
    app_call_parser.add_argument(
        "--data",
        default=None,
        help=(
            "JSON object to send as the request body. Defaults to "
            "`function`'s own \"example_payload\" (the same default "
            "curl-preview/export-curl already use for their own generated "
            "commands) when omitted."
        )
    )
    app_call_parser.add_argument(
        "--host",
        default="localhost",
        help=(
            "Host the compiled app is actually reachable at (default: "
            "localhost) -- the same convention `app-metrics`/`remote-curl` "
            "already use."
        )
    )
    app_call_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port the compiled app is actually reachable at (default: 8000, matching `serve`'s own default)."
    )
    app_call_parser.add_argument(
        "--api-key",
        default=_default_app_api_key(),
        dest="api_key",
        help=(
            "Value sent as the X-API-Key header (default: $NOTEBOOK_API_KEY "
            "if set -- the same variable a real deployment's own app "
            "already reads to decide which key(s) it accepts -- else the "
            "generated app's own default dev key), the same default every "
            "app-tasks subcommand already uses."
        )
    )
    app_call_parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the initial response before giving up (default: 10)."
    )
    app_call_parser.add_argument(
        "--callback-url",
        dest="callback_url",
        default=None,
        help=(
            "For a background function only: have the app also deliver "
            "the finished task's own result/error to this URL via a "
            "signed webhook once it completes (the same ?callback_url= "
            "a real caller could already pass -- curl-preview/"
            "export-curl/postman-preview already demonstrate it, but "
            "nothing let this CLI actually trigger a real delivery "
            "against a real deployed app). Must be an http:// or "
            "https:// URL, the same restriction the app itself enforces "
            "at request time. Ignored (with a warning) for a "
            "synchronous function, which never reads this parameter at "
            "all."
        )
    )
    app_call_parser.add_argument(
        "--wait",
        action="store_true",
        help=(
            "For a background function (its own POST only ever returns "
            "{\"task_id\": ...} immediately): poll GET /tasks/{task_id} "
            "until it leaves \"processing\" and print the real result/"
            "error instead, the same "
            "poll-until-done behavior a generated SDK client's own "
            "*_and_wait companion already gives -- just from the CLI, "
            "with no client of its own required. Ignored for a "
            "synchronous function, which already returns its real result "
            "immediately."
        )
    )
    app_call_parser.add_argument(
        "--wait-timeout",
        type=float,
        default=60.0,
        dest="wait_timeout",
        help="Seconds to keep polling under --wait before giving up (default: 60)."
    )
    app_call_parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        dest="poll_interval",
        help="Seconds to wait between polls under --wait (default: 1)."
    )
    app_call_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the app's own raw JSON response ({\"task_id\", "
            "\"status\"} for a background function's initial response, "
            "the task record itself under --wait, or the function's own "
            "return value for a synchronous one) instead of a "
            "human-readable summary, for scripting/automation."
        )
    )

    # app-tasks command group -- manage a deployed compiled app's own
    # already-submitted background tasks directly (list/get/delete/
    # redeliver-webhook), completing the "talk to a deployed app
    # directly" trio this session's own app-metrics (read aggregate
    # metrics) and app-call (submit a new call) already started: neither
    # one gives any way to inspect or act on a task *after* app-call
    # already submitted it (or a real caller did, entirely outside this
    # CLI) short of `app-call ... --wait`, which only ever follows the
    # one task it just created. A generated SDK client's own
    # list_tasks/get_task/delete_task/redeliver_task_webhook methods
    # already give this to a caller with code of their own to run them
    # from -- this closes the identical gap `verify-webhook`/`app-call`
    # already closed elsewhere for an operator with only a terminal.
    app_tasks_parser = subparsers.add_parser(
        "app-tasks",
        help=(
            "List, inspect, delete, retry, redeliver the webhook of, or "
            "bulk clean up a deployed compiled app's own already-"
            "submitted background tasks directly."
        )
    )
    app_tasks_subparsers = app_tasks_parser.add_subparsers(
        dest="app_tasks_command", required=True
    )

    app_tasks_list_parser = app_tasks_subparsers.add_parser(
        "list",
        help="List a deployed app's own background tasks via its GET /tasks."
    )
    app_tasks_list_parser.add_argument(
        "--status",
        default=None,
        choices=["processing", "completed", "failed"],
        help="Only show tasks with this exact status, via GET /tasks' own ?status= query param."
    )
    app_tasks_list_parser.add_argument(
        "--webhook-delivery-failed",
        default=None,
        dest="webhook_delivery_failed",
        choices=["true", "false"],
        help=(
            "Only show tasks whose most recent webhook delivery attempt "
            "did (\"true\") or didn't (\"false\") succeed, via GET "
            "/tasks' own ?webhook_delivery_failed= query param -- a task "
            "never submitted with a callback_url matches neither. "
            "Composes with --status exactly like the app's own filter "
            "does."
        )
    )
    app_tasks_list_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap how many tasks are returned, via GET /tasks' own ?limit= query param (app default: 100, max 1000)."
    )
    app_tasks_list_parser.add_argument(
        "--offset",
        type=int,
        default=None,
        help="Skip this many matching tasks before --limit is applied, via GET /tasks' own ?offset= query param."
    )
    _add_app_host_port_arguments(app_tasks_list_parser)
    # --watch/--interval mirror app-status/app-metrics' own identical
    # flags -- the one other read-only, repeatedly-useful-to-poll
    # surface this CLI already talks to a deployed app through, and the
    # one this session's own --watch feature never reached: an operator
    # who just kicked off `app-tasks redeliver-failed` or a batch of
    # `app-call`s, and wants to watch "processing_tasks" actually drain
    # to 0 (or a `--status processing` listing shrink to nothing), had
    # to run this command over and over by hand, or wrap it in a
    # separate `watch` utility of its own (unavailable on Windows).
    app_tasks_list_parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Keep polling every --interval seconds (Ctrl+C to stop) "
            "instead of listing once and exiting -- composes with every "
            "filter above exactly like a single listing already does "
            "(e.g. `--status processing --watch` to watch a batch of "
            "tasks drain)."
        )
    )
    app_tasks_list_parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between polls under --watch (default: 2)."
    )
    app_tasks_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit the app's own raw JSON response instead of a "
            "human-readable listing, for scripting/automation. Under "
            "--watch, one such object (plus a \"timestamp\") is printed "
            "per poll, one per line -- the same NDJSON-style streaming "
            "`app-status --json --watch` already gives."
        )
    )

    app_tasks_get_parser = app_tasks_subparsers.add_parser(
        "get",
        help="Show one background task's own full record via GET /tasks/{task_id}."
    )
    app_tasks_get_parser.add_argument(
        "task_id", help="Task id to look up, as reported by `app-tasks list` or `app-call`."
    )
    _add_app_host_port_arguments(app_tasks_get_parser)
    app_tasks_get_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    # `app-call --wait` already polls GET /tasks/{task_id} until a task
    # it just submitted leaves "processing", but that loop only exists
    # inline inside app-call's own handler -- a task_id learned any other
    # way (`app-tasks list`, an `app-call` run without --wait, a task_id
    # handed off between processes/scripts) had no CLI command able to
    # block on it at all, only `app-tasks get`'s own single one-shot
    # fetch (which reports "processing" and simply exits) or hand-rolling
    # the identical poll loop again.
    app_tasks_wait_parser = app_tasks_subparsers.add_parser(
        "wait",
        help="Poll a background task via GET /tasks/{task_id} until it leaves \"processing\"."
    )
    app_tasks_wait_parser.add_argument(
        "task_id", help="Task id to wait on, as reported by `app-tasks list` or `app-call`."
    )
    _add_app_host_port_arguments(app_tasks_wait_parser)
    app_tasks_wait_parser.add_argument(
        "--wait-timeout",
        type=float,
        default=60.0,
        dest="wait_timeout",
        help="Seconds to keep polling before giving up (default: 60)."
    )
    app_tasks_wait_parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        dest="poll_interval",
        help="Seconds to wait between polls (default: 1)."
    )
    app_tasks_wait_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    app_tasks_delete_parser = app_tasks_subparsers.add_parser(
        "delete",
        help="Delete one background task via DELETE /tasks/{task_id}."
    )
    app_tasks_delete_parser.add_argument(
        "task_id", help="Task id to delete, as reported by `app-tasks list` or `app-call`."
    )
    _add_app_host_port_arguments(app_tasks_delete_parser)
    app_tasks_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    # app-tasks retry -- unlike redeliver-webhook below (which only ever
    # resends a finished task's own already-recorded outcome, verbatim,
    # never re-running the notebook function itself), this actually
    # re-executes a *failed* task's underlying function with its original
    # inputs via the app's own POST /tasks/{task_id}/retry, producing a
    # brand-new task_id rather than mutating the one it retried.
    app_tasks_retry_parser = app_tasks_subparsers.add_parser(
        "retry",
        help=(
            "Re-run one failed background task's own underlying function "
            "with its original inputs via POST /tasks/{task_id}/retry, "
            "under a brand-new task_id."
        )
    )
    app_tasks_retry_parser.add_argument(
        "task_id",
        help=(
            "Failed task id to retry, as reported by `app-tasks list "
            "--status failed`."
        )
    )
    _add_app_host_port_arguments(app_tasks_retry_parser)
    app_tasks_retry_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    app_tasks_redeliver_webhook_parser = app_tasks_subparsers.add_parser(
        "redeliver-webhook",
        help=(
            "Manually retrigger delivery of one task's own already-"
            "recorded result/error via POST "
            "/tasks/{task_id}/redeliver-webhook."
        )
    )
    app_tasks_redeliver_webhook_parser.add_argument(
        "task_id",
        help=(
            "Task id whose webhook to redeliver, as reported by "
            "`app-tasks list --webhook-delivery-failed true`."
        )
    )
    _add_app_host_port_arguments(app_tasks_redeliver_webhook_parser)
    app_tasks_redeliver_webhook_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    # app-tasks redeliver-failed -- the bulk counterpart to
    # redeliver-webhook above, closing the loop this session's own
    # commits have been building one link at a time: GET /metrics
    # aggregates how many webhook deliveries have failed (added earlier
    # this session), GET /tasks?webhook_delivery_failed=true finds which
    # tasks those are (also added earlier this session, specifically so a
    # caller could find what redeliver-webhook needs), and app-tasks
    # redeliver-webhook itself retriggers exactly one. Nothing yet did
    # all three in one call -- an operator who ran `app-tasks list
    # --webhook-delivery-failed true` and got back a dozen task_ids still
    # had to run `app-tasks redeliver-webhook <id>` once per id by hand,
    # or script that loop themselves, even though this CLI already knows
    # how to do both halves.
    app_tasks_redeliver_failed_parser = app_tasks_subparsers.add_parser(
        "redeliver-failed",
        help=(
            "Redeliver every task's own currently-failed webhook in one "
            "call -- GET /tasks?webhook_delivery_failed=true followed by "
            "one POST /tasks/{task_id}/redeliver-webhook per match."
        )
    )
    app_tasks_redeliver_failed_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "List which tasks currently have a failed webhook delivery "
            "-- exactly what a real run would redeliver -- without "
            "actually redelivering any of them."
        )
    )
    _add_app_host_port_arguments(app_tasks_redeliver_failed_parser)
    app_tasks_redeliver_failed_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a machine-readable result ({\"dry_run\", \"results\": "
            "[{\"task_id\", \"status\", ...}, ...], \"succeeded_count\", "
            "\"failed_count\"}) instead of a human-readable summary, for "
            "scripting/automation -- the same {\"results\", "
            "\"succeeded_count\", \"failed_count\"} shape every batch "
            "endpoint this dashboard's own API already returns (e.g. "
            "POST /api/notebooks/tags-batch), applied here client-side "
            "since the app itself has no equivalent bulk endpoint of its "
            "own."
        )
    )

    # app-tasks purge-completed/purge-failed/cleanup/reset -- CLI wrappers
    # for four already-existing server endpoints (DELETE /tasks/completed,
    # DELETE /tasks/failed, POST /tasks/cleanup, POST /tasks/reset) that,
    # unlike every other /tasks* endpoint this CLI already talks to
    # directly (list/get/delete/redeliver-webhook/redeliver-failed/retry),
    # had no CLI counterpart at all -- an operator who wanted to bulk-clear
    # a deployment's own finished tasks (routine maintenance for a
    # long-running app, the exact TASKS-growing-without-bound scenario
    # _evict_expired_tasks itself exists to bound) had to reach for curl.
    # All four are irreversible deletes with no confirmation step of their
    # own server-side, so each prompts for one here -- the identical
    # pattern clear-deploy-history/prune-versions/tags-delete already use
    # for the same reason.
    app_tasks_purge_completed_parser = app_tasks_subparsers.add_parser(
        "purge-completed",
        help="Delete every completed task via DELETE /tasks/completed."
    )
    app_tasks_purge_completed_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `app-tasks purge-completed` asks for a y/N confirmation "
            "on the terminal first -- DELETE /tasks/completed itself has "
            "no confirmation step of its own, and is irreversible."
        )
    )
    _add_app_host_port_arguments(app_tasks_purge_completed_parser)
    app_tasks_purge_completed_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    app_tasks_purge_failed_parser = app_tasks_subparsers.add_parser(
        "purge-failed",
        help="Delete every failed task via DELETE /tasks/failed."
    )
    app_tasks_purge_failed_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `app-tasks purge-failed` asks for a y/N confirmation "
            "on the terminal first -- DELETE /tasks/failed itself has no "
            "confirmation step of its own, and is irreversible."
        )
    )
    _add_app_host_port_arguments(app_tasks_purge_failed_parser)
    app_tasks_purge_failed_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    app_tasks_cleanup_parser = app_tasks_subparsers.add_parser(
        "cleanup",
        help=(
            "Delete every completed AND failed task in one call via POST "
            "/tasks/cleanup -- equivalent to purge-completed followed by "
            "purge-failed, in a single request."
        )
    )
    app_tasks_cleanup_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `app-tasks cleanup` asks for a y/N confirmation on the "
            "terminal first -- POST /tasks/cleanup itself has no "
            "confirmation step of its own, and is irreversible."
        )
    )
    _add_app_host_port_arguments(app_tasks_cleanup_parser)
    app_tasks_cleanup_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    app_tasks_reset_parser = app_tasks_subparsers.add_parser(
        "reset",
        help=(
            "Delete EVERY task this app is tracking, regardless of status "
            "-- including one still processing -- via POST /tasks/reset."
        )
    )
    app_tasks_reset_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm the deletion without an interactive prompt. Without "
            "this, `app-tasks reset` asks for a y/N confirmation on the "
            "terminal first -- POST /tasks/reset itself has no "
            "confirmation step of its own, and is irreversible. Unlike "
            "purge-completed/purge-failed/cleanup above, this also drops "
            "any task still processing; its eventual result or error, "
            "once that background work finishes, is discarded rather "
            "than recorded anywhere."
        )
    )
    _add_app_host_port_arguments(app_tasks_reset_parser)
    app_tasks_reset_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the app's own raw JSON response instead of a human-readable summary, for scripting/automation."
    )

    # governance command group
    governance_parser = subparsers.add_parser(
        "governance", help="Inspect and manage deployment governance capabilities."
    )
    governance_subparsers = governance_parser.add_subparsers(
        dest="governance_command", required=True
    )

    doctor_parser = governance_subparsers.add_parser(
        "doctor",
        help="Inspect deployment governance persistence health.",
        description=(
            "Inspect deployment governance persistence health.\n\n"
            "By default, performs lightweight diagnostics. Use --deep to "
            "verify the integrity of every persisted governance trace.\n\n"
            "--deep persists its result as a durable audit-history record; "
            "running without --deep only reads existing audit history and "
            "does not create a new record.\n\n"
            "Exit codes: 0 healthy, 1 unhealthy, 2 diagnostics could not "
            "be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    doctor_parser.add_argument(
        "--deep",
        action="store_true",
        help="Perform a full persisted-record integrity audit.",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    doctor_parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        dest="batch_size",
        help="Number of persisted records read per integrity-audit batch. Default: 500.",
    )

    audits_parser = governance_subparsers.add_parser(
        "audits",
        help="Inspect recorded deployment governance integrity audit history.",
        description=(
            "Inspect recorded deployment governance integrity audit "
            "history.\n\n"
            "This command is read-only: it never executes a new audit. "
            "Run `governance doctor --deep` to record a new audit.\n\n"
            "Exit codes: 0 query completed (even with zero matches), "
            "2 query could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    audits_parser.add_argument(
        "--backend",
        default=None,
        help="Filter audits by persistence backend.",
    )
    audits_parser.add_argument(
        "--outcome",
        choices=[outcome.value for outcome in GovernanceIntegrityAuditOutcome],
        default=None,
        help="Filter by healthy or unhealthy outcome.",
    )
    audits_parser.add_argument(
        "--since",
        default=None,
        help="Include audits started at or after this ISO-8601 timestamp.",
    )
    audits_parser.add_argument(
        "--until",
        default=None,
        help="Include audits started at or before this ISO-8601 timestamp.",
    )
    audits_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of audit records to return. Default: 20.",
    )
    audits_parser.add_argument(
        "--trend",
        action="store_true",
        dest="include_trend",
        help="Include recent trend analysis (direction, streak, rates).",
    )
    audits_parser.add_argument(
        "--trend-window",
        type=int,
        default=20,
        dest="trend_window",
        help="Number of most recent audits to analyze for trends. Default: 20.",
    )
    audits_parser.add_argument(
        "--regression",
        action="store_true",
        dest="include_regression",
        help=(
            "Compare the latest audit against its immediately preceding "
            "audit to detect a newly introduced integrity regression."
        ),
    )
    audits_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    audits_subparsers = audits_parser.add_subparsers(
        dest="audits_command", required=False
    )

    prune_parser = audits_subparsers.add_parser(
        "prune",
        help="Preview or apply governance audit-history retention.",
        description=(
            "Preview (default) or apply an audit-history retention "
            "policy. At least one of --max-records or --max-age-days is "
            "required.\n\n"
            "A dry-run finding prunable records is not a failure; only "
            "invalid configuration or an execution error exits non-zero.\n\n"
            "Exit codes: 0 evaluation or pruning succeeded, "
            "2 invalid configuration or execution failure."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    prune_parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        dest="max_records",
        help="Retain at most this many most-recent audit records.",
    )
    prune_parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        dest="max_age_days",
        help="Retain only audit records started within this many days.",
    )
    prune_parser.add_argument(
        "--no-preserve-latest",
        action="store_false",
        dest="preserve_latest",
        default=True,
        help=(
            "Allow the single most recent audit record to be pruned too "
            "(by default it is always retained)."
        ),
    )
    prune_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete prunable records (default is a dry run).",
    )
    prune_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    export_parser = audits_subparsers.add_parser(
        "export",
        help="Export a portable governance audit evidence bundle.",
        description=(
            "Export a deterministic, self-contained JSON evidence bundle "
            "(selected audit records plus a summary and, by default, "
            "trend and regression analysis derived only from the "
            "exported records) to a file.\n\n"
            "The bundle is written to --output; only a concise success "
            "summary is printed to stdout.\n\n"
            "Exit codes: 0 export succeeded, 2 invalid configuration or "
            "execution failure (including refusing to overwrite an "
            "existing file without --force)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    export_parser.add_argument(
        "--output",
        required=True,
        dest="output",
        help="Path to write the evidence bundle JSON file to.",
    )
    export_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Maximum number of audit records to include. Default: all.",
    )
    export_parser.add_argument(
        "--trend",
        action="store_true",
        dest="include_trend",
        default=True,
        help="Include trend analysis derived from the exported records (default).",
    )
    export_parser.add_argument(
        "--no-trend",
        action="store_false",
        dest="include_trend",
        help="Omit trend analysis from the exported bundle.",
    )
    export_parser.add_argument(
        "--regression",
        action="store_true",
        dest="include_regression",
        default=True,
        help="Include regression analysis derived from the exported records (default).",
    )
    export_parser.add_argument(
        "--no-regression",
        action="store_false",
        dest="include_regression",
        help="Omit regression analysis from the exported bundle.",
    )
    export_parser.add_argument(
        "--trend-window",
        type=int,
        default=20,
        dest="trend_window",
        help="Number of most recent exported records to analyze for trends. Default: 20.",
    )
    export_parser.add_argument(
        "--manifest",
        action="store_true",
        dest="create_manifest",
        default=True,
        help="Write a SHA-256 tamper-evidence manifest alongside the evidence file (default).",
    )
    export_parser.add_argument(
        "--no-manifest",
        action="store_false",
        dest="create_manifest",
        help="Do not write a tamper-evidence manifest.",
    )
    export_parser.add_argument(
        "--compact",
        action="store_true",
        dest="compact",
        help="Write compact (non-indented) JSON instead of pretty-printed.",
    )
    export_parser.add_argument(
        "--force",
        action="store_true",
        dest="force",
        help="Overwrite the output and manifest files if they already exist.",
    )

    verify_parser = audits_subparsers.add_parser(
        "verify",
        help="Verify an exported evidence file against its manifest.",
        description=(
            "Verify a previously exported governance audit evidence file "
            "against its SHA-256 tamper-evidence manifest. This is a "
            "pure file-based operation: it does not bootstrap a "
            "persistence runtime, so it works even after the "
            "originating database is gone.\n\n"
            "If --manifest is omitted, it is derived from --evidence "
            "as <evidence>.manifest.json.\n\n"
            "Exit codes: 0 verified, 2 the manifest could not be "
            "loaded (missing/malformed/unsupported schema version), "
            "3 the evidence file does not match its manifest."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    verify_parser.add_argument(
        "--evidence",
        required=True,
        dest="evidence",
        help="Path to the evidence JSON file to verify.",
    )
    verify_parser.add_argument(
        "--manifest",
        default=None,
        dest="manifest",
        help="Path to the manifest file. Default: <evidence>.manifest.json.",
    )

    stats_parser = audits_subparsers.add_parser(
        "stats",
        help="Show an operational summary of governance audit history.",
        description=(
            "Show a compact operational summary of governance audit "
            "history: health rate, current and longest streaks, first/"
            "latest audit timestamps, and aggregate failure counts.\n\n"
            "This is read-only: it never executes a new audit. Run "
            "`governance doctor --deep` or `governance check` to record "
            "one.\n\n"
            "Exit codes: 0 the summary was produced (even for empty "
            "history), 2 the summary could not be produced."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    stats_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Calculate statistics from only the most recent N audits. Default: all audits.",
    )
    stats_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    replay_parser = audits_subparsers.add_parser(
        "replay",
        help="Reconstruct the context of previously recorded governance integrity audits.",
        description=(
            "Reconstruct the context of one or more previously recorded "
            "governance integrity audits from stored history: trend "
            "analysis, regression comparison, and debugging.\n\n"
            "This is read-only: it never executes a new audit and never "
            "changes persisted state.\n\n"
            "Exit codes: 0 the replay succeeded, 2 the replay could not "
            "be completed (unknown audit id, empty history, or invalid "
            "configuration)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    replay_parser.add_argument(
        "--audit-id",
        default=None,
        dest="audit_id",
        help="Replay one audit by its identifier.",
    )
    replay_parser.add_argument(
        "--latest",
        action="store_true",
        help="Replay the most recently started audit (the default).",
    )
    replay_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Replay the N most recently started audits.",
    )
    replay_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    diff_parser = audits_subparsers.add_parser(
        "diff",
        help="Compare two replayed governance integrity audits.",
        description=(
            "Compare two previously recorded governance integrity "
            "audits by replaying both and diffing their operational "
            "fields (audit_id and timestamps are excluded).\n\n"
            "When --previous and --current are both omitted (the "
            "default, equivalent to --latest), the two most recently "
            "started audits are compared.\n\n"
            "This is read-only: it never executes a new audit and never "
            "changes persisted state.\n\n"
            "Exit codes: 0 the diff succeeded, 2 the diff could not be "
            "completed (unknown audit id, insufficient history, or "
            "invalid configuration)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    diff_parser.add_argument(
        "--previous",
        default=None,
        dest="previous_audit_id",
        help="Identifier of the baseline audit to compare from.",
    )
    diff_parser.add_argument(
        "--current",
        default=None,
        dest="current_audit_id",
        help="Identifier of the audit to compare to.",
    )
    diff_parser.add_argument(
        "--latest",
        action="store_true",
        help="Compare the two most recently started audits (the default).",
    )
    diff_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    timeline_parser = audits_subparsers.add_parser(
        "timeline",
        help="Show a chronological timeline of governance integrity audits.",
        description=(
            "Show recorded governance integrity audits as chronological "
            "timeline events (identity, timestamps, state, and record "
            "counts) for visualization -- no derived calculations.\n\n"
            "This is read-only: it never executes a new audit. Run "
            "`governance doctor --deep` or `governance check` to record "
            "one.\n\n"
            "Exit codes: 0 the timeline was produced (even for empty "
            "history), 2 the timeline could not be produced."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    timeline_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Maximum number of timeline events to return. Default: all.",
    )
    timeline_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_parser = audits_subparsers.add_parser(
        "logs",
        help="Inspect recent structured governance log entries.",
        description=(
            "Inspect the structured log entries recorded by "
            "governance components (metrics, delivery engine, "
            "delivery runtime) through the shared governance "
            "logger.\n\n"
            "This is read-only: it never emits a new log entry."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_subparsers = logs_parser.add_subparsers(
        dest="logs_command", required=True
    )

    logs_tail_parser = logs_subparsers.add_parser(
        "tail",
        help="Show the most recently buffered governance log entries.",
        description=(
            "Show the most recently buffered governance log entries, "
            "newest first.\n\n"
            "Exit codes: 0 the log tail was produced (even if empty), "
            "2 it could not be (including an invalid --level)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_tail_parser.add_argument(
        "--level",
        type=str,
        default=None,
        dest="level",
        help=(
            "Only show entries at this level (debug, info, warning, "
            "error). Default: all levels."
        ),
    )
    logs_tail_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Maximum number of log entries to return. Default: all.",
    )
    logs_tail_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_list_parser = logs_subparsers.add_parser(
        "list",
        help=(
            "Show the durable governance log history, oldest first."
        ),
        description=(
            "Show the durable governance log history (via the "
            "configured log repository), oldest first, unlike "
            "`logs tail` which reads the in-process buffer.\n\n"
            "Exit codes: 0 the listing was produced (even if empty), "
            "2 it could not be (including an invalid --level)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_list_parser.add_argument(
        "--level",
        type=str,
        default=None,
        dest="level",
        help=(
            "Only show entries at this level (debug, info, warning, "
            "error). Default: all levels."
        ),
    )
    logs_list_parser.add_argument(
        "--component",
        type=str,
        default=None,
        dest="component",
        help="Only show entries from this component. Default: all.",
    )
    logs_list_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Maximum number of log entries to return. Default: all.",
    )
    logs_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_clear_parser = logs_subparsers.add_parser(
        "clear",
        help="Discard every entry in the durable governance log history.",
        description=(
            "Discard every entry currently stored in the configured "
            "log repository.\n\n"
            "Exit codes: 0 the log repository was cleared (even if "
            "it was already empty), 2 it could not be cleared."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_rotate_parser = logs_subparsers.add_parser(
        "rotate",
        help="Run governance log rotation now.",
        description=(
            "Run governance log rotation now, discarding entries "
            "outside the configured policy (oldest first, then "
            "anything older than the configured max age).\n\n"
            "--max-entries/--max-age override the configured policy "
            "for this invocation only.\n\n"
            "Exit codes: 0 rotation ran (even if nothing was "
            "discarded), 2 it could not run."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_rotate_parser.add_argument(
        "--max-entries",
        type=int,
        default=None,
        dest="max_entries",
        help="Override the configured max entry count for this run.",
    )
    logs_rotate_parser.add_argument(
        "--max-age",
        type=int,
        default=None,
        dest="max_age",
        help=(
            "Override the configured max age in days for this run. "
            "Default: use the configured policy."
        ),
    )
    logs_rotate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_rotation_parser = logs_subparsers.add_parser(
        "rotation",
        help="Show the configured governance log rotation policy.",
        description=(
            "Show the configured governance log rotation policy, "
            "without discarding anything.\n\n"
            "--max-entries/--max-age preview a different policy for "
            "this invocation only; no entries are discarded.\n\n"
            "Exit codes: 0 the policy was retrieved, 2 it could not "
            "be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_rotation_parser.add_argument(
        "--max-entries",
        type=int,
        default=None,
        dest="max_entries",
        help="Preview the policy with this max entry count instead.",
    )
    logs_rotation_parser.add_argument(
        "--max-age",
        type=int,
        default=None,
        dest="max_age",
        help="Preview the policy with this max age in days instead.",
    )
    logs_rotation_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_search_parser = logs_subparsers.add_parser(
        "search",
        help="Search the durable governance log history.",
        description=(
            "Search the durable governance log history (via the "
            "configured log repository), newest first.\n\n"
            "Every given filter combines with AND; --from/--to form "
            "an inclusive time range.\n\n"
            "Exit codes: 0 the search was produced (even if empty), "
            "2 it could not be (including an invalid --level or "
            "timestamp)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_search_parser.add_argument(
        "--level",
        type=str,
        default=None,
        dest="level",
        help=(
            "Only show entries at this level (debug, info, warning, "
            "error). Default: all levels."
        ),
    )
    logs_search_parser.add_argument(
        "--component",
        type=str,
        default=None,
        dest="component",
        help="Only show entries from this component. Default: all.",
    )
    logs_search_parser.add_argument(
        "--event",
        type=str,
        default=None,
        dest="event",
        help="Only show entries with this event name. Default: all.",
    )
    logs_search_parser.add_argument(
        "--from",
        default=None,
        dest="since",
        help=(
            "Only show entries at or after this ISO-8601 timestamp "
            "(inclusive)."
        ),
    )
    logs_search_parser.add_argument(
        "--to",
        default=None,
        dest="until",
        help=(
            "Only show entries at or before this ISO-8601 timestamp "
            "(inclusive)."
        ),
    )
    logs_search_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Maximum number of log entries to return. Default: all.",
    )
    logs_search_parser.add_argument(
        "--offset",
        type=int,
        default=None,
        dest="offset",
        help=(
            "Number of newest-first matching entries to skip before "
            "returning results. Default: 0."
        ),
    )
    logs_search_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_export_json_parser = logs_subparsers.add_parser(
        "export-json",
        help="Export the durable governance log history as JSON.",
        description=(
            "Export the durable governance log history matching the "
            "given filters to --output as a single JSON array, "
            "streamed entry by entry.\n\n"
            "Exit codes: 0 the export was written (even if empty), "
            "2 it could not be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_export_csv_parser = logs_subparsers.add_parser(
        "export-csv",
        help="Export the durable governance log history as CSV.",
        description=(
            "Export the durable governance log history matching the "
            "given filters to --output as CSV, streamed entry by "
            "entry. The structured fields mapping is JSON-encoded "
            "into a single fields_json column.\n\n"
            "Exit codes: 0 the export was written (even if empty), "
            "2 it could not be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_export_ndjson_parser = logs_subparsers.add_parser(
        "export-ndjson",
        help=(
            "Export the durable governance log history as "
            "newline-delimited JSON."
        ),
        description=(
            "Export the durable governance log history matching the "
            "given filters to --output as newline-delimited JSON "
            "(one compact JSON object per line), streamed entry by "
            "entry.\n\n"
            "Exit codes: 0 the export was written (even if empty), "
            "2 it could not be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    for export_parser in (
        logs_export_json_parser,
        logs_export_csv_parser,
        logs_export_ndjson_parser,
    ):
        export_parser.add_argument(
            "--output",
            type=str,
            required=True,
            dest="output_path",
            help="File path to write the export to.",
        )
        export_parser.add_argument(
            "--level",
            type=str,
            default=None,
            dest="level",
            help=(
                "Only export entries at this level (debug, info, "
                "warning, error). Default: all levels."
            ),
        )
        export_parser.add_argument(
            "--component",
            type=str,
            default=None,
            dest="component",
            help="Only export entries from this component. Default: all.",
        )
        export_parser.add_argument(
            "--from",
            default=None,
            dest="since",
            help=(
                "Only export entries at or after this ISO-8601 "
                "timestamp (inclusive)."
            ),
        )
        export_parser.add_argument(
            "--to",
            default=None,
            dest="until",
            help=(
                "Only export entries at or before this ISO-8601 "
                "timestamp (inclusive)."
            ),
        )

    logs_redaction_parser = logs_subparsers.add_parser(
        "redaction",
        help="Inspect and test governance log redaction rules.",
        description=(
            "Inspect the rules governance log redaction applies "
            "before entries are persisted or exported."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    redaction_subparsers = logs_redaction_parser.add_subparsers(
        dest="redaction_command", required=True
    )

    redaction_rules_parser = redaction_subparsers.add_parser(
        "rules",
        help="List the currently registered redaction rules.",
        description=(
            "List the currently registered governance log "
            "redaction rules (field name and replacement).\n\n"
            "Exit codes: 0 the rules were retrieved, 2 they could "
            "not be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    redaction_rules_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    redaction_test_parser = redaction_subparsers.add_parser(
        "test",
        help=(
            "Show what the current redaction rules would do to a "
            "sample payload."
        ),
        description=(
            "Show what the currently configured redaction rules "
            "would do to a built-in sample payload covering every "
            "default-sensitive field name plus a nested example. "
            "Never logs, persists, or exports anything.\n\n"
            "Exit codes: 0 the test ran, 2 it could not."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    redaction_test_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_context_parser = logs_subparsers.add_parser(
        "context",
        help=(
            "Demonstrate governance log execution context nesting."
        ),
        description=(
            "Demonstrate the governance log execution context "
            "service's nested push/pop scoping by pushing two "
            "sample scopes and reporting current() at each step. "
            "Nothing is logged, persisted, or exported.\n\n"
            "Exit codes: 0 the demonstration ran, 2 it could not."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_context_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_trace_parser = logs_subparsers.add_parser(
        "trace",
        help=(
            "Show every durable log entry belonging to one traced "
            "operation."
        ),
        description=(
            "Show every durable log entry belonging to one traced "
            "operation (matches by correlation_id or "
            "parent_correlation_id), oldest first.\n\n"
            "Pass a dispatch's root correlation_id to see every "
            "attempt; pass one attempt's own correlation_id to see "
            "just that attempt.\n\n"
            "Exit codes: 0 the trace was produced (even if empty), "
            "2 it could not be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_trace_parser.add_argument(
        "--correlation-id",
        type=str,
        required=True,
        dest="correlation_id",
        help="The correlation id to trace.",
    )
    logs_trace_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_sampling_parser = logs_subparsers.add_parser(
        "sampling",
        help="Inspect and update governance log sampling policy.",
        description=(
            "Inspect and update the policy governance log sampling "
            "uses to decide which entries are worth persisting "
            "durably."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sampling_subparsers = logs_sampling_parser.add_subparsers(
        dest="sampling_command", required=True
    )

    sampling_show_parser = sampling_subparsers.add_parser(
        "show",
        help="Show the configured sampling policy.",
        description=(
            "Show the configured governance log sampling policy.\n\n"
            "Exit codes: 0 the policy was retrieved, 2 it could not "
            "be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sampling_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    sampling_update_parser = sampling_subparsers.add_parser(
        "update",
        help="Update the sampling policy.",
        description=(
            "Update the governance log sampling policy. "
            "--default-rate replaces the default rate; "
            "--level/--rate (given together) set or replace one "
            "per-level override. Any value not given is carried "
            "over unchanged from the current policy.\n\n"
            "Exit codes: 0 the policy was updated, 2 it could not "
            "be (including an invalid rate or level)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sampling_update_parser.add_argument(
        "--default-rate",
        type=float,
        default=None,
        dest="default_rate",
        help="Replace the default sampling rate (0.0-1.0).",
    )
    sampling_update_parser.add_argument(
        "--level",
        type=str,
        default=None,
        dest="level",
        help=(
            "The level (debug, info, warning, error) to set a "
            "per-level rate override for. Must be given with --rate."
        ),
    )
    sampling_update_parser.add_argument(
        "--rate",
        type=float,
        default=None,
        dest="rate",
        help=(
            "The sampling rate (0.0-1.0) for --level. Must be given "
            "with --level."
        ),
    )
    sampling_update_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_flush_parser = logs_subparsers.add_parser(
        "flush",
        help="Force-flush the governance log batcher now.",
        description=(
            "Force-flush the governance log batcher's pending "
            "entries to the repository now, regardless of whether "
            "the configured batch size or flush interval has been "
            "reached.\n\n"
            "Exit codes: 0 the flush ran (even if nothing was "
            "pending), 2 it could not run."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_flush_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_pending_parser = logs_subparsers.add_parser(
        "pending",
        help="Show how many governance log entries are buffered.",
        description=(
            "Show how many governance log entries are currently "
            "buffered in the batcher, not yet written to the "
            "repository.\n\n"
            "Exit codes: 0 the count was retrieved, 2 it could not "
            "be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_pending_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_replay_parser = logs_subparsers.add_parser(
        "replay",
        help=(
            "Replay the durable governance log history "
            "chronologically."
        ),
        description=(
            "Replay the durable governance log history "
            "chronologically (oldest first), optionally starting "
            "from --from and/or filtered to one --event, optionally "
            "capped to the first --limit matching entries.\n\n"
            "Read-only: never mutates stored logs.\n\n"
            "Exit codes: 0 the replay was produced (even if empty), "
            "2 it could not be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_replay_next_parser = logs_subparsers.add_parser(
        "replay-next",
        help=(
            "Advance a governance log replay cursor and show what "
            "it returns."
        ),
        description=(
            "Build a governance log replay scoped to --from/--event "
            "and advance its cursor by up to --limit (default 1) "
            "entries, reporting them plus the resulting cursor.\n\n"
            "Each invocation is a fresh process, so the cursor "
            "always starts at position 0.\n\n"
            "Read-only: never mutates stored logs.\n\n"
            "Exit codes: 0 the call succeeded (even if nothing "
            "matched), 2 it could not."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    for replay_parser in (
        logs_replay_parser,
        logs_replay_next_parser,
    ):
        replay_parser.add_argument(
            "--from",
            default=None,
            dest="since",
            help=(
                "Only replay entries at or after this ISO-8601 "
                "timestamp (inclusive)."
            ),
        )
        replay_parser.add_argument(
            "--event",
            type=str,
            default=None,
            dest="event",
            help="Only replay entries with this event name.",
        )
        replay_parser.add_argument(
            "--limit",
            type=int,
            default=None,
            dest="limit",
            help=(
                "For `replay`: maximum number of entries to return "
                "(default: all). For `replay-next`: how many "
                "entries to advance the cursor by (default: 1)."
            ),
        )
        replay_parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit machine-readable JSON output.",
        )

    logs_config_parser = logs_subparsers.add_parser(
        "config",
        help="Inspect and reload governance logging configuration.",
        description=(
            "Inspect and reload the centralized governance logging "
            "configuration (minimum level, batch size, flush "
            "interval, and whether sampling/redaction are active)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_subparsers = logs_config_parser.add_subparsers(
        dest="logs_config_command", required=True
    )

    logs_config_show_parser = config_subparsers.add_parser(
        "show",
        help="Show the configured governance logging configuration.",
        description=(
            "Show the currently configured governance logging "
            "configuration.\n\n"
            "Exit codes: 0 the configuration was retrieved, 2 it "
            "could not be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_config_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_config_reload_parser = config_subparsers.add_parser(
        "reload",
        help="Reload governance logging configuration.",
        description=(
            "Re-read governance logging configuration from its "
            "source and apply it to the live logger and batcher, "
            "without restarting anything.\n\n"
            "Exit codes: 0 the configuration was reloaded and "
            "applied, 2 it could not be (including an invalid "
            "environment value)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_config_reload_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_bootstrap_parser = logs_subparsers.add_parser(
        "bootstrap",
        help=(
            "Build, initialize, and shut down the governance "
            "logging bootstrap."
        ),
        description=(
            "Build and initialize a GovernanceLoggingBootstrap "
            "(wiring and validating every logging component "
            "together, then applying current configuration), report "
            "the resulting health, then shut it down before "
            "exiting.\n\n"
            "Exit codes: 0 the bootstrap built, initialized, and "
            "shut down cleanly, 2 it could not."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_bootstrap_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    logs_health_parser = logs_subparsers.add_parser(
        "health",
        help="Report governance logging subsystem health.",
        description=(
            "Build and initialize a GovernanceLoggingBootstrap and "
            "report its health, then shut it down before exiting.\n\n"
            "Exit codes: 0 the health snapshot was produced, 2 it "
            "could not be."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    logs_health_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    session_parser = audits_subparsers.add_parser(
        "session",
        help="Reconstruct an ordered session of recorded governance integrity audits.",
        description=(
            "Reconstruct an ordered session of recorded governance "
            "integrity audits for navigation and analysis (newest to "
            "oldest).\n\n"
            "This is read-only: it never executes a new audit. Run "
            "`governance doctor --deep` or `governance check` to record "
            "one.\n\n"
            "Exit codes: 0 the session was reconstructed (even for "
            "empty history), 2 the session could not be reconstructed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    session_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Maximum number of audits to include. Default: all.",
    )
    session_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    bookmark_parser = audits_subparsers.add_parser(
        "bookmark",
        help="Manage named bookmarks for governance integrity audits.",
        description=(
            "Create and manage named bookmarks pointing at recorded "
            "governance integrity audits, for quick navigation.\n\n"
            "Bookmarks are separate metadata layered on top of audit "
            "history: read-only relative to audit history itself.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bookmark_subparsers = bookmark_parser.add_subparsers(
        dest="bookmark_command", required=True
    )

    bookmark_add_parser = bookmark_subparsers.add_parser(
        "add",
        help="Create a bookmark pointing at an audit.",
    )
    bookmark_add_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name for the new bookmark.",
    )
    bookmark_add_parser.add_argument(
        "--audit-id",
        default=None,
        dest="audit_id",
        help="Identifier of the audit to bookmark.",
    )
    bookmark_add_parser.add_argument(
        "--latest",
        action="store_true",
        help="Bookmark the most recently started audit (the default).",
    )
    bookmark_add_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    bookmark_list_parser = bookmark_subparsers.add_parser(
        "list",
        help="List every governance audit bookmark.",
    )
    bookmark_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    bookmark_show_parser = bookmark_subparsers.add_parser(
        "show",
        help="Show one governance audit bookmark.",
    )
    bookmark_show_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the bookmark to show.",
    )
    bookmark_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    bookmark_delete_parser = bookmark_subparsers.add_parser(
        "delete",
        help="Delete one governance audit bookmark.",
    )
    bookmark_delete_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the bookmark to delete.",
    )
    bookmark_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    labels_parser = audits_subparsers.add_parser(
        "labels",
        help="Manage many-to-many labels on governance integrity audits.",
        description=(
            "Apply, remove, and query many-to-many labels on recorded "
            "governance integrity audits, for search, filtering, and "
            "organization. Unlike a bookmark (a unique name per audit), "
            "the same label may apply to many audits and the same audit "
            "may carry many labels.\n\n"
            "Labels are independent of audit history itself.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    labels_subparsers = labels_parser.add_subparsers(
        dest="labels_command", required=True
    )

    label_add_parser = labels_subparsers.add_parser(
        "add",
        help="Apply a label to an audit.",
    )
    label_add_parser.add_argument(
        "--audit-id",
        required=True,
        dest="audit_id",
        help="Identifier of the audit to label.",
    )
    label_add_parser.add_argument(
        "--label",
        required=True,
        dest="label",
        help="Label to apply.",
    )
    label_add_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    label_remove_parser = labels_subparsers.add_parser(
        "remove",
        help="Remove a label from an audit.",
    )
    label_remove_parser.add_argument(
        "--audit-id",
        required=True,
        dest="audit_id",
        help="Identifier of the audit to unlabel.",
    )
    label_remove_parser.add_argument(
        "--label",
        required=True,
        dest="label",
        help="Label to remove.",
    )
    label_remove_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    label_show_parser = labels_subparsers.add_parser(
        "show",
        help="Show every label applied to one audit.",
    )
    label_show_parser.add_argument(
        "--audit-id",
        required=True,
        dest="audit_id",
        help="Identifier of the audit to show labels for.",
    )
    label_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    label_search_parser = labels_subparsers.add_parser(
        "search",
        help="Search for every audit carrying a label.",
    )
    label_search_parser.add_argument(
        "--label",
        required=True,
        dest="label",
        help="Label to search for.",
    )
    label_search_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    label_list_parser = labels_subparsers.add_parser(
        "list",
        help="List every governance audit label.",
    )
    label_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    audit_search_parser = audits_subparsers.add_parser(
        "search",
        help="Search governance integrity audit history by filter.",
        description=(
            "Search recorded governance integrity audits by audit id, "
            "health outcome, applied label, and/or bookmark. All "
            "specified filters are combined with AND; none of them do "
            "fuzzy matching. At least one filter is required.\n\n"
            "This is read-only: it never executes a new audit and never "
            "mutates audit history, labels, or bookmarks.\n\n"
            "Exit codes: 0 the search completed (even with zero "
            "matches), 2 the search could not be completed (no filter "
            "supplied, or invalid configuration)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    audit_search_parser.add_argument(
        "--audit-id",
        default=None,
        dest="audit_id",
        help="Filter by exact audit identifier.",
    )
    audit_search_parser.add_argument(
        "--healthy",
        action="store_true",
        help="Only include healthy audits.",
    )
    audit_search_parser.add_argument(
        "--unhealthy",
        action="store_true",
        help="Only include unhealthy audits.",
    )
    audit_search_parser.add_argument(
        "--label",
        default=None,
        dest="label",
        help="Filter by applied label.",
    )
    audit_search_parser.add_argument(
        "--bookmark",
        default=None,
        dest="bookmark",
        help="Filter by bookmark name.",
    )
    audit_search_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    query_parser = audits_subparsers.add_parser(
        "query",
        help="Save and reuse governance audit search filters.",
        description=(
            "Save a governance audit search filter under a name so it "
            "can be executed again later without retyping its "
            "filters.\n\n"
            "Saved queries are independent metadata: saving one never "
            "executes it and never mutates audit history, labels, or "
            "bookmarks.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    query_subparsers = query_parser.add_subparsers(
        dest="query_command", required=True
    )

    query_save_parser = query_subparsers.add_parser(
        "save",
        help="Save a search filter under a name.",
    )
    query_save_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name for the new saved query.",
    )
    query_save_parser.add_argument(
        "--audit-id",
        default=None,
        dest="audit_id",
        help="Filter by exact audit identifier.",
    )
    query_save_parser.add_argument(
        "--healthy",
        action="store_true",
        help="Only include healthy audits.",
    )
    query_save_parser.add_argument(
        "--unhealthy",
        action="store_true",
        help="Only include unhealthy audits.",
    )
    query_save_parser.add_argument(
        "--label",
        default=None,
        dest="label",
        help="Filter by applied label.",
    )
    query_save_parser.add_argument(
        "--bookmark",
        default=None,
        dest="bookmark",
        help="Filter by bookmark name.",
    )
    query_save_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    query_run_parser = query_subparsers.add_parser(
        "run",
        help="Execute a saved search filter.",
    )
    query_run_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the saved query to execute.",
    )
    query_run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    query_list_parser = query_subparsers.add_parser(
        "list",
        help="List every saved search filter.",
    )
    query_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    query_show_parser = query_subparsers.add_parser(
        "show",
        help="Show one saved search filter.",
    )
    query_show_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the saved query to show.",
    )
    query_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    query_delete_parser = query_subparsers.add_parser(
        "delete",
        help="Delete one saved search filter.",
    )
    query_delete_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the saved query to delete.",
    )
    query_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    collections_parser = audits_subparsers.add_parser(
        "collections",
        help="Manage explicit groups of governance integrity audits.",
        description=(
            "Create and manage named, explicit groups of governance "
            "integrity audits (e.g. a release, an incident, a "
            "migration, an investigation).\n\n"
            "Unlike a saved query (reusable filter criteria, "
            "re-evaluated on every run), a collection stores explicit "
            "membership decided by the operator. Collections are "
            "independent metadata.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    collections_subparsers = collections_parser.add_subparsers(
        dest="collections_command", required=True
    )

    collection_create_parser = collections_subparsers.add_parser(
        "create",
        help="Create a new collection.",
    )
    collection_create_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name for the new collection.",
    )
    collection_create_parser.add_argument(
        "--description",
        default=None,
        dest="description",
        help="Optional description for the collection.",
    )
    collection_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    collection_list_parser = collections_subparsers.add_parser(
        "list",
        help="List every collection.",
    )
    collection_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    collection_show_parser = collections_subparsers.add_parser(
        "show",
        help="Show one collection and its audits.",
    )
    collection_show_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the collection to show.",
    )
    collection_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    collection_delete_parser = collections_subparsers.add_parser(
        "delete",
        help="Delete one collection and all of its entries.",
    )
    collection_delete_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the collection to delete.",
    )
    collection_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    collection_add_parser = collections_subparsers.add_parser(
        "add",
        help="Add an audit to a collection.",
    )
    collection_add_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the collection to add the audit to.",
    )
    collection_add_parser.add_argument(
        "--audit-id",
        required=True,
        dest="audit_id",
        help="Identifier of the audit to add.",
    )
    collection_add_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    collection_remove_parser = collections_subparsers.add_parser(
        "remove",
        help="Remove an audit from a collection.",
    )
    collection_remove_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the collection to remove the audit from.",
    )
    collection_remove_parser.add_argument(
        "--audit-id",
        required=True,
        dest="audit_id",
        help="Identifier of the audit to remove.",
    )
    collection_remove_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    report_parser = audits_subparsers.add_parser(
        "report",
        help="Generate a portable report from audits or a collection.",
        description=(
            "Generate a portable, point-in-time JSON or Markdown "
            "report summarizing one or more governance integrity "
            "audits.\n\n"
            "This is read-only: it never executes a new audit and "
            "never mutates audit history or collections. If --output "
            "is omitted, the report is written to stdout.\n\n"
            "Exit codes: 0 the report was generated, 2 the report "
            "could not be generated (unknown audit or collection, or "
            "invalid configuration)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    report_subparsers = report_parser.add_subparsers(
        dest="report_command", required=True
    )

    report_collection_parser = report_subparsers.add_parser(
        "collection",
        help="Generate a report from every audit in a collection.",
    )
    report_collection_parser.add_argument(
        "--collection",
        required=True,
        dest="collection",
        help="Name of the collection to report on.",
    )
    report_collection_parser.add_argument(
        "--title",
        default=None,
        dest="title",
        help="Report title. Default: the collection's name.",
    )
    report_collection_parser.add_argument(
        "--output",
        default=None,
        dest="output",
        help="Path to write the report to. Default: stdout.",
    )
    report_collection_parser.add_argument(
        "--format",
        choices=["json", "md"],
        default="json",
        dest="report_format",
        help="Report format. Default: json.",
    )

    report_audits_parser = report_subparsers.add_parser(
        "audits",
        help="Generate a report from an explicit list of audits.",
    )
    report_audits_parser.add_argument(
        "--audit-id",
        action="append",
        dest="audit_ids",
        default=None,
        help=(
            "Identifier of an audit to include. Repeatable; the "
            "report preserves the order given."
        ),
    )
    report_audits_parser.add_argument(
        "--title",
        required=True,
        dest="title",
        help="Report title.",
    )
    report_audits_parser.add_argument(
        "--output",
        default=None,
        dest="output",
        help="Path to write the report to. Default: stdout.",
    )
    report_audits_parser.add_argument(
        "--format",
        choices=["json", "md"],
        default="json",
        dest="report_format",
        help="Report format. Default: json.",
    )

    templates_parser = audits_subparsers.add_parser(
        "templates",
        help="Manage reusable governance audit report templates.",
        description=(
            "Create and manage named, reusable report configurations "
            "that reference a collection or a saved query, plus an "
            "output format, so a consistent report can be generated "
            "again later without retyping its inputs.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    templates_subparsers = templates_parser.add_subparsers(
        dest="templates_command", required=True
    )

    template_create_parser = templates_subparsers.add_parser(
        "create",
        help="Create a new report template.",
    )
    template_create_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name for the new template.",
    )
    template_create_parser.add_argument(
        "--title",
        required=True,
        dest="title",
        help="Title used for reports generated from this template.",
    )
    template_create_parser.add_argument(
        "--collection",
        default=None,
        dest="collection",
        help="Name of the collection to source audits from.",
    )
    template_create_parser.add_argument(
        "--saved-query",
        default=None,
        dest="saved_query",
        help="Name of the saved query to source audits from.",
    )
    template_create_parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        dest="output_format",
        help="Output format for generated reports. Default: json.",
    )
    template_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    template_list_parser = templates_subparsers.add_parser(
        "list",
        help="List every report template.",
    )
    template_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    template_show_parser = templates_subparsers.add_parser(
        "show",
        help="Show one report template.",
    )
    template_show_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the template to show.",
    )
    template_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    template_delete_parser = templates_subparsers.add_parser(
        "delete",
        help="Delete one report template.",
    )
    template_delete_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the template to delete.",
    )
    template_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    template_generate_parser = templates_subparsers.add_parser(
        "generate",
        help="Generate a report from a template.",
    )
    template_generate_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the template to generate a report from.",
    )
    template_generate_parser.add_argument(
        "--output",
        default=None,
        dest="output",
        help="Path to write the report to. Default: stdout.",
    )

    schedules_parser = audits_subparsers.add_parser(
        "schedules",
        help="Manage execution plans for governance audit report templates.",
        description=(
            "Create and manage named execution plans (schedules) for "
            "report templates.\n\n"
            "This layer only manages schedules and execution metadata "
            "-- no background worker executes a schedule yet.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    schedules_subparsers = schedules_parser.add_subparsers(
        dest="schedules_command", required=True
    )

    schedule_create_parser = schedules_subparsers.add_parser(
        "create",
        help="Create a new report schedule.",
    )
    schedule_create_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name for the new schedule.",
    )
    schedule_create_parser.add_argument(
        "--template",
        required=True,
        dest="template",
        help="Name of the report template to schedule.",
    )
    schedule_create_parser.add_argument(
        "--frequency",
        required=True,
        choices=["daily", "weekly", "monthly"],
        dest="frequency",
        help="How often the schedule is intended to run.",
    )
    schedule_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    schedule_list_parser = schedules_subparsers.add_parser(
        "list",
        help="List every report schedule.",
    )
    schedule_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    schedule_show_parser = schedules_subparsers.add_parser(
        "show",
        help="Show one report schedule.",
    )
    schedule_show_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the schedule to show.",
    )
    schedule_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    schedule_enable_parser = schedules_subparsers.add_parser(
        "enable",
        help="Enable a report schedule.",
    )
    schedule_enable_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the schedule to enable.",
    )
    schedule_enable_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    schedule_disable_parser = schedules_subparsers.add_parser(
        "disable",
        help="Disable a report schedule.",
    )
    schedule_disable_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the schedule to disable.",
    )
    schedule_disable_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    schedule_delete_parser = schedules_subparsers.add_parser(
        "delete",
        help="Delete one report schedule.",
    )
    schedule_delete_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the schedule to delete.",
    )
    schedule_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    queue_parser = audits_subparsers.add_parser(
        "queue",
        help="Convert enabled report schedules into runnable execution jobs.",
        description=(
            "Convert enabled governance audit report schedules into "
            "runnable execution jobs, ready for a future worker to "
            "pick up.\n\n"
            "No background execution happens in this command; it only "
            "prepares the queue.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    queue_subparsers = queue_parser.add_subparsers(
        dest="queue_command", required=True
    )

    queue_enqueue_parser = queue_subparsers.add_parser(
        "enqueue",
        help="Queue one schedule as a pending job.",
    )
    queue_enqueue_parser.add_argument(
        "--schedule",
        required=True,
        dest="schedule",
        help="Name of the schedule to queue.",
    )
    queue_enqueue_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    queue_enqueue_due_parser = queue_subparsers.add_parser(
        "enqueue-due",
        help="Queue every currently enabled schedule.",
    )
    queue_enqueue_due_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    queue_list_parser = queue_subparsers.add_parser(
        "list",
        help="List every queued job.",
    )
    queue_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    queue_show_parser = queue_subparsers.add_parser(
        "show",
        help="Show one queued job.",
    )
    queue_show_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the job to show.",
    )
    queue_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    queue_delete_parser = queue_subparsers.add_parser(
        "delete",
        help="Remove one job from the queue.",
    )
    queue_delete_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the job to remove.",
    )
    queue_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    queue_clear_parser = queue_subparsers.add_parser(
        "clear",
        help="Remove every job from the queue.",
    )
    queue_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    worker_parser = audits_subparsers.add_parser(
        "worker",
        help="Execute queued governance audit execution jobs into reports.",
        description=(
            "Synchronously process queued governance audit execution "
            "jobs into generated reports.\n\n"
            "Single-threaded only: jobs run one at a time, in-process.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    worker_subparsers = worker_parser.add_subparsers(
        dest="worker_command", required=True
    )

    worker_run_parser = worker_subparsers.add_parser(
        "run",
        help="Run one queued job.",
    )
    worker_run_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the job to run.",
    )
    worker_run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    worker_run_all_parser = worker_subparsers.add_parser(
        "run-all",
        help="Run every currently queued job.",
    )
    worker_run_all_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    worker_history_parser = worker_subparsers.add_parser(
        "history",
        help="List every stored execution record.",
    )
    worker_history_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    worker_show_parser = worker_subparsers.add_parser(
        "show",
        help="Show one stored execution record.",
    )
    worker_show_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the execution record to show.",
    )
    worker_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    worker_clear_parser = worker_subparsers.add_parser(
        "clear",
        help="Remove every stored execution record.",
    )
    worker_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    retry_parser = audits_subparsers.add_parser(
        "retry",
        help="Recover failed governance audit execution jobs.",
        description=(
            "Retry a failed governance audit execution job by "
            "queuing a fresh job for the same schedule.\n\n"
            "The original failed execution record is never modified; "
            "only SUCCESS executions cannot be retried.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    retry_subparsers = retry_parser.add_subparsers(
        dest="retry_command", required=True
    )

    retry_run_parser = retry_subparsers.add_parser(
        "run",
        help="Retry one failed execution job.",
    )
    retry_run_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the failed execution job to retry.",
    )
    retry_run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    retry_history_parser = retry_subparsers.add_parser(
        "history",
        help="List every stored retry record.",
    )
    retry_history_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    retry_show_parser = retry_subparsers.add_parser(
        "show",
        help="Show one stored retry record.",
    )
    retry_show_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the original job to show the retry for.",
    )
    retry_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    retry_clear_parser = retry_subparsers.add_parser(
        "clear",
        help="Remove every stored retry record.",
    )
    retry_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dlq_parser = audits_subparsers.add_parser(
        "dlq",
        help="Preserve permanently failed governance audit executions.",
        description=(
            "Archive permanently failed governance audit executions "
            "into a dead letter queue for manual investigation.\n\n"
            "No automatic recovery: archived records stay archived "
            "until a human deletes them.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dlq_subparsers = dlq_parser.add_subparsers(
        dest="dlq_command", required=True
    )

    dlq_archive_parser = dlq_subparsers.add_parser(
        "archive",
        help="Archive one failed execution.",
    )
    dlq_archive_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the failed execution job to archive.",
    )
    dlq_archive_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dlq_list_parser = dlq_subparsers.add_parser(
        "list",
        help="List every dead letter record.",
    )
    dlq_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dlq_show_parser = dlq_subparsers.add_parser(
        "show",
        help="Show one dead letter record.",
    )
    dlq_show_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the dead letter record to show.",
    )
    dlq_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dlq_delete_parser = dlq_subparsers.add_parser(
        "delete",
        help="Remove one dead letter record.",
    )
    dlq_delete_parser.add_argument(
        "--job-id",
        required=True,
        dest="job_id",
        help="Identifier of the dead letter record to remove.",
    )
    dlq_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dlq_clear_parser = dlq_subparsers.add_parser(
        "clear",
        help="Remove every dead letter record.",
    )
    dlq_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    policy_parser = audits_subparsers.add_parser(
        "policy",
        help="Manage governance audit failure-handling policies.",
        description=(
            "Create and manage named governance audit failure "
            "policies: how many times a failed execution may be "
            "retried before falling back to a configured action.\n\n"
            "This command only manages policy configuration; no "
            "retries are executed automatically.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    policy_subparsers = policy_parser.add_subparsers(
        dest="policy_command", required=True
    )

    policy_create_parser = policy_subparsers.add_parser(
        "create",
        help="Create a new failure policy.",
    )
    policy_create_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name for the new policy.",
    )
    policy_create_parser.add_argument(
        "--action",
        required=True,
        dest="action",
        choices=[
            action.value
            for action in GovernanceIntegrityFailureAction
        ],
        help="Action to take once the retry budget is exhausted.",
    )
    policy_create_parser.add_argument(
        "--max-retries",
        required=True,
        type=int,
        dest="max_retry_attempts",
        help="Number of retry attempts allowed before the action applies.",
    )
    policy_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    policy_list_parser = policy_subparsers.add_parser(
        "list",
        help="List every failure policy.",
    )
    policy_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    policy_show_parser = policy_subparsers.add_parser(
        "show",
        help="Show one failure policy.",
    )
    policy_show_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the policy to show.",
    )
    policy_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    policy_update_parser = policy_subparsers.add_parser(
        "update",
        help="Update an existing failure policy.",
    )
    policy_update_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the policy to update.",
    )
    policy_update_parser.add_argument(
        "--action",
        required=False,
        default=None,
        dest="action",
        choices=[
            action.value
            for action in GovernanceIntegrityFailureAction
        ],
        help="New action to take once the retry budget is exhausted.",
    )
    policy_update_parser.add_argument(
        "--max-retries",
        required=False,
        default=None,
        type=int,
        dest="max_retry_attempts",
        help="New number of retry attempts allowed before the action applies.",
    )
    policy_update_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    policy_delete_parser = policy_subparsers.add_parser(
        "delete",
        help="Delete one failure policy.",
    )
    policy_delete_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the policy to delete.",
    )
    policy_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    metrics_parser = audits_subparsers.add_parser(
        "metrics",
        help="Track governance audit worker execution metrics.",
        description=(
            "Compute aggregate governance audit worker execution "
            "metrics: run counts, success rate, and average "
            "runtime.\n\n"
            "This command only reports metrics; no dashboards or "
            "alerts are produced.\n\n"
            "Exit codes: 0 the metrics were computed, 2 the metrics "
            "could not be computed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    metrics_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    metrics_subparsers = metrics_parser.add_subparsers(
        dest="metrics_command", required=False
    )

    metrics_template_parser = metrics_subparsers.add_parser(
        "template",
        help="Compute execution metrics for one template.",
    )
    metrics_template_parser.add_argument(
        "--template",
        required=True,
        dest="template",
        help="Name of the template to compute metrics for.",
    )
    metrics_template_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    alerts_parser = audits_subparsers.add_parser(
        "alerts",
        help="Generate alerts from governance audit execution metrics.",
        description=(
            "Generate alerts when governance audit worker execution "
            "metrics cross configured thresholds.\n\n"
            "This command only produces alerts; no notifications are "
            "sent.\n\n"
            "Exit codes: 0 alerts were generated (even if none were "
            "violated), 2 alerts could not be generated."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    alerts_parser.add_argument(
        "--min-success",
        type=float,
        default=DEFAULT_MINIMUM_SUCCESS_RATE,
        dest="minimum_success_rate",
        help=(
            "Minimum acceptable success rate percentage. "
            f"Default: {DEFAULT_MINIMUM_SUCCESS_RATE}."
        ),
    )
    alerts_parser.add_argument(
        "--max-failure",
        type=float,
        default=DEFAULT_MAXIMUM_FAILURE_RATE,
        dest="maximum_failure_rate",
        help=(
            "Maximum acceptable failure rate percentage. "
            f"Default: {DEFAULT_MAXIMUM_FAILURE_RATE}."
        ),
    )
    alerts_parser.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAXIMUM_AVERAGE_DURATION_MS,
        dest="maximum_average_duration_ms",
        help=(
            "Maximum acceptable average runtime in milliseconds. "
            f"Default: {DEFAULT_MAXIMUM_AVERAGE_DURATION_MS}."
        ),
    )
    alerts_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    alerts_subparsers = alerts_parser.add_subparsers(
        dest="alerts_command", required=False
    )

    alerts_template_parser = alerts_subparsers.add_parser(
        "template",
        help="Generate alerts from one template's execution metrics.",
    )
    alerts_template_parser.add_argument(
        "--template",
        required=True,
        dest="template",
        help="Name of the template to generate alerts for.",
    )
    alerts_template_parser.add_argument(
        "--min-success",
        type=float,
        default=DEFAULT_MINIMUM_SUCCESS_RATE,
        dest="minimum_success_rate",
        help=(
            "Minimum acceptable success rate percentage. "
            f"Default: {DEFAULT_MINIMUM_SUCCESS_RATE}."
        ),
    )
    alerts_template_parser.add_argument(
        "--max-failure",
        type=float,
        default=DEFAULT_MAXIMUM_FAILURE_RATE,
        dest="maximum_failure_rate",
        help=(
            "Maximum acceptable failure rate percentage. "
            f"Default: {DEFAULT_MAXIMUM_FAILURE_RATE}."
        ),
    )
    alerts_template_parser.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAXIMUM_AVERAGE_DURATION_MS,
        dest="maximum_average_duration_ms",
        help=(
            "Maximum acceptable average runtime in milliseconds. "
            f"Default: {DEFAULT_MAXIMUM_AVERAGE_DURATION_MS}."
        ),
    )
    alerts_template_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_parser = audits_subparsers.add_parser(
        "notifications",
        help="Manage the governance audit notification pipeline.",
        description=(
            "Convert generated governance audit execution alerts "
            "into queued delivery requests.\n\n"
            "Actual delivery providers come later; this command only "
            "queues notifications.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    notifications_subparsers = notifications_parser.add_subparsers(
        dest="notifications_command", required=True
    )

    notifications_queue_parser = notifications_subparsers.add_parser(
        "queue",
        help="Generate alerts and queue new notifications.",
    )
    notifications_queue_parser.add_argument(
        "--min-success",
        type=float,
        default=DEFAULT_MINIMUM_SUCCESS_RATE,
        dest="minimum_success_rate",
        help=(
            "Minimum acceptable success rate percentage. "
            f"Default: {DEFAULT_MINIMUM_SUCCESS_RATE}."
        ),
    )
    notifications_queue_parser.add_argument(
        "--max-failure",
        type=float,
        default=DEFAULT_MAXIMUM_FAILURE_RATE,
        dest="maximum_failure_rate",
        help=(
            "Maximum acceptable failure rate percentage. "
            f"Default: {DEFAULT_MAXIMUM_FAILURE_RATE}."
        ),
    )
    notifications_queue_parser.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAXIMUM_AVERAGE_DURATION_MS,
        dest="maximum_average_duration_ms",
        help=(
            "Maximum acceptable average runtime in milliseconds. "
            f"Default: {DEFAULT_MAXIMUM_AVERAGE_DURATION_MS}."
        ),
    )
    notifications_queue_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_list_parser = notifications_subparsers.add_parser(
        "list",
        help="List every queued notification.",
    )
    notifications_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_show_parser = notifications_subparsers.add_parser(
        "show",
        help="Show one queued notification.",
    )
    notifications_show_parser.add_argument(
        "--notification-id",
        required=True,
        dest="notification_id",
        help="Identifier of the notification to show.",
    )
    notifications_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_delete_parser = notifications_subparsers.add_parser(
        "delete",
        help="Remove one queued notification.",
    )
    notifications_delete_parser.add_argument(
        "--notification-id",
        required=True,
        dest="notification_id",
        help="Identifier of the notification to remove.",
    )
    notifications_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_clear_parser = notifications_subparsers.add_parser(
        "clear",
        help="Remove every queued notification.",
    )
    notifications_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_parser = notifications_subparsers.add_parser(
        "metrics",
        help="Show live governance audit notification delivery metrics.",
    )
    notifications_metrics_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    notifications_metrics_subparsers = (
        notifications_metrics_parser.add_subparsers(
            dest="notifications_metrics_command", required=False
        )
    )

    notifications_metrics_reset_parser = (
        notifications_metrics_subparsers.add_parser(
            "reset",
            help="Clear live notification delivery metrics.",
        )
    )
    notifications_metrics_reset_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_export_parser = (
        notifications_metrics_subparsers.add_parser(
            "export",
            help=(
                "Export the durably stored notification delivery "
                "metrics snapshot."
            ),
        )
    )
    notifications_metrics_export_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_reload_parser = (
        notifications_metrics_subparsers.add_parser(
            "reload",
            help=(
                "Reload notification delivery metrics from durable "
                "storage."
            ),
        )
    )
    notifications_metrics_reload_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_history_parser = (
        notifications_metrics_subparsers.add_parser(
            "history",
            help=(
                "List captured notification delivery metrics "
                "snapshots, newest first."
            ),
        )
    )
    notifications_metrics_history_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Maximum number of snapshots to list.",
    )
    notifications_metrics_history_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_latest_parser = (
        notifications_metrics_subparsers.add_parser(
            "latest",
            help=(
                "Show the most recently captured notification "
                "delivery metrics snapshot."
            ),
        )
    )
    notifications_metrics_latest_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_export_json_parser = (
        notifications_metrics_subparsers.add_parser(
            "export-json",
            help=(
                "Export current metrics (and optionally history) as "
                "JSON for offline analysis."
            ),
        )
    )
    notifications_metrics_export_json_parser.add_argument(
        "--history",
        action="store_true",
        dest="include_history",
        help="Include captured metrics history in the export.",
    )
    notifications_metrics_export_json_parser.add_argument(
        "--output",
        default=None,
        dest="output_path",
        help="File path to write the export to. Defaults to stdout.",
    )
    notifications_metrics_export_json_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit a machine-readable confirmation when --output is set.",
    )

    notifications_metrics_export_csv_parser = (
        notifications_metrics_subparsers.add_parser(
            "export-csv",
            help=(
                "Export current metrics (and optionally history) as "
                "CSV for offline analysis."
            ),
        )
    )
    notifications_metrics_export_csv_parser.add_argument(
        "--history",
        action="store_true",
        dest="include_history",
        help="Include captured metrics history in the export.",
    )
    notifications_metrics_export_csv_parser.add_argument(
        "--output",
        default=None,
        dest="output_path",
        help="File path to write the export to. Defaults to stdout.",
    )
    notifications_metrics_export_csv_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit a machine-readable confirmation when --output is set.",
    )

    notifications_metrics_aggregate_parser = (
        notifications_metrics_subparsers.add_parser(
            "aggregate",
            help=(
                "Aggregate captured notification delivery metrics "
                "history over a time window."
            ),
        )
    )
    notifications_metrics_aggregate_parser.add_argument(
        "--from",
        default=None,
        dest="range_start",
        help=(
            "ISO-8601 timezone-aware start of the window. Required "
            "unless --hourly or --daily is given."
        ),
    )
    notifications_metrics_aggregate_parser.add_argument(
        "--to",
        default=None,
        dest="range_end",
        help=(
            "ISO-8601 timezone-aware end of the window. Required "
            "unless --hourly or --daily is given."
        ),
    )
    notifications_metrics_aggregate_parser.add_argument(
        "--hourly",
        action="store_true",
        dest="hourly",
        help="Bucket the window into consecutive 1-hour aggregates.",
    )
    notifications_metrics_aggregate_parser.add_argument(
        "--daily",
        action="store_true",
        dest="daily",
        help="Bucket the window into consecutive 1-day aggregates.",
    )
    notifications_metrics_aggregate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_alerts_parser = (
        notifications_metrics_subparsers.add_parser(
            "alerts",
            help=(
                "Evaluate and show active notification delivery "
                "metric alerts."
            ),
        )
    )
    notifications_metrics_alerts_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    notifications_metrics_alerts_subparsers = (
        notifications_metrics_alerts_parser.add_subparsers(
            dest="notifications_metrics_alerts_command",
            required=False,
        )
    )

    notifications_metrics_alerts_clear_parser = (
        notifications_metrics_alerts_subparsers.add_parser(
            "clear",
            help="Dismiss every active notification delivery metric alert.",
        )
    )
    notifications_metrics_alerts_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_dashboard_parser = (
        notifications_metrics_subparsers.add_parser(
            "dashboard",
            help=(
                "Show a compact notification delivery metrics "
                "dashboard."
            ),
        )
    )
    notifications_metrics_dashboard_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_requests_parser = (
        notifications_metrics_subparsers.add_parser(
            "requests",
            help="Show governance API request metrics.",
        )
    )
    notifications_metrics_requests_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_collector_parser = (
        notifications_metrics_subparsers.add_parser(
            "collector",
            help="Inspect or trigger the background metrics collector.",
        )
    )
    notifications_metrics_collector_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    notifications_metrics_collector_subparsers = (
        notifications_metrics_collector_parser.add_subparsers(
            dest="notifications_metrics_collector_command",
            required=True,
        )
    )

    notifications_metrics_collector_status_parser = (
        notifications_metrics_collector_subparsers.add_parser(
            "status",
            help="Show whether the background metrics collector is running.",
        )
    )
    notifications_metrics_collector_status_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_collector_collect_parser = (
        notifications_metrics_collector_subparsers.add_parser(
            "collect",
            help="Manually trigger one metrics history collection.",
        )
    )
    notifications_metrics_collector_collect_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_retention_parser = (
        notifications_metrics_subparsers.add_parser(
            "retention",
            help="Inspect or run governance metrics history retention.",
        )
    )
    notifications_metrics_retention_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    notifications_metrics_retention_subparsers = (
        notifications_metrics_retention_parser.add_subparsers(
            dest="notifications_metrics_retention_command",
            required=True,
        )
    )

    notifications_metrics_retention_status_parser = (
        notifications_metrics_retention_subparsers.add_parser(
            "status",
            help=(
                "Show the retention policy and how many snapshots "
                "are expired."
            ),
        )
    )
    notifications_metrics_retention_status_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_retention_run_parser = (
        notifications_metrics_retention_subparsers.add_parser(
            "run",
            help="Delete every currently expired metrics snapshot.",
        )
    )
    notifications_metrics_retention_run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_config_parser = (
        notifications_metrics_subparsers.add_parser(
            "config",
            help="Show or reload governance metrics configuration.",
        )
    )
    notifications_metrics_config_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )
    notifications_metrics_config_subparsers = (
        notifications_metrics_config_parser.add_subparsers(
            dest="notifications_metrics_config_command",
            required=True,
        )
    )

    notifications_metrics_config_show_parser = (
        notifications_metrics_config_subparsers.add_parser(
            "show",
            help="Show the currently loaded metrics configuration.",
        )
    )
    notifications_metrics_config_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_config_reload_parser = (
        notifications_metrics_config_subparsers.add_parser(
            "reload",
            help="Reload metrics configuration from the environment.",
        )
    )
    notifications_metrics_config_reload_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_health_parser = (
        notifications_metrics_subparsers.add_parser(
            "health",
            help=(
                "Build, initialize, and report health of the "
                "governance metrics subsystem."
            ),
        )
    )
    notifications_metrics_health_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    notifications_metrics_bootstrap_parser = (
        notifications_metrics_subparsers.add_parser(
            "bootstrap",
            help=(
                "Run the full governance metrics bootstrap sequence "
                "as a smoke check."
            ),
        )
    )
    notifications_metrics_bootstrap_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    channels_parser = audits_subparsers.add_parser(
        "channels",
        help="Manage governance audit notification delivery channels.",
        description=(
            "Create and manage named delivery channels for future "
            "governance audit notification providers.\n\n"
            "No actual sending happens in this command; it only "
            "manages channel configuration.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    channels_subparsers = channels_parser.add_subparsers(
        dest="channels_command", required=True
    )

    channels_create_parser = channels_subparsers.add_parser(
        "create",
        help="Create a new notification channel.",
    )
    channels_create_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name for the new channel.",
    )
    channels_create_parser.add_argument(
        "--type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Kind of delivery target this channel points at.",
    )
    channels_create_parser.add_argument(
        "--destination",
        required=True,
        dest="destination",
        help="Delivery destination for this channel.",
    )
    channels_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    channels_list_parser = channels_subparsers.add_parser(
        "list",
        help="List every notification channel.",
    )
    channels_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    channels_show_parser = channels_subparsers.add_parser(
        "show",
        help="Show one notification channel.",
    )
    channels_show_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the channel to show.",
    )
    channels_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    channels_enable_parser = channels_subparsers.add_parser(
        "enable",
        help="Enable one notification channel.",
    )
    channels_enable_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the channel to enable.",
    )
    channels_enable_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    channels_disable_parser = channels_subparsers.add_parser(
        "disable",
        help="Disable one notification channel.",
    )
    channels_disable_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the channel to disable.",
    )
    channels_disable_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    channels_update_parser = channels_subparsers.add_parser(
        "update",
        help="Update a notification channel's destination.",
    )
    channels_update_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the channel to update.",
    )
    channels_update_parser.add_argument(
        "--destination",
        required=True,
        dest="destination",
        help="New delivery destination for this channel.",
    )
    channels_update_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    channels_delete_parser = channels_subparsers.add_parser(
        "delete",
        help="Delete one notification channel.",
    )
    channels_delete_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the channel to delete.",
    )
    channels_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dispatch_parser = audits_subparsers.add_parser(
        "dispatch",
        help="Match pending governance audit notifications to channels.",
        description=(
            "Match pending governance audit notifications to enabled "
            "delivery channels and record the resulting dispatch "
            "attempts.\n\n"
            "No external APIs are called in this command; it only "
            "records that a notification was matched to a channel.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dispatch_subparsers = dispatch_parser.add_subparsers(
        dest="dispatch_command", required=True
    )

    dispatch_run_parser = dispatch_subparsers.add_parser(
        "run",
        help="Dispatch every pending notification to enabled channels.",
    )
    dispatch_run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dispatch_list_parser = dispatch_subparsers.add_parser(
        "list",
        help="List every dispatch record.",
    )
    dispatch_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dispatch_show_parser = dispatch_subparsers.add_parser(
        "show",
        help="Show one dispatch record.",
    )
    dispatch_show_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch record to show.",
    )
    dispatch_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dispatch_delete_parser = dispatch_subparsers.add_parser(
        "delete",
        help="Remove one dispatch record.",
    )
    dispatch_delete_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch record to remove.",
    )
    dispatch_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    dispatch_clear_parser = dispatch_subparsers.add_parser(
        "clear",
        help="Remove every dispatch record.",
    )
    dispatch_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    deliver_parser = audits_subparsers.add_parser(
        "deliver",
        help="Execute queued governance audit notification dispatches.",
        description=(
            "Execute queued governance audit notification dispatches "
            "through pluggable, per-channel-type providers.\n\n"
            "Providers are local stubs in this command: no external "
            "I/O is performed.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    deliver_subparsers = deliver_parser.add_subparsers(
        dest="deliver_command", required=True
    )

    deliver_run_parser = deliver_subparsers.add_parser(
        "run",
        help="Deliver one queued dispatch.",
    )
    deliver_run_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch to deliver.",
    )
    deliver_run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    deliver_run_all_parser = deliver_subparsers.add_parser(
        "run-all",
        help="Deliver every currently queued dispatch.",
    )
    deliver_run_all_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_history_parser = audits_subparsers.add_parser(
        "delivery-history",
        help="Inspect permanently recorded governance audit deliveries.",
        description=(
            "Inspect the immutable history of governance audit "
            "notification delivery attempts.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    delivery_history_subparsers = (
        delivery_history_parser.add_subparsers(
            dest="delivery_history_command", required=True
        )
    )

    delivery_history_list_parser = (
        delivery_history_subparsers.add_parser(
            "list",
            help="List every delivery history record.",
        )
    )
    delivery_history_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_history_show_parser = (
        delivery_history_subparsers.add_parser(
            "show",
            help="Show one delivery history record.",
        )
    )
    delivery_history_show_parser.add_argument(
        "--delivery-id",
        required=True,
        dest="delivery_id",
        help="Identifier of the delivery history record to show.",
    )
    delivery_history_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_history_clear_parser = (
        delivery_history_subparsers.add_parser(
            "clear",
            help="Remove every delivery history record.",
        )
    )
    delivery_history_clear_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    preferences_parser = audits_subparsers.add_parser(
        "preferences",
        help="Manage governance audit notification routing preferences.",
        description=(
            "Create and manage named routing preferences: which "
            "channels a notification should reach once its severity "
            "meets a minimum threshold.\n\n"
            "The notification dispatcher resolves channels through "
            "these preferences instead of dispatching to every "
            "enabled channel.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    preferences_subparsers = preferences_parser.add_subparsers(
        dest="preferences_command", required=True
    )

    preferences_create_parser = preferences_subparsers.add_parser(
        "create",
        help="Create a new notification preference.",
    )
    preferences_create_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name for the new preference.",
    )
    preferences_create_parser.add_argument(
        "--minimum-severity",
        required=True,
        dest="minimum_severity",
        choices=[
            severity.value
            for severity in GovernanceIntegrityAlertSeverity
        ],
        help="Minimum alert severity this preference routes.",
    )
    preferences_create_parser.add_argument(
        "--channel",
        required=True,
        action="append",
        dest="channels",
        help="Channel name to route to. Repeatable.",
    )
    preferences_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    preferences_list_parser = preferences_subparsers.add_parser(
        "list",
        help="List every notification preference.",
    )
    preferences_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    preferences_show_parser = preferences_subparsers.add_parser(
        "show",
        help="Show one notification preference.",
    )
    preferences_show_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the preference to show.",
    )
    preferences_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    preferences_update_parser = preferences_subparsers.add_parser(
        "update",
        help="Update an existing notification preference.",
    )
    preferences_update_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the preference to update.",
    )
    preferences_update_parser.add_argument(
        "--minimum-severity",
        required=False,
        default=None,
        dest="minimum_severity",
        choices=[
            severity.value
            for severity in GovernanceIntegrityAlertSeverity
        ],
        help="New minimum alert severity this preference routes.",
    )
    preferences_update_parser.add_argument(
        "--channel",
        required=False,
        default=None,
        action="append",
        dest="channels",
        help="New channel name to route to. Repeatable.",
    )
    preferences_update_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    preferences_delete_parser = preferences_subparsers.add_parser(
        "delete",
        help="Delete one notification preference.",
    )
    preferences_delete_parser.add_argument(
        "--name",
        required=True,
        dest="name",
        help="Name of the preference to delete.",
    )
    preferences_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_policy_parser = audits_subparsers.add_parser(
        "delivery-policy",
        help="Manage per-channel governance audit delivery policies.",
        description=(
            "Create and manage per-channel governance audit delivery "
            "policies: retry, timeout, and rate-limit configuration "
            "that future providers can honor.\n\n"
            "This command configures delivery behavior only; current "
            "stub providers may ignore these values.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    delivery_policy_subparsers = (
        delivery_policy_parser.add_subparsers(
            dest="delivery_policy_command", required=True
        )
    )

    delivery_policy_create_parser = (
        delivery_policy_subparsers.add_parser(
            "create",
            help="Create a new delivery policy for a channel.",
        )
    )
    delivery_policy_create_parser.add_argument(
        "--channel",
        required=True,
        dest="channel_name",
        help="Name of the channel this policy applies to.",
    )
    delivery_policy_create_parser.add_argument(
        "--retry-limit",
        required=True,
        type=int,
        dest="retry_limit",
        help="Maximum number of delivery retries.",
    )
    delivery_policy_create_parser.add_argument(
        "--timeout",
        required=True,
        type=int,
        dest="timeout_seconds",
        help="Delivery timeout in seconds.",
    )
    delivery_policy_create_parser.add_argument(
        "--rate-limit",
        required=True,
        type=int,
        dest="rate_limit_per_minute",
        help="Maximum deliveries per minute.",
    )
    delivery_policy_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_policy_list_parser = (
        delivery_policy_subparsers.add_parser(
            "list",
            help="List every delivery policy.",
        )
    )
    delivery_policy_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_policy_show_parser = (
        delivery_policy_subparsers.add_parser(
            "show",
            help="Show one delivery policy.",
        )
    )
    delivery_policy_show_parser.add_argument(
        "--channel",
        required=True,
        dest="channel_name",
        help="Name of the channel to show the policy for.",
    )
    delivery_policy_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_policy_update_parser = (
        delivery_policy_subparsers.add_parser(
            "update",
            help="Update an existing delivery policy.",
        )
    )
    delivery_policy_update_parser.add_argument(
        "--channel",
        required=True,
        dest="channel_name",
        help="Name of the channel to update the policy for.",
    )
    delivery_policy_update_parser.add_argument(
        "--retry-limit",
        required=False,
        default=None,
        type=int,
        dest="retry_limit",
        help="New maximum number of delivery retries.",
    )
    delivery_policy_update_parser.add_argument(
        "--timeout",
        required=False,
        default=None,
        type=int,
        dest="timeout_seconds",
        help="New delivery timeout in seconds.",
    )
    delivery_policy_update_parser.add_argument(
        "--rate-limit",
        required=False,
        default=None,
        type=int,
        dest="rate_limit_per_minute",
        help="New maximum deliveries per minute.",
    )
    delivery_policy_update_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_policy_delete_parser = (
        delivery_policy_subparsers.add_parser(
            "delete",
            help="Delete one delivery policy.",
        )
    )
    delivery_policy_delete_parser.add_argument(
        "--channel",
        required=True,
        dest="channel_name",
        help="Name of the channel to delete the policy for.",
    )
    delivery_policy_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_parser = audits_subparsers.add_parser(
        "providers",
        help="Inspect registered governance audit delivery providers.",
        description=(
            "Inspect the delivery providers registered for each "
            "governance audit notification channel type.\n\n"
            "Providers are registered automatically at runtime "
            "construction; this command is read-only.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    providers_subparsers = providers_parser.add_subparsers(
        dest="providers_command", required=True
    )

    providers_list_parser = providers_subparsers.add_parser(
        "list",
        help="List every registered delivery provider.",
    )
    providers_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_show_parser = providers_subparsers.add_parser(
        "show",
        help="Show the provider registered for one channel type.",
    )
    providers_show_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to show the registered provider for.",
    )
    providers_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_capabilities_parser = providers_subparsers.add_parser(
        "capabilities",
        help="Show the capabilities of the provider for one channel type.",
    )
    providers_capabilities_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to show the provider's capabilities for.",
    )
    providers_capabilities_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_validate_parser = providers_subparsers.add_parser(
        "validate",
        help=(
            "Validate configured delivery policies against a channel "
            "type's provider capabilities."
        ),
    )
    providers_validate_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to validate configured delivery policies for.",
    )
    providers_validate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_health_parser = providers_subparsers.add_parser(
        "health",
        help="Check the health of the provider for one channel type.",
    )
    providers_health_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to check the registered provider's health for.",
    )
    providers_health_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_health_all_parser = providers_subparsers.add_parser(
        "health-all",
        help="Check the health of every registered provider.",
    )
    providers_health_all_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_enable_parser = providers_subparsers.add_parser(
        "enable",
        help="Enable the provider registered for one channel type.",
    )
    providers_enable_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to enable the registered provider for.",
    )
    providers_enable_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_disable_parser = providers_subparsers.add_parser(
        "disable",
        help="Disable the provider registered for one channel type.",
    )
    providers_disable_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to disable the registered provider for.",
    )
    providers_disable_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_replace_parser = providers_subparsers.add_parser(
        "replace",
        help=(
            "Replace the provider for one channel type with a fresh "
            "instance of the same provider class (a reload)."
        ),
    )
    providers_replace_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to replace the registered provider for.",
    )
    providers_replace_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    providers_metadata_parser = providers_subparsers.add_parser(
        "metadata",
        help="Show lifecycle metadata for one channel type's provider.",
    )
    providers_metadata_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to show the registered provider's metadata for.",
    )
    providers_metadata_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    config_parser = providers_subparsers.add_parser(
        "config",
        help="Manage typed runtime settings for governance audit providers.",
        description=(
            "Create and manage typed runtime settings for governance "
            "audit delivery providers, without modifying the "
            "provider implementation itself.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True
    )

    config_create_parser = config_subparsers.add_parser(
        "create",
        help="Create a new provider configuration.",
    )
    config_create_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to create the configuration for.",
    )
    config_create_parser.add_argument(
        "--set",
        action="append",
        dest="values",
        default=None,
        metavar="KEY=VALUE",
        help="Configuration key=value pair. Repeatable.",
    )
    config_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    config_list_parser = config_subparsers.add_parser(
        "list",
        help="List every stored provider configuration.",
    )
    config_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    config_show_parser = config_subparsers.add_parser(
        "show",
        help="Show one stored provider configuration.",
    )
    config_show_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to show the configuration for.",
    )
    config_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    config_update_parser = config_subparsers.add_parser(
        "update",
        help="Replace an existing provider configuration's values.",
    )
    config_update_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to update the configuration for.",
    )
    config_update_parser.add_argument(
        "--set",
        action="append",
        dest="values",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "Configuration key=value pair. Repeatable. Replaces the "
            "complete set of stored values."
        ),
    )
    config_update_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    config_delete_parser = config_subparsers.add_parser(
        "delete",
        help="Delete one stored provider configuration.",
    )
    config_delete_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to delete the configuration for.",
    )
    config_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    secrets_parser = providers_subparsers.add_parser(
        "secrets",
        help="Manage sensitive credentials for governance audit providers.",
        description=(
            "Create and manage sensitive credentials for governance "
            "audit delivery providers, stored separately from their "
            "typed configuration.\n\n"
            "This is local, unencrypted storage: a production "
            "deployment would need envelope encryption or an "
            "external secrets manager.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    secrets_subparsers = secrets_parser.add_subparsers(
        dest="secrets_command", required=True
    )

    secrets_create_parser = secrets_subparsers.add_parser(
        "create",
        help="Create a new provider secret set.",
    )
    secrets_create_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to create the secret set for.",
    )
    secrets_create_parser.add_argument(
        "--set",
        action="append",
        dest="values",
        default=None,
        metavar="KEY=VALUE",
        help="Secret key=value pair. Repeatable.",
    )
    secrets_create_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    secrets_list_parser = secrets_subparsers.add_parser(
        "list",
        help="List every stored provider secret set.",
    )
    secrets_list_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    secrets_show_parser = secrets_subparsers.add_parser(
        "show",
        help="Show one stored provider secret set.",
    )
    secrets_show_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to show the secret set for.",
    )
    secrets_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    secrets_update_parser = secrets_subparsers.add_parser(
        "update",
        help="Replace an existing provider secret set's values.",
    )
    secrets_update_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to update the secret set for.",
    )
    secrets_update_parser.add_argument(
        "--set",
        action="append",
        dest="values",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "Secret key=value pair. Repeatable. Replaces the "
            "complete set of stored values."
        ),
    )
    secrets_update_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    secrets_delete_parser = secrets_subparsers.add_parser(
        "delete",
        help="Delete one stored provider secret set.",
    )
    secrets_delete_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to delete the secret set for.",
    )
    secrets_delete_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    auth_parser = providers_subparsers.add_parser(
        "auth",
        help="Build and inspect governance audit provider authentication.",
        description=(
            "Build the provider-ready authentication context for a "
            "channel type from its resolved configuration and "
            "secrets.\n\n"
            "Secret values are never printed: header and parameter "
            "values are always redacted.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    auth_subparsers = auth_parser.add_subparsers(
        dest="auth_command", required=True
    )

    auth_show_parser = auth_subparsers.add_parser(
        "show",
        help="Show the redacted authentication context for one channel type.",
    )
    auth_show_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to show the authentication context for.",
    )
    auth_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    auth_validate_parser = auth_subparsers.add_parser(
        "validate",
        help="Validate that authentication can be built for one channel type.",
    )
    auth_validate_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to validate authentication for.",
    )
    auth_validate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    request_parser = providers_subparsers.add_parser(
        "request",
        help="Build and inspect governance audit provider requests.",
        description=(
            "Build the provider-ready request for delivering one "
            "notification through one channel type: resolves "
            "configuration, authentication, and delivery policy, "
            "then delegates the request shape to the provider.\n\n"
            "Header values are always redacted, since they may "
            "contain authentication credentials.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    request_subparsers = request_parser.add_subparsers(
        dest="request_command", required=True
    )

    request_show_parser = request_subparsers.add_parser(
        "show",
        help="Show the redacted request built for one notification and channel type.",
    )
    request_show_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to build the request for.",
    )
    request_show_parser.add_argument(
        "--notification-id",
        required=True,
        dest="notification_id",
        help="Identifier of the notification to build the request for.",
    )
    request_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    request_validate_parser = request_subparsers.add_parser(
        "validate",
        help="Validate that a request can be built for one notification and channel type.",
    )
    request_validate_parser.add_argument(
        "--channel-type",
        required=True,
        dest="channel_type",
        choices=[
            channel_type.value
            for channel_type in GovernanceIntegrityNotificationChannelType
        ],
        help="Channel type to validate the request for.",
    )
    request_validate_parser.add_argument(
        "--notification-id",
        required=True,
        dest="notification_id",
        help="Identifier of the notification to validate the request for.",
    )
    request_validate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    response_parser = providers_subparsers.add_parser(
        "response",
        help="Deliver and normalize governance audit provider responses.",
        description=(
            "Deliver the request built for one queued dispatch and "
            "normalize the provider's raw response into a common "
            "delivery outcome.\n\n"
            "This does not persist anything: it is a read-only "
            "inspection of what delivering this dispatch right now "
            "would produce.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    response_subparsers = response_parser.add_subparsers(
        dest="response_command", required=True
    )

    response_show_parser = response_subparsers.add_parser(
        "show",
        help="Show the raw response and normalized outcome for one dispatch.",
    )
    response_show_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch to deliver and normalize.",
    )
    response_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    response_validate_parser = response_subparsers.add_parser(
        "validate",
        help="Validate that a response can be delivered and normalized for one dispatch.",
    )
    response_validate_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch to validate.",
    )
    response_validate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    retries_parser = audits_subparsers.add_parser(
        "retries",
        help="Evaluate governance audit delivery retry decisions.",
        description=(
            "Deliver one dispatch and evaluate whether its failure "
            "should be retried, independent of the delivery engine "
            "itself.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    retries_subparsers = retries_parser.add_subparsers(
        dest="retries_command", required=True
    )

    retries_evaluate_parser = retries_subparsers.add_parser(
        "evaluate",
        help="Evaluate the retry decision for one dispatch at a given attempt.",
    )
    retries_evaluate_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch to evaluate.",
    )
    retries_evaluate_parser.add_argument(
        "--attempt",
        required=True,
        type=int,
        dest="attempt",
        help="Zero-based number of attempts already made.",
    )
    retries_evaluate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    retries_preview_parser = retries_subparsers.add_parser(
        "preview",
        help="Preview the retry decision for one dispatch at its first attempt.",
    )
    retries_preview_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch to preview.",
    )
    retries_preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    scheduler_parser = audits_subparsers.add_parser(
        "scheduler",
        help="Manage the governance audit delivery scheduler's queue.",
        description=(
            "Manage immediate, delayed, and retry dispatches through "
            "the unified delivery scheduler queue.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scheduler_subparsers = scheduler_parser.add_subparsers(
        dest="scheduler_command", required=True
    )

    scheduler_pending_parser = scheduler_subparsers.add_parser(
        "pending",
        help="List every pending scheduled dispatch.",
    )
    scheduler_pending_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    scheduler_ready_parser = scheduler_subparsers.add_parser(
        "ready",
        help="List every scheduled dispatch ready to run right now.",
    )
    scheduler_ready_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    scheduler_show_parser = scheduler_subparsers.add_parser(
        "show",
        help="Show one scheduled dispatch.",
    )
    scheduler_show_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch to show.",
    )
    scheduler_show_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    scheduler_cancel_parser = scheduler_subparsers.add_parser(
        "cancel",
        help="Cancel one scheduled dispatch.",
    )
    scheduler_cancel_parser.add_argument(
        "--dispatch-id",
        required=True,
        dest="dispatch_id",
        help="Identifier of the dispatch to cancel.",
    )
    scheduler_cancel_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_worker_parser = audits_subparsers.add_parser(
        "delivery-worker",
        help="Run the governance audit delivery worker.",
        description=(
            "Continuously consume the delivery scheduler's ready "
            "queue, executing each dispatch through the delivery "
            "engine and coordinating retries.\n\n"
            "Exit codes: 0 the operation succeeded, 2 the operation "
            "could not be completed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    delivery_worker_subparsers = delivery_worker_parser.add_subparsers(
        dest="delivery_worker_command", required=True
    )

    delivery_worker_run_parser = delivery_worker_subparsers.add_parser(
        "run",
        help="Run a single pass over the ready dispatch queue.",
    )
    delivery_worker_run_parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Explicit no-op flag: a single pass is always run. "
            "Accepted for compatibility with the documented CLI "
            "shape."
        ),
    )
    delivery_worker_run_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    delivery_worker_summary_parser = (
        delivery_worker_subparsers.add_parser(
            "summary",
            help="Show the most recent delivery worker run summary.",
        )
    )
    delivery_worker_summary_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    check_parser = governance_subparsers.add_parser(
        "check",
        help="Execute and enforce a governance integrity policy gate.",
        description=(
            "Execute a fresh deep integrity audit, persist it, and "
            "compare it against the immediately preceding recorded audit "
            "to enforce a governance policy.\n\n"
            "Unlike `governance audits --regression` (read-only inspection "
            "of existing history), this command always executes and "
            "records a brand-new audit.\n\n"
            "Exit codes: 0 policy passed, 2 check could not be executed, "
            "3 policy failed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check_parser.add_argument(
        "--policy",
        choices=["regression-only", "require-healthy"],
        default="regression-only",
        help=(
            "regression-only (default) fails only when the latest audit "
            "newly degraded from a healthy baseline. require-healthy "
            "fails whenever the latest audit is unhealthy, even if the "
            "failure is not new."
        ),
    )
    check_parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        dest="batch_size",
        help="Number of persisted records read per integrity-audit batch. Default: 500.",
    )
    check_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output.",
    )

    # completion command -- print a shell completion script for `parser`
    # itself, walked fresh off its own real subparsers tree (see
    # _completion_command_tree) rather than a hand-maintained list, so it
    # can never drift out of date the way a hardcoded one inevitably
    # would across ~70 top-level subcommands (and several more levels of
    # nesting under versions/tags/app-tasks/governance/audits/logs
    # alone) with no shell completion of any kind before this.
    completion_parser = subparsers.add_parser(
        "completion",
        help="Print a shell completion script for this CLI's own commands."
    )
    completion_parser.add_argument(
        "shell",
        choices=["bash", "zsh", "fish"],
        help="Which shell to generate a completion script for.",
    )

    args = parser.parse_args()

    if args.command == "completion":
        if args.shell == "bash":
            print(_generate_bash_completion(parser), end="")
        elif args.shell == "zsh":
            print(_generate_zsh_completion(parser), end="")
        else:
            print(_generate_fish_completion(parser), end="")
        return

    if args.command in _CORE_COMMANDS:
        try:
            _dispatch_core_command(args)
        except CLI_USER_FACING_ERRORS as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "governance":
        if args.governance_command == "doctor":
            if args.batch_size <= 0:
                parser.error("--batch-size must be greater than zero")
            exit_code = run_deployment_governance_doctor(
                deep=args.deep,
                json_output=args.json_output,
                integrity_audit_batch_size=args.batch_size,
            )
            sys.exit(exit_code)
        elif args.governance_command == "audits":
            if getattr(args, "audits_command", None) == "prune":
                if (
                    args.max_records is None
                    and args.max_age_days is None
                ):
                    parser.error(
                        "at least one of --max-records or "
                        "--max-age-days must be supplied"
                    )
                exit_code = run_deployment_governance_audit_prune(
                    max_records=args.max_records,
                    max_age_days=args.max_age_days,
                    preserve_latest=args.preserve_latest,
                    apply=args.apply,
                    json_output=args.json_output,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "export":
                exit_code = run_deployment_governance_audit_export(
                    output_path=args.output,
                    limit=args.limit,
                    include_trend=args.include_trend,
                    include_regression=args.include_regression,
                    trend_window=args.trend_window,
                    create_manifest=args.create_manifest,
                    pretty=not args.compact,
                    force=args.force,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "verify":
                exit_code = run_deployment_governance_audit_verify(
                    evidence_path=args.evidence,
                    manifest_path=args.manifest,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "stats":
                exit_code = run_deployment_governance_audit_stats(
                    limit=args.limit,
                    json_output=args.json_output,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "replay":
                exit_code = run_deployment_governance_audit_replay(
                    audit_id=args.audit_id,
                    limit=args.limit,
                    json_output=args.json_output,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "diff":
                if (
                    args.previous_audit_id is None
                ) != (
                    args.current_audit_id is None
                ):
                    parser.error(
                        "--previous and --current must be supplied "
                        "together"
                    )
                exit_code = run_deployment_governance_audit_diff(
                    previous_audit_id=args.previous_audit_id,
                    current_audit_id=args.current_audit_id,
                    json_output=args.json_output,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "timeline":
                exit_code = run_deployment_governance_audit_timeline(
                    limit=args.limit,
                    json_output=args.json_output,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "logs":
                if args.logs_command == "tail":
                    exit_code = run_deployment_governance_logging_tail(
                        level=args.level,
                        limit=args.limit,
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "list":
                    exit_code = run_deployment_governance_logging_list(
                        level=args.level,
                        component=args.component,
                        limit=args.limit,
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "clear":
                    exit_code = run_deployment_governance_logging_clear(
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "rotate":
                    exit_code = run_deployment_governance_logging_rotate(
                        max_entries=args.max_entries,
                        max_age=args.max_age,
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "rotation":
                    exit_code = (
                        run_deployment_governance_logging_rotation_status(
                            max_entries=args.max_entries,
                            max_age=args.max_age,
                            json_output=args.json_output,
                        )
                    )
                    sys.exit(exit_code)
                if args.logs_command == "search":
                    try:
                        since = parse_governance_audit_timestamp(
                            args.since
                        )
                        until = parse_governance_audit_timestamp(
                            args.until
                        )
                    except ValueError as exc:
                        parser.error(str(exc))
                        return
                    exit_code = run_deployment_governance_logging_search(
                        level=args.level,
                        component=args.component,
                        event=args.event,
                        since=since,
                        until=until,
                        limit=args.limit,
                        offset=args.offset,
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command in (
                    "export-json",
                    "export-csv",
                    "export-ndjson",
                ):
                    try:
                        since = parse_governance_audit_timestamp(
                            args.since
                        )
                        until = parse_governance_audit_timestamp(
                            args.until
                        )
                    except ValueError as exc:
                        parser.error(str(exc))
                        return
                    export_runner = {
                        "export-json": (
                            run_deployment_governance_logging_export_json
                        ),
                        "export-csv": (
                            run_deployment_governance_logging_export_csv
                        ),
                        "export-ndjson": (
                            run_deployment_governance_logging_export_ndjson
                        ),
                    }[args.logs_command]
                    exit_code = export_runner(
                        output_path=args.output_path,
                        level=args.level,
                        component=args.component,
                        since=since,
                        until=until,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "redaction":
                    if args.redaction_command == "rules":
                        exit_code = (
                            run_deployment_governance_logging_redaction_rules(
                                json_output=args.json_output,
                            )
                        )
                        sys.exit(exit_code)
                    if args.redaction_command == "test":
                        exit_code = (
                            run_deployment_governance_logging_redaction_test(
                                json_output=args.json_output,
                            )
                        )
                        sys.exit(exit_code)
                if args.logs_command == "context":
                    exit_code = run_deployment_governance_logging_context(
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "trace":
                    exit_code = run_deployment_governance_logging_trace(
                        correlation_id=args.correlation_id,
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "sampling":
                    if args.sampling_command == "show":
                        exit_code = (
                            run_deployment_governance_logging_sampling_show(
                                json_output=args.json_output,
                            )
                        )
                        sys.exit(exit_code)
                    if args.sampling_command == "update":
                        exit_code = (
                            run_deployment_governance_logging_sampling_update(
                                default_rate=args.default_rate,
                                level=args.level,
                                rate=args.rate,
                                json_output=args.json_output,
                            )
                        )
                        sys.exit(exit_code)
                if args.logs_command == "flush":
                    exit_code = run_deployment_governance_logging_flush(
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "pending":
                    exit_code = run_deployment_governance_logging_pending(
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command in ("replay", "replay-next"):
                    try:
                        since = parse_governance_audit_timestamp(
                            args.since
                        )
                    except ValueError as exc:
                        parser.error(str(exc))
                        return
                    replay_runner = (
                        run_deployment_governance_logging_replay
                        if args.logs_command == "replay"
                        else run_deployment_governance_logging_replay_next
                    )
                    exit_code = replay_runner(
                        since=since,
                        event=args.event,
                        limit=args.limit,
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
                if args.logs_command == "config":
                    if args.logs_config_command == "show":
                        exit_code = (
                            run_deployment_governance_logging_config_show(
                                json_output=args.json_output,
                            )
                        )
                        sys.exit(exit_code)
                    if args.logs_config_command == "reload":
                        exit_code = (
                            run_deployment_governance_logging_config_reload(
                                json_output=args.json_output,
                            )
                        )
                        sys.exit(exit_code)
                if args.logs_command == "bootstrap":
                    exit_code = (
                        run_deployment_governance_logging_bootstrap(
                            json_output=args.json_output,
                        )
                    )
                    sys.exit(exit_code)
                if args.logs_command == "health":
                    exit_code = run_deployment_governance_logging_health(
                        json_output=args.json_output,
                    )
                    sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "session":
                exit_code = run_deployment_governance_audit_session(
                    limit=args.limit,
                    json_output=args.json_output,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "bookmark":
                if args.bookmark_command == "add":
                    exit_code = run_deployment_governance_audit_bookmark_add(
                        name=args.name,
                        audit_id=args.audit_id,
                        use_latest=args.latest,
                        json_output=args.json_output,
                    )
                elif args.bookmark_command == "list":
                    exit_code = run_deployment_governance_audit_bookmark_list(
                        json_output=args.json_output,
                    )
                elif args.bookmark_command == "show":
                    exit_code = run_deployment_governance_audit_bookmark_show(
                        name=args.name,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_bookmark_delete(
                        name=args.name,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "labels":
                if args.labels_command == "add":
                    exit_code = run_deployment_governance_audit_label_add(
                        audit_id=args.audit_id,
                        label=args.label,
                        json_output=args.json_output,
                    )
                elif args.labels_command == "remove":
                    exit_code = run_deployment_governance_audit_label_remove(
                        audit_id=args.audit_id,
                        label=args.label,
                        json_output=args.json_output,
                    )
                elif args.labels_command == "show":
                    exit_code = run_deployment_governance_audit_label_show(
                        audit_id=args.audit_id,
                        json_output=args.json_output,
                    )
                elif args.labels_command == "search":
                    exit_code = run_deployment_governance_audit_label_search(
                        label=args.label,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_label_list(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "search":
                if args.healthy and args.unhealthy:
                    parser.error(
                        "--healthy and --unhealthy are mutually "
                        "exclusive"
                    )
                healthy = (
                    True
                    if args.healthy
                    else (False if args.unhealthy else None)
                )
                exit_code = run_deployment_governance_audit_search(
                    audit_id=args.audit_id,
                    healthy=healthy,
                    label=args.label,
                    bookmark=args.bookmark,
                    json_output=args.json_output,
                )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "query":
                if args.query_command == "save":
                    if args.healthy and args.unhealthy:
                        parser.error(
                            "--healthy and --unhealthy are mutually "
                            "exclusive"
                        )
                    healthy = (
                        True
                        if args.healthy
                        else (False if args.unhealthy else None)
                    )
                    exit_code = run_deployment_governance_audit_saved_query_save(
                        name=args.name,
                        audit_id=args.audit_id,
                        healthy=healthy,
                        label=args.label,
                        bookmark=args.bookmark,
                        json_output=args.json_output,
                    )
                elif args.query_command == "run":
                    exit_code = run_deployment_governance_audit_saved_query_run(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.query_command == "list":
                    exit_code = run_deployment_governance_audit_saved_query_list(
                        json_output=args.json_output,
                    )
                elif args.query_command == "show":
                    exit_code = run_deployment_governance_audit_saved_query_show(
                        name=args.name,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_saved_query_delete(
                        name=args.name,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "collections":
                if args.collections_command == "create":
                    exit_code = run_deployment_governance_audit_collection_create(
                        name=args.name,
                        description=args.description,
                        json_output=args.json_output,
                    )
                elif args.collections_command == "list":
                    exit_code = run_deployment_governance_audit_collection_list(
                        json_output=args.json_output,
                    )
                elif args.collections_command == "show":
                    exit_code = run_deployment_governance_audit_collection_show(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.collections_command == "delete":
                    exit_code = run_deployment_governance_audit_collection_delete(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.collections_command == "add":
                    exit_code = run_deployment_governance_audit_collection_add(
                        name=args.name,
                        audit_id=args.audit_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_collection_remove(
                        name=args.name,
                        audit_id=args.audit_id,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "report":
                if args.report_command == "collection":
                    exit_code = run_deployment_governance_audit_report_collection(
                        collection=args.collection,
                        title=args.title,
                        output_path=args.output,
                        report_format=args.report_format,
                    )
                else:
                    if not args.audit_ids:
                        parser.error(
                            "at least one --audit-id is required"
                        )
                    exit_code = run_deployment_governance_audit_report_audits(
                        title=args.title,
                        audit_ids=args.audit_ids,
                        output_path=args.output,
                        report_format=args.report_format,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "templates":
                if args.templates_command == "create":
                    if (args.collection is None) == (
                        args.saved_query is None
                    ):
                        parser.error(
                            "exactly one of --collection or "
                            "--saved-query must be supplied"
                        )
                    if args.collection is not None:
                        source = (
                            GovernanceIntegrityAuditReportSource.COLLECTION
                        )
                        source_name = args.collection
                    else:
                        source = (
                            GovernanceIntegrityAuditReportSource.SAVED_QUERY
                        )
                        source_name = args.saved_query
                    exit_code = run_deployment_governance_audit_report_template_create(
                        name=args.name,
                        title=args.title,
                        source=source,
                        source_name=source_name,
                        output_format=args.output_format,
                        json_output=args.json_output,
                    )
                elif args.templates_command == "list":
                    exit_code = run_deployment_governance_audit_report_template_list(
                        json_output=args.json_output,
                    )
                elif args.templates_command == "show":
                    exit_code = run_deployment_governance_audit_report_template_show(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.templates_command == "delete":
                    exit_code = run_deployment_governance_audit_report_template_delete(
                        name=args.name,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_report_template_generate(
                        name=args.name,
                        output_path=args.output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "schedules":
                if args.schedules_command == "create":
                    exit_code = run_deployment_governance_audit_report_schedule_create(
                        name=args.name,
                        template_name=args.template,
                        frequency=GovernanceIntegrityReportScheduleFrequency(
                            args.frequency
                        ),
                        json_output=args.json_output,
                    )
                elif args.schedules_command == "list":
                    exit_code = run_deployment_governance_audit_report_schedule_list(
                        json_output=args.json_output,
                    )
                elif args.schedules_command == "show":
                    exit_code = run_deployment_governance_audit_report_schedule_show(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.schedules_command == "enable":
                    exit_code = run_deployment_governance_audit_report_schedule_enable(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.schedules_command == "disable":
                    exit_code = run_deployment_governance_audit_report_schedule_disable(
                        name=args.name,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_report_schedule_delete(
                        name=args.name,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "queue":
                if args.queue_command == "enqueue":
                    exit_code = run_deployment_governance_audit_queue_enqueue(
                        schedule_name=args.schedule,
                        json_output=args.json_output,
                    )
                elif args.queue_command == "enqueue-due":
                    exit_code = run_deployment_governance_audit_queue_enqueue_due(
                        json_output=args.json_output,
                    )
                elif args.queue_command == "list":
                    exit_code = run_deployment_governance_audit_queue_list(
                        json_output=args.json_output,
                    )
                elif args.queue_command == "show":
                    exit_code = run_deployment_governance_audit_queue_show(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                elif args.queue_command == "delete":
                    exit_code = run_deployment_governance_audit_queue_delete(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_queue_clear(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "worker":
                if args.worker_command == "run":
                    exit_code = run_deployment_governance_audit_worker_run(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                elif args.worker_command == "run-all":
                    exit_code = run_deployment_governance_audit_worker_run_all(
                        json_output=args.json_output,
                    )
                elif args.worker_command == "history":
                    exit_code = run_deployment_governance_audit_worker_history(
                        json_output=args.json_output,
                    )
                elif args.worker_command == "show":
                    exit_code = run_deployment_governance_audit_worker_show(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_worker_clear(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "retry":
                if args.retry_command == "run":
                    exit_code = run_deployment_governance_audit_retry_run(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                elif args.retry_command == "history":
                    exit_code = run_deployment_governance_audit_retry_history(
                        json_output=args.json_output,
                    )
                elif args.retry_command == "show":
                    exit_code = run_deployment_governance_audit_retry_show(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_audit_retry_clear(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "dlq":
                if args.dlq_command == "archive":
                    exit_code = run_deployment_governance_dead_letter_archive(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                elif args.dlq_command == "list":
                    exit_code = run_deployment_governance_dead_letter_list(
                        json_output=args.json_output,
                    )
                elif args.dlq_command == "show":
                    exit_code = run_deployment_governance_dead_letter_show(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                elif args.dlq_command == "delete":
                    exit_code = run_deployment_governance_dead_letter_delete(
                        job_id=args.job_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_dead_letter_clear(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "policy":
                if args.policy_command == "create":
                    exit_code = run_deployment_governance_failure_policy_create(
                        name=args.name,
                        action=args.action,
                        max_retry_attempts=args.max_retry_attempts,
                        json_output=args.json_output,
                    )
                elif args.policy_command == "list":
                    exit_code = run_deployment_governance_failure_policy_list(
                        json_output=args.json_output,
                    )
                elif args.policy_command == "show":
                    exit_code = run_deployment_governance_failure_policy_show(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.policy_command == "update":
                    exit_code = run_deployment_governance_failure_policy_update(
                        name=args.name,
                        action=args.action,
                        max_retry_attempts=args.max_retry_attempts,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_failure_policy_delete(
                        name=args.name,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "metrics":
                if getattr(args, "metrics_command", None) == "template":
                    exit_code = (
                        run_deployment_governance_execution_metrics_for_template(
                            template_name=args.template,
                            json_output=args.json_output,
                        )
                    )
                else:
                    exit_code = run_deployment_governance_execution_metrics(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "alerts":
                if getattr(args, "alerts_command", None) == "template":
                    exit_code = (
                        run_deployment_governance_execution_alerts_for_template(
                            template_name=args.template,
                            minimum_success_rate=args.minimum_success_rate,
                            maximum_failure_rate=args.maximum_failure_rate,
                            maximum_average_duration_ms=(
                                args.maximum_average_duration_ms
                            ),
                            json_output=args.json_output,
                        )
                    )
                else:
                    exit_code = run_deployment_governance_execution_alerts(
                        minimum_success_rate=args.minimum_success_rate,
                        maximum_failure_rate=args.maximum_failure_rate,
                        maximum_average_duration_ms=(
                            args.maximum_average_duration_ms
                        ),
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "notifications":
                if args.notifications_command == "queue":
                    exit_code = run_deployment_governance_notifications_queue(
                        minimum_success_rate=args.minimum_success_rate,
                        maximum_failure_rate=args.maximum_failure_rate,
                        maximum_average_duration_ms=(
                            args.maximum_average_duration_ms
                        ),
                        json_output=args.json_output,
                    )
                elif args.notifications_command == "list":
                    exit_code = run_deployment_governance_notifications_list(
                        json_output=args.json_output,
                    )
                elif args.notifications_command == "show":
                    exit_code = run_deployment_governance_notifications_show(
                        notification_id=args.notification_id,
                        json_output=args.json_output,
                    )
                elif args.notifications_command == "delete":
                    exit_code = run_deployment_governance_notifications_delete(
                        notification_id=args.notification_id,
                        json_output=args.json_output,
                    )
                elif args.notifications_command == "metrics":
                    metrics_subcommand = getattr(
                        args, "notifications_metrics_command", None
                    )

                    if metrics_subcommand == "reset":
                        exit_code = run_deployment_governance_metrics_reset(
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "export":
                        exit_code = run_deployment_governance_metrics_export(
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "reload":
                        exit_code = run_deployment_governance_metrics_reload(
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "history":
                        exit_code = run_deployment_governance_metrics_history(
                            limit=args.limit,
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "latest":
                        exit_code = run_deployment_governance_metrics_latest(
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "export-json":
                        exit_code = run_deployment_governance_metrics_export_json(
                            include_history=args.include_history,
                            output_path=args.output_path,
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "export-csv":
                        exit_code = run_deployment_governance_metrics_export_csv(
                            include_history=args.include_history,
                            output_path=args.output_path,
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "aggregate":
                        exit_code = run_deployment_governance_metrics_aggregate(
                            start=args.range_start,
                            end=args.range_end,
                            hourly=args.hourly,
                            daily=args.daily,
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "alerts":
                        if (
                            getattr(
                                args,
                                "notifications_metrics_alerts_command",
                                None,
                            )
                            == "clear"
                        ):
                            exit_code = run_deployment_governance_metrics_alerts_clear(
                                json_output=args.json_output,
                            )
                        else:
                            exit_code = run_deployment_governance_metrics_alerts(
                                json_output=args.json_output,
                            )
                    elif metrics_subcommand == "dashboard":
                        exit_code = run_deployment_governance_metrics_dashboard(
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "requests":
                        exit_code = run_deployment_governance_metrics_requests(
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "collector":
                        if (
                            args.notifications_metrics_collector_command
                            == "collect"
                        ):
                            exit_code = run_deployment_governance_metrics_collector_collect(
                                json_output=args.json_output,
                            )
                        else:
                            exit_code = run_deployment_governance_metrics_collector_status(
                                json_output=args.json_output,
                            )
                    elif metrics_subcommand == "retention":
                        if (
                            args.notifications_metrics_retention_command
                            == "run"
                        ):
                            exit_code = run_deployment_governance_metrics_retention_run(
                                json_output=args.json_output,
                            )
                        else:
                            exit_code = run_deployment_governance_metrics_retention_status(
                                json_output=args.json_output,
                            )
                    elif metrics_subcommand == "config":
                        if (
                            args.notifications_metrics_config_command
                            == "reload"
                        ):
                            exit_code = run_deployment_governance_metrics_config_reload(
                                json_output=args.json_output,
                            )
                        else:
                            exit_code = run_deployment_governance_metrics_config_show(
                                json_output=args.json_output,
                            )
                    elif metrics_subcommand == "health":
                        exit_code = run_deployment_governance_metrics_health(
                            json_output=args.json_output,
                        )
                    elif metrics_subcommand == "bootstrap":
                        exit_code = run_deployment_governance_metrics_bootstrap(
                            json_output=args.json_output,
                        )
                    else:
                        exit_code = run_deployment_governance_metrics(
                            json_output=args.json_output,
                        )
                else:
                    exit_code = run_deployment_governance_notifications_clear(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "channels":
                if args.channels_command == "create":
                    exit_code = run_deployment_governance_notification_channel_create(
                        name=args.name,
                        channel_type=args.channel_type,
                        destination=args.destination,
                        json_output=args.json_output,
                    )
                elif args.channels_command == "list":
                    exit_code = run_deployment_governance_notification_channel_list(
                        json_output=args.json_output,
                    )
                elif args.channels_command == "show":
                    exit_code = run_deployment_governance_notification_channel_show(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.channels_command == "enable":
                    exit_code = run_deployment_governance_notification_channel_enable(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.channels_command == "disable":
                    exit_code = run_deployment_governance_notification_channel_disable(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.channels_command == "update":
                    exit_code = run_deployment_governance_notification_channel_update(
                        name=args.name,
                        destination=args.destination,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_notification_channel_delete(
                        name=args.name,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "dispatch":
                if args.dispatch_command == "run":
                    exit_code = run_deployment_governance_notification_dispatch_run(
                        json_output=args.json_output,
                    )
                elif args.dispatch_command == "list":
                    exit_code = run_deployment_governance_notification_dispatch_list(
                        json_output=args.json_output,
                    )
                elif args.dispatch_command == "show":
                    exit_code = run_deployment_governance_notification_dispatch_show(
                        dispatch_id=args.dispatch_id,
                        json_output=args.json_output,
                    )
                elif args.dispatch_command == "delete":
                    exit_code = run_deployment_governance_notification_dispatch_delete(
                        dispatch_id=args.dispatch_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_notification_dispatch_clear(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "deliver":
                if args.deliver_command == "run":
                    exit_code = run_deployment_governance_delivery_run(
                        dispatch_id=args.dispatch_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_delivery_run_all(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if (
                getattr(args, "audits_command", None)
                == "delivery-history"
            ):
                if args.delivery_history_command == "list":
                    exit_code = run_deployment_governance_delivery_history_list(
                        json_output=args.json_output,
                    )
                elif args.delivery_history_command == "show":
                    exit_code = run_deployment_governance_delivery_history_show(
                        delivery_id=args.delivery_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_delivery_history_clear(
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "preferences":
                if args.preferences_command == "create":
                    exit_code = run_deployment_governance_notification_preference_create(
                        name=args.name,
                        minimum_severity=args.minimum_severity,
                        channels=args.channels,
                        json_output=args.json_output,
                    )
                elif args.preferences_command == "list":
                    exit_code = run_deployment_governance_notification_preference_list(
                        json_output=args.json_output,
                    )
                elif args.preferences_command == "show":
                    exit_code = run_deployment_governance_notification_preference_show(
                        name=args.name,
                        json_output=args.json_output,
                    )
                elif args.preferences_command == "update":
                    exit_code = run_deployment_governance_notification_preference_update(
                        name=args.name,
                        minimum_severity=args.minimum_severity,
                        channels=args.channels,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_notification_preference_delete(
                        name=args.name,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if (
                getattr(args, "audits_command", None)
                == "delivery-policy"
            ):
                if args.delivery_policy_command == "create":
                    exit_code = run_deployment_governance_delivery_policy_create(
                        channel_name=args.channel_name,
                        retry_limit=args.retry_limit,
                        timeout_seconds=args.timeout_seconds,
                        rate_limit_per_minute=args.rate_limit_per_minute,
                        json_output=args.json_output,
                    )
                elif args.delivery_policy_command == "list":
                    exit_code = run_deployment_governance_delivery_policy_list(
                        json_output=args.json_output,
                    )
                elif args.delivery_policy_command == "show":
                    exit_code = run_deployment_governance_delivery_policy_show(
                        channel_name=args.channel_name,
                        json_output=args.json_output,
                    )
                elif args.delivery_policy_command == "update":
                    exit_code = run_deployment_governance_delivery_policy_update(
                        channel_name=args.channel_name,
                        retry_limit=args.retry_limit,
                        timeout_seconds=args.timeout_seconds,
                        rate_limit_per_minute=args.rate_limit_per_minute,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_delivery_policy_delete(
                        channel_name=args.channel_name,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "providers":
                if args.providers_command == "list":
                    exit_code = run_deployment_governance_provider_list(
                        json_output=args.json_output,
                    )
                elif args.providers_command == "show":
                    exit_code = run_deployment_governance_provider_show(
                        channel_type=args.channel_type,
                        json_output=args.json_output,
                    )
                elif args.providers_command == "capabilities":
                    exit_code = (
                        run_deployment_governance_provider_capabilities(
                            channel_type=args.channel_type,
                            json_output=args.json_output,
                        )
                    )
                elif args.providers_command == "validate":
                    exit_code = (
                        run_deployment_governance_provider_validate(
                            channel_type=args.channel_type,
                            json_output=args.json_output,
                        )
                    )
                elif args.providers_command == "health":
                    exit_code = run_deployment_governance_provider_health(
                        channel_type=args.channel_type,
                        json_output=args.json_output,
                    )
                elif args.providers_command == "health-all":
                    exit_code = (
                        run_deployment_governance_provider_health_all(
                            json_output=args.json_output,
                        )
                    )
                elif args.providers_command == "enable":
                    exit_code = run_deployment_governance_provider_enable(
                        channel_type=args.channel_type,
                        json_output=args.json_output,
                    )
                elif args.providers_command == "disable":
                    exit_code = run_deployment_governance_provider_disable(
                        channel_type=args.channel_type,
                        json_output=args.json_output,
                    )
                elif args.providers_command == "replace":
                    exit_code = run_deployment_governance_provider_replace(
                        channel_type=args.channel_type,
                        json_output=args.json_output,
                    )
                elif args.providers_command == "metadata":
                    exit_code = run_deployment_governance_provider_metadata(
                        channel_type=args.channel_type,
                        json_output=args.json_output,
                    )
                elif args.providers_command == "config":
                    if args.config_command == "create":
                        exit_code = (
                            run_deployment_governance_provider_config_create(
                                channel_type=args.channel_type,
                                values=args.values,
                                json_output=args.json_output,
                            )
                        )
                    elif args.config_command == "list":
                        exit_code = (
                            run_deployment_governance_provider_config_list(
                                json_output=args.json_output,
                            )
                        )
                    elif args.config_command == "show":
                        exit_code = (
                            run_deployment_governance_provider_config_show(
                                channel_type=args.channel_type,
                                json_output=args.json_output,
                            )
                        )
                    elif args.config_command == "update":
                        exit_code = (
                            run_deployment_governance_provider_config_update(
                                channel_type=args.channel_type,
                                values=args.values,
                                json_output=args.json_output,
                            )
                        )
                    else:
                        exit_code = (
                            run_deployment_governance_provider_config_delete(
                                channel_type=args.channel_type,
                                json_output=args.json_output,
                            )
                        )
                elif args.providers_command == "secrets":
                    if args.secrets_command == "create":
                        exit_code = (
                            run_deployment_governance_provider_secrets_create(
                                channel_type=args.channel_type,
                                values=args.values,
                                json_output=args.json_output,
                            )
                        )
                    elif args.secrets_command == "list":
                        exit_code = (
                            run_deployment_governance_provider_secrets_list(
                                json_output=args.json_output,
                            )
                        )
                    elif args.secrets_command == "show":
                        exit_code = (
                            run_deployment_governance_provider_secrets_show(
                                channel_type=args.channel_type,
                                json_output=args.json_output,
                            )
                        )
                    elif args.secrets_command == "update":
                        exit_code = (
                            run_deployment_governance_provider_secrets_update(
                                channel_type=args.channel_type,
                                values=args.values,
                                json_output=args.json_output,
                            )
                        )
                    else:
                        exit_code = (
                            run_deployment_governance_provider_secrets_delete(
                                channel_type=args.channel_type,
                                json_output=args.json_output,
                            )
                        )
                elif args.providers_command == "auth":
                    if args.auth_command == "show":
                        exit_code = (
                            run_deployment_governance_provider_auth_show(
                                channel_type=args.channel_type,
                                json_output=args.json_output,
                            )
                        )
                    else:
                        exit_code = (
                            run_deployment_governance_provider_auth_validate(
                                channel_type=args.channel_type,
                                json_output=args.json_output,
                            )
                        )
                elif args.providers_command == "request":
                    if args.request_command == "show":
                        exit_code = (
                            run_deployment_governance_provider_request_show(
                                channel_type=args.channel_type,
                                notification_id=args.notification_id,
                                json_output=args.json_output,
                            )
                        )
                    else:
                        exit_code = (
                            run_deployment_governance_provider_request_validate(
                                channel_type=args.channel_type,
                                notification_id=args.notification_id,
                                json_output=args.json_output,
                            )
                        )
                else:
                    if args.response_command == "show":
                        exit_code = (
                            run_deployment_governance_provider_response_show(
                                dispatch_id=args.dispatch_id,
                                json_output=args.json_output,
                            )
                        )
                    else:
                        exit_code = (
                            run_deployment_governance_provider_response_validate(
                                dispatch_id=args.dispatch_id,
                                json_output=args.json_output,
                            )
                        )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "retries":
                if args.retries_command == "evaluate":
                    exit_code = run_deployment_governance_retries_evaluate(
                        dispatch_id=args.dispatch_id,
                        attempt=args.attempt,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_retries_preview(
                        dispatch_id=args.dispatch_id,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if getattr(args, "audits_command", None) == "scheduler":
                if args.scheduler_command == "pending":
                    exit_code = run_deployment_governance_scheduler_pending(
                        json_output=args.json_output,
                    )
                elif args.scheduler_command == "ready":
                    exit_code = run_deployment_governance_scheduler_ready(
                        json_output=args.json_output,
                    )
                elif args.scheduler_command == "show":
                    exit_code = run_deployment_governance_scheduler_show(
                        dispatch_id=args.dispatch_id,
                        json_output=args.json_output,
                    )
                else:
                    exit_code = run_deployment_governance_scheduler_cancel(
                        dispatch_id=args.dispatch_id,
                        json_output=args.json_output,
                    )
                sys.exit(exit_code)
            if (
                getattr(args, "audits_command", None)
                == "delivery-worker"
            ):
                if args.delivery_worker_command == "run":
                    exit_code = (
                        run_deployment_governance_delivery_worker_run(
                            json_output=args.json_output,
                        )
                    )
                else:
                    exit_code = (
                        run_deployment_governance_delivery_worker_summary(
                            json_output=args.json_output,
                        )
                    )
                sys.exit(exit_code)
            try:
                since = parse_governance_audit_timestamp(args.since)
                until = parse_governance_audit_timestamp(args.until)
            except ValueError as exc:
                parser.error(str(exc))
                return
            outcome = (
                None
                if args.outcome is None
                else GovernanceIntegrityAuditOutcome(args.outcome)
            )
            exit_code = run_deployment_governance_audit_history(
                backend=args.backend,
                outcome=outcome,
                started_at_or_after=since,
                started_at_or_before=until,
                limit=args.limit,
                include_trend=args.include_trend,
                trend_window=args.trend_window,
                include_regression=args.include_regression,
                json_output=args.json_output,
            )
            sys.exit(exit_code)
        elif args.governance_command == "check":
            if args.batch_size <= 0:
                parser.error("--batch-size must be greater than zero")
            policy = (
                GovernanceIntegrityCheckPolicy.REQUIRE_HEALTHY
                if args.policy == "require-healthy"
                else GovernanceIntegrityCheckPolicy.REGRESSION_ONLY
            )
            exit_code = run_deployment_governance_check(
                policy=policy,
                batch_size=args.batch_size,
                json_output=args.json_output,
            )
            sys.exit(exit_code)

if __name__ == "__main__":
    main()
