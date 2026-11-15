(function (KL) {
  'use strict';

  var THEME_KEY = 'kl.theme';
  var GLASS_KEY = 'kl.glass';
  var SERIF_KEY = 'kl.noSerif';
  var root = document.documentElement;
  var state = { theme: 'archive', glass: false, noSerif: false };
  var lenses = [];
  var currentTargets = [];
  var glassTimer = 0;
  var glassToken = 0;
  var loading = false;
  var waiters = [];
  var glassSupport = null;

  function read() {
    try {
      state.theme = localStorage.getItem(THEME_KEY) === 'modern' ? 'modern' : 'archive';
      state.glass = localStorage.getItem(GLASS_KEY) === '1';
      state.noSerif = localStorage.getItem(SERIF_KEY) === '1';
    } catch (_) { /* storage unavailable */ }
  }

  function persist(key, value) {
    try { localStorage.setItem(key, value); } catch (_) { /* ignore */ }
  }

  function apply() {
    root.setAttribute('data-theme', state.theme);
    root.classList.toggle('no-serif', state.noSerif);
    root.classList.toggle('glass-on', state.theme === 'modern' && state.glass);
  }

  function targets() {
    var list = [];
    if (window.matchMedia && window.matchMedia('(max-width: 860px)').matches) return list;
    var glass = document.querySelector('.reader-glass');
    if (glass) list.push({ selector: '.reader-glass', el: glass, zIndex: 3, tint: 'rgba(255, 255, 255, 0.22)', content: false });
    return list;
  }

  function destroyGlass() {
    lenses.forEach(function (lens) {
      try { lens.destroy(); } catch (_) { /* already gone */ }
    });
    lenses = [];
    currentTargets = [];
  }

  function loadLiquid(callback) {
    if (window.liquidGL) { callback(); return; }
    waiters.push(callback);
    if (loading) return;
    loading = true;
    var script = document.createElement('script');
    script.src = '/assets/vendor/liquidGL.js';
    script.async = true;
    script.onload = function () {
      loading = false;
      var pending = waiters.slice();
      waiters = [];
      pending.forEach(function (fn) { fn(); });
    };
    script.onerror = function () {
      loading = false;
      waiters = [];
      root.classList.add('glass-fallback');
      if (window.console) console.warn('liquidGL 加载失败，已回退为 CSS 毛玻璃');
    };
    document.head.appendChild(script);
  }

  function sameTargets(next) {
    if (!lenses.length || next.length !== currentTargets.length) return false;
    for (var i = 0; i < next.length; i += 1) {
      if (next[i].el !== currentTargets[i]) return false;
    }
    return true;
  }

  function canUseGlass() {
    if (glassSupport !== null) return glassSupport;
    if (navigator.webdriver || /HeadlessChrome/i.test(navigator.userAgent || '')) {
      glassSupport = false;
      return glassSupport;
    }
    try {
      var canvas = document.createElement('canvas');
      var gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      if (!gl) { glassSupport = false; return glassSupport; }
      var ext = gl.getExtension('WEBGL_debug_renderer_info');
      var renderer = ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '') : '';
      glassSupport = !/swiftshader|software|llvmpipe|basic render/i.test(renderer);
      return glassSupport;
    } catch (_) {
      glassSupport = false;
      return glassSupport;
    }
  }

  function initGlass() {
    var token = ++glassToken;
    var next = targets();
    if (state.theme !== 'modern' || !state.glass || !next.length) {
      destroyGlass();
      return;
    }
    if (!canUseGlass()) {
      destroyGlass();
      root.classList.add('glass-fallback');
      return;
    }
    loadLiquid(function () {
      if (token !== glassToken) return;
      if (state.theme !== 'modern' || !state.glass) return;
      var fresh = targets();
      if (sameTargets(fresh)) return;
      destroyGlass();
      currentTargets = fresh.map(function (item) { return item.el; });
      fresh.forEach(function (item) {
        try {
          lenses.push(window.liquidGL({
            engine: 'webgl2',
            snapshot: 'body',
            target: item.selector,
            content: item.content,
            resolution: 1.0,
            refraction: 0,
            bevelDepth: 0,
            bevelWidth: 0,
            frost: 18,
            shadow: false,
            specular: false,
            tilt: false,
            interaction: 'none',
            magnify: 1,
            tint: 'rgba(255, 255, 255, 0.45)',
            reveal: 'none',
            zIndex: item.zIndex
          }));
        } catch (err) {
          root.classList.add('glass-fallback');
          if (window.console) console.warn('liquidGL 初始化失败，已回退为 CSS 毛玻璃', err);
        }
      });
      if (lenses.length) root.classList.remove('glass-fallback');
    });
  }

  function scheduleGlass() {
    clearTimeout(glassTimer);
    glassTimer = setTimeout(initGlass, 80);
  }

  function refresh() {
    apply();
    scheduleGlass();
  }

  function setTheme(value) {
    state.theme = value === 'modern' ? 'modern' : 'archive';
    persist(THEME_KEY, state.theme);
    apply();
    scheduleGlass();
  }

  function setGlass(value) {
    state.glass = !!value;
    persist(GLASS_KEY, state.glass ? '1' : '0');
    apply();
    scheduleGlass();
  }

  function setNoSerif(value) {
    state.noSerif = !!value;
    persist(SERIF_KEY, state.noSerif ? '1' : '0');
    apply();
  }

  read();
  KL.theme = {
    get: function () { return { theme: state.theme, glass: state.glass, noSerif: state.noSerif }; },
    apply: apply,
    refresh: refresh,
    setTheme: setTheme,
    setGlass: setGlass,
    setNoSerif: setNoSerif,
    destroyGlass: destroyGlass
  };
  apply();
  var resizeTimer = 0;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { apply(); scheduleGlass(); }, 160);
  });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { refresh(); });
  } else {
    refresh();
  }
})(window.KL = window.KL || {});
