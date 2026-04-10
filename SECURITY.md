# Security Policy

KernelIQ runs a persistent background daemon, reads system state, queries kernel journals, and can propose actions that affect running processes and services. This document explains the trust model, what data is collected, what may leave your machine, and how to report vulnerabilities.

---

## Supported Versions


| Version         | Supported |
| --------------- | --------- |
| 0.1.x (current) | ✅ Active  |


---

## Intended Use and Environment

KernelIQ is designed for **personal workstations and development machines**. It is not hardened for multi-user servers, shared infrastructure, or production environments.

**Appropriate use:**

- Personal Linux workstations
- Developer laptops and VMs
- Home lab machines
- Single-user Ubuntu desktops

**Not recommended for:**

- Production servers or shared hosts
- Machines with untrusted local users
- Environments with strict data residency requirements when using cloud backends
- Any system where the `kerneliq` user account should not have read access to journals and process state

---

## Threat Model

### What KernelIQ can do

The daemon runs as the current user — not root. It can read anything the user can read. It cannot access files or system state that require elevated privileges.


| Capability                            | Notes                                                       |
| ------------------------------------- | ----------------------------------------------------------- |
| Read `/proc` for process state        | Standard user access                                        |
| Read systemd journal via `journalctl` | User journal only unless user is in `systemd-journal` group |
| Read disk usage, network connections  | Via `psutil` — user-level                                   |
| Write to local SQLite database        | `~/kernelIQ/kerneliq.db` only                               |
| Propose shell commands                | Never executed without explicit `[y/N]` confirmation        |
| Run allowed read-only commands        | Whitelist-enforced — see below                              |


### What KernelIQ cannot do

- Run commands as root
- Access files outside user permissions
- Execute any command without your explicit confirmation
- Modify system configuration autonomously
- Send telemetry to any Anthropic or third-party server

### Attack surface


| Vector                                          | Risk   | Mitigation                                                                            |
| ----------------------------------------------- | ------ | ------------------------------------------------------------------------------------- |
| Malicious LLM output proposing harmful commands | Low    | Deterministic whitelist + explicit user confirmation required for all action commands |
| SQL injection via LLM-generated queries         | Low    | SQL validator blocks all non-SELECT statements except controlled INSERT               |
| Daemon process compromise                       | Low    | Runs as current user with no elevated privileges                                      |
| Config file with malicious API endpoint         | Medium | Only configure `OLLAMA_BASE_URL` to hosts you control                                 |
| Prompt injection via system logs                | Low    | Journal content is passed as evidence context, not as executable instructions         |


---

## Data Collection

### What is stored locally

The daemon writes the following to `~/kernelIQ/kerneliq.db` on your machine:


| Table                | Contents                                                                                                          |
| -------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `telemetry_samples`  | CPU %, memory, disk usage, network connection counts, PSI pressure, temperatures — no file contents, no user data |
| `process_samples`    | PID, process name, command line, CPU %, memory — same as `ps aux`                                                 |
| `service_samples`    | systemd service names and states — same as `systemctl list-units`                                                 |
| `investigation_logs` | Every command run and SQL query during investigations, with output summaries                                      |
| `diagnoses`          | Final diagnosis text, confidence scores, proposed actions                                                         |
| `alerts`             | Triggered alert type, severity, evidence summary, timestamp                                                       |


### What is never stored

- File contents
- Network packet data or payloads
- Passwords, secrets, or environment variables
- SSH keys or credential files
- User home directory listings

### Database location and retention

All data is stored in `~/kernelIQ/kerneliq.db`. A daily retention job (`kerneliq-retention.timer`) prunes old records. Default retention is 30 days. No data is sent anywhere automatically.

---

## What May Leave Your Machine

This depends entirely on which LLM backend you configure. **If you use a local Ollama model, nothing leaves your machine.**

### Cloud backends

When you use a cloud API backend (DeepSeek, OpenAI, Anthropic, Google), the following is sent to that provider during an investigation:

- The system prompt (investigation instructions and database schema — no personal data)
- The user's question as typed
- Condensed summaries of command output (e.g. top 5 processes by CPU, service states)
- Condensed SQL query results (e.g. last 5 telemetry rows)
- The model's own intermediate reasoning steps

### What is NOT sent to cloud backends

- Raw journal logs beyond condensed keyword-matched lines
- Full process lists — only condensed top-N summaries
- File contents of any kind
- Historical telemetry beyond what fits in the sliding context window
- API keys or secrets from your config file

### Backend data handling

KernelIQ has no control over how your chosen cloud provider handles data once received. Review the privacy policy of your configured provider:

- [DeepSeek Privacy Policy](https://www.deepseek.com/privacy)
- [OpenAI Privacy Policy](https://openai.com/policies/privacy-policy)
- [Anthropic Privacy Policy](https://www.anthropic.com/privacy)
- [Google AI Privacy Policy](https://policies.google.com/privacy)

**For maximum privacy, use a local Ollama model.**

---

## Safety Enforcement

### Command whitelist

KernelIQ maintains a hardcoded whitelist of allowed shell commands. The model cannot execute commands outside this list regardless of what it outputs. The whitelist includes only read-only diagnostic tools:

`ps`, `top`, `pgrep`, `pidof`, `pstree`, `free`, `vmstat`, `df`, `du`, `lsblk`, `findmnt`, `iostat`, `stat`, `file`, `ss`, `netstat`, `lsof`, `ip`, `nslookup`, `dig`, `ping`, `traceroute`, `ls`, `cat`, `head`, `tail`, `find`, `wc`, `grep`, `awk`, `sort`, `uniq`, `journalctl`, `dmesg`, `last`, `lastb`, `who`, `w`, `uptime`, `uname`, `hostname`, `timedatectl`, `lscpu`, `lspci`, `lsusb`, `systemctl status`, `systemctl list-units`, `systemctl is-active`, `systemctl is-failed`, `systemctl show`, `nvidia-smi`, `rocm-smi`, `docker ps`, `docker stats`, `docker logs`, `docker inspect`, `docker images`

Action commands (`kill`, `systemctl restart`, `ionice`, `renice`, `rm`, `docker stop`) are proposed by the model but require your explicit `[y/N]` confirmation before execution. You can always type `n` and nothing will run.

### SQL validator

The SQL validator blocks all statements except `SELECT` and a controlled subset of `INSERT` used for logging. The following are blocked unconditionally regardless of model output:

`DROP`, `DELETE`, `UPDATE`, `ALTER`, `CREATE`, `TRUNCATE`, `ATTACH`, `PRAGMA` (write variants)

### Malformed model output

If the model returns output that does not match the expected `COMMAND:` / `SQL:` / `DIAGNOSIS:` format, the investigation loop logs the step and either retries with a correction message or terminates the investigation. No partial or malformed commands are executed.

---

## Reporting a Vulnerability

If you discover a security vulnerability in KernelIQ, please do not open a public GitHub issue.

**Report privately via GitHub Security Advisories:**

1. Go to the [KernelIQ repository](https://github.com/abhinayshrestha/kernelIQ)
2. Click **Security** → **Report a vulnerability**
3. Fill in the form with as much detail as possible

Please include:

- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix if you have one

You will receive a response within 7 days. Once the vulnerability is confirmed and patched, a security advisory will be published and you will be credited unless you prefer to remain anonymous.

---

## Secure Configuration Recommendations

```toml
[kerneliq]
# For maximum privacy — no data leaves your machine
MODEL_BACKEND = "ollama"
OLLAMA_BASE_URL = "http://localhost:11434"
LOCAL_LLM_MODEL = "llama3.1:8b"
```

If using a cloud backend:

- Use a dedicated API key for KernelIQ — do not reuse keys from other services
- Store `kerneliq.toml` with restricted permissions: `chmod 600 ~/kernelIQ/kerneliq.toml`
- Rotate your API key if you believe it was exposed

---

## Known Security Limitations

- **No audit log for confirmed actions.** When you confirm a destructive command, the action is logged in `investigation_logs` but there is no separate audit trail. Future versions will add a dedicated action log.
- **Journal access depends on group membership.** If your user is in the `systemd-journal` group, the daemon and REPL can read system-wide journal entries including logs from other users' processes.
- **Prompt injection via logs is theoretically possible.** If an attacker can write to a log file that KernelIQ reads via `journalctl`, they could attempt to inject instructions into the investigation context. The model is instructed to treat log content as evidence only, but this is a prompt-level control, not a code-level one.
- **API keys are stored in plaintext.** `kerneliq.toml` stores API keys as plaintext. Restrict file permissions and do not commit the file to version control (it is gitignored by default).

---

*Last updated: April 2026*