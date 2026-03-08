/* Calendar View — Vanilla JS (IIFE, no deps) */
(function () {
    "use strict";

    var MONTHS = ["", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"];
    var DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

    var state = {
        view: "month",
        year: 0, month: 0,
        weekStart: null, // Date string YYYY-MM-DD
        today: "",
        events: {},
        deadlines: {},
        selectedDate: null,
    };

    // DOM refs
    var $grid, $title, $monthC, $weekC, $detail, $detailTitle, $detailBody;

    function init() {
        var s = window.__calState || {};
        state.year = s.year;
        state.month = s.month;
        state.today = s.today;
        state.events = s.events || {};
        state.deadlines = s.deadlines || {};

        $grid = document.getElementById("calGrid");
        $title = document.getElementById("calTitle");
        $monthC = document.getElementById("calMonthContainer");
        $weekC = document.getElementById("calWeekContainer");
        $detail = document.getElementById("calDetail");
        $detailTitle = document.getElementById("calDetailTitle");
        $detailBody = document.getElementById("calDetailBody");

        bindEvents();
    }

    /* ── Grid date computation ─────────────────────────────────────── */

    function getGridDates(year, month) {
        var first = new Date(year, month - 1, 1);
        var offset = (first.getDay() + 6) % 7; // Monday=0
        var dates = [];
        for (var i = 0; i < 42; i++) {
            var d = new Date(year, month - 1, 1 - offset + i);
            dates.push(fmtDate(d));
        }
        return dates;
    }

    function fmtDate(d) {
        var y = d.getFullYear();
        var m = ("0" + (d.getMonth() + 1)).slice(-2);
        var day = ("0" + d.getDate()).slice(-2);
        return y + "-" + m + "-" + day;
    }

    function parseDate(s) {
        var p = s.split("-");
        return new Date(+p[0], +p[1] - 1, +p[2]);
    }

    function fmtMonthKey(year, month) {
        return year + "-" + ("0" + month).slice(-2);
    }

    /* ── Fetch ─────────────────────────────────────────────────────── */

    function fetchRange(startStr, endStr, cb) {
        fetch("/api/calendar/range?start=" + startStr + "&end=" + endStr)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                state.events = data.events || {};
                state.deadlines = data.deadlines || {};
                if (cb) cb();
            })
            .catch(function () { if (cb) cb(); });
    }

    /* ── Month rendering ───────────────────────────────────────────── */

    function renderMonth() {
        var dates = getGridDates(state.year, state.month);
        var mk = fmtMonthKey(state.year, state.month);
        var html = "";

        for (var i = 0; i < dates.length; i++) {
            var d = dates[i];
            var isToday = d === state.today;
            var isOther = d.slice(0, 7) !== mk;
            var isSel = d === state.selectedDate;
            var cls = "cal-cell";
            if (isToday) cls += " cal-cell--today";
            if (isOther) cls += " cal-cell--other";
            if (isSel) cls += " cal-cell--selected";

            var evts = state.events[d] || [];
            var dls = state.deadlines[d] || [];
            var total = evts.length + dls.length;
            var maxDots = 3;

            var dots = "";
            var shown = 0;
            for (var e = 0; e < evts.length && shown < maxDots; e++, shown++) {
                dots += '<span class="cal-dot cal-dot--event"></span>';
            }
            for (var dl = 0; dl < dls.length && shown < maxDots; dl++, shown++) {
                dots += '<span class="cal-dot cal-dot--deadline"></span>';
            }
            if (total > maxDots) {
                dots += '<span class="cal-dot--more">+' + (total - maxDots) + '</span>';
            }

            var dayNum = parseInt(d.slice(8), 10);
            html += '<div class="' + cls + '" data-date="' + d + '">' +
                '<div class="cal-day-num">' + dayNum + '</div>' +
                '<div class="cal-dots">' + dots + '</div></div>';
        }

        $grid.innerHTML = html;
        $title.textContent = MONTHS[state.month] + " " + state.year;
    }

    /* ── Week rendering ────────────────────────────────────────────── */

    function getWeekStart(dateStr) {
        var d = parseDate(dateStr);
        var dow = (d.getDay() + 6) % 7; // Mon=0
        d.setDate(d.getDate() - dow);
        return fmtDate(d);
    }

    function renderWeek() {
        if (!state.weekStart) {
            state.weekStart = getWeekStart(state.today);
        }

        var days = [];
        var ws = parseDate(state.weekStart);
        for (var i = 0; i < 7; i++) {
            var d = new Date(ws);
            d.setDate(d.getDate() + i);
            days.push(fmtDate(d));
        }

        // Update title
        var wsD = parseDate(days[0]);
        var weD = parseDate(days[6]);
        $title.textContent = fmtShortDate(wsD) + " \u2013 " + fmtShortDate(weD);

        var startHour = 7, endHour = 21;
        var totalHours = endHour - startHour;

        // Header
        var html = '<div class="cal-week-header"><div class="cal-week-header-cell"></div>';
        for (var i = 0; i < 7; i++) {
            var isT = days[i] === state.today ? " today" : "";
            var dd = parseDate(days[i]);
            html += '<div class="cal-week-header-cell' + isT + '">' +
                DOW[i] + " " + dd.getDate() + '</div>';
        }
        html += '</div>';

        // All-day row
        html += '<div class="cal-week-allday"><div class="cal-week-allday-gutter">all day</div>';
        for (var i = 0; i < 7; i++) {
            var evts = (state.events[days[i]] || []).filter(function (e) { return e.all_day; });
            html += '<div class="cal-week-allday-cell">';
            for (var e = 0; e < evts.length; e++) {
                html += '<div class="cal-week-allday-event">' + esc(evts[e].summary) + '</div>';
            }
            html += '</div>';
        }
        html += '</div>';

        // Time grid
        var pxPerHour = 48;
        html += '<div class="cal-week-grid" style="height:' + (totalHours * pxPerHour) + 'px">';

        // Time labels column
        html += '<div class="cal-week-time-col">';
        for (var h = startHour; h < endHour; h++) {
            html += '<div class="cal-week-time-label">' + fmtHour(h) + '</div>';
        }
        html += '</div>';

        // Day columns
        for (var i = 0; i < 7; i++) {
            html += '<div class="cal-week-day-col">';
            // Hour lines
            for (var h = 0; h < totalHours; h++) {
                html += '<div class="cal-week-hour-line" style="top:' + (h * pxPerHour) + 'px"></div>';
            }

            // Timed events
            var evts = (state.events[days[i]] || []).filter(function (e) { return !e.all_day && e.start; });
            for (var e = 0; e < evts.length; e++) {
                var ev = evts[e];
                var sTime = new Date(ev.start);
                var eTime = ev.end ? new Date(ev.end) : new Date(sTime.getTime() + 3600000);
                var topMin = (sTime.getHours() - startHour) * 60 + sTime.getMinutes();
                var durMin = (eTime - sTime) / 60000;
                if (topMin < 0) { durMin += topMin; topMin = 0; }
                if (durMin < 15) durMin = 15;
                var topPx = topMin * (pxPerHour / 60);
                var heightPx = durMin * (pxPerHour / 60);

                html += '<div class="cal-event-block" style="top:' + topPx + 'px;height:' + heightPx + 'px">' +
                    '<div class="cal-event-block-title">' + esc(ev.summary) + '</div>' +
                    '<div class="cal-event-block-time">' + fmtTime(sTime) + '</div></div>';
            }

            // Deadline markers
            var dls = state.deadlines[days[i]] || [];
            for (var dl = 0; dl < dls.length; dl++) {
                var dlItem = dls[dl];
                var dlTime = dlItem.deadline ? new Date(dlItem.deadline) : null;
                if (!dlTime) continue;
                var dlTop = ((dlTime.getHours() - startHour) * 60 + dlTime.getMinutes()) * (pxPerHour / 60);
                if (dlTop < 0) dlTop = 0;
                html += '<div class="cal-deadline-line" style="top:' + dlTop + 'px">' +
                    '<span class="cal-deadline-label">' + esc(dlItem.title) + '</span></div>';
            }

            html += '</div>';
        }
        html += '</div>';

        $weekC.innerHTML = html;
    }

    /* ── Day detail panel ──────────────────────────────────────────── */

    function showDetail(dateStr) {
        state.selectedDate = dateStr;
        var d = parseDate(dateStr);
        var label = DOW[(d.getDay() + 6) % 7] + " " + d.getDate() + " " +
            MONTHS[d.getMonth() + 1] + " " + d.getFullYear();
        $detailTitle.textContent = label;

        var evts = state.events[dateStr] || [];
        var dls = state.deadlines[dateStr] || [];
        var html = "";

        if (evts.length) {
            html += '<div class="cal-detail-section"><div class="cal-detail-section-title">Events</div>';
            for (var i = 0; i < evts.length; i++) {
                var e = evts[i];
                var timeStr = "";
                if (e.all_day) { timeStr = "All day"; }
                else if (e.start) {
                    timeStr = fmtTime(new Date(e.start));
                    if (e.end) timeStr += " \u2013 " + fmtTime(new Date(e.end));
                }
                html += '<div class="cal-detail-event">' +
                    '<div><strong>' + esc(e.summary) + '</strong></div>' +
                    '<div class="cal-detail-event-time">' + timeStr + '</div>';
                if (e.calendar) html += '<div class="cal-detail-event-cal">' + esc(e.calendar) + '</div>';
                if (e.location) html += '<div class="cal-detail-event-loc">' + esc(e.location) + '</div>';
                html += '</div>';
            }
            html += '</div>';
        }

        if (dls.length) {
            html += '<div class="cal-detail-section"><div class="cal-detail-section-title">Deadlines</div>';
            for (var i = 0; i < dls.length; i++) {
                var dl = dls[i];
                html += '<div class="cal-detail-deadline">' +
                    '<span class="badge badge-' + dl.priority + '">' + dl.priority + '</span>' +
                    '<a href="/tasks/' + dl.id + '">' + esc(dl.title) + '</a>';
                if (dl.project_name) html += '<span style="color:var(--text-dim);font-size:0.75rem">' + esc(dl.project_name) + '</span>';
                html += '</div>';
            }
            html += '</div>';
        }

        if (!evts.length && !dls.length) {
            html = '<div style="color:var(--text-dim);text-align:center;padding:16px">No events or deadlines</div>';
        }

        $detailBody.innerHTML = html;
        $detail.classList.add("open");

        // Re-render month to highlight selected
        if (state.view === "month") renderMonth();
    }

    function hideDetail() {
        state.selectedDate = null;
        $detail.classList.remove("open");
        if (state.view === "month") renderMonth();
    }

    /* ── Navigation ────────────────────────────────────────────────── */

    function navigate(delta) {
        hideDetail();
        if (state.view === "month") {
            state.month += delta;
            if (state.month > 12) { state.month = 1; state.year++; }
            if (state.month < 1) { state.month = 12; state.year--; }
            var dates = getGridDates(state.year, state.month);
            fetchRange(dates[0], dates[41], renderMonth);
        } else {
            var ws = parseDate(state.weekStart);
            ws.setDate(ws.getDate() + delta * 7);
            state.weekStart = fmtDate(ws);
            var we = new Date(ws); we.setDate(we.getDate() + 6);
            fetchRange(fmtDate(ws), fmtDate(we), renderWeek);
        }
    }

    function goToday() {
        hideDetail();
        var td = parseDate(state.today);
        state.year = td.getFullYear();
        state.month = td.getMonth() + 1;
        state.weekStart = getWeekStart(state.today);
        var dates = getGridDates(state.year, state.month);
        fetchRange(dates[0], dates[41], function () {
            if (state.view === "month") renderMonth(); else renderWeek();
        });
    }

    function setView(view) {
        state.view = view;
        document.querySelectorAll(".cal-view-btn").forEach(function (b) {
            b.classList.toggle("active", b.dataset.view === view);
        });
        if (view === "month") {
            $monthC.classList.add("active");
            $weekC.classList.remove("active");
            renderMonth();
        } else {
            $monthC.classList.remove("active");
            $weekC.classList.add("active");
            if (!state.weekStart) state.weekStart = getWeekStart(state.today);
            var ws = parseDate(state.weekStart);
            var we = new Date(ws); we.setDate(we.getDate() + 6);
            fetchRange(fmtDate(ws), fmtDate(we), renderWeek);
        }
    }

    /* ── Events ────────────────────────────────────────────────────── */

    function bindEvents() {
        document.getElementById("calPrev").addEventListener("click", function () { navigate(-1); });
        document.getElementById("calNext").addEventListener("click", function () { navigate(1); });
        document.getElementById("calToday").addEventListener("click", goToday);
        document.getElementById("calDetailClose").addEventListener("click", hideDetail);

        // View toggle
        document.querySelectorAll(".cal-view-btn").forEach(function (btn) {
            btn.addEventListener("click", function () { setView(btn.dataset.view); });
        });

        // Cell clicks (delegation on grid)
        $grid.addEventListener("click", function (e) {
            var cell = e.target.closest(".cal-cell");
            if (!cell) return;
            var dateStr = cell.dataset.date;
            if (dateStr === state.selectedDate) { hideDetail(); } else { showDetail(dateStr); }
        });
    }

    /* ── Helpers ────────────────────────────────────────────────────── */

    function esc(s) {
        if (!s) return "";
        var d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function fmtTime(d) {
        var h = d.getHours(), m = d.getMinutes();
        return ("0" + h).slice(-2) + ":" + ("0" + m).slice(-2);
    }

    function fmtHour(h) {
        return (h < 10 ? "0" : "") + h + ":00";
    }

    function fmtShortDate(d) {
        return d.getDate() + " " + MONTHS[d.getMonth() + 1].slice(0, 3);
    }

    /* ── Boot ──────────────────────────────────────────────────────── */
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
