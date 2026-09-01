"""T0.1: report which Threads API scopes the current access token actually has.

Threads (graph.threads.net) tokens are issued via the same OAuth flow as
Facebook Graph API tokens, so `debug_token` on graph.facebook.com works for
introspection. Run manually: `python -m scripts.check_threads_scopes`.
"""
import os
import sys
from datetime import datetime, timezone

import requests

REQUIRED_SCOPES = [
    "threads_basic",
    "threads_content_publish",
    "threads_manage_insights",
    "threads_manage_replies",
]

DEBUG_TOKEN_URL = "https://graph.facebook.com/debug_token"


def main() -> int:
    token = os.environ["THREADS_ACCESS_TOKEN"]
    app_id = os.environ["THREADS_APP_ID"]
    app_secret = os.environ["THREADS_APP_SECRET"]
    app_token = f"{app_id}|{app_secret}"

    resp = requests.get(DEBUG_TOKEN_URL, params={"input_token": token, "access_token": app_token}, timeout=15)

    lines = [
        "# Threads API scope check",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    if resp.status_code != 200:
        lines += [
            f"`debug_token` request failed: HTTP {resp.status_code} — {resp.text}",
            "",
            "Fall back to manual check: Meta App Dashboard → your app → "
            "App Review → Permissions and Features, and note which of the "
            "scopes below show 'Advanced Access' or 'Standard Access'.",
            "",
            "## Required scopes",
        ]
        for scope in REQUIRED_SCOPES:
            lines.append(f"- [ ] `{scope}`")
        write_report(lines)
        return 1

    data = resp.json().get("data", {})
    granted = set(data.get("scopes", []))

    lines.append(f"Token type: {data.get('type')}, app id: {data.get('app_id')}, expires_at: {data.get('expires_at')}")
    lines.append("")
    lines.append("## Scope status")
    for scope in REQUIRED_SCOPES:
        mark = "x" if scope in granted else " "
        lines.append(f"- [{mark}] `{scope}`")

    missing = [s for s in REQUIRED_SCOPES if s not in granted]
    lines.append("")
    if missing:
        lines.append(f"**Missing {len(missing)} scope(s):** {', '.join(missing)} — submit for App Review (2–6 weeks + 1–2 weeks business verification per SPEC.md T0.1).")
    else:
        lines.append("All required scopes granted.")

    write_report(lines)
    return 0 if not missing else 1


def write_report(lines: list[str]) -> None:
    with open("docs/threads_scopes.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
