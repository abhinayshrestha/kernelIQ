# Contributing to KernelIQ

Welcome, and thank you so much for considering contributing to KernelIQ! Whether you're here to report a bug, suggest a feature, improve documentation, submit a fix, or share labeled investigation traces, every contribution is genuinely appreciated and helps this project grow.

KernelIQ is still in its early days, and your involvement — no matter how small — makes a real difference.

---

## Code of Conduct

We want this to be a kind and welcoming space for everyone. Please be respectful, patient, and constructive in all interactions. This project is currently maintained by a single person in their spare time, so a little kindness goes a long way. We're all here because we care about building something useful together.

---

## Ways to Contribute

There are many ways to get involved, and all of them are valued:


| Type                     | How                                                                        |
| ------------------------ | -------------------------------------------------------------------------- |
| Report a bug             | Open a GitHub issue with steps to reproduce — this helps tremendously      |
| Fix a bug                | Open a PR against `main` with a clear description of the fix               |
| Suggest a new feature    | Please open an issue first so we can discuss the idea together             |
| Improve documentation    | PRs for the README, docstrings, or new guides are always appreciated       |
| Contribute training data | Run investigations, label them, and help improve the model — details below |
| Test on new distros      | Try KernelIQ on your distribution and share your results in an issue       |


---

## Getting Started with Development

Setting up a local development environment is straightforward. If you run into any issues along the way, please don't hesitate to open an issue — we're happy to help.

### Prerequisites

- Ubuntu 24.04 is recommended, though other Linux distributions may work as well
- Python 3.11 or later
- Git
- An LLM backend — either Ollama running locally, or a cloud API key (DeepSeek, OpenAI, Claude, or Gemini)

### Clone and install

```bash
# Fork the repo on GitHub first, then:
git clone https://github.com/abhinayshrestha/kernelIQ.git ~/kernelIQ
cd ~/kernelIQ

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .

# Copy the example config and customize it for your setup
cp kerneliq.toml.example kerneliq.toml
nano kerneliq.toml
```

### Running the daemon during development

For development, it's easiest to run the daemon in the foreground so you can see its output in real time:

```bash
python -m daemon.loop
```

Then, in a separate terminal, launch the interactive REPL:

```bash
kerneliq
```

### Handy development commands

These commands are useful while working on KernelIQ:

```bash
# Check whether the daemon is running
systemctl --user status kerneliq-daemon

# Watch telemetry being written to the database in real time
watch -n 5 'sqlite3 ~/kernelIQ/kerneliq.db \
  "SELECT timestamp, cpu_percent, mem_used_mb FROM telemetry_samples \
   ORDER BY timestamp DESC LIMIT 5;"'

# Browse the database interactively with datasette
datasette kerneliq.db --host 0.0.0.0 --port 8001

# Follow the daemon logs
journalctl --user -u kerneliq-daemon -f

# Launch the config wizard from the REPL
kerneliq
# then type: !config
```

---

## Project Structure

Here's an overview of how the codebase is organized to help you find your way around:

```
kernelIQ/
├── daemon/              # Telemetry collection loop
│   ├── loop.py          # Main daemon entry point
│   ├── collector.py     # System metrics collection
│   ├── process_sampler.py
│   ├── service_sampler.py
│   └── alerter.py       # Threshold-based alert detection
│
├── executor/            # Safety enforcement — all commands pass through here
│   ├── whitelist.py     # Allowed command list
│   ├── validator.py     # Command and SQL validation
│   ├── runner.py        # Shell execution
│   └── sql_runner.py    # SQLite execution and result formatting
│
├── model/               # LLM investigation engine
│   ├── investigation.py # Main investigation loop
│   ├── context_window.py
│   ├── condenser.py     # Deterministic output condensation
│   ├── summarizer.py
│   ├── client.py        # Multi-backend LLM client
│   └── system_prompt.py
│
├── shell/               # REPL and terminal UI
│   ├── repl.py
│   ├── banner.py
│   ├── renderer.py
│   ├── streamer.py
│   ├── confirm.py
│   └── config_wizard.py
│
├── db/                  # Database schema and helpers
│   ├── schema.py
│   ├── helpers.py
│   └── retention.py
│
└── dataset/             # Training data pipeline
    ├── logger.py
    ├── rebuild.py
    └── generator.py
```

### Design principles we'd love you to keep in mind

These principles are central to how KernelIQ works. Understanding them will make contributing smoother and help your PRs get merged faster:

- **The model never directly executes anything.** Every command flows through `executor/` and its validators. Please avoid adding code paths that bypass this — it's the foundation of KernelIQ's safety model.
- **Context growth is bounded.** The sliding window in `model/context_window.py` keeps token usage flat regardless of how long an investigation runs. Please avoid unbounded list appends to the message history.
- **Condensation is deterministic.** `model/condenser.py` contains no LLM calls by design. Condensation needs to be instant and free, so please help us keep it that way.
- **Safety is enforced in code, not in prompts.** The command whitelist and SQL validator are hard-coded safeguards. We never rely on prompt instructions alone to restrict what the model can do.

---

## Submitting Changes

### Before you open a PR

We'd really appreciate it if you could run through these quick checks before submitting:

1. **Test your change manually** — run a real investigation and confirm everything works as expected
2. **Verify the daemon starts cleanly** — `systemctl --user restart kerneliq-daemon`
3. **Confirm the REPL launches** — run `kerneliq` and make sure the banner appears correctly
4. **If you touched safety-related code** (`executor/whitelist.py` or `executor/validator.py`), please double-check that blocked commands are still properly blocked

### PR guidelines

- **One logical change per PR.** If you have multiple unrelated fixes, we'd be grateful if you could split them into separate PRs — it makes review much easier.
- **Tell us the "what" and the "why."** A good PR description explains the problem being solved, not just the code that changed.
- **Keep changes focused.** A PR that touches many files for a small fix can be hard to review. If it's getting large, consider breaking it up.
- **Please preserve the safety model.** PRs that weaken the whitelist, validator, or user-confirmation requirement unfortunately cannot be merged — safety is non-negotiable in this project.

### Branch naming

We use a simple naming convention:

```
feature/short-description
fix/short-description
docs/short-description
refactor/short-description
```

### Commit style

Clear, lowercase imperative messages work best:

```
fix: handle missing kerneliq.toml gracefully
feat: add swap pressure to telemetry samples
docs: add uninstall instructions to README
refactor: simplify context window eviction logic
```

---

## Contributing Training Data

This is one of the most impactful contributions you can make right now, and we would be truly grateful for it.

Every time you use KernelIQ and receive a diagnosis, you can label it directly from inside the REPL:

```
kerneliq> !correct    # the diagnosis was accurate and the suggested fix worked
kerneliq> !wrong      # the diagnosis was incorrect or the fix made things worse
kerneliq> !partial    # the diagnosis was partially right
```

Labels are stored locally in `~/kernelIQ/kerneliq.db`. You can review them anytime:

```bash
kerneliq
# then type: !dataset
```

If you'd like to share your labeled traces to help build the specialist model training dataset, please open an issue titled **"Training data contribution"** and we'll work together on a safe export process that strips any sensitive process names or file paths before sharing. Your privacy matters.

### What makes a great training trace

- A real system problem (not a simulated one)
- At least 3 investigation steps before the final diagnosis
- A confident final diagnosis (80%+)
- An honest label — please only use `!correct` if you've verified the fix actually resolved the issue

---

## Reporting Bugs

Found something broken? We'd love to hear about it so we can fix it. Please open a GitHub issue with the following:

1. **What you did** — the exact question you typed into the REPL
2. **What happened** — the full REPL output, copy-pasted
3. **What you expected** — what a correct diagnosis would have looked like
4. **Your environment** — OS version, Python version, LLM backend and model name

Including the output from these commands in your report is very helpful:

```bash
python3 --version
cat ~/kernelIQ/kerneliq.toml | grep MODEL_BACKEND
systemctl --user status kerneliq-daemon
journalctl --user -u kerneliq-daemon --since "10 minutes ago"
```

If you've discovered a security vulnerability, please do not open a public issue. Instead, see [SECURITY.md](SECURITY.md) for responsible disclosure instructions. Thank you for helping keep KernelIQ safe.

---

## Feature Requests

Have an idea for something new? We'd love to hear it! Please open an issue with the **enhancement** label and include:

- The problem you're trying to solve
- How you'd envision it working
- Whether you'd be interested in implementing it yourself (no pressure either way!)

Features that align with the core investigation loop, the safety model, or the training pipeline are the most likely to be prioritized. We generally avoid features that introduce heavy dependencies, weaken safety enforcement, or require external cloud infrastructure — but we're always open to discussing ideas.

---

## Questions

If you have questions about the project, how something works, or where to get started, please feel free to open a GitHub Discussion or an issue labeled **question**. There are no silly questions here.

Response time is best-effort since this is currently a one-person project, but every question will be read and answered. Your patience is appreciated.

---

*Thank you for being part of KernelIQ. Every contribution, big or small, helps make Linux diagnosis better for everyone.*