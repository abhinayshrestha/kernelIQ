"""Progressive summarization of investigation steps dropped from
the sliding window.

Uses deterministic string append — no LLM call. Each evicted
step is appended directly to the early summary with its step
header and digest. This preserves all diagnostic facts with
zero latency and zero API cost.
"""

from __future__ import annotations


def update_summary(
    existing_summary: str,
    step_number: int,
    command_type: str,
    command_run: str,
    digest: str,
) -> str:
    """Append one evicted step to the running early summary.

    No LLM call. Deterministic string concatenation. The step
    header and digest are appended directly so the investigation
    LLM can see exactly what was run and what was found in
    earlier steps.

    Args:
        existing_summary: Current summary string, or empty string
            if this is the first step being summarized.
        step_number: The step number being incorporated.
        command_type: "command" or "sql".
        command_run: The command or SQL query that was run.
        digest: The condensed digest of that step's output.

    Returns:
        Updated summary string with the new step appended,
        or just the new step block if existing_summary is empty.
    """
    step_block = (
        f"Step {step_number} [{command_type}]: {command_run}\n"
        f"{digest}"
    )
    if not existing_summary:
        return step_block
    return f"{existing_summary}\n\nStep {step_number} [{command_type}]: {command_run}\n{digest}"
