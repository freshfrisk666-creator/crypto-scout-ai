from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents.scanner import load_config
from database.db import init_db
from runner.crypto_monitor import monitor_once
from runner.crypto_scanner import main as run_scanner
from scripts.build_dashboard import build_dashboard


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "actions_state.json"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except (TypeError, ValueError):
        return None


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "last_scan_at": None,
            "last_scan_status": "never_run",
        }

    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state, dict):
            raise ValueError("State file must contain a JSON object.")

        return state

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"Unable to read {STATE_PATH}: {exc}"
        ) from exc


def write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    temporary = STATE_PATH.with_suffix(".json.tmp")

    with temporary.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as file:
        json.dump(state, file, indent=2, ensure_ascii=False)
        file.write("\n")

    temporary.replace(STATE_PATH)


def set_output(name: str, value: str | bool | int) -> None:
    """Publish a GitHub Actions step output when running inside Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT")

    if not output_path:
        return

    with open(
        output_path,
        "a",
        encoding="utf-8",
        newline="\n",
    ) as file:
        rendered = (
            str(value).lower()
            if isinstance(value, bool)
            else value
        )
        file.write(f"{name}={rendered}\n")


def main() -> None:
    config = load_config()
    strategy = config.get("strategy", {})
    safety = config.get("safety", {})

    if safety.get("paper_trading_only") is not True:
        raise RuntimeError(
            "Safety check failed: "
            "safety.paper_trading_only must be true."
        )

    if safety.get("live_orders_enabled") is not False:
        raise RuntimeError(
            "Safety check failed: "
            "safety.live_orders_enabled must be false."
        )

    if safety.get("leverage_enabled") is not False:
        raise RuntimeError(
            "Safety check failed: "
            "safety.leverage_enabled must be false."
        )

    init_db(
        starting_cash=float(
            strategy.get("starting_cash", 10000.0)
        )
    )

    now = now_utc()
    state = load_state()

    last_scan = parse_timestamp(
        state.get("last_scan_at")
    )

    scan_interval_minutes = int(
        config.get("automation", {}).get(
            "scan_interval_minutes",
            60,
        )
    )

    if scan_interval_minutes < 5:
        raise ValueError(
            "automation.scan_interval_minutes must be at least 5."
        )

    event_name = os.environ.get(
        "GITHUB_EVENT_NAME",
        "",
    )

    # cron-job.org invokes workflow_dispatch with scheduled_tick=true.
    # The manual GitHub button defaults to false and forces a scan.
    scheduled_tick = (
        os.environ.get("SCHEDULED_TICK", "")
        .strip()
        .lower()
        in {"true", "1", "yes"}
    )

    manual_run = (
        event_name == "workflow_dispatch"
        and not scheduled_tick
    )

    force_scan = (
        manual_run
        or os.environ.get(
            "CRYPTO_FORCE_SCAN",
            "",
        ).strip() == "1"
    )

    scan_due = (
        force_scan
        or last_scan is None
        or now - last_scan
        >= timedelta(minutes=scan_interval_minutes)
    )

    print("\nCRYPTO SCOUT AI — GITHUB ACTIONS CYCLE")
    print("=" * 80)
    print(f"Current UTC time: {now.isoformat()}")
    print(f"Trigger: {event_name or 'local'}")
    print(f"External scheduler tick: {scheduled_tick}")
    print(f"Manual forced scan: {manual_run}")
    print(f"Scan interval: {scan_interval_minutes} minutes")
    print(
        "Last successful scan: "
        f"{state.get('last_scan_at') or 'not yet run'}"
    )

    # Monitor existing positions first, so a closed position can free
    # virtual cash or a slot before the scanner considers new entries.
    closed = monitor_once(config)

    scan_ran = False

    if scan_due:
        print("\nScan is due — running the 4h/1h scanner.")

        run_scanner()

        finished_at = now_utc().isoformat()
        state["last_scan_at"] = finished_at
        state["last_scan_status"] = "success"
        state["last_scan_error"] = None

        write_state(state)
        scan_ran = True

    else:
        age = (
            now - last_scan
            if last_scan is not None
            else None
        )

        print(
            "\nScan not due — monitor only. "
            f"Last scan age: {age.total_seconds() / 60:.1f} minutes."
            if age is not None
            else "\nScan not due — monitor only."
        )

    dashboard_file = build_dashboard(
        config=config,
        state=state,
    )

    print(f"\nDashboard data generated: {dashboard_file}")

    # Do not redeploy the dashboard on every external five-minute tick.
    # Publish on a manual run, after a scan, after a closure, or about every
    # 15 minutes.
    publish_dashboard = (
        manual_run
        or scan_ran
        or bool(closed)
        or now.minute % 15 <= 4
    )

    set_output("scan_ran", scan_ran)
    set_output("closed_count", len(closed))
    set_output("publish_dashboard", publish_dashboard)
    set_output(
        "last_scan_at",
        state.get("last_scan_at") or "",
    )

    print(f"\nMonitor closed positions this cycle: {len(closed)}")
    print(f"Scan ran this cycle: {scan_ran}")
    print(
        "Dashboard deployment requested: "
        f"{publish_dashboard}"
    )
    print("Paper mode only. No exchange order endpoints are used.")


if __name__ == "__main__":
    main()