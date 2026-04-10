"""Interactive confirmation for proposed destructive or follow-up shell commands."""

from __future__ import annotations

from prompt_toolkit import prompt as _pt_prompt

_BOLD_YELLOW = "\033[1;33m"
_RST = "\033[0m"


def confirm_action(command: str) -> bool:
    """Prompt the user to confirm running a proposed command.

    Shows the command in bold yellow, then asks ``Proceed? [y/N]: ``. Accepts
    only ``y`` or ``yes`` (case insensitive). On EOF, interrupt, or any other
    input, returns ``False``.

    Args:
        command: The exact command line the user may approve.

    Returns:
        ``True`` if the user explicitly confirmed; ``False`` otherwise.
    """
    print(f"{_BOLD_YELLOW}{command}{_RST}")
    try:
        raw = _pt_prompt("Proceed? [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        return False

    answer = raw.strip().lower()
    return answer in ("y", "yes")
