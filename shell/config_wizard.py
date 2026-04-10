# Requires: pip install prompt_toolkit
"""Interactive terminal wizard to edit ``kerneliq.toml`` using prompt_toolkit.

Run from the REPL via ``!config``: prompt_toolkit opens /dev/tty internally so the
wizard works correctly from inside the KernelIQ REPL which uses readline.

Writes the repo-root ``kerneliq.toml`` with every documented key after step 3.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.application import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

_REPO_ROOT = Path(__file__).resolve().parent.parent
_KERNELIQ_TOML = _REPO_ROOT / "kerneliq.toml"

_TOML_HEADER = """# Full mirror of config.py defaults — change any line to verify overrides.
# Gitignored. Restart: systemctl --user restart kerneliq-daemon; restart REPL.

[kerneliq]
"""

_BASELINE: dict[str, Any] = {
    "DAEMON_COLLECTION_INTERVAL_SEC": 15,
    "OLLAMA_BASE_URL": "http://192.168.64.1:11434",
    "OLLAMA_CHAT_TIMEOUT_SEC": 120,
    "LOCAL_LLM_MODEL": "llama3.2:3b",
    "MODEL_BACKEND": "ollama",
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_MODEL": "",
    "OPENAI_API_KEY": "",
    "OPENAI_MODEL": "",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "",
    "GOOGLE_API_KEY": "",
    "GOOGLE_MODEL": "",
    "ALERT_DISK_WARNING_PERCENT": 80.0,
    "ALERT_DISK_CRITICAL_PERCENT": 95.0,
    "ALERT_MEMORY_FREE_WARNING_PERCENT": 10.0,
    "ALERT_MEMORY_FREE_CRITICAL_PERCENT": 5.0,
    "ALERT_SWAP_USED_WARNING_PERCENT": 80.0,
    "ALERT_CPU_SUSTAINED_THRESHOLD_PERCENT": 80.0,
    "ALERT_CPU_SUSTAINED_SAMPLE_COUNT": 3,
    "ALERT_IOWAIT_THRESHOLD_PERCENT": 20.0,
    "ALERT_IOWAIT_SAMPLE_COUNT": 3,
    "ALERT_ZOMBIE_MIN_COUNT": 5,
    "ALERT_SERVICE_FLAP_WINDOW_MINUTES": 5,
    "ALERT_SERVICE_FLAP_MIN_STATE_CHANGES": 3,
    "ALERT_SERVICE_FLAP_STABILITY_MINUTES": 2,
    "ALERT_OOM_JOURNAL_SINCE": "2 minutes ago",
}

_WRITE_ORDER: list[str] = [
    "DAEMON_COLLECTION_INTERVAL_SEC",
    "OLLAMA_BASE_URL",
    "OLLAMA_CHAT_TIMEOUT_SEC",
    "LOCAL_LLM_MODEL",
    "MODEL_BACKEND",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "GOOGLE_API_KEY",
    "GOOGLE_MODEL",
    "ALERT_DISK_WARNING_PERCENT",
    "ALERT_DISK_CRITICAL_PERCENT",
    "ALERT_MEMORY_FREE_WARNING_PERCENT",
    "ALERT_MEMORY_FREE_CRITICAL_PERCENT",
    "ALERT_SWAP_USED_WARNING_PERCENT",
    "ALERT_CPU_SUSTAINED_THRESHOLD_PERCENT",
    "ALERT_CPU_SUSTAINED_SAMPLE_COUNT",
    "ALERT_IOWAIT_THRESHOLD_PERCENT",
    "ALERT_IOWAIT_SAMPLE_COUNT",
    "ALERT_ZOMBIE_MIN_COUNT",
    "ALERT_SERVICE_FLAP_WINDOW_MINUTES",
    "ALERT_SERVICE_FLAP_MIN_STATE_CHANGES",
    "ALERT_SERVICE_FLAP_STABILITY_MINUTES",
    "ALERT_OOM_JOURNAL_SINCE",
]

_BACKENDS: list[str] = ["ollama", "deepseek", "openai", "claude", "google"]

_MODEL_HINTS: dict[str, str] = {
    "deepseek": "deepseek-chat, deepseek-reasoner",
    "openai": "gpt-4o, gpt-4o-mini, o1, o3-mini",
    "claude": "claude-opus-4-5, claude-sonnet-4-5, claude-haiku-4-5",
    "google": "gemini-2.0-flash, gemini-1.5-pro",
}

_CLOUD_KEYS: dict[str, tuple[str, str]] = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL"),
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
    "google": ("GOOGLE_API_KEY", "GOOGLE_MODEL"),
}

_SCREEN3_FIELDS: list[tuple[str, str, Any, str]] = [
    ("ALERT_DISK_WARNING_PERCENT", "Disk warning %", 80.0, "float"),
    ("ALERT_DISK_CRITICAL_PERCENT", "Disk critical %", 95.0, "float"),
    ("ALERT_MEMORY_FREE_WARNING_PERCENT", "Memory free warning %", 10.0, "float"),
    ("ALERT_MEMORY_FREE_CRITICAL_PERCENT", "Memory free critical %", 5.0, "float"),
    ("ALERT_SWAP_USED_WARNING_PERCENT", "Swap used warning %", 80.0, "float"),
    ("ALERT_CPU_SUSTAINED_THRESHOLD_PERCENT", "CPU sustained %", 80.0, "float"),
    ("ALERT_CPU_SUSTAINED_SAMPLE_COUNT", "CPU sustained samples", 3, "int"),
    ("ALERT_IOWAIT_THRESHOLD_PERCENT", "IO wait %", 20.0, "float"),
    ("ALERT_IOWAIT_SAMPLE_COUNT", "IO wait samples", 3, "int"),
    ("ALERT_ZOMBIE_MIN_COUNT", "Zombie min count", 5, "int"),
    ("ALERT_SERVICE_FLAP_WINDOW_MINUTES", "Flap window (min)", 5, "int"),
    ("ALERT_SERVICE_FLAP_MIN_STATE_CHANGES", "Flap min changes", 3, "int"),
    ("ALERT_SERVICE_FLAP_STABILITY_MINUTES", "Flap stability (min)", 2, "int"),
    ("ALERT_OOM_JOURNAL_SINCE", "OOM journal since", "2 minutes ago", "str"),
]

_N_FOCUS = 21   # 5 radios + 2 backend fields + 14 threshold fields
_S2_WIDTH = 50  # display width for backend setting fields
_S3_WIDTH = 30  # display width for threshold fields


def _make_buffer(initial_value: str) -> Buffer:
    """Create a prompt_toolkit Buffer pre-loaded with *initial_value*."""
    buf = Buffer(name=f"field_{id(initial_value)}")
    buf.set_document(Document(initial_value, len(initial_value)))
    return buf


def _load_merged_settings() -> dict[str, Any]:
    """Return baseline settings overwritten by existing ``kerneliq.toml``."""
    out = dict(_BASELINE)
    path = _KERNELIQ_TOML
    if not path.is_file():
        return out
    with path.open("rb") as f:
        data = tomllib.load(f)
    blob = data.get("kerneliq", data)
    if isinstance(blob, dict):
        for key, val in blob.items():
            if key in out:
                out[key] = val
    mb = str(out.get("MODEL_BACKEND") or "ollama").strip().lower()
    if mb in _BACKENDS:
        out["MODEL_BACKEND"] = mb
    else:
        out["MODEL_BACKEND"] = "ollama"
    return out


def _format_toml_value(key: str, val: Any) -> str:
    """Emit a single ``KEY = value`` fragment (value only, no key)."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int) and not isinstance(val, bool):
        return str(val)
    if isinstance(val, float):
        return repr(val)
    s = str(val)
    esc = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{esc}"'


def write_kerneliq_toml(settings: dict[str, Any]) -> None:
    """Write ``kerneliq.toml`` with every key in ``_WRITE_ORDER``."""
    lines = [_TOML_HEADER.rstrip("\n")]
    for key in _WRITE_ORDER:
        val = settings.get(key, _BASELINE[key])
        if key == "MODEL_BACKEND":
            lines.append("# ollama | deepseek | openai | claude | google")
        if key == "DEEPSEEK_API_KEY":
            lines.append(
                "# Cloud: set the pair for the backend you use "
                "(empty *_MODEL → default in model/backends.py)."
            )
        lines.append(f"{key} = {_format_toml_value(key, val)}")
    text = "\n".join(lines) + "\n"
    _KERNELIQ_TOML.write_text(text, encoding="utf-8")


def _backend_index(backend: str) -> int:
    """Return the index of *backend* in ``_BACKENDS``, defaulting to 0."""
    b = str(backend or "ollama").strip().lower()
    try:
        return _BACKENDS.index(b)
    except ValueError:
        return 0


def _screen2_field_defs(backend: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows for screen 2: key, label, value, hint (optional)."""
    b = str(backend).strip().lower()
    if b == "ollama":
        url = str(settings.get("OLLAMA_BASE_URL") or "")
        mdl = str(settings.get("LOCAL_LLM_MODEL") or "")
        return [
            {
                "key": "OLLAMA_BASE_URL",
                "label": "Ollama Host URL",
                "buffer": _make_buffer(url),
                "hint": None,
            },
            {
                "key": "LOCAL_LLM_MODEL",
                "label": "Local Model Name",
                "buffer": _make_buffer(mdl),
                "hint": None,
            },
        ]
    api_k, model_k = _CLOUD_KEYS[b]
    hint = _MODEL_HINTS.get(b)
    ak = str(settings.get(api_k) or "")
    mk = str(settings.get(model_k) or "")
    return [
        {
            "key": api_k,
            "label": "API Key",
            "buffer": _make_buffer(ak),
            "hint": None,
        },
        {
            "key": model_k,
            "label": "Model Name",
            "buffer": _make_buffer(mk),
            "hint": hint,
        },
    ]


def _run_wizard_app() -> str | None:
    """Build and run the prompt_toolkit full-screen wizard.

    Returns ``'saved'``, ``'quit'``, ``'error:...'``, or ``None``.
    """
    settings = _load_merged_settings()
    sel = _backend_index(str(settings.get("MODEL_BACKEND")))

    state: dict[str, Any] = {
        "selected_backend": sel,
        "focus": sel,
        "settings": settings,
        "s2_fields": _screen2_field_defs(_BACKENDS[sel], settings),
        "s3_fields": [
            {
                "key": k,
                "label": lbl,
                "buffer": _make_buffer(str(settings.get(k, dflt))),
                "typ": typ,
                "default": dflt,
            }
            for k, lbl, dflt, typ in _SCREEN3_FIELDS
        ],
        "scroll_offset": 0,
        "confirming_exit": False,
    }

    def _get_fd() -> dict[str, Any]:
        """Return the field dict for the currently focused input."""
        foc = state["focus"]
        if foc < 7:
            return state["s2_fields"][foc - 5]
        return state["s3_fields"][foc - 7]

    def _select_backend() -> None:
        """Set selected_backend = focus and rebuild s2_fields if backend changed."""
        foc = state["focus"]
        old = state["selected_backend"]
        state["selected_backend"] = foc
        if _BACKENDS[foc] != _BACKENDS[old]:
            state["s2_fields"] = _screen2_field_defs(_BACKENDS[foc], state["settings"])

    def _commit_and_exit(event: Any) -> None:
        """Persist all field values to kerneliq.toml and exit the app."""
        s = state["settings"]
        for fd in state["s2_fields"]:
            s[fd["key"]] = fd["buffer"].text
        s["MODEL_BACKEND"] = _BACKENDS[state["selected_backend"]]
        for fd in state["s3_fields"]:
            t, raw, d = fd["typ"], fd["buffer"].text.strip(), fd["default"]
            if t == "int":
                try:
                    s[fd["key"]] = int(raw)
                except ValueError:
                    s[fd["key"]] = int(d)
            elif t == "float":
                try:
                    s[fd["key"]] = float(raw)
                except ValueError:
                    s[fd["key"]] = float(d)
            else:
                s[fd["key"]] = raw if raw else str(d)
        s["MODEL_BACKEND"] = str(s.get("MODEL_BACKEND") or "ollama").lower()
        merged = dict(_BASELINE)
        merged.update(s)
        try:
            write_kerneliq_toml(merged)
        except OSError as exc:
            event.app.exit(result=f"error:{exc}")
            return
        event.app.exit(result="saved")

    def render() -> list[tuple[str, str]]:
        """Build the visible wizard page as styled text fragments.

        Each logical line is a list of (style, text) fragments so input
        fields can render the cursor block in reverse video.  Content
        lines are accumulated first, then scroll logic selects the
        visible slice.  The hint bar is always appended after the slice.
        """
        all_lines: list[list[tuple[str, str]]] = []
        sel = state["selected_backend"]
        foc = state["focus"]
        focus_line = 0

        def add(cls: str, text: str) -> None:
            all_lines.append([(cls, text)])

        def add_multi(frags: list[tuple[str, str]]) -> None:
            all_lines.append(frags)

        def blank(n: int = 1) -> None:
            for _ in range(n):
                all_lines.append([("", "\n")])

        def field_box(fd: dict[str, Any], gf: int, width: int) -> None:
            """Render a bordered input box for a field."""
            nonlocal focus_line
            buf = fd["buffer"]
            val = buf.text
            cursor = buf.cursor_position
            focused = gf == foc

            box_style = "class:field-box-focused" if focused else "class:field-box"
            txt_style = "class:field-text-focused" if focused else "class:field-text"

            inner = width - 4

            if cursor <= inner - 1:
                h_off = 0
            else:
                h_off = cursor - inner + 1

            raw = val + " " * max(0, inner - len(val))
            segment = raw[h_off : h_off + inner]
            vis_cursor = cursor - h_off

            top = "\u250c" + "\u2500" * (width - 2) + "\u2510\n"
            if focused:
                focus_line = len(all_lines)
            add(box_style, "  " + top)

            if focused:
                before = segment[:vis_cursor]
                at_char = (
                    segment[vis_cursor : vis_cursor + 1]
                    if vis_cursor < len(segment)
                    else " "
                )
                after = segment[vis_cursor + 1 :]
                add_multi([
                    (box_style, "  \u2502 "),
                    (txt_style, before),
                    ("class:cursor", at_char),
                    (txt_style, after),
                    (box_style, " \u2502\n"),
                ])
            else:
                padded = segment[:inner].ljust(inner)
                add_multi([
                    (box_style, "  \u2502 "),
                    (txt_style, padded),
                    (box_style, " \u2502\n"),
                ])

            bot = "\u2514" + "\u2500" * (width - 2) + "\u2518\n"
            add(box_style, "  " + bot)

        # ── Title bar ────────────────────────────────────────────
        add("class:header", "  \u25c6 KernelIQ Config\n")
        add("class:divider", "  " + "\u2500" * 40 + "\n")
        blank()

        # ── Step 1 — backend radios ───────────────────────────────
        add("class:step-label", "  STEP 1 \u2014 MODEL BACKEND\n")
        blank()
        for i, name in enumerate(_BACKENDS):
            if i == sel and i == foc:
                focus_line = len(all_lines)
                add("class:selected", f"  \u25cf {name}\n")
            elif i == sel:
                add("class:selected", f"  \u25cf {name}\n")
            elif i == foc:
                focus_line = len(all_lines)
                add("class:focused", f"  \u25b6 {name}\n")
            else:
                add("class:normal", f"  \u25cb {name}\n")
        blank()
        add("class:divider", "  " + "\u2500" * 40 + "\n")
        blank()

        # ── Step 2 — backend settings ─────────────────────────────
        add("class:step-label",
            f"  STEP 2 \u2014 BACKEND SETTINGS  [{_BACKENDS[sel].upper()}]\n")
        blank()
        for fi, fd in enumerate(state["s2_fields"]):
            gf = 5 + fi
            focused = gf == foc
            lbl_style = "class:field-box-focused" if focused else "class:field-label"
            add(lbl_style, f"  {fd['label']}\n")
            field_box(fd, gf, _S2_WIDTH)
            if fd.get("hint"):
                add("class:dim", f"    e.g. {fd['hint']}\n")
            blank()

        add("class:divider", "  " + "\u2500" * 40 + "\n")
        blank()

        # ── Step 3 — alert thresholds ─────────────────────────────
        add("class:step-label", "  STEP 3 \u2014 ALERT THRESHOLDS\n")
        blank()
        for fi, fd in enumerate(state["s3_fields"]):
            gf = 7 + fi
            focused = gf == foc
            lbl_style = "class:field-box-focused" if focused else "class:field-label"
            add(lbl_style, f"  {fd['label']}\n")
            field_box(fd, gf, _S3_WIDTH)
            blank()

        add("class:divider", "  " + "\u2500" * 40 + "\n")

        # ── Scroll logic ──────────────────────────────────────────
        try:
            h = get_app().output.get_size().rows - 2
        except Exception:
            h = 40
        h = max(5, h)

        so = state["scroll_offset"]
        if focus_line < so:
            so = focus_line
        if focus_line >= so + h:
            so = focus_line - h + 1
        so = max(0, so)
        state["scroll_offset"] = so

        visible = all_lines[so : so + h]

        result: list[tuple[str, str]] = []
        for frags in visible:
            result.extend(frags)

        # Hint bar pinned at bottom
        if state["confirming_exit"]:
            result.append((
                "class:hint-bar",
                "  Save before closing? [s]ave  [q]uit  [c]ancel \n",
            ))
        else:
            result.append((
                "class:hint-bar",
                "  Tab/\u2191\u2193 navigate   Space/Enter select   "
                "\u2190\u2192 Home End   Ctrl+K/U   Ctrl+V paste   Esc exit \n",
            ))

        return result

    # ── Key bindings ──────────────────────────────────────────────
    kb = KeyBindings()

    confirming     = Condition(lambda: state["confirming_exit"])
    not_confirming = Condition(lambda: not state["confirming_exit"])
    in_radio = Condition(lambda: not state["confirming_exit"] and state["focus"] < 5)
    in_input = Condition(lambda: not state["confirming_exit"] and state["focus"] >= 5)

    @kb.add("tab", filter=not_confirming)
    @kb.add("down", filter=not_confirming)
    def _next(event: Any) -> None:
        state["focus"] = (state["focus"] + 1) % _N_FOCUS

    @kb.add("s-tab", filter=not_confirming)
    @kb.add("up", filter=not_confirming)
    def _prev(event: Any) -> None:
        state["focus"] = (state["focus"] - 1) % _N_FOCUS

    @kb.add("c-c")
    @kb.add("c-q")
    def _ctrl_quit(event: Any) -> None:
        event.app.exit(result="quit")

    @kb.add("escape", filter=not_confirming)
    def _esc_open(event: Any) -> None:
        state["confirming_exit"] = True

    @kb.add("escape", filter=confirming)
    @kb.add("c", filter=confirming)
    @kb.add("C", filter=confirming)
    def _esc_cancel(event: Any) -> None:
        state["confirming_exit"] = False

    @kb.add("s", filter=confirming)
    @kb.add("S", filter=confirming)
    def _confirm_save(event: Any) -> None:
        _commit_and_exit(event)

    @kb.add("q", filter=confirming)
    @kb.add("Q", filter=confirming)
    def _confirm_quit(event: Any) -> None:
        event.app.exit(result="quit")

    @kb.add("q", filter=in_radio)
    @kb.add("Q", filter=in_radio)
    def _quit_radio(event: Any) -> None:
        event.app.exit(result="quit")

    @kb.add("enter", filter=not_confirming)
    def _enter(event: Any) -> None:
        foc = state["focus"]
        if foc < 5:
            _select_backend()
        elif foc < 20:
            state["focus"] += 1
        else:
            _commit_and_exit(event)

    @kb.add("space", filter=in_radio)
    def _space(event: Any) -> None:
        _select_backend()

    @kb.add("backspace", filter=in_input)
    def _bs(event: Any) -> None:
        _get_fd()["buffer"].delete_before_cursor(1)

    @kb.add("delete", filter=in_input)
    def _del(event: Any) -> None:
        _get_fd()["buffer"].delete(1)

    @kb.add("left", filter=in_input)
    def _left(event: Any) -> None:
        _get_fd()["buffer"].cursor_left()

    @kb.add("right", filter=in_input)
    def _right(event: Any) -> None:
        _get_fd()["buffer"].cursor_right()

    @kb.add("home", filter=in_input)
    @kb.add("c-a", filter=in_input)
    def _home(event: Any) -> None:
        _get_fd()["buffer"].cursor_position = 0

    @kb.add("end", filter=in_input)
    @kb.add("c-e", filter=in_input)
    def _end(event: Any) -> None:
        buf = _get_fd()["buffer"]
        buf.cursor_position = len(buf.text)

    @kb.add("c-k", filter=in_input)
    def _kill_line(event: Any) -> None:
        buf = _get_fd()["buffer"]
        buf.delete(len(buf.text) - buf.cursor_position)

    @kb.add("c-u", filter=in_input)
    def _kill_to_start(event: Any) -> None:
        buf = _get_fd()["buffer"]
        buf.delete_before_cursor(buf.cursor_position)

    @kb.add("c-v", filter=in_input)
    def _paste(event: Any) -> None:
        text = None

        try:
            import pyperclip  # type: ignore
            text = pyperclip.paste()
        except Exception:
            pass

        if not text:
            import subprocess
            for cmd in (
                ["wl-paste", "--no-newline"],
                ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"],
            ):
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if result.returncode == 0 and result.stdout:
                        text = result.stdout
                        break
                except Exception:
                    continue

        if text:
            text = text.replace("\n", " ").replace("\r", "")
            _get_fd()["buffer"].insert_text(text)

    @kb.add(Keys.BracketedPaste, filter=in_input)
    def _bracketed_paste(event: Any) -> None:
        text = event.data or ""
        text = text.replace("\n", " ").replace("\r", "")
        if text:
            _get_fd()["buffer"].insert_text(text)

    @kb.add("<any>", filter=in_input)
    def _typechar(event: Any) -> None:
        key = event.key_sequence[0].key
        if isinstance(key, str) and len(key) == 1 and key.isprintable():
            _get_fd()["buffer"].insert_text(key)

    layout = Layout(
        HSplit([
            Window(
                content=FormattedTextControl(render, focusable=False),
                dont_extend_height=False,
            ),
        ])
    )

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        mouse_support=False,
        style=Style.from_dict({
            "":                  "bg:#0d1117 fg:#e6edf3",
            "header":            "bold fg:#58a6ff",
            "step-label":        "bold fg:#f0f6fc bg:#161b22",
            "selected":          "bold fg:#3fb950",
            "focused":           "bold fg:#f0f6fc",
            "normal":            "fg:#8b949e",
            "dim":               "italic fg:#6e7681",
            "field-label":       "fg:#8b949e",
            "field-box":         "fg:#30363d",
            "field-box-focused": "fg:#58a6ff",
            "field-text":        "fg:#e6edf3",
            "field-text-focused":"bold fg:#f0f6fc",
            "cursor":            "reverse fg:#f0f6fc",
            "divider":           "fg:#21262d",
            "hint-bar":          "bold fg:#f0f6fc bg:#161b22",
            "section-gap":       "",
        }),
    )
    return app.run()


def run_config_wizard() -> str:
    """Launch the interactive config wizard and return a human-readable result message."""
    try:
        result = _run_wizard_app()
    except Exception as e:
        return f"Config wizard error: {e}"
    if result == "saved":
        return (
            f"\033[1m• Wrote {_KERNELIQ_TOML}\n"
            "• Restart the daemon and REPL to apply:\n"
            "  systemctl --user restart kerneliq-daemon\033[0m"
        )
    if result == "quit":
        return "Config wizard closed without saving."
    if isinstance(result, str) and result.startswith("error:"):
        return f"Could not save config: {result[6:]}"
    return "Config wizard finished."
