# Container SSH Access

How to land directly in the running `ai-claude` tmux session (with Claude Code attached) by SSHing into the roost container.

Three entry points all attach to the **same** tmux session and see each other:

| Entry point | How |
|---|---|
| **Browser** | `http://localhost:8080/terminal/` (ttyd, started by the container entrypoint) |
| **SSH from the host** | `ssh -p 2222 dev@127.0.0.1` |
| **SSH from a laptop** | `ssh -J dev@<host> -p 2222 dev@127.0.0.1` (ProxyJump through the host) |

The port 2222 binding is **loopback-only by default** — there is no public SSH surface unless you intentionally open it.

---

## TL;DR

Inside `docker-compose.override.yml` on the host:

```yaml
services:
  roost:
    ports: !override
      - "127.0.0.1:8090:8080"
      - "127.0.0.1:2222:22"
    cap_add:
      - AUDIT_WRITE
    volumes:
      - ./ssh/authorized_keys:/home/dev/.ssh/authorized_keys:ro
      - ./ssh/sshd_config.d:/etc/ssh/sshd_config.d:ro
      - ./ssh/bash_profile:/home/dev/.bash_profile:ro
```

Three host-side files under `roost/ssh/`:

```
ssh/
├── authorized_keys              # mirror of the host's ~/.ssh/authorized_keys
├── bash_profile                 # auto-launches tmux+claude on TTY login
└── sshd_config.d/
    └── 00-roost.conf            # pubkey-only, no password, no root login
```

Then `docker compose up -d` (recreate, not restart — `cap_add` changes need a fresh container).

---

## Why this exists

The container's entrypoint already starts a long-lived tmux session named `ai-claude` running `claude` (Claude Code), and runs `ttyd` on `:7681` so the browser can attach to it. SSH should drop the operator into the same session — without forcing them to remember the `tmux attach` command, without putting an SSH listener on the public internet, and without each connection spawning its own claude instance.

So the design is:

1. **Loopback-only port** — `:2222` on the host's `127.0.0.1`, never on `0.0.0.0`. Remote access goes through ProxyJump so the SSH auth at the host's `:22` is the front door; the container's sshd is reached only after authenticating to the host.
2. **One shared tmux session** — `tmux new-session -A -s ai-claude claude` in the SSH user's `.bash_profile`. The `-A` flag means "attach if it exists, create otherwise" — and the entrypoint has already created it, so every login attaches.
3. **Soft trigger, not `ForceCommand`** — the `.bash_profile` check is `$SSH_TTY` + no `$TMUX` + stdin is a TTY. Non-interactive `ssh user@host 'cmd'` invocations skip the launch entirely, so automation tools still work cleanly.

---

## Configuration files

### `ssh/authorized_keys`

Mirror of the host's `~/.ssh/authorized_keys`. Same trust set as the VPS itself — anyone who can SSH to the host can SSH into the container. One place to manage keys.

```bash
cp ~/.ssh/authorized_keys /path/to/roost/ssh/authorized_keys
chmod 600 /path/to/roost/ssh/authorized_keys
```

If you want a tighter scope (e.g. only one key reaches the container even if more keys reach the host), maintain this file independently instead of mirroring.

### `ssh/sshd_config.d/00-roost.conf`

Drop-in that locks down the container's sshd:

```
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PermitEmptyPasswords no
X11Forwarding no
AllowAgentForwarding yes
AllowTcpForwarding yes
ClientAliveInterval 60
ClientAliveCountMax 30
```

### `ssh/bash_profile`

Bind-mounted as `/home/dev/.bash_profile` inside the container.

```bash
[ -f ~/.bashrc ] && . ~/.bashrc

if [ -n "$SSH_TTY" ] && [ -z "$TMUX" ] && [ -t 0 ]; then
    exec tmux new-session -A -s ai-claude claude
fi
```

Three guards before launching:
- `$SSH_TTY` set → only SSH-with-TTY logins, not `docker exec` or non-TTY commands
- `$TMUX` empty → don't recurse if we're already inside tmux
- `-t 0` → stdin is a real terminal (defensive against weird transports)

### `docker-compose.override.yml`

Three bind-mounts plus the AUDIT_WRITE capability:

```yaml
services:
  roost:
    ports: !override
      - "127.0.0.1:8090:8080"
      - "127.0.0.1:2222:22"
    cap_add:
      - AUDIT_WRITE
    volumes:
      - ./ssh/authorized_keys:/home/dev/.ssh/authorized_keys:ro
      - ./ssh/sshd_config.d:/etc/ssh/sshd_config.d:ro
      - ./ssh/bash_profile:/home/dev/.bash_profile:ro
```

The `!override` tag replaces the base `docker-compose.yml`'s ports list entirely — without it, the base file's unqualified `"2222:22"` (which binds `0.0.0.0`) would still apply.

---

## Why `CAP_AUDIT_WRITE` is mandatory

The container's `openssh-server` is built against `libaudit`. PAM's session module tries to write a login audit record. Docker drops `CAP_AUDIT_WRITE` from container processes by default, so the audit write fails with `EPERM`, and **sshd cleanly tears down the session immediately after successful authentication**.

Symptom: SSH connects, pubkey auth succeeds in the verbose client log, then:

```
Connection to 127.0.0.1 closed by remote host.
Connection to 127.0.0.1 closed.
```

…with no shell output. Looks like a `.bash_profile` failure but isn't.

Diagnosis recipe (when SSH-in-roost is broken):

```bash
docker exec roost-roost-1 bash -c \
    'kill 8; /usr/sbin/sshd -e -E /tmp/sshd.log &'
# from host
ssh -tt -p 2222 -i ~/.ssh/<key> dev@127.0.0.1 'echo hi' < /dev/null
# inspect log
docker exec roost-roost-1 grep -i audit /tmp/sshd.log
```

Look for `linux_audit_write_entry failed: Operation not permitted`. Fix: ensure `cap_add: [AUDIT_WRITE]` is present **and the container has been recreated** (capability changes don't apply on plain restart).

---

## Access patterns

### From the host machine

```bash
ssh -p 2222 dev@127.0.0.1
```

Lands in the live `ai-claude` tmux session. Detach with `Ctrl-b d` (closes your SSH but keeps Claude Code running for the next session).

### From a laptop

The container's port is loopback on the host, so reach it via ProxyJump through the host's `:22`:

```bash
ssh -J dev@<host-public-ip> -p 2222 dev@127.0.0.1
```

…or persist it in `~/.ssh/config`:

```
Host roost
  HostName 127.0.0.1
  Port 2222
  User dev
  ProxyJump dev@<host-public-ip>
```

then `ssh roost`.

### Automation (non-interactive)

`.bash_profile`'s tmux launch only fires when `$SSH_TTY` is set, so this works cleanly:

```bash
ssh -p 2222 dev@127.0.0.1 'docker ps; whoami'
```

The command runs, output returns, no tmux.

### Escape hatch — raw shell, no tmux

If you need a plain bash session without the auto-attach (debugging, file inspection, etc.):

```bash
ssh -p 2222 -t dev@127.0.0.1 'bash --noprofile -i'
```

The `--noprofile` skips `.bash_profile` entirely.

### Browser route

`http://localhost:8080/terminal/` reaches the same `ai-claude` session via the ttyd process the container started at boot. No SSH keys needed — auth is the roost-web session.

---

## Multi-operator caveat

All three entry points attach to **one** shared tmux session. Two concurrent SSH clients see each other's keystrokes. This is a feature for pair-debugging and a footgun for unrelated operators.

If you need isolation per operator, change the `.bash_profile` line to use a per-user session name:

```bash
exec tmux new-session -A -s "ai-claude-$USER" claude
```

…and accept that each operator now runs their own Claude Code instance (multiple instances against the same `claude-auth/` bind-mount — usually fine but watch for token-spend implications).

---

## Trust model

- **Keys** live in `ssh/authorized_keys` on the host. Anyone whose public key is in that file lands in roost's Claude session.
- **Claude Code auth** is the host's, bind-mounted as `/home/dev/.claude` (path: `roost/claude-auth/`). Anyone who SSHs in spends against the host's Anthropic credit. Treat that as you would any privileged shell on the host.
- **Container is the sandbox** — Claude Code only affects what's inside the container, including data volumes (`roost_roost-data`, `roost_roost-config`) and bind-mounts (`.env`, the three auth dirs, and the SSH config files). The host filesystem outside those mounts is unreachable from inside.

---

## Persistence

The container's `/home/dev` is **ephemeral** — recreated on every `docker compose up -d` that triggers a container replacement. That's why every piece of state above is a bind-mount: `.ssh/authorized_keys`, `.bash_profile`, `.claude/`, `.codex/`, `.gemini/`. Without those mounts, the next container recreate would wipe your access.

The `ai-claude` tmux session itself does not persist across container restarts — it's re-created from scratch by the entrypoint each time. This means Claude Code's in-memory state (conversation history shown in the TUI) resets on container restart; but Claude Code's authenticated session, on-disk projects, and config persist via the `.claude/` bind-mount.

---

## Editing config

| Change | What to run |
|---|---|
| Add/remove SSH keys | Edit `ssh/authorized_keys` — picked up immediately, no restart needed (bind-mount is live, sshd reads `authorized_keys` per-connection) |
| Change sshd policy (`ssh/sshd_config.d/*.conf`) | `docker compose restart roost` — sshd re-reads its config at start |
| Change `bash_profile` | No restart needed — read on next login |
| Change `cap_add` or volumes in `docker-compose.override.yml` | `docker compose up -d` (or `down`/`up`) — capability/volume changes need a fresh container, not a restart |

---

## Base-file caveat

The base `docker-compose.yml` still ships with an unqualified `"2222:22"` port mapping (binds `0.0.0.0`). The override masks it via `ports: !override`. If you ever deploy roost without `docker-compose.override.yml`, the public binding returns. Defence in depth: if you don't need the loopback fallback, edit the base file too.
