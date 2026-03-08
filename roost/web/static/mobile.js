/* Roost Mobile — Toast, swipe-to-complete, pull-to-refresh, HTMX bridges */
(function () {
  'use strict';

  var toastContainer = document.getElementById('mToastContainer');

  /* ── Toast ──────────────────────────────────────────────────── */
  function toast(msg, type) {
    type = type || 'info';
    var el = document.createElement('div');
    el.className = 'm-toast ' + type;
    el.textContent = msg;
    toastContainer.appendChild(el);
    setTimeout(function () {
      el.classList.add('removing');
      setTimeout(function () { el.remove(); }, 200);
    }, 3000);
  }

  /* ── Swipe to complete ─────────────────────────────────────── */
  function initSwipe(containerSel, onComplete) {
    var container = document.querySelector(containerSel);
    if (!container) return;
    var cards = container.querySelectorAll('.m-task-card[data-task-id]');
    cards.forEach(function (card) {
      var inner = card.querySelector('.m-swipe-inner');
      if (!inner) return;
      var startX = 0, currentX = 0, dragging = false;
      var threshold = -70;

      inner.addEventListener('touchstart', function (e) {
        startX = e.touches[0].clientX;
        currentX = 0;
        dragging = true;
        inner.style.transition = 'none';
      }, { passive: true });

      inner.addEventListener('touchmove', function (e) {
        if (!dragging) return;
        var dx = e.touches[0].clientX - startX;
        if (dx > 0) dx = 0; // only swipe left
        currentX = dx;
        inner.style.transform = 'translateX(' + dx + 'px)';
      }, { passive: true });

      inner.addEventListener('touchend', function () {
        dragging = false;
        inner.style.transition = 'transform 0.2s ease';
        if (currentX < threshold) {
          inner.style.transform = 'translateX(-100%)';
          var taskId = card.getAttribute('data-task-id');
          if (onComplete) onComplete(taskId, card);
        } else {
          inner.style.transform = 'translateX(0)';
        }
      });
    });
  }

  /* ── Pull to refresh ───────────────────────────────────────── */
  function initPTR(containerSel, onRefresh) {
    var container = document.querySelector(containerSel);
    if (!container) return;
    var indicator = document.querySelector('.m-ptr-indicator');
    var startY = 0, pulling = false;

    container.addEventListener('touchstart', function (e) {
      if (container.scrollTop === 0) {
        startY = e.touches[0].clientY;
        pulling = true;
      }
    }, { passive: true });

    container.addEventListener('touchmove', function (e) {
      if (!pulling) return;
      var dy = e.touches[0].clientY - startY;
      if (dy > 60 && indicator) {
        indicator.classList.add('visible');
      }
    }, { passive: true });

    container.addEventListener('touchend', function () {
      if (!pulling) return;
      pulling = false;
      if (indicator && indicator.classList.contains('visible')) {
        indicator.classList.remove('visible');
        if (onRefresh) onRefresh();
        else location.reload();
      }
    });
  }

  /* ── Offline detection ─────────────────────────────────────── */
  var offlineBanner = document.getElementById('mOffline');
  function updateOnline() {
    if (offlineBanner) {
      offlineBanner.classList.toggle('show', !navigator.onLine);
    }
  }
  window.addEventListener('online', updateOnline);
  window.addEventListener('offline', updateOnline);
  updateOnline();

  /* ── HTMX event bridges ────────────────────────────────────── */

  // Toast bridge — server sends HX-Trigger: {"showToast": {"msg":"...", "type":"success"}}
  document.body.addEventListener('showToast', function(evt) {
    var d = evt.detail || {};
    toast(d.msg || 'Done', d.type || 'info');
  });

  // Edit-complete bridge — server sends HX-Trigger: editComplete after save
  document.body.addEventListener('editComplete', function() {
    var editMode = document.getElementById('editMode');
    var viewMode = document.getElementById('viewMode');
    var toggle = document.getElementById('editToggle');
    if (editMode) editMode.style.display = 'none';
    if (viewMode) viewMode.style.display = 'block';
    if (toggle) toggle.textContent = 'Edit';
  });

  // Global HTMX error handler
  document.body.addEventListener('htmx:responseError', function() {
    toast('Request failed', 'error');
  });

  /* ── Public API ────────────────────────────────────────────── */
  window.MobileUI = {
    toast: toast,
    initSwipe: initSwipe,
    initPTR: initPTR,
  };
})();
