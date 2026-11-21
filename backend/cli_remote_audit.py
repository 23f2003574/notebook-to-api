"""`remote-audit`: read the governance audit trail of a running dashboard, and the dashboard
credential handling every dashboard-facing CLI command shares.

The audit trail is the hash-chained compile/deploy/lifecycle record behind /governance/audit.
(The `governance audits` command group works on different, local tables and is unrelated.)

Exit codes of `remote-audit verify`: 0 the chain is intact, 3 it is broken, 4 the audit service is
unavailable (could not be checked: that is not evidence of tampering). `list` and `show` use 0 for
success and 1 for any error."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EXIT_OK, EXIT_ERROR, EXIT_BROKEN, EXIT_UNAVAILABLE = 0, 1, 3, 4


def dashboard_key() -> str:
    """The dashboard key for requests to the dashboard: $NOTEBOOK_API_DASHBOARD_KEY, or the first
    line of the file named by $NOTEBOOK_API_DASHBOARD_KEY_FILE (so it need not sit in the process
    environment or a command line). Empty when none is configured."""
    path = os.getenv("NOTEBOOK_API_DASHBOARD_KEY_FILE", "").strip()
    if path:
        try:
            lines = Path(path).read_text().strip().splitlines()
        except OSError:
            raise RuntimeError("NOTEBOOK_API_DASHBOARD_KEY_FILE could not be read") from None
        if lines:
            return lines[0].strip()
    return os.getenv("NOTEBOOK_API_DASHBOARD_KEY", "").strip()


def install_dashboard_credentials(httpx, dashboard_url: str) -> bool:
    """Send the dashboard key as X-API-Key on every httpx request to `dashboard_url` -- and only
    to it: a request to any other host never carries the key. An explicit X-API-Key header on a
    call wins. Returns whether a key is configured."""
    key = dashboard_key()
    if not key:
        return False
    base = dashboard_url.rstrip("/") + "/"

    def wrap(name, original):
        def call(*args, **kwargs):
            url = args[1] if name in ("request", "stream") and len(args) > 1 else (args[0] if args else kwargs.get("url"))
            if isinstance(url, str) and (url.rstrip("/") + "/").startswith(base):
                headers = dict(kwargs.get("headers") or {})
                if not any(h.lower() == "x-api-key" for h in headers):
                    headers["X-API-Key"] = key
                kwargs["headers"] = headers
            return original(*args, **kwargs)

        call.__wrapped__ = original
        return call

    for name in ("get", "post", "put", "patch", "delete", "request", "stream"):
        current = getattr(httpx, name)
        if not hasattr(current, "__wrapped__"):  # install once
            setattr(httpx, name, wrap(name, current))
    return True


def _get(httpx, base, path, params, timeout):
    try:
        return httpx.get(f"{base}{path}", params=params, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ConnectionError(
            f"Could not reach the dashboard at {base}: {exc}. Is it running? (see `python -m backend.dashboard`)"
        ) from None


def _detail(response) -> str:
    try:
        body = response.json()
        return str(body.get("detail", body)) if isinstance(body, dict) else response.text
    except ValueError:
        return response.text


def _row(record) -> str:
    when = str(record.get("occurred_at", ""))[:19].replace("T", " ")
    return (f"#{record['sequence']:<6} {when}  {record['outcome']:<8} {record['action']:<24} "
            f"{record['resource']}  (actor: {record['actor']})")


def run(args) -> int:
    import httpx

    base = args.dashboard_url.rstrip("/")
    as_json = args.json_output
    try:
        if args.remote_audit_command == "list":
            params = {"limit": args.limit}
            for key, value in (("notebook", args.notebook), ("action", args.action),
                               ("outcome", args.outcome), ("cursor", args.cursor)):
                if value:
                    params[key] = value
            if args.scope == "notebook":
                params["scope"] = "notebook"
            response = _get(httpx, base, "/governance/audit/history", params, args.timeout)
            if response.status_code >= 400:
                print(f"Error: the dashboard rejected the request ({response.status_code}): {_detail(response)}", file=sys.stderr)
                return EXIT_ERROR
            body = response.json()
            if as_json:
                print(json.dumps(body, indent=2, sort_keys=True))
                return EXIT_OK
            if not body["records"]:
                print("No audit records match.")
            for record in body["records"]:
                print(_row(record))
            if body.get("has_more"):
                print(f"\nMore records: add --cursor {body['next_cursor']}")
            return EXIT_OK

        if args.remote_audit_command == "show":
            response = _get(httpx, base, f"/governance/audit/{args.sequence}", None, args.timeout)
            if response.status_code == 404:
                print(f"Error: no audit record #{args.sequence} (it never existed or was purged).", file=sys.stderr)
                return EXIT_ERROR
            if response.status_code >= 400:
                print(f"Error: the dashboard rejected the request ({response.status_code}): {_detail(response)}", file=sys.stderr)
                return EXIT_ERROR
            record = response.json()
            if as_json:
                print(json.dumps(record, indent=2, sort_keys=True))
            else:
                print(_row(record))
                print(f"  recorded:      {record['occurred_at']}")
                print(f"  record hash:   {record['record_hash']}")
                print(f"  previous hash: {record['previous_hash']}")
                for key, value in sorted(record["metadata"].items()):
                    print(f"  {key}: {value}")
            return EXIT_OK

        # verify
        try:
            response = _get(httpx, base, "/governance/audit/verify", None, args.timeout)
        except ConnectionError as exc:
            return _unavailable(str(exc), as_json)
        if response.status_code >= 500 or response.status_code in (401, 403, 404):
            return _unavailable(f"the dashboard answered {response.status_code}: {_detail(response)}", as_json)
        if response.status_code >= 400:
            print(f"Error: {_detail(response)}", file=sys.stderr)
            return EXIT_ERROR
        result = response.json()
        if as_json:
            print(json.dumps({"status": "intact" if result["valid"] else "broken", **result}, indent=2, sort_keys=True))
        elif result["valid"]:
            mode = {"keyed": "keyed integrity (HMAC)", "keyed_unverified": "keyed, but the server lacks the key to check it",
                    }.get(result.get("integrity"), "unkeyed (SHA-256 chain only)")
            print(f"Chain intact: {result['checked']} records checked; {mode}.")
        else:
            print(f"Chain BROKEN at record #{result['first_broken_sequence']}: {result['reason']}. "
                  "Records from that one on cannot be trusted.")
        return EXIT_OK if result["valid"] else EXIT_BROKEN
    except ConnectionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _unavailable(message: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"status": "unavailable", "reason": message}, indent=2, sort_keys=True))
    else:
        print(f"Audit service unavailable: {message}. This does not mean the chain is broken; "
              "it could not be checked.", file=sys.stderr)
    return EXIT_UNAVAILABLE


def audit_summary_lines(data) -> list[str]:
    """What compile/deploy print about the audit record the dashboard wrote for them."""
    if not isinstance(data, dict):
        return []
    ref = data.get("governance_audit")
    if isinstance(ref, dict) and "sequence" in ref:
        replay = " (an earlier retry of this request)" if ref.get("replayed") else ""
        return [f"Governance audit record: #{ref['sequence']}{replay} -- `remote-audit show {ref['sequence']}`"]
    if data.get("governance_audit_error"):
        return [f"Governance audit: {data['governance_audit_error']} (the operation itself completed)"]
    return []
