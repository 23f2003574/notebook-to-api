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
