(function (KL) {
  'use strict';

  var ICONS = {
    chat: '<path d="M4 5h16v10H9l-5 4Z"/><path d="M8 9h8M8 12h5"/>',
    library: '<path d="M4 5h4v14H4zM8 5h4v14H8zM13 5l4 1-3 13-4-1z"/>',
    globe: '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4c2.5 2.3 2.5 13.7 0 16M12 4c-2.5 2.3-2.5 13.7 0 16"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/>',
    file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5M9 12h6M9 16h6"/>',
    folder: '<path d="M3 6h7l2 2h9v11H3z"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2 6"/><path d="M20 4v7h-7"/>',
    close: '<path d="m5 5 14 14M19 5 5 19"/>',
    copy: '<rect x="8" y="8" width="11" height="12" rx="1"/><path d="M5 16V4h11"/>',
    check: '<path d="m4 12 5 5L20 6"/>',
    alert: '<path d="M12 3 2 21h20Z"/><path d="M12 9v5M12 17h.01"/>',
    stop: '<rect x="6" y="6" width="12" height="12"/>',
    send: '<path d="M3 12 21 4l-5 17-4-7Z"/><path d="m12 14 9-10"/>',
    key: '<circle cx="8" cy="12" r="4"/><path d="M12 12h9M17 12v3M20 12v2"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    book: '<path d="M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4Z"/><path d="M5 16a4 4 0 0 1 4-4h10"/>',
    arrowLeft: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
    spark: '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z"/><path d="m19 16 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8Z"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5M5 20h14"/>',
    edit: '<path d="m4 20 4-1 11-11-3-3L5 16Z"/><path d="m14 6 3 3"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'
  };

  function byId(id) { return document.getElementById(id); }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function icon(name, cls) {
    var body = ICONS[name] || ICONS.spark;
    return '<svg class="' + (cls || '') + '" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>';
  }

  function formatBytes(value) {
    var bytes = Number(value) || 0;
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function formatDate(value) {
    if (!value) return '—';
    var date = new Date(value);
    if (isNaN(date.getTime())) return '—';
    var pad = function (n) { return n < 10 ? '0' + n : String(n); };
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }

  function relativeTime(value) {
    if (!value) return '刚刚';
    var date = new Date(value);
    if (isNaN(date.getTime())) return '刚刚';
    var seconds = Math.floor((Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟前';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' 小时前';
    if (seconds < 86400 * 7) return Math.floor(seconds / 86400) + ' 天前';
    return formatDate(value);
  }

  function toast(message, type) {
    var root = byId('toast-root');
    if (!root) return;
    var node = document.createElement('div');
    node.className = 'toast' + (type ? ' is-' + type : '');
    node.textContent = message;
    root.appendChild(node);
    setTimeout(function () {
      node.style.opacity = '0';
      node.style.transition = 'opacity .25s';
      setTimeout(function () { node.remove(); }, 280);
    }, type === 'error' ? 5200 : 3200);
  }

  function openModal(options) {
    var root = byId('modal-root');
    var opts = options || {};
    root.innerHTML = '<div class="overlay" data-modal-overlay></div>'
      + '<div class="modal" role="dialog" aria-modal="true" aria-label="' + escapeHtml(opts.title || '对话框') + '">'
      + (opts.title ? '<h3>' + escapeHtml(opts.title) + '</h3>' : '')
      + (opts.sub ? '<p class="modal-sub">' + escapeHtml(opts.sub) + '</p>' : '')
      + '<div class="modal-body">' + (opts.body || '') + '</div>'
      + (opts.footer ? '<div class="modal-actions">' + opts.footer + '</div>' : '')
      + '</div>';
    var overlay = root.querySelector('[data-modal-overlay]');
    var modal = root.querySelector('.modal');

    function close() {
      root.innerHTML = '';
      document.removeEventListener('keydown', onKey);
    }
    function onKey(event) {
      if (event.key === 'Escape') close();
    }
    overlay.addEventListener('click', close);
    document.addEventListener('keydown', onKey);
    root.querySelectorAll('[data-close]').forEach(function (button) {
      button.addEventListener('click', close);
    });
    var first = modal.querySelector('input, textarea, select, button');
    if (first) setTimeout(function () { first.focus(); }, 30);
    return { root: modal, close: close };
  }

  function confirmDialog(options) {
    var opts = options || {};
    return new Promise(function (resolve) {
      var modal = openModal({
        title: opts.title || '确认操作',
        sub: opts.message || '',
        body: opts.body || '',
        footer: '<button class="btn btn-ghost" data-cancel>' + escapeHtml(opts.cancelText || '取消') + '</button>'
          + '<button class="btn ' + (opts.danger ? 'btn-danger' : 'btn-primary') + '" data-confirm>' + escapeHtml(opts.confirmText || '确认') + '</button>'
      });
      modal.root.querySelector('[data-cancel]').addEventListener('click', function () {
        modal.close();
        resolve(false);
      });
      modal.root.querySelector('[data-confirm]').addEventListener('click', function () {
        modal.close();
        resolve(true);
      });
    });
  }

  function openDrawer(options) {
    var root = byId('drawer-root');
    var opts = options || {};
    root.innerHTML = '<div class="overlay" data-drawer-overlay></div>'
      + '<aside class="drawer" role="dialog" aria-modal="true" aria-label="' + escapeHtml(opts.title || '详情') + '">'
      + '<div class="drawer-head"><h3>' + escapeHtml(opts.title || '') + '</h3>'
      + '<button class="btn btn-ghost btn-icon" data-drawer-close aria-label="关闭">' + icon('close') + '</button></div>'
      + '<div class="drawer-body">' + (opts.body || '') + '</div>'
      + '</aside>';
    var overlay = root.querySelector('[data-drawer-overlay]');
    function close() {
      root.innerHTML = '';
      document.removeEventListener('keydown', onKey);
    }
    function onKey(event) { if (event.key === 'Escape') close(); }
    overlay.addEventListener('click', close);
    root.querySelector('[data-drawer-close]').addEventListener('click', close);
    document.addEventListener('keydown', onKey);
    return { close: close, root: root.querySelector('.drawer') };
  }

  async function copyText(text) {
    var value = String(text == null ? '' : text);
    try {
      await navigator.clipboard.writeText(value);
      toast('已复制到剪贴板', 'ok');
      return true;
    } catch (_) {
      var area = document.createElement('textarea');
      area.value = value;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
      area.remove();
      toast(ok ? '已复制到剪贴板' : '复制失败，请手动选择', ok ? 'ok' : 'error');
      return ok;
    }
  }

  var STATUS = {
    complete: ['已完成', 'badge--moss'],
    partial: ['部分完成', 'badge--brass'],
    failed: ['生成失败', 'badge--danger'],
    generating: ['生成中', 'badge--accent'],
    planning: ['规划中', 'badge--accent'],
    external: ['外部目录', ''],
    pending: ['等待', ''],
    done: ['完成', 'badge--moss']
  };

  function statusBadge(status) {
    var item = STATUS[status] || [status || '未知', ''];
    return '<span class="badge ' + item[1] + '">' + escapeHtml(item[0]) + '</span>';
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments;
      var context = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(context, args); }, wait);
    };
  }

  function scrollBottom(node) {
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }

  KL.ui = {
    byId: byId,
    icon: icon,
    escape: escapeHtml,
    toast: toast,
    modal: openModal,
    confirm: confirmDialog,
    drawer: openDrawer,
    copy: copyText,
    formatBytes: formatBytes,
    formatDate: formatDate,
    relativeTime: relativeTime,
    statusBadge: statusBadge,
    debounce: debounce,
    scrollBottom: scrollBottom
  };
})(window.KL = window.KL || {});
