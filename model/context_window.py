"""Sliding window context management for the KernelIQ investigation loop.

Maintains the last WINDOW_SIZE investigation steps in full and
progressively summarizes older steps into a compact early summary.
Builds the messages list for every investigation LLM call.

The messages list is always exactly 2 or 3 items regardless of
how many steps have run:
  [system, user]  — before any steps or when no last assistant msg
  [system, user, assistant]  — when last assistant msg is provided

Token growth is bounded because each step entry contains only
a condensed digest from condenser.py, not raw executor output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field  # noqa: F401 — field kept for subclass use

from model.summarizer import update_summary
from model.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_SIZE = 3
_LAST_ASSISTANT_MSG_CAP = 600


@dataclass
class StepEntry:
    """One investigation step stored in the sliding window.

    Attributes:
        step_number: Sequential step counter starting at 1.
        command_type: Either "command" or "sql".
        command_run: The exact command or SQL query that was run.
        digest: Condensed diagnostic digest from condenser.py.
    """

    step_number: int
    command_type: str
    command_run: str
    digest: str


class ContextWindow:
    """Sliding window of recent steps plus progressive early summary.

    Args:
        question: The original user question for this investigation.
        window_size: Number of most recent steps to keep in full.
            Older steps are progressively summarized into
            ``early_summary``. Defaults to ``_DEFAULT_WINDOW_SIZE``.

    Attributes:
        question: The original user question.
        window_size: Maximum number of full steps retained.
        early_summary: Running bullet-point summary of all steps
            that have been evicted from the window. Empty string
            until at least one step is evicted.
    """

    def __init__(
        self,
        question: str,
        window_size: int = _DEFAULT_WINDOW_SIZE,
    ) -> None:
        self.question = question
        self.window_size = window_size
        self.early_summary: str = ""
        self._steps: list[StepEntry] = []

    # ------------------------------------------------------------------
    # Mutation

    def add_step(self, step: StepEntry) -> None:
        """Append a step to the window, evicting and summarizing oldest if full.

        When the window already holds ``window_size`` steps, the oldest
        step is removed and its digest is merged into ``early_summary``
        via :func:`model.summarizer.update_summary`. The LLM call inside
        ``update_summary`` can fail silently — in that case
        ``early_summary`` is returned unchanged and the investigation
        loop continues unblocked. The evicted step's digest was already
        persisted to ``investigation_logs`` by the caller.

        Args:
            step: The completed :class:`StepEntry` to add.
        """
        if len(self._steps) >= self.window_size:
            evicted = self._steps.pop(0)
            logger.debug(
                "Evicting step %d from window; merging into early summary.",
                evicted.step_number,
            )
            self.early_summary = update_summary(
                existing_summary=self.early_summary,
                step_number=evicted.step_number,
                command_type=evicted.command_type,
                command_run=evicted.command_run,
                digest=evicted.digest,
            )
        self._steps.append(step)

    # ------------------------------------------------------------------
    # Query

    @property
    def step_count(self) -> int:
        """Number of steps currently held in the full window."""
        return len(self._steps)

    # ------------------------------------------------------------------
    # Messages construction

    def build_messages(
        self,
        last_assistant_msg: str | None = None,
    ) -> list[dict[str, str]]:
        """Build the messages list for the next investigation LLM call.

        The returned list is always 2 or 3 items:

        - ``[system, user]`` when ``last_assistant_msg`` is ``None``
        - ``[system, user, assistant]`` when a last assistant message
          is provided (capped at ``_LAST_ASSISTANT_MSG_CAP`` chars)

        The user message embeds the original question, the early
        summary of evicted steps (if any), and the full digests of
        all steps currently in the window.

        Args:
            last_assistant_msg: The last raw assistant response from
                the investigation loop, or ``None`` for the first call.

        Returns:
            List of ``{"role": ..., "content": ...}`` dicts ready to
            pass to :func:`model.client.chat`.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": self._build_user_content()},
        ]
        if last_assistant_msg:
            capped = last_assistant_msg[:_LAST_ASSISTANT_MSG_CAP]
            if len(last_assistant_msg) > _LAST_ASSISTANT_MSG_CAP:
                capped += "\n... [truncated]"
            messages.append({"role": "assistant", "content": capped})
        return messages

    # ------------------------------------------------------------------
    # Internal helpers

    def _build_user_content(self) -> str:
        """Compose the user turn text from question, summary, and window steps.

        Sections are separated by blank lines and only included when
        they contain content: the early summary is omitted before any
        eviction occurs, and the step list is omitted on the first call.

        Returns:
            Single string that becomes the ``user`` role message.
        """
        parts: list[str] = [f"Question: {self.question}"]

        if self.early_summary:
            parts.append("Early context summary:\n" + self.early_summary)

        if self._steps:
            step_blocks: list[str] = []
            for entry in self._steps:
                header = (
                    f"Step {entry.step_number} [{entry.command_type}]: "
                    f"{entry.command_run}"
                )
                step_blocks.append(f"{header}\n{entry.digest}")
            parts.append(
                "Recent investigation steps:\n" + "\n\n".join(step_blocks)
            )

        parts.append(
            "Continue investigating with COMMAND: or SQL:, "
            "or provide DIAGNOSIS: if you have enough evidence."
        )

        return "\n\n".join(parts)
