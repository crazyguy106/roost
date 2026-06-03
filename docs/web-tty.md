# Web tty

`/tty` is an xterm.js page bridged through a PTY to a persistent per-user
tmux session inside the container. It's the operator's primary path on
FA-edition installs where SSH isn't an option.

## Why

FA-edition (Financial Adviser) operators are not developers — asking them
to SSH into the container is a non-starter. The web tty puts the same
shell behind the existing web auth layer:

- one click from the sidebar (`Terminal`)
- closing the tab detaches; reopening the page reattaches to the same
  tmux session with full scrollback
- works on every browser that supports WebSocket and JS

The host's own `docker-compose.override.yml` SSH shortcut (port 2222)
stays in place as a developer affordance — see `docs/container-ssh-access.md`.

## How it works

```
Browser ── xterm.js ──┐
                      │  WebSocket /ws/tty (binary keystrokes + JSON resize)
                      ▼
roost.web.api_tty ── pty.openpty() ──► tmux -L roost-tty new-session -A -s roost-<user_id>
                                              │
                                              └──► whatever the operator runs (claude, bash, etc.)
```

- Auth: the WebSocket handler re-checks `websocket.session.get("user")`
  because Starlette's `BaseHTTPMiddleware`-based `UnifiedAuthMiddleware`
  does **not** run on WS upgrades. Anonymous clients get `close(1008)`.
- tmux uses a dedicated socket (`-L roost-tty`) so these sessions don't
  collide with any other tmux on the box.
- Detach behaviour: closing the WS tears down the *tmux client*; the
  tmux server, the session, and anything running inside it survive.

## Files

| File | Role |
|------|------|
| `roost/web/api_tty.py` | WS endpoint, PTY bridge, tmux spawn |
| `roost/web/templates/tty.html` | xterm.js front-end (CDN-loaded) |
| `roost/web/pages.py::page_tty` | `GET /tty` page route |
| `roost/web/app.py` | router registration |
| `tests/test_web_tty.py` | page + WS auth smoke tests |

## Roadmap

Phase 1.5 (this doc) is single-window. Phase 1.6 will add multiple tmux
windows (tabs), a `chat_windows` SQLite mapping to link a window to a
specific lead/conversation, and a cap-and-evict picker that recommends
which idle window to close when the cap is reached.
