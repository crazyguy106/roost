// Agentic Workflow frontend — renders the plan-then-execute SSE stream
// from POST /api/agentic/message. See docs/agentic-workflow-phase1.md §4
// for the event protocol.

(function () {
    const form = document.getElementById('agenticForm');
    const input = document.getElementById('agenticInput');
    const sendBtn = document.getElementById('agenticSendBtn');
    const stream = document.getElementById('agenticStream');

    if (!form || !input || !stream) return;

    let busy = false;
    // Map call_id → tool row element so we can update on _returned / _failed.
    const toolRows = new Map();

    // ── Provider switcher (mirrors /chat) ────────────────────────────
    // window.__roostProvider is seeded by agentic.html at first paint.
    let currentProvider = window.__roostProvider || null;

    function paintProviderSwitch() {
        document.querySelectorAll('#providerSwitch button').forEach(b => {
            b.classList.toggle('active', b.dataset.provider === currentProvider);
        });
        const label = document.getElementById('providerLabel');
        if (label && currentProvider) label.textContent = currentProvider;
    }
    document.querySelectorAll('#providerSwitch button').forEach(b => {
        b.addEventListener('click', () => {
            currentProvider = b.dataset.provider;
            const url = new URL(location.href);
            url.searchParams.set('provider', currentProvider);
            history.replaceState(null, '', url.toString());
            paintProviderSwitch();
        });
    });
    paintProviderSwitch();

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function appendNode(node) {
        stream.appendChild(node);
        stream.scrollTop = stream.scrollHeight;
    }

    function renderUserMessage(text) {
        const node = el('div', 'user-msg', text);
        appendNode(node);
    }

    function renderPlan(payload) {
        const data = payload.data || {};
        const card = el('div', data.fallback_used ? 'plan-card fallback' : 'plan-card');
        const title = el('h3', null, data.fallback_used ? 'Fallback plan (planner unavailable)' : 'Plan');
        card.appendChild(title);

        if (data.summary) {
            card.appendChild(el('div', 'plan-summary', data.summary));
        }

        (data.steps || []).forEach(step => {
            const row = el('div', 'plan-step');
            const idSpan = el('span', 'step-id', `${step.step_id}.`);
            row.appendChild(idSpan);
            row.appendChild(document.createTextNode(step.description || ''));
            if (Array.isArray(step.tools) && step.tools.length) {
                const chipWrap = el('div');
                step.tools.forEach(t => chipWrap.appendChild(el('span', 'tool-chip', t)));
                row.appendChild(chipWrap);
            }
            card.appendChild(row);
        });

        const meta = el('div', 'plan-meta');
        const calls = data.estimated_tool_calls ?? 0;
        const dur = data.estimated_duration_s ?? 0;
        meta.textContent = `≈ ${calls} tool call${calls === 1 ? '' : 's'} · ~${dur}s · plan_id ${payload.plan_id || '—'}`;
        card.appendChild(meta);

        appendNode(card);
    }

    function renderToolCalled(payload) {
        const row = el('div', 'tool-row');
        const status = el('div', 'status', '⏳');
        row.appendChild(status);

        const head = el('div');
        if (payload.step_id !== undefined && payload.step_id !== null) {
            const lbl = el('span', 'step-label', `step ${payload.step_id}`);
            head.appendChild(lbl);
        }
        const name = el('span', 'name', payload.tool || '');
        head.appendChild(name);
        row.appendChild(head);

        const dur = el('div', 'duration', '…');
        row.appendChild(dur);

        if (payload.args && typeof payload.args === 'object') {
            const argsBlock = el('div', 'args');
            try {
                argsBlock.textContent = JSON.stringify(payload.args, null, 2);
            } catch (_) {
                argsBlock.textContent = String(payload.args);
            }
            row.appendChild(argsBlock);
        }

        toolRows.set(payload.call_id, { row, status, dur });
        appendNode(row);
    }

    function renderToolReturned(payload) {
        const entry = toolRows.get(payload.call_id);
        if (!entry) return;
        entry.status.textContent = '✓';
        entry.status.style.color = 'oklch(0.7 0.15 145)';
        if (payload.duration_ms !== undefined) {
            entry.dur.textContent = `${payload.duration_ms} ms`;
        } else {
            entry.dur.textContent = '';
        }
        const result = payload.result;
        if (result !== undefined && result !== null) {
            const block = el('div', 'result');
            try {
                block.textContent = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
            } catch (_) {
                block.textContent = String(result);
            }
            entry.row.appendChild(block);
        }
    }

    function renderToolFailed(payload) {
        const entry = toolRows.get(payload.call_id);
        if (!entry) return;
        entry.status.textContent = '✗';
        entry.status.style.color = 'oklch(0.7 0.2 25)';
        if (payload.duration_ms !== undefined) {
            entry.dur.textContent = `${payload.duration_ms} ms`;
        }
        const block = el('div', 'error', payload.error || 'Tool failed');
        entry.row.appendChild(block);
    }

    function renderThinking(text) {
        appendNode(el('div', 'thinking-line', text));
    }

    function renderHeld(payload) {
        const card = el('div', 'held-card');
        card.appendChild(el('h4', null, 'Confirmation required'));
        (payload.actions || []).forEach(a => {
            card.appendChild(el('div', null, `${a.tool_name} — ${a.description || ''}`));
        });
        card.appendChild(el('div', 'plan-meta', 'Check your Telegram for the verification code.'));
        appendNode(card);
    }

    function renderFinal(text) {
        const node = el('div', 'final-response', text || '(empty response)');
        appendNode(node);
    }

    function renderError(text) {
        appendNode(el('div', 'error-line', text || 'Unknown error'));
    }

    function dispatch(event) {
        switch (event.type) {
            case 'plan':
                renderPlan(event);
                break;
            case 'tool_called':
                renderToolCalled(event);
                break;
            case 'tool_returned':
                renderToolReturned(event);
                break;
            case 'tool_failed':
                renderToolFailed(event);
                break;
            case 'thinking':
            case 'progress':
                renderThinking(event.text || '');
                break;
            case 'held':
                renderHeld(event);
                break;
            case 'final':
                renderFinal(event.text || '');
                break;
            case 'error':
                renderError(event.text || '');
                break;
            default:
                // Forward-compatible: future event types fall through silently.
                break;
        }
    }

    async function send(message) {
        if (busy) return;
        busy = true;
        sendBtn.disabled = true;
        input.disabled = true;
        toolRows.clear();
        renderUserMessage(message);

        try {
            const payload = { message };
            if (currentProvider) payload.provider = currentProvider;
            const res = await fetch('/api/agentic/message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            if (!res.ok || !res.body) {
                let detail = '';
                try {
                    const j = await res.json();
                    detail = j.error || JSON.stringify(j);
                } catch (_) {
                    detail = `HTTP ${res.status}`;
                }
                renderError(detail);
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buf = '';
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buf += decoder.decode(value, { stream: true });
                let idx;
                while ((idx = buf.indexOf('\n\n')) !== -1) {
                    const chunk = buf.slice(0, idx);
                    buf = buf.slice(idx + 2);
                    const line = chunk.trim();
                    if (!line.startsWith('data:')) continue;
                    const payload = line.slice(5).trim();
                    if (payload === '[DONE]') return;
                    try {
                        const obj = JSON.parse(payload);
                        dispatch(obj);
                    } catch (_) {
                        // Ignore malformed lines.
                    }
                }
            }
        } catch (err) {
            renderError(String(err));
        } finally {
            busy = false;
            sendBtn.disabled = false;
            input.disabled = false;
            input.value = '';
            input.focus();
        }
    }

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const text = (input.value || '').trim();
        if (!text || busy) return;
        send(text);
    });

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            form.requestSubmit();
        }
    });
})();
