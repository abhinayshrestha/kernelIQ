"""Rebuild ``training_data.jsonl`` from SQLite diagnoses and investigation logs.

The JSONL file is regenerated so labels such as ``user_feedback`` always match
the database, even when feedback was updated after the example was first saved.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from db.helpers import query
from db.schema import get_db_path

from dataset.logger import build_training_example, get_dataset_path, save_example


def get_all_session_ids(db_path: str) -> list[str]:
    """Return session ids eligible for export, ordered by diagnosis time ascending.

    Uses the latest ``diagnoses`` row per ``session_id`` (highest ``id``). A
    session is included only when that row has ``confidence >= 50.0`` and
    ``user_feedback`` is NULL or not ``\"wrong\"``.

    Args:
        db_path: Path to the KernelIQ SQLite database.

    Returns:
        List of ``session_id`` strings, oldest qualifying diagnosis first.
    """
    rows = query(
        db_path,
        (
            "WITH latest AS ("
            "  SELECT session_id, timestamp, confidence, user_feedback, "
            "         ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY id DESC) AS rn "
            "  FROM diagnoses"
            ") "
            "SELECT session_id FROM latest "
            "WHERE rn = 1 AND confidence >= 50.0 "
            "AND (user_feedback IS NULL OR user_feedback != 'wrong') "
            "ORDER BY timestamp ASC"
        ),
        (),
    )
    out: list[str] = []
    for row in rows:
        sid = row.get("session_id")
        if sid is not None and str(sid).strip():
            out.append(str(sid))
    return out


def rebuild_dataset(verbose: bool = True) -> dict:
    """Back up the JSONL file, then rewrite it from the database.

    Args:
        verbose: When ``True``, print one progress line per exported example.

    Returns:
        Summary with keys ``total_sessions``, ``exported``, ``skipped``,
        and ``output_file`` (absolute path to ``training_data.jsonl``).
    """
    db_path = get_db_path()
    session_ids = get_all_session_ids(db_path)
    out_path = Path(get_dataset_path())
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.is_file():
        bak = out_path.with_name(out_path.name + ".bak")
        os.replace(str(out_path), str(bak))

    out_path.write_text("", encoding="utf-8")

    total = len(session_ids)
    exported = 0
    skipped = 0
    for session_id in session_ids:
        example = build_training_example(session_id, db_path)
        if example is None:
            skipped += 1
            continue
        save_example(example)
        exported += 1
        if verbose:
            q = str(example.get("question") or "").replace("\n", " ").strip() or "—"
            conf = example.get("final_response") or {}
            try:
                pct = float(conf.get("confidence") or 0.0)
            except (TypeError, ValueError):
                pct = 0.0
            print(f"  [{exported}/{total}] {session_id} — {q} ({pct:.0f}%)")

    return {
        "total_sessions": total,
        "exported": exported,
        "skipped": skipped,
        "output_file": str(out_path.resolve()),
    }


def print_summary(summary: dict) -> None:
    """Print a short human-readable summary after :func:`rebuild_dataset`."""
    print("Rebuild complete.")
    print(f"  Exported: {summary['exported']} examples")
    print(
        f"  Skipped:  {summary['skipped']} sessions "
        "(no logs or below threshold)"
    )
    print(f"  File:     {summary['output_file']}")


if __name__ == "__main__":
    result = rebuild_dataset(verbose=True)
    print_summary(result)
