# Changelog

All notable changes to KernelIQ will be documented here.

---

## [0.1.0](https://github.com/abhinayshrestha/kernelIQ/releases/tag/v0.1.0) — 2026-04-10

Initial public release.

### Added

**Daemon**

- Background telemetry daemon collecting system state every 60 seconds into local SQLite
- CPU percent overall and per-core, load averages (1m / 5m / 15m), iowait
- Memory total, used, available, buffers, cached, swap
- PSI pressure metrics — CPU, memory, and IO (some and full, avg10)
- Page fault rate per interval (minor and major)
- Disk usage per mount and disk IO per device
- Network IO per interface and connection counts by state (ESTABLISHED, SYN_RECV, TIME_WAIT, CLOSE_WAIT)
- Per-process sampling — PID, name, command line, CPU %, RSS memory, state, cumulative IO bytes
- systemd service state sampling — active state and sub state per service
- Temperature sensors via psutil
- GPU metrics via nvidia-smi / rocm-smi when available
- Deterministic threshold-based alerter with native desktop notifications (libnotify)
- Nine alert types: CPU_SUSTAINED, MEMORY_CRITICAL, DISK_CRITICAL, SWAP_HIGH, IOWAIT_HIGH, OOM_KILL, SERVICE_FAILED, SERVICE_FLAPPING, ZOMBIE_ACCUMULATION
- Daily retention timer via systemd user unit (kerneliq-retention.timer)

**Investigation engine**

- Natural language investigation loop — ask questions, get structured diagnoses
- Sliding window context architecture — last 3 steps in full, older steps in early summary, bounded token growth regardless of investigation length
- Deterministic output condensation per tool type (ps, iostat, journalctl, systemctl list-units) — no LLM calls for condensation
- SQL result formatter with schema-aware column alignment and human-readable output
- Command deduplication — same command blocked from running twice in one investigation
- Max 10 investigation steps per question with forced diagnosis at limit
- Structured DIAGNOSIS output: Observation / Evidence / Action / Confidence / Command

**Safety enforcement**

- Hardcoded command whitelist — read-only shell commands only, runs automatically
- SQL validator — blocks DROP, DELETE, UPDATE, ALTER, TRUNCATE unconditionally
- Three-tier permission model — read-only auto, SQL validated, destructive requires explicit [y/N] confirmation
- Model output parser — malformed responses logged and handled without execution

**Multi-backend LLM client**

- Ollama (local inference)
- DeepSeek API
- OpenAI API
- Anthropic Claude API
- Google Gemini API
- Single config line to switch backends

**REPL**

- Interactive terminal REPL with prompt_toolkit
- System health banner on startup — CPU, RAM, disk, IO, daemon status, backend status
- `!status` — live system health summary
- `!alerts` — unresolved alerts with severity
- `!alerts --summarize` — LLM health briefing across all alerts
- `!fix` — investigate and resolve alerts one by one
- `!history` — last 10 diagnoses with confidence scores
- `!last` — most recent diagnosis
- `!correct` / `!wrong` / `!partial` — label last diagnosis for training dataset
- `!dataset` — training dataset statistics
- `!config` — full-screen TUI config wizard with Buffer-based text input, cursor navigation, bracketed paste support
- `Generating...` indicator between investigation steps

**Install**

- `install.sh` — one-command install with automatic venv creation
- Handles Ubuntu `python3.X-venv` package requirement automatically with apt fallback
- pip bootstrap via ensurepip → get-pip.py → apt fallback chain
- Writes user systemd units with correct repo paths
- Auto-creates `kerneliq.toml` from example
- Appends `kerneliq` alias to `~/.bashrc`
- Launches REPL immediately via `exec` after install completes
- `scripts/bootstrap.sh` — one-liner curl install from any machine

**Documentation**

- README with architecture diagram, demo output, configuration guide, model quality guide, known limitations, roadmap
- SECURITY.md — threat model, data collection details, what may leave the machine, vulnerability reporting
- CONTRIBUTING.md — development setup, project structure, design principles, training data contribution guide
- CHANGELOG.md

### Known limitations in this release

- Telemetry collection interval is 60 seconds — configurable interval coming in next release
- `llama3.2:3b` local model hallucinates — use `llama3.1:8b` minimum for local inference
- No streaming output — REPL waits for full LLM response before displaying
- Tested on Ubuntu 24.04 (aarch64 and x86_64) — other distributions should work but are unverified
- Specialist 3B model not yet available — uses configured backend

---

## Unreleased

### Planned for 0.2.0

- Configurable telemetry collection interval
- Streaming diagnosis output in the REPL
- Tighter context management to further bound prompt growth
- Better install defaults and startup behavior

### Planned for 0.3.0

- Deep investigation mode — extended step limit with kernel-specific tools
- Web UI alongside terminal REPL
- Multi-machine investigation support

### Long term

- KernelIQ specialist 3B model — fine-tuned on Linux investigation traces
- GGUF inference via llama.cpp

---

