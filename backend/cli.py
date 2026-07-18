import argparse
import os
import subprocess
import sys
from pathlib import Path

# Import the compiler function
from backend.compiler import compile_notebook
# Import inspector for analysis
from backend.inspector import inspect_notebook
from backend.serve import serve_notebook
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
# export_openapi_schema is imported lazily (see below) because it imports
# generated/app.py at module load time, which re-executes a previously
# compiled notebook's top-level code as a side effect (stray stdout output).
# Importing it eagerly here would leak that output into every CLI
# invocation, including unrelated commands like `governance doctor --json`.


def main():
    parser = argparse.ArgumentParser(
        prog="notebook-to-api",
        description="Compile Jupyter notebooks into FastAPI services and optionally build Docker images."
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

    # inspect command (show analysis report)
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a notebook and display analysis report.")
    inspect_parser.add_argument("notebook", help="Path to the notebook file.")
    inspect_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where compilation artifacts would be placed (used to list generated files)."
    )

    # openapi export command
    openapi_parser = subparsers.add_parser(
        "export-openapi", help="Export OpenAPI schema from generated FastAPI app."
    )
    openapi_parser.add_argument(
        "--output",
        default="generated/openapi.json",
        help="Path to write the OpenAPI JSON file."
    )

    # serve command (live notebook server)
    serve_parser = subparsers.add_parser("serve", help="Serve notebook as live API with hot recompilation.")
    serve_parser.add_argument("notebook", help="Path to the notebook file.")
    serve_parser.add_argument(
        "--output",
        default="generated",
        help="Output directory where the FastAPI app will be written."
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

    args = parser.parse_args()

    if args.command == "compile":
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        compile_notebook(notebook_path=args.notebook, output_dir=str(output_dir))
        print("\nCompilation finished. FastAPI app is ready in", output_dir)
    elif args.command == "inspect":
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        inspect_notebook(notebook_path=args.notebook, output_dir=str(output_dir))
    elif args.command == "export-openapi":
        from backend.exporters.openapi_exporter import export_openapi_schema
        export_openapi_schema(args.output)
    elif args.command == "serve":
        serve_notebook(args.notebook, args.output)
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
                else:
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
    elif args.command == "deploy":
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        compile_notebook(notebook_path=args.notebook, output_dir=str(output_dir))
        # Build Docker image
        dockerfile_path = output_dir / "Dockerfile"
        if not dockerfile_path.is_file():
            raise FileNotFoundError(f"Dockerfile not found at {dockerfile_path}. Ensure the compiler generated it.")
        print(f"Building Docker image '{args.tag}' from {output_dir} …")
        subprocess.run(["docker", "build", "-t", args.tag, "."], cwd=str(output_dir), check=True)
        print(f"Docker image '{args.tag}' built successfully.")

if __name__ == "__main__":
    main()
