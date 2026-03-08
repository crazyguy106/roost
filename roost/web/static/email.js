/* Email Triage — vanilla JS controller for /inbox page */
(function () {
    'use strict';

    // ── State ────────────────────────────────────────────────────────
    let emails = [];
    let currentIndex = -1;
    let currentThreadId = null;
    let actionsTaken = [];

    // ── DOM refs ─────────────────────────────────────────────────────
    const searchQuery = document.getElementById('searchQuery');
    const searchBtn = document.getElementById('searchBtn');
    const emailList = document.getElementById('emailList');
    const emailEmpty = document.getElementById('emailEmpty');
    const emailLoading = document.getElementById('emailLoading');
    const emailListSection = document.getElementById('emailListSection');
    const threadSection = document.getElementById('threadSection');
    const threadSubject = document.getElementById('threadSubject');
    const threadMessages = document.getElementById('threadMessages');
    const threadActions = document.getElementById('threadActions');
    const threadBack = document.getElementById('threadBack');
    const composeSection = document.getElementById('composeSection');
    const composeTo = document.getElementById('composeTo');
    const composeSubject = document.getElementById('composeSubject');
    const composeCc = document.getElementById('composeCc');
    const composeBody = document.getElementById('composeBody');
    const composeSend = document.getElementById('composeSend');
    const composeAIDraft = document.getElementById('composeAIDraft');
    const composeCancel = document.getElementById('composeCancel');
    const actionCount = document.getElementById('actionCount');
    const statusBar = document.getElementById('statusBar');

    // ── Helpers ──────────────────────────────────────────────────────

    function esc(s) {
        if (!s) return '';
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function show(el) { el.classList.remove('email-hidden'); }
    function hide(el) { el.classList.add('email-hidden'); }

    function showStatus(msg) {
        statusBar.textContent = msg;
        show(statusBar);
    }
    function hideStatus() { hide(statusBar); }

    function updateActionCount() {
        actionCount.textContent = actionsTaken.length + ' action' + (actionsTaken.length !== 1 ? 's' : '');
    }

    function extractEmail(fromHeader) {
        var m = fromHeader.match(/<([^>]+)>/);
        return m ? m[1] : fromHeader;
    }

    function extractName(fromHeader) {
        var m = fromHeader.match(/^([^<]+)</);
        return m ? m[1].trim().replace(/"/g, '') : fromHeader;
    }

    function shortDate(dateStr) {
        if (!dateStr) return '';
        // Strip timezone offset for display
        return dateStr.split('+')[0].split('-0')[0].trim();
    }

    function setLoading(btn, loading) {
        btn.disabled = loading;
        if (loading) {
            btn.dataset.origText = btn.textContent;
            btn.textContent = 'Loading...';
        } else if (btn.dataset.origText) {
            btn.textContent = btn.dataset.origText;
        }
    }

    function setAllButtons(disabled) {
        threadActions.querySelectorAll('button').forEach(function (b) { b.disabled = disabled; });
    }

    // ── API calls ────────────────────────────────────────────────────

    function loadEmails(query) {
        emailList.innerHTML = '';
        emailEmpty.style.display = 'none';
        emailLoading.style.display = 'block';
        hide(threadSection);
        hide(composeSection);
        hideStatus();

        fetch('/api/emails/search?q=' + encodeURIComponent(query) + '&limit=10')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                emailLoading.style.display = 'none';
                if (data.error) {
                    showStatus('Error: ' + data.error);
                    return;
                }
                emails = data.emails || [];
                if (emails.length === 0) {
                    emailEmpty.style.display = 'block';
                    return;
                }
                renderEmailList();
            })
            .catch(function (err) {
                emailLoading.style.display = 'none';
                showStatus('Failed to load emails: ' + err);
            });
    }

    function renderEmailList() {
        emailList.innerHTML = '';
        for (var i = 0; i < emails.length; i++) {
            var msg = emails[i];
            var el = document.createElement('div');
            el.className = 'email-item card' + (i === currentIndex ? ' active' : '');
            el.dataset.index = i;
            el.innerHTML =
                '<div class="email-item-from">' + esc(extractName(msg.from || '')) + '</div>' +
                '<div class="email-item-subject">' + esc(msg.subject || '(no subject)') + '</div>' +
                '<div class="email-item-snippet">' + esc((msg.snippet || '').substring(0, 120)) + '</div>' +
                '<div class="email-item-date">' + esc(shortDate(msg.date || '')) + '</div>';
            el.addEventListener('click', (function (idx) {
                return function () { viewThread(idx); };
            })(i));
            emailList.appendChild(el);
        }
    }

    function viewThread(idx) {
        if (idx < 0 || idx >= emails.length) return;
        currentIndex = idx;
        currentThreadId = emails[idx].threadId;
        renderEmailList(); // update active highlight

        threadMessages.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-dim);">Loading thread...</div>';
        threadSubject.textContent = emails[idx].subject || '(no subject)';
        show(threadSection);
        hide(composeSection);
        setAllButtons(true);

        // Scroll to thread
        threadSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        fetch('/api/emails/thread/' + encodeURIComponent(currentThreadId))
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) {
                renderThread(data);
                setAllButtons(false);
            })
            .catch(function (err) {
                threadMessages.innerHTML = '<div class="empty">Failed to load thread: ' + esc(String(err)) + '</div>';
                setAllButtons(false);
            });
    }

    function renderThread(data) {
        var msgs = data.messages || [];
        threadMessages.innerHTML = '';
        for (var i = 0; i < msgs.length; i++) {
            var m = msgs[i];
            var div = document.createElement('div');
            div.className = 'thread-msg';
            div.innerHTML =
                '<div class="thread-msg-header">' +
                '<strong>' + esc(extractName(m.from || '')) + '</strong>' +
                '<span class="thread-msg-date">' + esc(shortDate(m.date || '')) + '</span>' +
                '</div>' +
                '<div class="thread-msg-body">' + esc(m.body || '').replace(/\n/g, '<br>') + '</div>';
            threadMessages.appendChild(div);
        }
    }

    function startReply() {
        if (currentIndex < 0) return;
        var msg = emails[currentIndex];
        composeTo.value = extractEmail(msg.from || '');
        var subj = msg.subject || '';
        composeSubject.value = subj.toLowerCase().startsWith('re:') ? subj : 'Re: ' + subj;
        composeCc.value = '';
        composeBody.value = '';
        show(composeSection);
        composeBody.focus();
        composeSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function generateAIDraft(btn) {
        if (!currentThreadId) return;
        var instruction = prompt('What should the reply say?\n(e.g. "confirm dates and ask about venue")');
        if (!instruction) return;

        setLoading(btn, true);
        showStatus('Generating AI draft...');

        fetch('/api/emails/draft-ai', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ thread_id: currentThreadId, instruction: instruction }),
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || 'AI draft failed'); });
                return r.json();
            })
            .then(function (data) {
                setLoading(btn, false);
                hideStatus();
                // Fill compose panel
                startReply();
                composeBody.value = data.body || '';
            })
            .catch(function (err) {
                setLoading(btn, false);
                showStatus('AI draft failed: ' + err);
            });
    }

    function archiveThread(btn) {
        if (!currentThreadId) return;
        setLoading(btn, true);

        fetch('/api/emails/archive/' + encodeURIComponent(currentThreadId), { method: 'POST' })
            .then(function (r) {
                if (!r.ok) throw new Error('Archive failed');
                return r.json();
            })
            .then(function () {
                setLoading(btn, false);
                var subj = emails[currentIndex] ? emails[currentIndex].subject : '';
                actionsTaken.push('Archived: ' + (subj || '').substring(0, 40));
                updateActionCount();

                // Remove from list and advance
                emails.splice(currentIndex, 1);
                if (emails.length === 0) {
                    hide(threadSection);
                    hide(composeSection);
                    emailList.innerHTML = '';
                    emailEmpty.style.display = 'block';
                    currentIndex = -1;
                    return;
                }
                if (currentIndex >= emails.length) currentIndex = emails.length - 1;
                renderEmailList();
                viewThread(currentIndex);
            })
            .catch(function (err) {
                setLoading(btn, false);
                showStatus('Archive failed: ' + err);
            });
    }

    function createTask(btn) {
        if (currentIndex < 0) return;
        var msg = emails[currentIndex];
        setLoading(btn, true);

        fetch('/api/emails/task', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                subject: msg.subject || '(no subject)',
                from_name: extractName(msg.from || ''),
                thread_id: msg.threadId || '',
            }),
        })
            .then(function (r) {
                if (!r.ok) throw new Error('Task creation failed');
                return r.json();
            })
            .then(function (data) {
                setLoading(btn, false);
                actionsTaken.push('Task #' + data.id + ': ' + (msg.subject || '').substring(0, 40));
                updateActionCount();
                showStatus('Task #' + data.id + ' created');
                setTimeout(hideStatus, 2000);
            })
            .catch(function (err) {
                setLoading(btn, false);
                showStatus('Task failed: ' + err);
            });
    }

    function sendReply() {
        var to = composeTo.value.trim();
        var subject = composeSubject.value.trim();
        var body = composeBody.value.trim();
        if (!to || !body) {
            showStatus('To and Body are required');
            return;
        }

        setLoading(composeSend, true);
        showStatus('Sending...');

        fetch('/api/emails/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                to: to,
                subject: subject,
                body: body,
                cc: composeCc.value.trim(),
                thread_id: currentThreadId || '',
            }),
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || 'Send failed'); });
                return r.json();
            })
            .then(function () {
                setLoading(composeSend, false);
                actionsTaken.push('Replied: ' + subject.substring(0, 40));
                updateActionCount();
                hide(composeSection);
                showStatus('Sent!');
                setTimeout(hideStatus, 2000);
            })
            .catch(function (err) {
                setLoading(composeSend, false);
                showStatus('Send failed: ' + err);
            });
    }

    // ── Event wiring ─────────────────────────────────────────────────

    searchBtn.addEventListener('click', function () {
        loadEmails(searchQuery.value.trim() || 'is:unread label:INBOX');
    });

    searchQuery.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') searchBtn.click();
    });

    threadBack.addEventListener('click', function () {
        hide(threadSection);
        hide(composeSection);
        currentIndex = -1;
        currentThreadId = null;
        renderEmailList();
    });

    threadActions.addEventListener('click', function (e) {
        var btn = e.target.closest('button[data-action]');
        if (!btn) return;
        var action = btn.dataset.action;
        if (action === 'reply') startReply();
        else if (action === 'ai') generateAIDraft(btn);
        else if (action === 'archive') archiveThread(btn);
        else if (action === 'task') createTask(btn);
    });

    composeSend.addEventListener('click', sendReply);

    composeAIDraft.addEventListener('click', function () {
        generateAIDraft(composeAIDraft);
    });

    composeCancel.addEventListener('click', function () {
        hide(composeSection);
    });

    // ── Init ─────────────────────────────────────────────────────────
    loadEmails(searchQuery.value.trim());
})();
