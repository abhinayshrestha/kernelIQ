"""Interactive read-eval-print loop for KernelIQ local diagnosis."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prompt_toolkit import prompt as _pt_prompt
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory

from db.helpers import (
    auto_resolve_alert,
    bulk_resolve_all,
    check_alert_condition_resolved,
    get_unresolved_alerts,
    query,
    resolve_alert,
    update_feedback,
)
from db.schema import get_db_path
from dataset.logger import get_dataset_stats
from dataset.rebuild import print_summary, rebuild_dataset
from executor.runner import run_confirmed_command
from model.classifier import is_system_question
from model.client import chat
from model.investigation import investigate

from config import user_config_path
from shell.banner import render_banner
from shell.confirm import confirm_action
from shell.config_wizard import run_config_wizard
from shell.renderer import render_diagnosis
from shell.streamer import print_step

_BOLD_GREEN = "\033[1;32m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_RST = "\033[0m"

_RULE = "━" * 41


def _severity_style(severity: str | None) -> str:
    """ANSI prefix for alert severity (critical / warning / info)."""
    sev = str(severity or "").lower()
    if sev == "critical":
        return f"{_BOLD}{_RED}"
    if sev == "warning":
        return f"{_BOLD}{_YELLOW}"
    if sev == "info":
        return _CYAN
    return _DIM


def _fix_question_for_alert(alert: dict) -> str:
    """Build a focused investigation question for one alert row."""
    at = str(alert.get("alert_type") or "").strip()
    subj = str(alert.get("subject") or "").strip()
    ev = str(alert.get("evidence_summary") or "").strip()
    if at == "SERVICE_FAILED":
        return (
            f"{subj} service has failed. Investigate why it failed and suggest "
            "how to fix it."
        )
    if at == "DISK_CRITICAL":
        return (
            f"Disk usage is critical on {subj}. Investigate what is consuming "
            "space and suggest cleanup."
        )
    if at == "MEMORY_CRITICAL":
        return (
            "Available memory has dropped critically. Investigate what is "
            "consuming RAM and suggest action."
        )
    if at == "CPU_SUSTAINED":
        return (
            "CPU has been above 80% for 45 seconds. Investigate which process "
            "is causing sustained high CPU and suggest fix."
        )
    if at == "IOWAIT_HIGH":
        return (
            "iowait has been above 20% for 45 seconds. Investigate what is "
            "causing disk IO bottleneck and suggest fix."
        )
    if at == "OOM_KILL":
        return (
            f"The kernel killed a process: {ev}. Investigate what caused memory "
            "exhaustion."
        )
    if at == "SWAP_HIGH":
        return (
            "Swap usage is critically high. Investigate what is consuming RAM "
            "and causing swap pressure."
        )
    if at == "ZOMBIE_ACCUMULATION":
        return (
            "Multiple zombie processes detected. Investigate which parent "
            "process is not reaping its children."
        )
    if at == "SERVICE_FLAPPING":
        return (
            f"{subj} service is crashlooping. Investigate why it keeps failing "
            "and restarting."
        )
    return f"{ev}. Investigate and suggest fix."


def run_fix_session(db_path: str, priority_id: int | None = None) -> None:
    """Walk unresolved alerts: optional investigate, confirm commands, resolve."""
    if priority_id is not None:
        rows = query(
            db_path,
            "SELECT * FROM alerts WHERE id = ? AND resolved = 0",
            (priority_id,),
        )
        if not rows:
            print(
                f"Alert #{priority_id} not found or already resolved."
            )
            return
        priority_alert = dict(rows[0])
        rest = [
            a
            for a in get_unresolved_alerts(db_path)
            if int(a["id"]) != int(priority_id)
        ]
        queue: list[dict] = [priority_alert] + rest
    else:
        queue = get_unresolved_alerts(db_path)

    if not queue:
        print(
            f"{_GREEN}No unresolved alerts. System looks clean.{_RST}"
        )
        return

    n_total = len(queue)
    print(f"{n_total} unresolved alert(s). Investigating one at a time.")
    print()

    investigated = 0
    skipped = 0
    resolved_n = 0

    try:
        for alert in queue:
            aid = int(alert["id"])
            sev_raw = str(alert.get("severity") or "").strip()
            sty = _severity_style(sev_raw)
            print(f"{sty}{_RULE}{_RST}")
            print(f"{sty}Alert [#{aid}] — {sev_raw.upper() or '—'}{_RST}")
            print(f"Type:     {alert.get('alert_type') or '—'}")
            print(f"Subject:  {alert.get('subject') or '—'}")
            print(f"Evidence: {alert.get('evidence_summary') or '—'}")
            ts_raw = str(alert.get("timestamp") or "").strip()
            ts_disp = _format_local_datetime(ts_raw) if ts_raw else "—"
            print(f"Time:     {ts_disp}")
            print(f"{sty}{_RULE}{_RST}")

            inv = _pt_prompt("Investigate? [y/N]: ").strip().lower()
            if inv != "y":
                print("Skipped.")
                skipped += 1
                continue

            investigated += 1
            question = _fix_question_for_alert(alert)
            result = investigate(question, on_step=print_step)
            print(render_diagnosis(result))

            cmd = result.get("command_proposed")
            if cmd and str(cmd).strip():
                if confirm_action(str(cmd).strip()):
                    run_res = run_confirmed_command(str(cmd).strip())
                    print(_format_run_output(run_res))
                else:
                    print("Action skipped.")

            mark = _pt_prompt("Mark alert as resolved? [y/N]: ").strip().lower()
            if mark == "y":
                resolve_alert(db_path, aid)
                print(f"{_GREEN}✓ Alert #{aid} resolved.{_RST}")
                resolved_n += 1
            else:
                print("Alert kept open.")

    except KeyboardInterrupt:
        print("\nFix session interrupted.")
        return

    still_open = len(get_unresolved_alerts(db_path))
    print(f"{_RULE}")
    print("Fix session complete.")
    print(f"  Investigated:  {investigated}")
    print(f"  Skipped:       {skipped}")
    print(f"  Resolved:      {resolved_n}")
    print(f"  Still open:    {still_open}")
    print(f"{_RULE}")


def _print_unresolved_alerts_list(db_path: str) -> None:
    """Print unresolved alerts using :func:`~db.helpers.get_unresolved_alerts`."""
    rows = get_unresolved_alerts(db_path)
    if not rows:
        print(f"{_GREEN}No unresolved alerts.{_RST}")
        return
    for row in rows:
        aid = row.get("id")
        sev_disp = str(row.get("severity") or "").strip() or "—"
        sty = _severity_style(row.get("severity"))
        at = str(row.get("alert_type") or "").strip() or "—"
        subj = str(row.get("subject") or "").replace("\n", " ").strip() or "—"
        ev = str(row.get("evidence_summary") or "").replace("\n", " ").strip() or "—"
        ts = str(row.get("timestamp") or "")
        ts_disp = _format_local_datetime(ts) if ts else "—"
        print(f"#{aid}  {sty}{sev_disp}{_RST}  {at} — {subj}")
        print(f"  {ev}")
        print(f"  {ts_disp}")


def _handle_alerts_command(db_path: str, line: str) -> None:
    """Dispatch ``!alerts`` variants (list, resolve, auto, bulk, summarize)."""
    raw = line.strip()
    m = re.match(r"!alerts\s*(.*)$", raw, re.IGNORECASE)
    suffix = (m.group(1) or "").strip() if m else ""

    if not suffix:
        _print_unresolved_alerts_list(db_path)
        return

    if suffix.startswith("--id="):
        id_part = suffix[5:].strip()
        if not id_part.isdigit():
            print("Usage: !alerts --id=<number>")
            return
        aid = int(id_part)
        rows = query(db_path, "SELECT * FROM alerts WHERE id = ?", (aid,))
        if not rows or int(rows[0].get("resolved") or 0) != 0:
            print(f"Alert #{aid} not found or already resolved.")
            return
        alert = dict(rows[0])
        sty = _severity_style(alert.get("severity"))
        sev_disp = str(alert.get("severity") or "").strip() or "—"
        print(f"{sty}#{aid}  {sev_disp}{_RST}  {alert.get('alert_type')} — {alert.get('subject')}")
        print(f"  {alert.get('evidence_summary') or '—'}")
        ts_raw = str(alert.get("timestamp") or "").strip()
        ts_disp = _format_local_datetime(ts_raw) if ts_raw else "—"
        print(f"  {ts_disp}")
        ans = _pt_prompt("Mark as resolved without investigating? [y/N]: ").strip().lower()
        if ans == "y":
            resolve_alert(db_path, aid)
            print(f"{_GREEN}✓ Alert #{aid} resolved.{_RST}")
        else:
            print("Cancelled.")
        return

    if suffix == "--resolve=auto":
        unresolved = get_unresolved_alerts(db_path)
        if not unresolved:
            print("No unresolved alerts.")
            return
        resolved_list: list[tuple[int, str, str]] = []
        still_active: list[tuple[int, str]] = []
        for alert in unresolved:
            ok, reason = check_alert_condition_resolved(db_path, alert)
            if ok:
                auto_resolve_alert(db_path, int(alert["id"]), reason)
                resolved_list.append(
                    (int(alert["id"]), str(alert.get("alert_type") or ""), reason)
                )
            else:
                still_active.append(
                    (int(alert["id"]), str(alert.get("alert_type") or ""))
                )
        nr = len(resolved_list)
        print(f"✓ Auto-resolved {nr} alerts:")
        for aid, atype, rsn in resolved_list:
            print(f"  #{aid} {atype} — {rsn}")
        if still_active:
            print(f"{len(still_active)} alert(s) still active:")
            for aid, atype in still_active:
                print(f"  #{aid} {atype} — use !fix {aid}")
        return

    if suffix == "--resolve-all":
        unresolved = get_unresolved_alerts(db_path)
        if not unresolved:
            print("No unresolved alerts.")
            return
        n = len(unresolved)
        ans = _pt_prompt(
            f"This will dismiss {n} alerts without investigating. "
            "Are you sure? [y/N]: "
        ).strip().lower()
        if ans == "y":
            bulk_resolve_all(db_path)
            print(f"{_GREEN}✓ Dismissed {n} alerts.{_RST}")
        else:
            print("Cancelled.")
        return

    if suffix == "--summarize":
        unresolved = get_unresolved_alerts(db_path)
        if not unresolved:
            print(
                f"{_GREEN}No unresolved alerts. System looks clean.{_RST}"
            )
            return
        alerts_text = ""
        for a in unresolved:
            alerts_text += (
                f"Alert #{a['id']} — {a.get('severity')}\n"
                f"Type: {a.get('alert_type')}\n"
                f"Subject: {a.get('subject')}\n"
                f"Evidence: {a.get('evidence_summary')}\n"
                f"Time: {a.get('timestamp')}\n\n"
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are KernelIQ, a Linux system diagnosis agent. Analyze "
                    "these unresolved system alerts and provide a concise health "
                    "briefing. Group related alerts, explain connections between "
                    "them, identify likely root causes, and prioritize what to fix "
                    "first. Be specific, use the evidence provided, and keep "
                    "response under 200 words. Write for a DevOps engineer."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Current unresolved alerts:\n\n{alerts_text}\n"
                    "Provide a health briefing."
                ),
            },
        ]
        print(f"Analyzing {len(unresolved)} unresolved alerts...")
        response = chat(messages)
        print(_RULE)
        print("System Health Briefing")
        print(_RULE)
        print(response)
        print(_RULE)
        return

    print(
        "Usage: !alerts, !alerts --id=<n>, !alerts --resolve=auto, "
        "!alerts --resolve-all, !alerts --summarize"
    )


def _parse_relative_duration(s: str) -> timedelta:
    """Parse a duration like ``1h``, ``30m``, ``2d`` into a :class:`~datetime.timedelta`."""
    t = s.strip().lower()
    m = re.fullmatch(r"(\d+)([hmd])", t)
    if not m:
        raise ValueError(f"invalid duration {s!r}; use e.g. 1h, 30m, 2d")
    n = int(m.group(1))
    u = m.group(2)
    if u == "h":
        return timedelta(hours=n)
    if u == "m":
        return timedelta(minutes=n)
    return timedelta(days=n)


def _parse_db_timestamp(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp from the database (UTC if naive)."""
    raw = (ts or "").strip()
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_hhmm(ts_str: str) -> str:
    """Format a DB timestamp as local ``HH:MM``."""
    dt = _parse_db_timestamp(ts_str)
    return dt.astimezone().strftime("%H:%M")


def _format_local_datetime(ts_str: str) -> str:
    """Format a DB timestamp as a local human-readable datetime."""
    raw = (ts_str or "").strip()
    if not raw:
        return "—"
    try:
        dt = _parse_db_timestamp(raw)
    except Exception:
        return raw
    if dt == datetime.min.replace(tzinfo=timezone.utc):
        return raw
    return dt.astimezone().strftime("%b %d %Y %H:%M:%S")


def _timeline_cutoff_iso(delta: timedelta) -> str:
    """UTC ISO string for ``now - delta`` (matches stored telemetry timestamps)."""
    return (datetime.now(timezone.utc) - delta).isoformat()


def _print_timeline(db_path: str, duration_label: str, delta: timedelta) -> None:
    """Print merged timeline for investigations, diagnoses, and alerts."""
    since = _timeline_cutoff_iso(delta)
    inv = query(
        db_path,
        "SELECT timestamp, command_run FROM investigation_logs WHERE timestamp >= ?",
        (since,),
    )
    diag = query(
        db_path,
        "SELECT timestamp, question, confidence FROM diagnoses WHERE timestamp >= ?",
        (since,),
    )
    alrt = query(
        db_path,
        (
            "SELECT timestamp, alert_type, subject, severity FROM alerts "
            "WHERE timestamp >= ?"
        ),
        (since,),
    )
    events: list[tuple[datetime, str]] = []
    for row in inv:
        ts = str(row.get("timestamp") or "")
        cmd = str(row.get("command_run") or "").replace("\n", " ").strip() or "—"
        line = (
            f"{_DIM}{_format_hhmm(ts)}{_RST}  "
            f"{_BOLD}[investigate]{_RST}  {cmd}"
        )
        events.append((_parse_db_timestamp(ts), line))
    for row in diag:
        ts = str(row.get("timestamp") or "")
        q = str(row.get("question") or "").replace("\n", " ").strip() or "—"
        pct = _format_history_confidence(row.get("confidence"))
        line = (
            f"{_DIM}{_format_hhmm(ts)}{_RST}  "
            f"{_BOLD}[diagnosis]{_RST}  {q} — {pct}%"
        )
        events.append((_parse_db_timestamp(ts), line))
    for row in alrt:
        ts = str(row.get("timestamp") or "")
        at = str(row.get("alert_type") or "").strip() or "—"
        subj = str(row.get("subject") or "").replace("\n", " ").strip() or "—"
        sev = str(row.get("severity") or "").strip() or "—"
        line = (
            f"{_DIM}{_format_hhmm(ts)}{_RST}  "
            f"{_BOLD}[alert]{_RST}  {at} — {subj} — {sev}"
        )
        events.append((_parse_db_timestamp(ts), line))
    events.sort(key=lambda x: x[0])
    if not events:
        print(f"No activity in the last {duration_label}.")
        return
    for _, line in events:
        print(line)


def _fmt_mb(val: float | None) -> str:
    if val is None:
        return "?"
    v = float(val)
    if v == int(v):
        return str(int(v))
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _print_status(db_path: str) -> None:
    """Print one-line health summary and latest telemetry snapshot."""
    count_rows = query(
        db_path,
        "SELECT COUNT(*) AS n FROM alerts WHERE resolved = 0",
        (),
    )
    n = int(count_rows[0]["n"]) if count_rows else 0
    if n == 0:
        print(f"{_GREEN}HEALTHY — No issues{_RST}")
    else:
        alert_word = "alert" if n == 1 else "alerts"
        print(f"{_YELLOW}WARNING — {n} unresolved {alert_word}{_RST}")

    tel = query(
        db_path,
        (
            "SELECT cpu_percent, mem_total_mb, mem_used_mb, swap_used_mb, load_avg_1 "
            "FROM telemetry_samples ORDER BY id DESC LIMIT 1"
        ),
        (),
    )
    if not tel:
        print("No telemetry data collected yet.")
        return
    r = tel[0]
    cpu = r.get("cpu_percent")
    if cpu is None:
        cpu_s = "?"
    else:
        cpu_s = f"{float(cpu):.1f}".rstrip("0").rstrip(".")
    mem_u = _fmt_mb(r.get("mem_used_mb"))
    mem_t = _fmt_mb(r.get("mem_total_mb"))
    swap_u = _fmt_mb(r.get("swap_used_mb"))
    load = r.get("load_avg_1")
    load_s = "?"
    if load is not None:
        load_s = f"{float(load):.2f}".rstrip("0").rstrip(".")
    print(
        f"CPU: {cpu_s}%  RAM: {mem_u}/{mem_t}MB  "
        f"Swap: {swap_u}MB  Load: {load_s}"
    )


def _latest_diagnosis_id_for_session(db_path: str, session_id: str) -> int | None:
    """Return the most recent ``diagnoses.id`` for ``session_id``, or None if missing."""
    rows = query(
        db_path,
        "SELECT id FROM diagnoses WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (session_id,),
    )
    if not rows:
        return None
    return int(rows[0]["id"])


def _format_history_confidence(confidence: float | None) -> str:
    """Format confidence for one-line history rows."""
    if confidence is None:
        return "?"
    c = float(confidence)
    if c == int(c):
        return str(int(c))
    return f"{c:.1f}".rstrip("0").rstrip(".")


def _print_diagnosis_history(db_path: str) -> None:
    """Print the last 10 diagnoses as timestamp — question — confidence%."""
    rows = query(
        db_path,
        (
            "SELECT timestamp, question, confidence FROM diagnoses "
            "ORDER BY id DESC LIMIT 10"
        ),
        (),
    )
    if not rows:
        print("No diagnosis history.")
        return
    for row in rows:
        ts = str(row.get("timestamp") or "")
        q = str(row.get("question") or "").replace("\n", " ").strip()
        pct = _format_history_confidence(row.get("confidence"))
        print(f"{ts} — {q} — {pct}%")


def _format_run_output(result: dict) -> str:
    """Format executor run result dict (e.g. from :func:`~executor.runner.run_confirmed_command`)."""
    if not result.get("success"):
        return f"Error: {result.get('error', 'unknown error')}"
    out = result.get("output") or ""
    stderr = result.get("stderr") or ""
    rc = result.get("return_code")
    parts = [f"exit_code={rc}", out.rstrip()]
    if stderr.strip():
        parts.append(f"stderr:\n{stderr.rstrip()}")
    return "\n".join(parts).strip()


def start_repl() -> None:
    """Start the KernelIQ interactive prompt: questions, investigation, optional command run.

    Prints the startup banner (telemetry, daemon, LLM reachability, dataset), then
    loops on ``kerneliq>`` until ``exit``/``quit``, EOF, or interrupt. Each question
    runs :func:`~model.investigation.investigate` with live step printing on stderr.

    Special input lines: ``!help``, ``!correct`` / ``!right``, ``!wrong``, ``!partial``,
    ``!last``, ``!history``, ``!timeline``, ``!alerts`` (and variants), ``!fix``,
    ``!status``, ``!config``, ``!dataset``, ``!rebuild`` (see help text).
    """
    # import logging
    # logging.basicConfig(level=logging.DEBUG)

    render_banner()

    _pt_prompt_text = ANSI(f"{_BOLD_GREEN}kerneliq> {_RST}")
    _hist_path = Path.home() / ".local" / "share" / "kerneliq" / "history"
    _hist_path.parent.mkdir(parents=True, exist_ok=True)
    _history = FileHistory(str(_hist_path))

    db_path = get_db_path()
    last_result: dict | None = None
    last_diagnosis_id: int | None = None

    while True:
        try:
            line = _pt_prompt(_pt_prompt_text, history=_history)
        except KeyboardInterrupt:
            print()
            print("Goodbye.")
            break
        except EOFError:
            print("Goodbye.")
            break

        text = line.strip()
        lowered = text.lower()

        if lowered in ("!exit", "!quit"):
            print("Goodbye.")
            break

        if lowered == "!clear":
            os.system("clear")
            continue

        if lowered == "!help":
            print("Commands:")
            print("  !help        Show this help.")
            print("  !clear       Clear the terminal screen.")
            print("  !exit        Leave the REPL.")
            print("  !quit        Same as !exit.")
            print("  !correct    Mark the last diagnosis feedback: correct.")
            print("  !right      Same as !correct.")
            print("  !wrong      Mark the last diagnosis feedback: wrong.")
            print("  !partial    Mark the last diagnosis feedback: partial.")
            print("  !last       Show the last diagnosis again (no re-run).")
            print("  !history    Last 10 diagnoses (timestamp — question — %).")
            print(
                "  !timeline   Merged activity timeline (default last 1h; "
                "e.g. !timeline 6h, 30m, 2d)."
            )
            print("  !fix              Investigate all alerts")
            print("  !fix <id>         Investigate specific alert")
            print("  !alerts           Show unresolved alerts")
            print("  !alerts --id=<n>  Resolve specific alert")
            print("  !alerts --resolve=auto  Auto-dismiss resolved")
            print("  !alerts --resolve-all   Dismiss all alerts")
            print("  !alerts --summarize     LLM health briefing")
            print("  !status     Health line + latest CPU/RAM/swap/load.")
            print(
                "  !config     Where to override settings (kerneliq.toml) and how to restart."
            )
            print("  !dataset    Training JSONL dataset stats (count, confidence, …).")
            print("  !rebuild    Regenerate training_data.jsonl from the database.")
            print("  <text>      Ask a diagnosis question.")
            continue

        if lowered in ("!correct", "!right"):
            if last_diagnosis_id is None:
                print(f"{_YELLOW}No diagnosis to mark yet.{_RST}")
                continue
            update_feedback(db_path, last_diagnosis_id, "correct")
            print(f"{_GREEN}Thanks — marked as correct.{_RST}")
            continue

        if lowered == "!wrong":
            if last_diagnosis_id is None:
                print(f"{_YELLOW}No diagnosis to mark yet.{_RST}")
                continue
            update_feedback(db_path, last_diagnosis_id, "wrong")
            print(f"{_YELLOW}Thanks — marked as wrong.{_RST}")
            continue

        if lowered == "!partial":
            if last_diagnosis_id is None:
                print(f"{_YELLOW}No diagnosis to mark yet.{_RST}")
                continue
            update_feedback(db_path, last_diagnosis_id, "partial")
            print(f"{_YELLOW}Thanks — marked as partial.{_RST}")
            continue

        if lowered == "!last":
            if last_result is None:
                print("No previous diagnosis.")
                continue
            print(render_diagnosis(last_result))
            continue

        if lowered == "!history":
            _print_diagnosis_history(db_path)
            continue

        if lowered == "!timeline" or lowered.startswith("!timeline "):
            raw_dur = text[9:].strip() if len(text) > 9 else ""
            duration_label = raw_dur if raw_dur else "1h"
            try:
                delta = _parse_relative_duration(duration_label)
            except ValueError as e:
                print(f"{_YELLOW}{e}{_RST}")
                continue
            _print_timeline(db_path, duration_label, delta)
            continue

        if lowered.startswith("!alerts"):
            _handle_alerts_command(db_path, text)
            continue

        if lowered == "!fix" or lowered.startswith("!fix "):
            if lowered == "!fix":
                run_fix_session(db_path)
            else:
                rest = text.strip()[5:].strip()
                if rest.isdigit():
                    run_fix_session(db_path, priority_id=int(rest))
                else:
                    print("Usage: !fix or !fix <alert_id>")
            continue

        if lowered == "!status":
            _print_status(db_path)
            continue

        if lowered == "!config":
            print(run_config_wizard())
            continue

        if lowered == "!dataset":
            stats = get_dataset_stats()
            bf = stats["by_feedback"]
            bb = stats["by_backend"]
            correct_n = int(bf.get("correct", 0))
            wrong_n = int(bf.get("wrong", 0))
            partial_n = int(bf.get("partial", 0))
            unlabeled_n = int(bf.get(None, 0))
            deepseek_n = int(bb.get("deepseek", 0))
            ollama_n = int(bb.get("ollama", 0))
            dr = stats["date_range"]
            earliest = dr.get("earliest")
            latest = dr.get("latest")
            if earliest and latest:
                dr_s = f"{earliest} to {latest}"
            elif earliest or latest:
                dr_s = f"{earliest or '—'} to {latest or '—'}"
            else:
                dr_s = "—"
            avg = stats["avg_confidence"]
            avg_s = f"{avg:.1f}".rstrip("0").rstrip(".")
            print("Dataset Stats")
            print(f"Total examples: {stats['total_examples']}")
            print(f"Avg confidence: {avg_s}%")
            print(
                "By feedback: "
                f"correct={correct_n} wrong={wrong_n} partial={partial_n} "
                f"unlabeled={unlabeled_n}"
            )
            print(f"By backend: deepseek={deepseek_n} ollama={ollama_n}")
            print(f"Date range: {dr_s}")
            continue

        if lowered == "!rebuild":
            print("Rebuilding dataset from database...")
            summary = rebuild_dataset(verbose=False)
            print_summary(summary)
            continue

        if not text:
            continue

        # if not is_system_question(text):
        #     print(
        #         f"{_YELLOW}That doesn't look like a system diagnosis question.{_RST}"
        #     )
        #     print(
        #         f"{_DIM}Try asking something like: why is my cpu high, "
        #         "is nginx running, what is using my disk.{_RST}"
        #     )
        #     continue

        print(f"{_DIM}Investigating...{_RST}")
        result = investigate(text, on_step=print_step)
        last_result = result
        sid = result.get("session_id")
        if isinstance(sid, str) and sid:
            last_diagnosis_id = _latest_diagnosis_id_for_session(db_path, sid)
        else:
            last_diagnosis_id = None
        print(render_diagnosis(result))

        cmd = result.get("command_proposed")
        if cmd and str(cmd).strip():
            if confirm_action(str(cmd).strip()):
                run_res = run_confirmed_command(str(cmd).strip())
                print(_format_run_output(run_res))
            else:
                print("Action skipped.")


if __name__ == "__main__":
    start_repl()
