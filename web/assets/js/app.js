(function (KL) {
  'use strict';

  var state = KL.state = {
    settings: null,
    kbs: [],
    kb: null,
    path: null,
    route: { name: 'library', kbId: null, pathId: null },
    chat: {
      mode: localStorage.getItem('kl.mode') || 'kb',
      thinking: localStorage.getItem('kl.thinking') || 'off',
      enableTools: localStorage.getItem('kl.editTools') !== '0',
      kbId: null,
      messages: [],
      loadedKey: null,
      streaming: false,
      controller: null
    },
    generation: null,
    openFile: null,
    highlights: {}
  };

  var routeToken = 0;

  function parseHash() {
    var raw = (location.hash || '').replace(/^#/, '');
    var parts = raw.split('/').filter(Boolean).map(function (part) { return decodeURIComponent(part); });
    if (parts[0] === 'settings') return { name: 'settings', kbId: null, pathId: null };
    if (parts[0] === 'library') {
      if (parts.length >= 3) return { name: 'path', kbId: parts[1], pathId: parts[2] };
      if (parts.length === 2) return { name: 'kb', kbId: parts[1], pathId: null };
      return { name: 'library', kbId: null, pathId: null };
    }
    return { name: 'library', kbId: null, pathId: null };
  }

  async function loadBase() {
    var results = await Promise.all([
      KL.api.get('/api/settings'),
      KL.api.get('/api/kbs')
    ]);
    state.settings = results[0];
    state.kbs = results[1].kbs || [];
    if (state.chat.kbId && !state.kbs.some(function (kb) { return kb.id === state.chat.kbId; })) {
      state.chat.kbId = null;
      state.chat.loadedKey = null;
    }
    if (!state.chat.kbId && state.kbs.length) state.chat.kbId = state.kbs[0].id;
    updateRail();
  }

  async function refreshBase() {
    try {
      await loadBase();
    } catch (err) {
      KL.ui.toast(err.message, 'error');
    }
  }

  function setRoute(route) {
    state.route = route;
    updateRail();
  }

  async function route() {
    var token = ++routeToken;
    if (location.hash === '#/chat' || location.hash === '#/web') {
      history.replaceState(null, '', location.pathname + '#/library');
    }
    var parsed = parseHash();
    if (!parsed.name) parsed = { name: 'library', kbId: null, pathId: null };
    setRoute(parsed);

    if (state.generation && state.generation.streaming) {
      var gen = state.generation;
      gen.controller.abort();
      gen.streaming = false;
    }

    try {
      await loadBase();
      if (token !== routeToken) return;

      if (parsed.name === 'kb') {
        state.kb = await KL.api.get('/api/kbs/' + encodeURIComponent(parsed.kbId));
      } else if (parsed.name === 'path') {
        var pair = await Promise.all([
          KL.api.get('/api/kbs/' + encodeURIComponent(parsed.kbId)),
          KL.api.get('/api/kbs/' + encodeURIComponent(parsed.kbId) + '/paths/' + encodeURIComponent(parsed.pathId))
        ]);
        state.kb = pair[0];
        state.path = pair[1];
      } else {
        state.kb = null;
        state.path = null;
      }
      if (token !== routeToken) return;
      KL.views.renderView();
      updateRail();
      if (KL.theme) KL.theme.refresh();
    } catch (err) {
      if (token !== routeToken) return;
      var root = document.getElementById('view-root');
      root.innerHTML = '<div class="page"><div class="empty"><div class="empty-mark">ERROR / 500</div>'
        + '<h3>页面加载失败</h3><p>' + KL.ui.escape(err.message || '未知错误') + '</p>'
        + '<div class="row" style="justify-content:center"><button class="btn btn-primary" id="reload-page">重新加载</button>'
        + '<a class="btn btn-ghost" href="#/settings">检查设置</a></div></div></div>';
      var reload = document.getElementById('reload-page');
      if (reload) reload.addEventListener('click', function () { location.reload(); });
    }
  }

  function go(hash) {
    if (location.hash === hash) {
      route();
      return;
    }
    location.hash = hash;
  }

  function updateRail() {
    var settings = state.settings;
    var stamp = document.getElementById('conn-stamp');
    var model = document.getElementById('rail-model');
    if (stamp) {
      stamp.className = 'stamp';
      if (!settings) {
        stamp.textContent = '正在连接';
      } else if (settings.api && settings.api.hasApiKey) {
        stamp.textContent = 'API 已连接';
        stamp.classList.add('is-ok');
      } else {
        stamp.textContent = 'API 未配置';
        stamp.classList.add('is-bad');
      }
    }
    if (model) {
      if (settings) {
        var host = settings.api.baseUrl || '';
        try { host = new URL(host).host; } catch (_) { /* keep */ }
        model.textContent = (settings.api.model || '未选择模型') + (host ? ' · ' + host : '');
        model.title = (settings.api.baseUrl || '') + '\n' + (settings.kb.root || '');
      } else {
        model.textContent = '—';
      }
    }
    var active = state.route.name;
    if (active === 'kb' || active === 'path') active = 'library';
    document.querySelectorAll('[data-nav]').forEach(function (link) {
      link.classList.toggle('is-active', link.getAttribute('data-nav') === active);
    });
  }

  function applyRailCollapsed(collapsed) {
    var shell = document.querySelector('.app-shell');
    var rail = document.querySelector('.rail');
    if (shell) shell.classList.toggle('is-rail-collapsed', collapsed);
    if (rail) rail.classList.toggle('is-collapsed', collapsed);
    var button = document.getElementById('rail-toggle');
    if (button) {
      button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      button.title = collapsed ? '展开侧边栏' : '收起侧边栏';
    }
    try { localStorage.setItem('kl.rail', collapsed ? '1' : '0'); } catch (_) { /* ignore */ }
  }

  function initRail() {
    applyRailCollapsed(localStorage.getItem('kl.rail') === '1');
    var button = document.getElementById('rail-toggle');
    if (button) {
      button.addEventListener('click', function () {
        var shell = document.querySelector('.app-shell');
        applyRailCollapsed(!(shell && shell.classList.contains('is-rail-collapsed')));
      });
    }
  }

  function init() {
    initRail();
    window.addEventListener('hashchange', route);
    if (!location.hash) {
      history.replaceState(null, '', location.pathname + '#/library');
    }
    route();
  }

  KL.app = {
    route: route,
    go: go,
    refreshBase: refreshBase,
    updateRail: updateRail,
    applyRailCollapsed: applyRailCollapsed,
    renderView: function () { KL.views.renderView(); updateRail(); if (KL.theme) KL.theme.refresh(); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window.KL = window.KL || {});
