(function (KL) {
  'use strict';

  var esc = KL.ui.escape;
  var icon = KL.ui.icon;
  var byId = KL.ui.byId;

  function val(value) { return esc(value == null ? '' : value); }

  async function runAction(button, task) {
    button.disabled = true;
    try {
      await task();
    } catch (err) {
      KL.ui.toast(err.message || '操作失败', 'error');
    } finally {
      button.disabled = false;
    }
  }

  function pageHead(eyebrow, title, sub, actions) {
    return '<div class="page-head"><div><div class="eyebrow">' + esc(eyebrow) + '</div>'
      + '<h1 class="page-title">' + title + '</h1>'
      + (sub ? '<p class="page-sub">' + esc(sub) + '</p>' : '')
      + '</div>'
      + (actions ? '<div class="page-actions">' + actions + '</div>' : '')
      + '</div>';
  }

  function page(inner) {
    return '<div class="page">' + inner + '</div>';
  }

  function card(title, no, body, actions, sub) {
    return '<section class="card">'
      + '<div class="card-head"><div>'
      + '<h2 class="card-title">' + (no ? '<span class="no">' + esc(no) + '</span>' : '') + title + '</h2>'
      + (sub ? '<p class="card-sub">' + esc(sub) + '</p>' : '')
      + '</div>' + (actions ? '<div class="row">' + actions + '</div>' : '') + '</div>'
      + body + '</section>';
  }

  function emptyState(mark, title, text, actions) {
    return '<div class="empty"><div class="empty-mark">' + esc(mark) + '</div>'
      + '<h3>' + esc(title) + '</h3><p>' + esc(text) + '</p>'
      + (actions ? '<div class="row" style="justify-content:center">' + actions + '</div>' : '')
      + '</div>';
  }

  function field(label, input, hint) {
    return '<div class="field"><label>' + esc(label) + '</label>' + input
      + (hint ? '<div class="hint">' + hint + '</div>' : '') + '</div>';
  }

  function input(id, value, placeholder, type) {
    return '<input class="input" id="' + id + '" type="' + (type || 'text') + '" value="' + val(value) + '" placeholder="' + val(placeholder) + '">';
  }

  function textarea(id, value, placeholder, rows) {
    return '<textarea class="textarea" id="' + id + '" rows="' + (rows || 3) + '" placeholder="' + val(placeholder) + '">' + val(value) + '</textarea>';
  }

  function select(id, options, current) {
    var html = '<select class="select" id="' + id + '">';
    options.forEach(function (item) {
      html += '<option value="' + esc(item[0]) + '"' + (String(item[0]) === String(current) ? ' selected' : '') + '>' + esc(item[1]) + '</option>';
    });
    return html + '</select>';
  }

  function sourceCountText(settings) {
    if (!settings) return '—';
    return settings.api.model || '—';
  }

  // ------------------------------------------------------------- settings
  function renderSettings() {
    var settings = KL.state.settings || {};
    var api = settings.api || {};
    var web = settings.web || {};
    var kb = settings.kb || {};
    var keyNote = api.hasApiKey ? '已保存 ' + (api.apiKeyMasked || '') : '尚未保存';
    if (api.keySource === 'env') keyNote += ' · 来自环境变量';
    var webNote = web.hasApiKey ? '已保存 ' + (web.apiKeyMasked || '') : '未配置';
    if (web.keySource === 'inherit') webNote += ' · 复用上方 Key';
    if (web.keySource === 'env') webNote += ' · 来自环境变量';

    var presets = [
      ['DeepSeek', 'https://api.deepseek.com', 'deepseek-chat'],
      ['OpenAI', 'https://api.openai.com/v1', 'gpt-4o-mini'],
      ['Ollama', 'http://127.0.0.1:11434/v1', 'llama3.1'],
      ['自定义', '', '']
    ];
    var presetHtml = presets.map(function (item) {
      return '<button type="button" class="chip" data-preset="' + esc(item[1]) + '" data-model="' + esc(item[2]) + '">' + esc(item[0]) + '</button>';
    }).join('');

    var left = '<div class="settings-stack">'
      + card('模型服务', '01',
        '<div class="chips" style="margin-bottom:18px">' + presetHtml + '</div>'
        + '<div class="form-grid">'
        + field('Base URL', input('set-base', api.baseUrl, 'https://api.deepseek.com'))
        + field('模型', input('set-model', api.model, 'deepseek-chat'))
        + '<div class="span-2">' + field('API Key', '<div class="row" style="flex-wrap:nowrap"><input class="input input-secret" id="set-key" type="password" placeholder="' + (api.hasApiKey ? '已保存，留空表示不修改' : 'sk-...') + '"><button type="button" class="btn btn-ghost btn-icon" id="toggle-key" aria-label="显示或隐藏 Key">' + icon('key') + '</button></div>',
          '当前：' + esc(keyNote) + '。Key 只保存在本机用户配置目录，接口不会回传明文。') + '</div>'
        + '</div>'
        + '<div class="row" style="margin-top:18px"><button class="btn btn-primary" id="save-api">保存模型配置</button>'
        + '<button class="btn btn-ghost" id="test-api">测试连接</button>'
        + (api.hasApiKey ? '<button class="btn btn-danger btn-sm" id="clear-key">清除 Key</button>' : '')
        + '</div>'
        + '<div class="status-line" id="api-status"><span class="dot"></span><span>保存后可测试连接（会发起 1 次极小的模型请求）</span></div>',
        '', '兼容 OpenAI Chat Completions 协议；本地服务可留空 Key。')
      + card('知识库根目录', '02',
        '<div class="form-row"><div class="field" style="flex:1">' + input('set-root', kb.root, 'D:/KnowledgeLib/library') + '</div>'
        + '<button class="btn btn-primary" id="save-root">保存并初始化</button></div>'
        + '<div class="hint" style="margin-top:10px">每个知识库会在这个目录下新建独立文件夹；每个学习路径再占一个子文件夹。当前目录：' + esc(kb.root || '—') + '</div>',
        '', '目录不存在时会自动创建并检测可写性。')
      + card('DeepSeek 联网接口', '03',
        '<div class="form-grid">'
        + field('Web 接口 Base URL', input('set-web-base', web.baseUrl, 'https://api.deepseek.com/v1'))
        + field('Web API Key（可选）', '<input class="input input-secret" id="set-web-key" type="password" placeholder="' + (web.hasApiKey ? '已保存，留空表示不修改' : '留空则复用上方 Key') + '">')
        + field('默认搜索条数', input('set-search-count', web.searchCount, '5', 'number'))
        + field('自动抓取网页数', input('set-fetch-top', web.fetchTopN, '2', 'number'))
        + '</div>'
        + '<div class="hint" style="margin-top:10px">当前：' + esc(webNote) + '。Search 与 Fetch 按成功请求计费（每千次约 5 元），测试按钮会产生 2 次请求。</div>'
        + '<div class="row" style="margin-top:14px"><button class="btn btn-primary" id="save-web">保存联网配置</button><button class="btn btn-ghost" id="test-web">测试 Search + Fetch</button></div>'
        + '<div id="web-status" class="status-line"><span class="dot"></span><span>未测试</span></div>',
        '', '对应 DeepSeek 官方 /v1/tools/search 与 /v1/tools/fetch。')
      + '</div>';

    var appearance = KL.theme ? KL.theme.get() : { theme: 'archive', glass: false, noSerif: false };
    var modern = appearance.theme === 'modern';
    var right = '<div class="settings-stack">'
      + card('外观', '04',
        '<div class="theme-picker">'
        + '<button type="button" class="theme-choice' + (modern ? '' : ' is-on') + '" data-theme-choice="archive" aria-pressed="' + (modern ? 'false' : 'true') + '">'
        + '<span class="theme-swatch theme-swatch--archive"></span><strong>档案室</strong><em>纸张 · 衬线 · 朱砂</em></button>'
        + '<button type="button" class="theme-choice' + (modern ? ' is-on' : '') + '" data-theme-choice="modern" aria-pressed="' + (modern ? 'true' : 'false') + '">'
        + '<span class="theme-swatch theme-swatch--modern"></span><strong>现代</strong><em>玻璃 · 圆角 · 冷色</em></button>'
        + '</div>'
        + '<label class="theme-checkline' + (modern ? '' : ' is-disabled') + '"><input type="checkbox" id="theme-glass"' + (appearance.glass ? ' checked' : '') + (modern ? '' : ' disabled') + '>'
        + '<span><strong>液态玻璃</strong><small>使用 liquidGL 为阅读助手加玻璃质感；不支持 WebGL 时自动回退为 CSS 毛玻璃。侧栏与卡片本身使用 CSS 玻璃。</small></span></label>'
        + '<label class="theme-checkline"><input type="checkbox" id="theme-no-serif"' + (appearance.noSerif ? ' checked' : '') + '>'
        + '<span><strong>禁用衬线字体</strong><small>兼容性选项：标题、正文与引用全部改用无衬线字体。</small></span></label>'
        + '<div class="theme-note hint" id="theme-note">' + (modern ? '现代主题已启用；液态玻璃可随时开关。' : '档案室主题保留纸张质感与衬线标题。') + '</div>',
        '', '外观设置保存在本机浏览器，不会写入用户配置。')
      + card('使用顺序', '05',
        '<ol class="md" style="padding-left:20px;margin:0"><li>保存模型配置并测试连接。</li><li>设置知识库根目录。</li><li>到文库新建知识库。</li><li>进入知识库，填写领域、周期与偏好，生成学习路径。</li></ol>',
        '', '生成会调用 1 次规划 + N 次讲义请求。')
      + card('安全与边界', '06',
        '<p class="settings-note">应用只监听 127.0.0.1；API Key 保存在用户配置目录，前端只拿到掩码。所有资料写入你配置的目录，删除采用软删除（data/trash）。</p>'
      + (settings.configPath ? '<p class="mono small muted" style="margin-top:10px;word-break:break-all">配置：' + esc(settings.configPath) + '</p>' : '')
        + '<p class="settings-note" style="margin-top:10px">DeepSeek Search / Fetch 是外部请求，会把搜索词与 URL 发送到接口服务商。</p>',
        '', '')
      + '<div class="card"><div class="eyebrow">ARCHIVE NOTE</div><div class="serif" style="font-size:19px;line-height:1.5;margin-top:6px">“先把路径写出来，<br>再让每一讲自己长成资料。”</div>'
        + '<div class="hint" style="margin-top:10px">资料编号固定为 00 总览 + 01..NN 讲义，文件名与正文首行都会带编号。</div></div>'
      + '</div>';

    return page(pageHead('SETTINGS / 04', '设置', '模型、知识库、Git 与联网接口的工作台。', '<a class="btn btn-ghost" href="#/library">' + icon('library') + '去文库</a>')
      + '<div class="settings-grid">' + left + right + '</div>'
      + renderGitCard());
  }

  function bindSettings(root) {
    var settings = KL.state.settings || {};
    if (KL.theme) {
      root.querySelectorAll('[data-theme-choice]').forEach(function (button) {
        button.addEventListener('click', function () {
          KL.theme.setTheme(button.getAttribute('data-theme-choice'));
          if (KL.app && KL.app.renderView) KL.app.renderView();
        });
      });
      var glassToggle = byId('theme-glass');
      if (glassToggle) glassToggle.addEventListener('change', function () {
        KL.theme.setGlass(glassToggle.checked);
        if (KL.app && KL.app.renderView) KL.app.renderView();
      });
      var serifToggle = byId('theme-no-serif');
      if (serifToggle) serifToggle.addEventListener('change', function () {
        KL.theme.setNoSerif(serifToggle.checked);
        if (KL.app && KL.app.renderView) KL.app.renderView();
      });
    }
    root.querySelectorAll('[data-preset]').forEach(function (button) {
      button.addEventListener('click', function () {
        var base = button.getAttribute('data-preset');
        var model = button.getAttribute('data-model');
        if (base) byId('set-base').value = base;
        if (model) byId('set-model').value = model;
      });
    });
    byId('toggle-key').addEventListener('click', function () {
      var inputNode = byId('set-key');
      inputNode.type = inputNode.type === 'password' ? 'text' : 'password';
    });
    byId('save-api').addEventListener('click', async function () {
      var payload = { api: { baseUrl: byId('set-base').value.trim(), model: byId('set-model').value.trim() } };
      var key = byId('set-key').value.trim();
      if (key) payload.api.apiKey = key;
      await runAction(byId('save-api'), async function () {
        await KL.api.put('/api/settings', payload);
        KL.ui.toast('模型配置已保存', 'ok');
        await KL.app.refreshBase();
        KL.app.route();
      });
    });
    byId('test-api').addEventListener('click', async function () {
      var button = byId('test-api');
      var status = byId('api-status');
      status.innerHTML = '<span class="loading"></span><span>正在请求模型…</span>';
      button.disabled = true;
      try {
        var result = await KL.api.post('/api/settings/test', {});
        if (result.ok) {
          status.innerHTML = '<span class="dot ok"></span><span>连接成功 · ' + esc(result.model || '') + ' · ' + result.latencyMs + ' ms · 回复：' + esc(result.reply || '') + '</span>';
        } else {
          status.innerHTML = '<span class="dot bad"></span><span>' + esc(result.message || '连接失败') + '</span>';
        }
      } catch (err) {
        status.innerHTML = '<span class="dot bad"></span><span>' + esc(err.message) + '</span>';
      } finally {
        button.disabled = false;
      }
    });
    var clearButton = byId('clear-key');
    if (clearButton) {
      clearButton.addEventListener('click', async function () {
        if (!(await KL.ui.confirm({ title: '清除 API Key', message: '删除本机保存的 Key？之后生成与对话会不可用。', danger: true, confirmText: '清除' }))) return;
        await KL.api.put('/api/settings', { api: { clearApiKey: true } });
        KL.ui.toast('Key 已清除', 'ok');
        await KL.app.refreshBase();
        KL.app.route();
      });
    }
    byId('save-root').addEventListener('click', async function () {
      await runAction(byId('save-root'), async function () {
        await KL.api.put('/api/settings', { kb: { root: byId('set-root').value.trim() } });
        KL.ui.toast('根目录已保存并初始化', 'ok');
        await KL.app.refreshBase();
        KL.app.route();
      });
    });
    byId('save-web').addEventListener('click', async function () {
      var payload = { web: {
        baseUrl: byId('set-web-base').value.trim(),
        searchCount: Number(byId('set-search-count').value),
        fetchTopN: Number(byId('set-fetch-top').value)
      } };
      var key = byId('set-web-key').value.trim();
      if (key) payload.web.apiKey = key;
      await runAction(byId('save-web'), async function () {
        await KL.api.put('/api/settings', payload);
        KL.ui.toast('联网配置已保存', 'ok');
        await KL.app.refreshBase();
        KL.app.route();
      });
    });
    byId('test-web').addEventListener('click', async function () {
      var status = byId('web-status');
      var button = byId('test-web');
      button.disabled = true;
      status.innerHTML = '<span class="loading"></span><span>正在调用 Search 与 Fetch…</span>';
      try {
        var result = await KL.api.post('/api/settings/test-web', {});
        var lines = (result.checks || []).map(function (check) {
          return (check.ok ? '✓ ' : '✗ ') + (check.name === 'search' ? 'Search' : 'Fetch') + ' · ' + check.latencyMs + 'ms · ' + (check.ok ? check.detail : check.message);
        });
        status.innerHTML = '<span class="dot ' + (result.ok ? 'ok' : 'bad') + '"></span><span>' + esc(lines.join(' ｜ ')) + '</span>';
      } catch (err) {
        status.innerHTML = '<span class="dot bad"></span><span>' + esc(err.message) + '</span>';
      } finally {
        button.disabled = false;
      }
    });
    bindGitCard();
  }

  // ------------------------------------------------------------- library
  function renderLibrary() {
    var kbs = KL.state.kbs || [];
    var settings = KL.state.settings || {};
    var root = (settings.kb && settings.kb.root) || '';
    var actions = '<button class="btn btn-ghost" id="edit-root">' + icon('edit') + '修改根目录</button>'
      + '<button class="btn btn-primary" id="new-kb">' + icon('plus') + '新建知识库</button>';
    var body = '<div class="root-line" style="margin-bottom:22px">' + icon('folder')
      + '<span class="path" title="' + val(root) + '">' + esc(root) + '</span>'
      + '<span class="spacer"></span><span class="badge">' + kbs.length + ' 个库</span></div>';

    if (!kbs.length) {
      body += emptyState('LIBRARY / 00', '书架还是空的', '设置知识库根目录后，新建第一个知识库；每个知识库可以生成多条学习路径。',
        '<button class="btn btn-primary" id="new-kb-empty">' + icon('plus') + '新建知识库</button>');
    } else {
      body += '<div class="kb-grid">' + kbs.map(function (kb) {
        return '<a class="kb-card" href="#/library/' + encodeURIComponent(kb.id) + '">'
          + '<div class="row"><span class="badge badge--brass">' + esc(kb.dirName) + '</span><span class="spacer"></span><span class="mono small muted">' + KL.ui.relativeTime(kb.updatedAt) + '</span></div>'
          + '<h3>' + esc(kb.name) + '</h3>'
          + '<p class="kb-desc">' + esc(kb.description || '尚未填写描述。') + '</p>'
          + '<div class="kb-meta"><div><div class="kb-count">' + kb.pathCount + '</div><span>学习路径</span></div>'
          + '<div style="text-align:right"><div class="kb-count">' + kb.fileCount + '</div><span>编号资料 · ' + KL.ui.formatBytes(kb.totalBytes) + '</span></div></div>'
          + '</a>';
      }).join('') + '</div>';
    }

    return page(pageHead('LIBRARY / 02', '文库', '知识库是你存放学习路径的书架；每条路径都是一个独立目录。', actions) + body);
  }

  function bindLibrary(root) {
    var go = function () { KL.app.go('#/library'); };
    var newButtons = [byId('new-kb'), byId('new-kb-empty')].filter(Boolean);
    newButtons.forEach(function (button) { button.addEventListener('click', openNewKb); });
    byId('edit-root').addEventListener('click', function () {
      var current = (KL.state.settings.kb && KL.state.settings.kb.root) || '';
      var modal = KL.ui.modal({
        title: '知识库根目录',
        sub: '每个知识库会在这里创建一个独立文件夹。',
        body: field('绝对路径', input('root-edit', current, 'D:/KnowledgeLib/library')),
        footer: '<button class="btn btn-ghost" data-close>取消</button><button class="btn btn-primary" id="save-root-edit">保存</button>'
      });
      modal.root.querySelector('#save-root-edit').addEventListener('click', async function () {
        try {
          await KL.api.put('/api/settings', { kb: { root: byId('root-edit').value.trim() } });
          modal.close();
          KL.ui.toast('根目录已更新', 'ok');
          await KL.app.refreshBase();
          KL.app.route();
        } catch (err) { KL.ui.toast(err.message, 'error'); }
      });
    });
  }

  function openNewKb() {
    var modal = KL.ui.modal({
      title: '新建知识库',
      sub: '一个知识库对应根目录下的一个独立文件夹。',
      body: '<div class="stack">' + field('名称', input('kb-name', '', '例如：编程语言')) + field('描述（可选）', textarea('kb-desc', '', '这个库准备装什么学习路径？', 3)) + '</div><div class="form-error" id="kb-error" hidden></div>',
      footer: '<button class="btn btn-ghost" data-close>取消</button><button class="btn btn-primary" id="create-kb">' + icon('plus') + '添加</button>'
    });
    var submit = async function () {
      var error = byId('kb-error');
      error.hidden = true;
      var name = byId('kb-name').value.trim();
      if (!name) { error.textContent = '请填写知识库名称'; error.hidden = false; return; }
      var button = byId('create-kb');
      button.disabled = true;
      try {
        var detail = await KL.api.post('/api/kbs', { name: name, description: byId('kb-desc').value.trim() });
        modal.close();
        KL.ui.toast('知识库已创建', 'ok');
        await KL.app.refreshBase();
        KL.app.go('#/library/' + encodeURIComponent(detail.id));
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        button.disabled = false;
      }
    };
    byId('create-kb').addEventListener('click', submit);
    byId('kb-name').addEventListener('keydown', function (event) { if (event.key === 'Enter') submit(); });
  }

  function openRenameDialog(options) {
    var modal = KL.ui.modal({
      title: options.title,
      sub: '只修改显示名称；磁盘目录名与文件内容不受影响。',
      body: field('名称', input(options.inputId, options.current, '请输入新的名称'))
        + '<div class="form-error" id="rename-error" hidden></div>',
      footer: '<button class="btn btn-ghost" data-close>取消</button>'
        + '<button class="btn btn-primary" id="rename-save">保存</button>'
    });
    var inputNode = modal.root.querySelector('#' + options.inputId);
    var error = modal.root.querySelector('#rename-error');
    var button = modal.root.querySelector('#rename-save');
    var submit = async function () {
      error.hidden = true;
      var name = inputNode.value.trim();
      if (!name) {
        error.textContent = '名称不能为空';
        error.hidden = false;
        inputNode.focus();
        return;
      }
      button.disabled = true;
      try {
        await options.save(name);
        modal.close();
        KL.ui.toast(options.toast, 'ok');
        KL.app.route();
      } catch (err) {
        error.textContent = err.message || '重命名失败';
        error.hidden = false;
        button.disabled = false;
      }
    };
    button.addEventListener('click', submit);
    inputNode.addEventListener('click', function () { inputNode.select(); });
    inputNode.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        submit();
      }
    });
    setTimeout(function () { inputNode.select(); }, 60);
  }

  // ------------------------------------------------------------- kb detail
  function moduleGoalChips() {
    var goals = [
      ['system', '系统掌握'], ['quickstart', '快速上手'], ['project', '项目实战'], ['interview', '面试准备'], ['basics', '补齐基础']
    ];
    return '<div class="chips" id="goal-chips">' + goals.map(function (item, index) {
      return '<button type="button" class="chip' + (item[0] === 'project' ? ' is-on' : '') + '" data-goal="' + item[0] + '">' + esc(item[1]) + '</button>';
    }).join('') + '</div>';
  }

  function defaultModuleCount(total) {
    return Math.max(4, Math.min(12, Math.round(total / 5)));
  }

  // ------------------------------------------------------------------ git
  function gitBadge(git) {
    if (!git || git.available === false) return '<span class="badge badge--danger">Git 不可用</span>';
    if (git.legacyRepos && git.legacyRepos.length) return '<span class="badge badge--danger">需迁移旧仓库</span>';
    if (!git.managed) return '<span class="badge">未初始化</span>';
    if (git.conflicts && git.conflicts.length) return '<span class="badge badge--danger">有冲突</span>';
    if (git.dirty) return '<span class="badge badge--brass">有本地改动</span>';
    return '<span class="badge badge--moss">已初始化</span>';
  }

  function gitDirectionLabel(value) {
    return ({ sync: '先拉后推', pull: '仅拉取', push: '仅推送' })[value] || '先拉后推';
  }

  function gitStrategyLabel(value) {
    return ({ abort: '停止并保留现场', local: '优先本地', remote: '优先远端', manual: '保留冲突待手工' })[value] || '停止并保留现场';
  }

  function gitConflictText(git) {
    var list = (git && git.conflicts) || [];
    if (!list.length) return '';
    return '冲突文件：' + list.slice(0, 8).join('、') + (list.length > 8 ? ' 等 ' + list.length + ' 个' : '');
  }

  function renderGitCard() {
    var git = KL.state.git || {};
    var settings = git.settings || {};
    var root = git.root || ((KL.state.settings && KL.state.settings.kb && KL.state.settings.kb.root) || '');
    var legacy = git.legacyRepos || [];
    var body;
    if (git.available === false) {
      body = '<div class="form-error">' + esc(git.error || '当前环境没有可用的 Git。') + '</div>'
        + '<p class="hint" style="margin-top:12px">安装 Git 并确保 <span class="mono">git</span> 在 PATH 中，然后刷新本页。Git 只在本机执行，不会上传任何资料到本应用以外的服务。</p>';
    } else if (!git.managed) {
      body = '<p class="settings-note">为整个文库集创建<strong>一个</strong> Git 仓库，覆盖知识库根目录下的全部知识库。根目录：<span class="mono">' + esc(root || '—') + '</span>。初始化会把当前全部资料提交为第一个版本；remote 可稍后再填。</p>'
        + (legacy.length ? '<div class="git-warning"><strong>检测到旧版每库仓库</strong><span>' + esc(legacy.slice(0, 8).join('、')) + (legacy.length > 8 ? ' 等 ' + legacy.length + ' 个' : '')
          + '。全局同步不会包含这些独立仓库；可勾选下方迁移，把旧 .git 备份到 data/trash/git-repos 后再建立根仓库。</span></div>' : '')
        + '<div class="form-grid" style="margin-top:16px">'
        + '<div class="span-2">' + field('远端地址（可选）', input('git-remote-init', '', 'https://github.com/user/repo.git 或 git@github.com:user/repo.git')) + '</div>'
        + field('分支', input('git-branch-init', 'main', 'main'))
        + '</div>'
        + (legacy.length ? '<label class="check-line" style="margin-top:12px"><input type="checkbox" id="git-migrate-legacy"> <span>迁移旧版每库 .git（先备份到 data/trash/git-repos）</span></label>' : '')
        + '<div class="row" style="margin-top:16px"><button class="btn btn-primary" id="git-init">' + icon('git') + '初始化文库集仓库</button>'
        + '<span class="hint">初始化后仍可单独修改 remote。</span></div>'
        + '<div class="form-error" id="git-error" hidden></div>';
    } else {
      var last = git.lastCommit;
      var lastText = last ? (esc(last.short) + ' · ' + esc(last.subject) + ' · ' + KL.ui.relativeTime(last.at)) : '还没有提交';
      var upstreamText = git.upstream ? '跟踪 ' + esc(git.upstream) : '未设置上游分支';
      var ahead = git.ahead == null ? 0 : git.ahead;
      var behind = git.behind == null ? 0 : git.behind;
      var syncText = git.upstream ? ('↑' + ahead + ' · ↓' + behind) : '—';
      var conflictHtml = '';
      if (git.conflicts && git.conflicts.length) {
        conflictHtml = '<div class="git-warning"><strong>有未解决的冲突</strong><span>' + esc(gitConflictText(git))
          + '。当前策略：' + esc(gitStrategyLabel(settings.strategy)) + '。切换策略后再次同步，或手工解决。</span></div>';
      }
      var lastClass = git.lastStatus === 'error' ? ' is-bad' : (git.lastStatus === 'ok' ? ' is-ok' : '');
      body = '<div class="git-status-grid">'
        + '<div class="git-stat"><span>分支</span><strong class="mono">' + esc(git.branch || 'main') + '</strong><small>' + upstreamText + '</small></div>'
        + '<div class="git-stat git-stat--wide"><span>远端</span><strong class="git-remote" title="' + val(git.remoteUrl) + '">' + (git.remoteUrl ? esc(git.remoteUrl) : '未设置') + '</strong><small>' + (git.remoteUrl ? 'origin' : '请先保存 remote') + '</small></div>'
        + '<div class="git-stat git-stat--wide"><span>范围</span><strong class="git-remote" title="' + val(root) + '">' + esc(root || '—') + '</strong><small>' + esc(git.kbCount || 0) + ' 个知识库 · 整个文库集</small></div>'
        + '<div class="git-stat"><span>工作区</span><strong>' + (git.dirty ? esc(git.changedFiles) + ' 个改动' : '干净') + '</strong><small>' + (git.changed && git.changed.length ? esc(git.changed.slice(0, 3).join(' · ')) : '没有待提交内容') + '</small></div>'
        + '<div class="git-stat"><span>本地 / 远端</span><strong class="mono">' + syncText + '</strong><small>' + (git.upstream ? '领先 / 落后提交数' : '完成首次拉取或推送后显示') + '</small></div>'
        + '<div class="git-stat git-stat--wide"><span>最近提交</span><strong class="git-last-commit">' + lastText + '</strong><small>' + (settings.autoSync ? '自动同步：每 ' + esc(settings.intervalMinutes || 15) + ' 分钟 · ' + esc(gitDirectionLabel(settings.autoDirection)) : '自动同步未开启') + '</small></div>'
        + '</div>'
        + conflictHtml
        + '<div class="row git-actions">'
        + '<button class="btn btn-accent" id="git-sync">' + icon('refresh') + '立即同步</button>'
        + '<button class="btn btn-ghost" id="git-pull">拉取</button>'
        + '<button class="btn btn-ghost" id="git-push">推送</button>'
        + '<button class="btn btn-ghost btn-sm" id="git-refresh">' + icon('refresh') + '刷新状态</button>'
        + '<span class="git-last' + lastClass + '">' + (git.lastSyncAt ? '上次成功：' + KL.ui.formatDate(git.lastSyncAt) : '尚未同步') + '</span>'
        + '</div>'
        + '<div class="git-last' + lastClass + '">' + (git.lastMessage ? '最近结果：' + esc(git.lastMessage) : '') + '</div>'
        + '<div class="git-remote-row"><div class="field" style="flex:1"><label>Remote URL</label>'
        + '<input class="input" id="git-remote" type="text" value="' + val(git.remoteUrl) + '" placeholder="https://github.com/user/repo.git">'
        + '<div class="hint">作用于整个知识库根目录，而不是单个知识库；也支持 SSH、file:// 与本地路径。remote 名称固定为 origin。</div></div>'
        + '<button class="btn btn-primary" id="git-save-remote">保存 remote</button></div>'
        + '<div class="git-settings">'
        + '<div class="git-settings-head"><strong>全局自动同步与冲突处理</strong><span class="hint">设置写入用户配置 settings.json 的 git 段；服务每 30 秒检查一次，到期才执行。</span></div>'
        + '<div class="form-grid">'
        + '<label class="check-line"><input type="checkbox" id="git-auto-sync"' + (settings.autoSync ? ' checked' : '') + '> <span>开启自动同步</span></label>'
        + field('间隔（分钟）', input('git-interval', settings.intervalMinutes || 15, '15', 'number'))
        + field('自动方向', select('git-direction', [['sync', '先拉后推'], ['pull', '仅拉取'], ['push', '仅推送']], settings.autoDirection || 'sync'))
        + field('冲突处理', select('git-strategy', [['abort', '停止并保留现场'], ['local', '优先本地'], ['remote', '优先远端'], ['manual', '保留冲突待手工']], settings.strategy || 'abort'))
        + '<label class="check-line"><input type="checkbox" id="git-auto-commit"' + (settings.autoCommit === false ? '' : ' checked') + '> <span>同步前自动提交本地改动</span></label>'
        + '</div>'
        + '<details class="git-advanced"><summary>自定义推送 / 拉取命令（可选）</summary>'
        + '<p class="hint">命令必须以 <span class="mono">git</span> 或 <span class="mono">gh</span> 开头，不经过 shell；gh 需已安装并登录。支持占位符 <span class="mono">{branch}</span>、<span class="mono">{remote}</span>、<span class="mono">{url}</span>、<span class="mono">{kb}</span>。留空则使用内置的 fetch/merge 与 push。</p>'
        + '<div class="form-grid">'
        + '<div class="span-2">' + field('自定义拉取命令', input('git-pull-command', settings.pullCommand || '', 'git pull --rebase origin {branch}')) + '</div>'
        + '<div class="span-2">' + field('自定义推送命令', input('git-push-command', settings.pushCommand || '', 'git push origin {branch}')) + '</div>'
        + '</div></details>'
        + '<div class="row" style="margin-top:14px"><button class="btn btn-primary" id="git-save-settings">保存同步设置</button>'
        + '<span class="hint">手动同步会使用当前设置；拉取后会自动刷新整个文库索引。</span></div>'
        + '</div>'
        + '<div class="form-error" id="git-error" hidden></div>';
    }
    return '<section class="card git-card" id="git-card">'
      + '<div class="card-head"><div><h2 class="card-title"><span class="no">GIT</span>文库集 Git 同步</h2>'
      + '<p class="card-sub">整个知识库根目录共用一个仓库、一个 remote 与一套同步设置。</p></div>' + gitBadge(git) + '</div>'
      + body + '</section>';
  }

  function showGitError(err) {
    var box = byId('git-error');
    var message = err && err.message ? err.message : 'Git 操作失败';
    var payload = err && err.details && err.details.error ? err.details.error.details : null;
    if (payload && payload.conflicts && payload.conflicts.length) {
      message += '：' + payload.conflicts.slice(0, 8).join('、');
    }
    if (payload && payload.repos && payload.repos.length) {
      message += '：' + payload.repos.slice(0, 8).join('、');
    }
    if (box) {
      box.textContent = message;
      box.hidden = false;
    } else {
      KL.ui.toast(message, 'error');
    }
  }

  async function reloadGitCard() {
    try {
      KL.state.git = await KL.api.get('/api/git');
    } catch (err) {
      KL.state.git = { available: false, managed: false, error: err.message, settings: {} };
    }
    var card = byId('git-card');
    if (card) {
      card.outerHTML = renderGitCard();
      bindGitCard();
    }
  }

  function bindGitCard() {
    if (!byId('git-card')) return;
    var runGitAction = async function (button, task, refreshOnError) {
      button.disabled = true;
      try {
        await task();
      } catch (err) {
        if (refreshOnError) await reloadGitCard();
        showGitError(err);
        button.disabled = false;
      }
    };
    var init = byId('git-init');
    if (init) {
      init.addEventListener('click', function () {
        runGitAction(init, async function () {
          var migrate = byId('git-migrate-legacy');
          await KL.api.post('/api/git/init', {
            remoteUrl: byId('git-remote-init').value.trim(),
            branch: byId('git-branch-init').value.trim() || 'main',
            migrateLegacy: !!(migrate && migrate.checked)
          });
          KL.ui.toast('文库集 Git 仓库已初始化并提交', 'ok');
          await reloadGitCard();
        });
      });
    }
    var saveRemote = byId('git-save-remote');
    if (saveRemote) {
      saveRemote.addEventListener('click', function () {
        runGitAction(saveRemote, async function () {
          await KL.api.put('/api/git/remote', {
            url: byId('git-remote').value.trim()
          });
          KL.ui.toast('Remote 已保存', 'ok');
          await reloadGitCard();
        });
      });
    }
    var saveSettings = byId('git-save-settings');
    if (saveSettings) {
      saveSettings.addEventListener('click', function () {
        runGitAction(saveSettings, async function () {
          var interval = Number(byId('git-interval').value);
          await KL.api.put('/api/git/settings', {
            autoSync: !!byId('git-auto-sync').checked,
            autoCommit: !!byId('git-auto-commit').checked,
            intervalMinutes: interval,
            autoDirection: byId('git-direction').value,
            strategy: byId('git-strategy').value,
            pullCommand: byId('git-pull-command').value.trim(),
            pushCommand: byId('git-push-command').value.trim()
          });
          KL.ui.toast('文库集同步设置已保存', 'ok');
          await reloadGitCard();
        });
      });
    }
    [['git-sync', 'sync'], ['git-pull', 'pull'], ['git-push', 'push']].forEach(function (item) {
      var button = byId(item[0]);
      if (!button) return;
      button.addEventListener('click', function () {
        runGitAction(button, async function () {
          var result = await KL.api.post('/api/git/sync', {
            direction: item[1]
          });
          KL.ui.toast(result.result && result.result.message ? result.result.message : '同步完成', 'ok');
          await reloadGitCard();
        }, true);
      });
    });
    var refresh = byId('git-refresh');
    if (refresh) refresh.addEventListener('click', function () { reloadGitCard(); });
  }

  function renderKb() {
    var kb = KL.state.kb || {};
    var settings = KL.state.settings || {};
    var paths = kb.paths || [];
    var hasKey = !!(settings.api && settings.api.hasApiKey);
    var deepseek = !!(settings.api && settings.api.provider === 'deepseek');
    var actions = '<a class="btn btn-ghost" href="#/library">' + icon('arrowLeft') + '返回文库</a>'
      + '<button class="btn btn-ghost" id="rename-kb">' + icon('edit') + '重命名</button>'
      + '<button class="btn btn-danger btn-sm" id="delete-kb">' + icon('trash') + '删除知识库</button>';
    var head = pageHead('LIBRARY / ' + (kb.name || '知识库'), esc(kb.name || '知识库'),
      kb.description || '在下面填写学习需求，生成一条编号学习路径。', actions);

    var form = '<div class="card">'
      + '<div class="card-head"><div><h2 class="card-title"><span class="no">01</span>新建学习路径</h2>'
      + '<p class="card-sub">输入领域、预期学习时间与偏好，应用会先规划，再逐讲写讲义。</p></div>'
      + '<span class="badge badge--accent">' + (hasKey ? '模型已配置' : '需要 API Key') + '</span></div>'
      + (hasKey ? '' : '<div class="form-error" style="margin-bottom:16px">还没有可用的 API Key。请先到 <a href="#/settings">设置</a> 填写并保存。</div>')
      + '<div class="form-grid">'
      + '<div class="span-2">' + field('想学习的领域', input('gen-domain', '', '例如：Rust 异步编程 / 中国古籍修复 / 产品经理数据分析')) + '</div>'
      + field('预期周期（周）', input('gen-weeks', '4', '4', 'number'))
      + field('每周投入（小时）', input('gen-hours', '10', '10', 'number'))
      + field('基础水平', select('gen-level', [['zero', '零基础'], ['beginner', '入门'], ['intermediate', '进阶'], ['advanced', '高级']], 'zero'))
      + field('模块数（留空自动）', input('gen-count', '', '自动', 'number'))
      + '<div class="span-2">' + field('目标偏好', moduleGoalChips()) + '</div>'
      + '<div class="span-2">' + field('偏好要求', textarea('gen-prefs', '', '例如：偏重可运行示例，每天只能投入 1.5 小时，希望有练习与自测。', 3)) + '</div>'
      + '<div class="span-2">' + field('路径名称（可选）', input('gen-name', '', '默认使用模型给出的标题')) + '</div>'
      + '<div class="span-2 gen-options">'
      + '<label class="check-line"><input type="checkbox" id="gen-web"' + (deepseek ? '' : ' disabled') + '> <span>联网生成</span></label>'
      + '<span class="hint">' + (deepseek ? '使用 DeepSeek 服务端 web_tools；联网时自动用 deepseek-flash。' : '当前模型不是 DeepSeek，联网生成不可用。') + '</span>'
      + '<label class="gen-thinking"><span class="mono small">思考强度</span>'
      + select('gen-thinking', [['off', '关闭'], ['low', '低'], ['high', '高'], ['max', '最高']], 'off') + '</label>'
      + '</div>'
      + '</div>'
      + '<div class="row" style="margin-top:18px;justify-content:space-between;align-items:flex-end">'
      + '<div class="hint" id="gen-summary">总计 — 小时 · 建议 — 个模块</div>'
      + '<button class="btn btn-accent" id="start-gen"' + (hasKey ? '' : ' disabled') + '>' + icon('spark') + '生成学习路径</button>'
      + '</div><div class="form-error" id="gen-form-error" hidden></div></div>';

    var list = '<div class="card"><div class="card-head"><div><h2 class="card-title"><span class="no">02</span>已有学习路径</h2>'
      + '<p class="card-sub">' + paths.length + ' 条路径 · ' + paths.reduce(function (sum, item) { return sum + (item.files || []).length; }, 0) + ' 个编号文件</p></div></div>';
    if (!paths.length) {
      list += '<div class="hint">还没有学习路径。填写上面的表单，生成第一条。</div>';
    } else {
      list += '<div class="path-grid">' + paths.map(function (path) {
        var total = path.moduleCount || 0;
        var done = path.doneCount || 0;
        var pct = total ? Math.round(done / total * 100) : 0;
        return '<a class="path-card" href="#/library/' + encodeURIComponent(kb.id) + '/' + encodeURIComponent(path.id) + '">'
          + '<div class="path-meta">' + KL.ui.statusBadge(path.status)
          + '<span class="badge">' + esc(path.domain || '—') + '</span>'
          + '<span class="spacer"></span><span class="mono small muted">' + KL.ui.relativeTime(path.updatedAt) + '</span></div>'
          + '<h4>' + esc(path.name) + '</h4>'
          + '<div class="hint">' + done + ' / ' + total + ' 讲 · ' + (path.files || []).length + ' 个文件</div>'
          + '<div class="progress"><i style="width:' + pct + '%"></i></div></a>';
      }).join('') + '</div>';
    }
    list += '</div>';

    return page(head + form + list);
  }

  function bindKb(root) {
    var kb = KL.state.kb || {};
    var summary = byId('gen-summary');
    function updateSummary() {
      var weeks = Number(byId('gen-weeks').value) || 0;
      var hours = Number(byId('gen-hours').value) || 0;
      var total = weeks * hours;
      var count = Number(byId('gen-count').value) || defaultModuleCount(total);
      summary.textContent = '总计 ' + total + ' 小时 · 建议 ' + defaultModuleCount(total) + ' 个模块 · 当前 ' + count + ' 个';
    }
    ['gen-weeks', 'gen-hours', 'gen-count'].forEach(function (id) {
      byId(id).addEventListener('input', updateSummary);
      byId(id).addEventListener('focus', function () { this.select(); });
    });
    updateSummary();

    byId('goal-chips').querySelectorAll('[data-goal]').forEach(function (button) {
      button.addEventListener('click', function () { button.classList.toggle('is-on'); });
    });

    byId('start-gen').addEventListener('click', function () {
      var error = byId('gen-form-error');
      error.hidden = true;
      var domain = byId('gen-domain').value.trim();
      var weeks = Number(byId('gen-weeks').value);
      var hours = Number(byId('gen-hours').value);
      var count = Number(byId('gen-count').value) || 0;
      if (domain.length < 2) { error.textContent = '学习领域至少需要 2 个字符'; error.hidden = false; return; }
      if (!weeks || weeks < 1 || weeks > 52) { error.textContent = '周期需要在 1..52 周之间'; error.hidden = false; return; }
      if (!hours || hours < 1 || hours > 40) { error.textContent = '每周投入需要在 1..40 小时之间'; error.hidden = false; return; }
      var goals = Array.prototype.map.call(byId('goal-chips').querySelectorAll('.is-on'), function (item) { return item.getAttribute('data-goal'); });
      var payload = {
        domain: domain,
        weeks: weeks,
        hoursPerWeek: hours,
        level: byId('gen-level').value,
        goals: goals,
        preferences: byId('gen-prefs').value.trim(),
        name: byId('gen-name').value.trim(),
        webEnabled: !!(byId('gen-web') && byId('gen-web').checked),
        thinking: byId('gen-thinking') ? byId('gen-thinking').value : 'off'
      };
      if (count) payload.moduleCount = count;
      KL.views.startGeneration(kb.id, payload);
    });

    byId('rename-kb').addEventListener('click', function () {
      openRenameDialog({
        title: '重命名知识库',
        inputId: 'rename-kb-name',
        current: kb.name || '',
        save: function (name) {
          return KL.api.put('/api/kbs/' + encodeURIComponent(kb.id), { name: name });
        },
        toast: '知识库已重命名'
      });
    });
    byId('delete-kb').addEventListener('click', async function () {
      if (!(await KL.ui.confirm({ title: '删除知识库', message: '会把整个「' + kb.name + '」目录移动到 data/trash，可在磁盘恢复。', danger: true, confirmText: '删除' }))) return;
      try {
        await KL.api.del('/api/kbs/' + encodeURIComponent(kb.id));
        KL.ui.toast('知识库已移入回收站', 'ok');
        await KL.app.refreshBase();
        KL.app.go('#/library');
      } catch (err) { KL.ui.toast(err.message, 'error'); }
    });
  }

  KL.views = { renderSettings: renderSettings, bindSettings: bindSettings, renderLibrary: renderLibrary, bindLibrary: bindLibrary, renderKb: renderKb, bindKb: bindKb, openRenameDialog: openRenameDialog };
  KL.views.part1Ready = true;
})(window.KL = window.KL || {});

(function (KL) {
  'use strict';

  var esc = KL.ui.escape;
  var icon = KL.ui.icon;
  var byId = KL.ui.byId;

  function val(value) { return esc(value == null ? '' : value); }

  function safeHref(value) {
    var text = String(value == null ? '' : value).trim();
    return /^https?:\/\//i.test(text) ? text : '';
  }

  function pageHead(eyebrow, title, sub, actions) {
    return '<div class="page-head"><div><div class="eyebrow">' + esc(eyebrow) + '</div>'
      + '<h1 class="page-title">' + title + '</h1>'
      + (sub ? '<p class="page-sub">' + esc(sub) + '</p>' : '')
      + '</div>' + (actions ? '<div class="page-actions">' + actions + '</div>' : '') + '</div>';
  }

  function page(inner) { return '<div class="page">' + inner + '</div>'; }

  function emptyState(mark, title, text, actions) {
    return '<div class="empty"><div class="empty-mark">' + esc(mark) + '</div><h3>' + esc(title) + '</h3>'
      + '<p>' + esc(text) + '</p>' + (actions ? '<div class="row" style="justify-content:center">' + actions + '</div>' : '') + '</div>';
  }

  function field(label, input, hint) {
    return '<div class="field"><label>' + esc(label) + '</label>' + input + (hint ? '<div class="hint">' + hint + '</div>' : '') + '</div>';
  }

  function input(id, value, placeholder, type) {
    return '<input class="input" id="' + id + '" type="' + (type || 'text') + '" value="' + val(value) + '" placeholder="' + val(placeholder) + '">';
  }

  function textarea(id, value, placeholder, rows) {
    return '<textarea class="textarea" id="' + id + '" rows="' + (rows || 3) + '" placeholder="' + val(placeholder) + '">' + val(value) + '</textarea>';
  }

  function statusBadge(status) { return KL.ui.statusBadge(status); }

  // ------------------------------------------------------------- generation
  function startGeneration(kbId, payload) {
    var generation = {
      kbId: kbId,
      payload: payload,
      pathId: null,
      name: '',
      plan: null,
      modules: {},
      total: payload.moduleCount || 0,
      done: 0,
      status: 'starting',
      error: '',
      logs: [],
      controller: new AbortController(),
      streaming: true
    };
    KL.state.generation = generation;
    renderGeneration(generation);
    window.scrollTo(0, 0);

    function log(message) {
      var stamp = new Date().toTimeString().slice(0, 8);
      generation.logs.push({ time: stamp, message: message });
      if (generation.logs.length > 60) generation.logs.shift();
      var box = byId('gen-log');
      if (box) {
        box.innerHTML = generation.logs.map(function (item) {
          return '<div><time>' + esc(item.time) + '</time><span>' + esc(item.message) + '</span></div>';
        }).join('');
        box.scrollTop = box.scrollHeight;
      }
    }

    function refresh() {
      var bar = byId('gen-bar-fill');
      var counter = byId('gen-counter');
      var pct = generation.total ? Math.min(100, Math.round(generation.done / generation.total * 100)) : 4;
      if (bar) bar.style.width = Math.max(4, pct) + '%';
      if (counter) counter.textContent = generation.done + ' / ' + (generation.total || '—') + ' 讲';
      var grid = byId('gen-grid');
      if (grid) {
        var modules = generation.plan ? generation.plan.modules || [] : [];
        grid.innerHTML = modules.map(function (module) {
          var item = generation.modules[module.index] || { status: 'pending' };
          var cls = item.status === 'done' ? ' is-done' : item.status === 'failed' ? ' is-failed' : item.status === 'running' ? ' is-running' : '';
          var label = item.status === 'done' ? '已完成' : item.status === 'failed' ? '失败' : item.status === 'running' ? '生成中' : '等待';
          return '<div class="gen-module' + cls + '" id="gen-m-' + module.index + '"><div class="no">' + String(module.index).padStart(2, '0') + '</div>'
            + '<h4>' + esc(module.title) + '</h4><div class="state">' + label + '</div></div>';
        }).join('');
      }
      var title = byId('gen-title');
      if (title && generation.name) title.textContent = generation.name;
    }

    function onEvent(name, data) {
      if (name === 'start') {
        generation.pathId = data.pathId;
        generation.name = data.name || '';
        generation.status = 'planning';
        log('已创建路径目录，开始规划。');
        refresh();
      } else if (name === 'plan') {
        generation.plan = data.plan;
        generation.total = (data.plan.modules || []).length;
        data.plan.modules.forEach(function (module) { generation.modules[module.index] = { status: 'pending', title: module.title }; });
        generation.status = 'generating';
        log('规划完成：' + generation.total + ' 个模块。');
        var status = byId('gen-status');
        if (status) status.textContent = '正在逐讲撰写讲义…';
        refresh();
      } else if (name === 'module') {
        generation.modules[data.index] = { status: data.status === 'started' ? 'running' : data.status, title: data.title };
        if (data.status === 'done') { generation.done += 1; log('第 ' + String(data.index).padStart(2, '0') + ' 讲完成（' + (data.bytes || 0) + ' 字节）。'); }
        else if (data.status === 'failed') log('第 ' + String(data.index).padStart(2, '0') + ' 讲失败：' + (data.error || '未知错误'));
        else log('第 ' + String(data.index).padStart(2, '0') + ' 讲开始。');
        refresh();
      } else if (name === 'file') {
        if (data.index === 0) log('总览文件已写入。');
      } else if (name === 'error') {
        generation.error = data.message || '生成失败';
        generation.status = 'failed';
        log('错误：' + generation.error);
        var box = byId('gen-error');
        if (box) { box.textContent = generation.error; box.hidden = false; }
      } else if (name === 'done') {
        generation.status = data.status;
        generation.streaming = false;
        log('结束：' + (data.status === 'complete' ? '全部完成' : data.status === 'partial' ? '部分完成，可继续生成' : '生成失败'));
        renderGenerationActions(generation);
        KL.app.refreshBase();
      }
    }

    KL.api.stream(
      '/api/kbs/' + encodeURIComponent(kbId) + '/paths',
      payload,
      onEvent,
      generation.controller.signal
    ).catch(function (err) {
      generation.streaming = false;
      if (err && err.name === 'AbortError') {
        generation.status = 'partial';
        log('已取消生成；已完成的文件会保留，可稍后继续生成。');
      } else {
        generation.error = err.message || '生成失败';
        generation.status = 'failed';
        log('错误：' + generation.error);
        var box = byId('gen-error');
        if (box) { box.textContent = generation.error; box.hidden = false; }
      }
      renderGenerationActions(generation);
    }).then(function () {
      if (generation.streaming) {
        generation.streaming = false;
        generation.status = generation.status === 'starting' ? 'failed' : generation.status;
        renderGenerationActions(generation);
      }
    });
  }

  function renderGeneration(generation) {
    var kb = KL.state.kb || {};
    var spec = generation.payload;
    var title = generation.name || (spec.domain + ' 学习路径');
    var html = '<div class="page">'
      + pageHead('GENERATION / 01', '正在生成学习路径', '规划 → 逐讲撰写 → 编号落盘。可以取消，已完成的文件会保留。',
        '<a class="btn btn-ghost" href="#/library/' + encodeURIComponent(generation.kbId) + '">' + icon('arrowLeft') + '返回知识库</a>'
        + '<button class="btn btn-danger" id="gen-cancel">' + icon('stop') + '取消生成</button>')
      + '<div class="card">'
      + '<div class="card-head"><div><h2 class="card-title" id="gen-title">' + esc(title) + '</h2>'
      + '<p class="card-sub">' + esc(spec.domain) + ' · ' + spec.weeks + ' 周 × ' + spec.hoursPerWeek + ' 小时 · 目标 ' + (generation.total || '—') + ' 讲</p></div>'
      + '<div class="mono small muted" id="gen-counter">0 / ' + (generation.total || '—') + ' 讲</div></div>'
      + '<div class="gen-bar"><i id="gen-bar-fill" style="width:4%"></i></div>'
      + '<div class="hint" id="gen-status">正在提交规划请求…</div>'
      + '<div class="form-error" id="gen-error" hidden></div>'
      + '<div class="gen-grid" id="gen-grid" style="margin-top:18px"></div>'
      + '<div class="gen-log" id="gen-log"></div>'
      + '</div></div>';
    byId('view-root').innerHTML = html;
    byId('gen-cancel').addEventListener('click', function () {
      generation.controller.abort();
      byId('gen-cancel').disabled = true;
    });
  }

  function renderGenerationActions(generation) {
    var host = byId('gen-status');
    if (!host) return;
    var pathHref = generation.pathId
      ? '#/library/' + encodeURIComponent(generation.kbId) + '/' + encodeURIComponent(generation.pathId)
      : '#/library/' + encodeURIComponent(generation.kbId);
    host.innerHTML = '<div class="row" style="margin-top:8px">'
      + '<a class="btn btn-primary" href="' + pathHref + '">' + icon('book') + (generation.status === 'complete' ? '查看学习路径' : '查看生成结果') + '</a>'
      + (generation.status === 'partial' ? '<span class="hint">有缺失章节，可在路径详情页继续生成。</span>' : '')
      + '</div>';
    var cancel = byId('gen-cancel');
    if (cancel) cancel.disabled = true;
    var bar = byId('gen-bar-fill');
    if (bar && generation.status === 'complete') bar.style.width = '100%';
  }

  // ------------------------------------------------------------- path detail
  function fileOrder(a, b) {
    var ai = a.index == null ? 9999 : a.index;
    var bi = b.index == null ? 9999 : b.index;
    if (ai !== bi) return ai - bi;
    return a.name.localeCompare(b.name, 'zh-CN');
  }

  function fileButtonHtml(file) {
    var number = file.index == null ? '--' : String(file.index).padStart(2, '0');
    return '<button class="file-item" data-file-name="' + val(file.name) + '">'
      + '<span class="file-no">' + number + '</span><span class="file-name">' + esc(file.name.replace(/^\d+-/, '').replace(/\.md$/i, '')) + '</span></button>';
  }

  async function refreshFileStrip(kb, path) {
    try {
      var detail = await KL.api.get('/api/kbs/' + encodeURIComponent(kb.id) + '/paths/' + encodeURIComponent(path.id));
      KL.state.path = detail;
      var files = (detail.files || []).slice().sort(fileOrder);
      var list = byId('file-list');
      if (!list) return;
      list.innerHTML = '<span class="file-strip-label">编号资料</span>' + (files.map(fileButtonHtml).join('') || '<span class="hint" style="padding:6px 8px">还没有文件</span>');
      var count = byId('path-file-count');
      if (count) count.textContent = files.length + ' 个文件';
      list.querySelectorAll('[data-file-name]').forEach(function (button) {
        button.addEventListener('click', function () {
          loadPathFile(kb.id, path.id, button.getAttribute('data-file-name'));
        });
      });
    } catch (err) {
      KL.ui.toast(err.message || '刷新文件列表失败', 'error');
    }
  }

  function renderPath() {
    var kb = KL.state.kb || {};
    var path = KL.state.path;
    if (!path) return page(emptyState('PATH / 404', '找不到学习路径', '它可能已被删除或移动。', '<a class="btn btn-ghost" href="#/library">返回文库</a>'));
    var files = (path.files || []).slice().sort(fileOrder);
    var spec = path.spec || {};
    var actions = '<a class="btn btn-ghost" href="#/library/' + encodeURIComponent(kb.id) + '">' + icon('arrowLeft') + '返回知识库</a>'
      + '<button class="btn btn-ghost" id="path-chat-toggle">' + icon('chat') + '阅读助手</button>'
      + '<button class="btn btn-ghost" id="path-refresh">' + icon('refresh') + '刷新</button>'
      + '<button class="btn btn-ghost" id="path-rename">' + icon('edit') + '重命名</button>'
      + (path.status !== 'complete' && path.status !== 'external'
        ? '<button class="btn btn-accent" id="path-resume">' + icon('spark') + '继续生成</button>' : '')
      + '<button class="btn btn-danger btn-sm" id="path-delete">' + icon('trash') + '删除</button>';
    var chips = '<div class="row" style="margin-bottom:16px">'
      + statusBadge(path.status)
      + '<span class="badge">' + esc(spec.domain || path.domain || '—') + '</span>'
      + '<span class="badge">' + (spec.weeks || '—') + ' 周 × ' + (spec.hoursPerWeek || '—') + ' 小时</span>'
      + '<span class="badge">' + (path.moduleCount || 0) + ' 讲</span>'
      + '<span class="badge" id="path-file-count">' + (path.files || []).length + ' 个文件</span>'
      + (spec.moduleCount ? '<span class="badge badge--brass">计划 ' + spec.moduleCount + ' 讲</span>' : '')
      + (spec.webEnabled ? '<span class="badge badge--accent">联网生成</span>' : '')
      + (spec.thinking && spec.thinking !== 'off' ? '<span class="badge">思考 ' + thinkingLabel(spec.thinking) + '</span>' : '')
      + '</div>';
    var errors = '';
    if (path.errors && path.errors.length) {
      errors = '<div class="form-error" style="margin-bottom:16px">生成记录：' + esc(path.errors.map(function (item) { return (item.index ? String(item.index).padStart(2, '0') + '：' : '') + item.message; }).join('；')) + '</div>';
    }
    if (path.status === 'failed' && !files.length) {
      errors += '<div class="card"><div class="card-head"><div><h2 class="card-title">这次生成没有落盘文件</h2>'
        + '<p class="card-sub">可以点击「继续生成」重试，或回到知识库重新创建路径。</p></div></div></div>';
    }
    var fileButtons = files.map(fileButtonHtml).join('');
    var initial = files.length ? files[0].name : '';
    if (!KL.state.reader || KL.state.reader.pathId !== path.id) {
      KL.state.reader = { pathId: path.id, name: initial };
    }
    var collapsed = readerChatCollapsed();
    var body = '<div class="reader-layout' + (collapsed ? ' is-chat-collapsed' : '') + '" id="reader-layout">'
      + '<div class="reader-main">'
      + '<div class="file-strip" id="file-list"><span class="file-strip-label">编号资料</span>'
      + (fileButtons || '<span class="hint" style="padding:6px 8px">还没有文件</span>') + '</div>'
      + '<section class="doc" id="doc-view">' + (initial ? '<div class="hint">正在读取…</div>' : '<div class="hint">生成完成后，这里会显示 00 总览。</div>') + '</section>'
      + '</div>'
      + readerChatPanel(kb, path)
      + '</div>';
    return page(pageHead('PATH / ' + (path.domain || 'LEARNING PATH'), esc(path.name || '学习路径'), '每个文件都以编号开头：00 总览 + 01..NN 讲义。', actions) + chips + errors + body);
  }

  async function loadPathFile(kbId, pathId, name) {
    var doc = byId('doc-view');
    if (!doc) return;
    KL.state.reader = { pathId: pathId, name: name };
    var current = byId('reader-current');
    if (current) current.textContent = '正在读：' + name;
    var footerCurrent = byId('reader-composer-current');
    if (footerCurrent) footerCurrent.textContent = name;
    doc.innerHTML = '<div class="hint">正在读取 ' + esc(name) + '…</div>';
    document.querySelectorAll('.file-item').forEach(function (item) {
      item.classList.toggle('is-on', item.getAttribute('data-file-name') === name);
    });
    try {
      var data = await KL.api.get('/api/kbs/' + encodeURIComponent(kbId) + '/file?path=' + encodeURIComponent(pathId) + '&name=' + encodeURIComponent(name));
      var number = data.index == null ? '--' : String(data.index).padStart(2, '0');
      doc.innerHTML = '<div class="doc-head"><div><div class="eyebrow">' + esc(number) + ' / MARKDOWN</div>'
        + '<div class="mono small muted">' + esc(name) + '</div>'
        + (data.warning ? '<div class="hint">' + esc(data.warning) + '</div>' : '') + '</div>'
        + '<div class="doc-meta">' + KL.ui.formatBytes(data.bytes) + '</div></div>'
        + '<div class="md" id="doc-md">' + KL.md.render(data.content) + '</div>';
      doc.querySelectorAll('[data-file]').forEach(function (link) {
        link.addEventListener('click', function (event) {
          event.preventDefault();
          loadPathFile(kbId, pathId, link.getAttribute('data-file'));
        });
      });
      var key = pathId + ':' + name;
      (KL.state.highlights[key] || []).forEach(function (item) {
        highlightText(doc, item.quote, item.note, false);
      });
    } catch (err) {
      doc.innerHTML = '<div class="form-error">' + esc(err.message) + '</div>';
    }
  }

  function highlightText(root, quote, note, scroll) {
    return KL.highlight ? KL.highlight.apply(root, quote, note, scroll) : false;
  }

  function applyHighlight(file, quote, note, pathId, scroll) {
    var reader = KL.state.reader || {};
    var key = (pathId || reader.pathId) + ':' + file;
    KL.state.highlights = KL.state.highlights || {};
    var list = KL.state.highlights[key] = KL.state.highlights[key] || [];
    var exists = list.some(function (item) { return item.quote === quote; });
    if (!exists) list.push({ quote: quote, note: note || '' });
    if (reader.name === file) {
      var doc = byId('doc-md');
      if (doc) highlightText(doc, quote, note, scroll);
    }
  }

  function bindPath(root) {
    var kb = KL.state.kb || {};
    var path = KL.state.path || {};
    if (!path.id) return;
    var files = (path.files || []).slice().sort(fileOrder);
    var initial = files.length ? files[0].name : '';
    if (KL.state.openFile) {
      var wanted = KL.state.openFile;
      var found = files.some(function (file) { return file.name === wanted.name; });
      if (found) initial = wanted.name;
      KL.state.openFile = null;
    }
    root.querySelectorAll('[data-file-name]').forEach(function (button) {
      button.addEventListener('click', function () {
        loadPathFile(kb.id, path.id, button.getAttribute('data-file-name'));
      });
    });
    bindReaderChat(kb, path);
    if (initial) loadPathFile(kb.id, path.id, initial);

    byId('path-chat-toggle').addEventListener('click', function () {
      var panel = byId('reader-chat');
      if (!panel) return;
      setReaderChatCollapsed(panel, !panel.classList.contains('is-collapsed'));
    });
    byId('path-refresh').addEventListener('click', function () { KL.app.route(); });
    byId('path-rename').addEventListener('click', function () {
      KL.views.openRenameDialog({
        title: '重命名学习路径',
        inputId: 'rename-path-name',
        current: path.name || '',
        save: function (name) {
          return KL.api.put('/api/kbs/' + encodeURIComponent(kb.id) + '/paths/' + encodeURIComponent(path.id), { name: name });
        },
        toast: '学习路径已重命名'
      });
    });
    byId('path-delete').addEventListener('click', async function () {
      if (!(await KL.ui.confirm({ title: '删除学习路径', message: '整个目录会移动到 data/trash，可在磁盘恢复。', danger: true, confirmText: '删除' }))) return;
      try {
        await KL.api.del('/api/kbs/' + encodeURIComponent(kb.id) + '/paths/' + encodeURIComponent(path.id));
        KL.ui.toast('学习路径已移入回收站', 'ok');
        await KL.app.refreshBase();
        KL.app.go('#/library/' + encodeURIComponent(kb.id));
      } catch (err) { KL.ui.toast(err.message, 'error'); }
    });
    var resume = byId('path-resume');
    if (resume) {
      resume.addEventListener('click', function () {
        var modal = KL.ui.modal({
          title: '继续生成',
          sub: '只补缺失的讲次，已完成文件不会覆盖。',
          body: '<div class="gen-log" id="resume-log" style="max-height:220px"></div><div class="form-error" id="resume-error" hidden></div>',
          footer: '<button class="btn btn-ghost" data-close>关闭</button>'
        });
        var log = function (message) {
          var box = byId('resume-log');
          if (box) box.innerHTML += '<div><time>' + new Date().toTimeString().slice(0, 8) + '</time><span>' + esc(message) + '</span></div>';
        };
        KL.api.stream('/api/kbs/' + encodeURIComponent(kb.id) + '/paths/' + encodeURIComponent(path.id) + '/resume', {}, function (name, data) {
          if (name === 'plan') log('规划已就绪，共 ' + (data.plan.modules || []).length + ' 讲。');
          else if (name === 'module') log('第 ' + String(data.index).padStart(2, '0') + ' 讲：' + data.status);
          else if (name === 'done') {
            log('完成：' + data.status);
            setTimeout(function () { modal.close(); KL.app.route(); }, 500);
          } else if (name === 'error') {
            var box = byId('resume-error');
            if (box) { box.textContent = data.message || '续生成失败'; box.hidden = false; }
            log('错误：' + (data.message || ''));
          }
        }, new AbortController().signal).catch(function (err) {
          var box = byId('resume-error');
          if (box) { box.textContent = err.message; box.hidden = false; }
        });
      });
    }
  }

  // ------------------------------------------------------------- chat
  var MODES = [
    ['kb', '仅知识库'],
    ['kb_web', '联网增强'],
    ['plain', '纯对话']
  ];
  var THINKING = [
    ['off', '关闭'],
    ['low', '低'],
    ['high', '高'],
    ['max', '最高']
  ];

  function thinkingLabel(value) {
    var match = THINKING.filter(function (item) { return item[0] === value; })[0];
    return match ? match[1] : '关闭';
  }

  function thinkingOptions(current) {
    return '<select class="select select--compact" id="reader-thinking">' + THINKING.map(function (item) {
      return '<option value="' + item[0] + '"' + (item[0] === current ? ' selected' : '') + '>' + item[1] + '</option>';
    }).join('') + '</select>';
  }

  function chatStorageKey(kbId) { return 'kl.chat.' + (kbId || 'plain'); }

  function loadChat(kbId) {
    try {
      var raw = localStorage.getItem(chatStorageKey(kbId));
      if (!raw) return [];
      var data = JSON.parse(raw);
      if (!Array.isArray(data)) return [];
      return data.slice(-30).map(function (item) {
        return Object.assign({}, item, { streaming: false });
      });
    } catch (_) { return []; }
  }

  function saveChat(kbId, messages) {
    try { localStorage.setItem(chatStorageKey(kbId), JSON.stringify(messages.slice(-30))); } catch (_) { /* ignore */ }
  }

  function ensureChat() {
    var kbs = KL.state.kbs || [];
    var chat = KL.state.chat;
    if (!chat.kbId || !kbs.some(function (kb) { return kb.id === chat.kbId; })) {
      chat.kbId = kbs.length ? kbs[0].id : null;
    }
    var key = chat.kbId || 'plain';
    if (chat.loadedKey !== key) {
      chat.messages = loadChat(chat.kbId);
      chat.loadedKey = key;
    }
    if (!chat.mode || chat.mode === 'web') chat.mode = chat.mode === 'web' ? 'kb_web' : 'kb';
    if (['kb', 'kb_web', 'plain'].indexOf(chat.mode) < 0) chat.mode = 'kb';
  }

  function ensureChatFor(kbId) {
    var chat = KL.state.chat;
    if (chat.kbId !== kbId) {
      chat.kbId = kbId;
      chat.loadedKey = null;
    }
    ensureChat();
  }

  function readerChatCollapsed() {
    try {
      var stored = localStorage.getItem('kl.readerChat');
      if (stored === '1') return true;
      if (stored === '0') return false;
    } catch (_) { /* ignore */ }
    return window.innerWidth <= 1180;
  }

  function sourceChips(msg) {
    if (!msg.sources || !msg.sources.length) return '';
    return '<div class="msg__sources" id="sources-' + msg.id + '">' + msg.sources.map(function (source) {
      return '<button type="button" data-cite="' + val(source.ref) + '">' + esc(source.ref) + ' · ' + esc((source.title || '').slice(0, 24)) + '</button>';
    }).join('') + '</div>';
  }

  function messageHtml(msg) {
    var user = msg.role === 'user';
    var body = '';
    if (!user && msg.reasoning) {
      body += '<details class="reasoning"' + (msg.streaming ? ' open' : '') + ' id="reasoning-' + msg.id + '"><summary>思考过程</summary><div id="reason-' + msg.id + '">' + esc(msg.reasoning) + '</div></details>';
    }
    if (msg.error) body += '<div class="form-error">' + esc(msg.error) + '</div>';
    if (user) {
      body += '<div class="md">' + KL.md.render(msg.content) + '</div>';
    } else if (msg.streaming) {
      body += '<div class="msg__text streaming" id="live-' + msg.id + '">' + esc(msg.content || '') + '</div>';
    } else {
      body += '<div class="md">' + (msg.content ? KL.md.render(msg.content) : '<span class="hint">没有内容。</span>') + '</div>';
    }
    if (!user && msg.toolLog && msg.toolLog.length) {
      body += '<details class="tool-log"><summary>工具调用 · ' + msg.toolLog.length + '</summary>'
        + msg.toolLog.map(function (item) {
          return '<div><span class="mono">' + esc(item.tool || 'tool') + '</span>'
            + (item.file ? ' · ' + esc(item.file) : '')
            + (item.message ? ' · ' + esc(item.message) : '')
            + '</div>';
        }).join('') + '</details>';
    }
    if (!user) body += sourceChips(msg);
    if (!user && msg.content && !msg.streaming) {
      body += '<div class="toolbar"><button type="button" data-copy="' + msg.id + '">' + icon('copy') + ' 复制</button></div>';
    }
    return '<div class="msg msg--' + (user ? 'user' : 'assistant') + '" data-msg="' + msg.id + '">'
      + '<div class="msg__rail"><div class="msg__seal">' + (user ? '你' : '答') + '</div></div>'
      + '<div class="msg__body"><div class="msg__meta"><span>' + (user ? 'YOU' : 'ASSISTANT') + '</span>' + (msg.model ? '<span>· ' + esc(msg.model) + '</span>' : '') + '<span>· ' + esc(msg.ts ? KL.ui.relativeTime(msg.ts) : '') + '</span></div>'
      + '<div class="msg__bubble' + (msg.error ? ' is-error' : '') + '">' + body + '</div></div></div>';
  }

  function renderChat() {
    ensureChat();
    var kbs = KL.state.kbs || [];
    var settings = KL.state.settings || {};
    var chat = KL.state.chat;
    var hasKey = !!(settings.api && settings.api.hasApiKey);
    var kb = kbs.filter(function (item) { return item.id === chat.kbId; })[0] || null;
    var mode = chat.mode || 'kb';
    var needsKb = mode === 'kb' || mode === 'kb_web';
    var options = kbs.map(function (item) {
      return '<option value="' + esc(item.id) + '"' + (item.id === chat.kbId ? ' selected' : '') + '>' + esc(item.name) + '</option>';
    }).join('');
    if (!options) options = '<option value="">尚无知识库</option>';
    var seg = '<div class="seg" id="chat-mode">' + MODES.map(function (item) {
      return '<button type="button" data-mode="' + item[0] + '" class="' + (item[0] === mode ? 'is-on' : '') + '">' + esc(item[1]) + '</button>';
    }).join('') + '</div>';
    var controls = '<select class="select" id="chat-kb" style="max-width:210px"' + (kbs.length ? '' : ' disabled') + '>' + options + '</select>'
      + seg
      + '<button class="btn btn-ghost btn-sm" id="chat-clear">清空</button>';

    var inner = chat.messages.length
      ? chat.messages.map(messageHtml).join('')
      : '<div class="empty" style="margin-top:30px"><div class="empty-mark">DIALOGUE / 01</div><h3>从一句问题开始</h3>'
        + '<p>' + (kb ? '基于「' + esc(kb.name) + '」里的编号资料提问；也可以切到联网模式补充最新信息。' : '先去文库创建知识库，或切换到纯对话。') + '</p>'
        + (kb ? '<div class="suggestion"><button type="button" data-suggest="这套资料里最难的概念是什么？">最难的概念是什么</button><button type="button" data-suggest="给我一个 7 天复习计划">7 天复习计划</button><button type="button" data-suggest="用例子解释第 3 讲">用例子解释第 3 讲</button></div>' : '')
        + '</div>';

    var disabled = !hasKey || (needsKb && !kb);
    var composer = '<div class="composer' + (disabled ? ' is-disabled' : '') + '">'
      + '<div class="composer-box"><textarea id="chat-input" rows="1" placeholder="' + (disabled ? (hasKey ? '先选择一个知识库' : '先在设置中配置 API Key') : '输入问题，Enter 发送，Shift+Enter 换行') + '"' + (disabled ? ' disabled' : '') + '></textarea>'
      + (chat.streaming
        ? '<button class="btn btn-danger" id="chat-stop">' + icon('stop') + '停止</button>'
        : '<button class="btn btn-accent" id="chat-send"' + (disabled ? ' disabled' : '') + '>' + icon('send') + '发送</button>')
      + '</div>'
      + '<div class="composer-foot"><span>' + (hasKey ? esc(settings.api.model || '') : 'API 未配置') + ' · ' + esc((kb && kb.name) || '未选择知识库') + '</span><span><span class="kbd">Enter</span> 发送 · <span class="kbd">Shift</span> + <span class="kbd">Enter</span> 换行</span></div>'
      + '</div>';

    var keyNotice = hasKey ? '' : '<div class="form-error" style="margin-bottom:12px">尚未配置 API Key，问答与生成不可用。请到 <a href="#/settings">设置</a> 填写。</div>';
    var header = '<div class="chat-head"><div><div class="eyebrow">DIALOGUE / 01</div><h1 class="page-title">对话</h1>'
      + '<p class="page-sub">' + (kb ? '正在使用「' + esc(kb.name) + '」的编号资料。' : '选择知识库，或切到纯对话。') + '</p></div>'
      + '<div class="chat-controls">' + controls + '</div></div>';

    return '<div class="page"><div class="chat-shell">' + header + keyNotice
      + '<div class="chat-scroll" id="chat-scroll"><div class="chat-inner" id="chat-inner">' + inner + '</div></div>'
      + '<div class="chat-note" id="chat-tool"></div>'
      + composer + '</div></div>';
  }

  function bindChat(root) {
    ensureChat();
    var chat = KL.state.chat;
    var scroll = byId('chat-scroll');
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
    var kbSelect = byId('chat-kb');
    if (kbSelect) {
      kbSelect.addEventListener('change', function () {
        if (chat.streaming) { KL.ui.toast('请先停止当前回答', 'error'); kbSelect.value = chat.kbId || ''; return; }
        chat.kbId = kbSelect.value || null;
        chat.loadedKey = null;
        chat.messages = loadChat(chat.kbId);
        chat.loadedKey = chat.kbId || 'plain';
        KL.state.route = { name: 'chat', kbId: chat.kbId, pathId: null };
        KL.app.route();
      });
    }
    root.querySelectorAll('#chat-mode [data-mode]').forEach(function (button) {
      button.addEventListener('click', function () {
        if (chat.streaming) return;
        chat.mode = button.getAttribute('data-mode');
        try { localStorage.setItem('kl.mode', chat.mode); } catch (_) { /* ignore */ }
        KL.app.renderView();
      });
    });
    byId('chat-clear').addEventListener('click', async function () {
      if (chat.streaming) { KL.ui.toast('请先停止当前回答', 'error'); return; }
      if (!chat.messages.length) return;
      if (!(await KL.ui.confirm({ title: '清空对话', message: '只清空浏览器里的这段会话，不会删除知识库文件。', confirmText: '清空' }))) return;
      chat.messages = [];
      saveChat(chat.kbId, chat.messages);
      KL.app.renderView();
    });
    var textarea = byId('chat-input');
    if (textarea) {
      textarea.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          sendChat();
        }
      });
      textarea.addEventListener('input', function () {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(140, textarea.scrollHeight) + 'px';
      });
    }
    var send = byId('chat-send');
    if (send) send.addEventListener('click', sendChat);
    var stop = byId('chat-stop');
    if (stop) stop.addEventListener('click', function () {
      if (chat.controller) chat.controller.abort();
    });
    root.querySelectorAll('[data-suggest]').forEach(function (button) {
      button.addEventListener('click', function () {
        if (textarea) { textarea.value = button.getAttribute('data-suggest'); textarea.focus(); }
      });
    });
    root.querySelectorAll('[data-cite]').forEach(function (button) {
      button.addEventListener('click', function () {
        openSource(button.getAttribute('data-cite'), chat.messages);
      });
    });
    root.querySelectorAll('[data-copy]').forEach(function (button) {
      button.addEventListener('click', function () {
        var msg = chat.messages.filter(function (item) { return item.id === button.getAttribute('data-copy'); })[0];
        if (msg) KL.ui.copy(msg.content);
      });
    });
  }

  function sendChat() {
    var chat = KL.state.chat;
    if (chat.streaming) return;
    var textarea = byId('chat-input');
    if (!textarea) return;
    var text = textarea.value.trim();
    if (!text) return;
    var settings = KL.state.settings || {};
    if (!settings.api || !settings.api.hasApiKey) {
      KL.ui.toast('请先在设置中配置 API Key', 'error');
      return;
    }
    var needsKb = chat.mode === 'kb' || chat.mode === 'kb_web';
    if (needsKb && !chat.kbId) {
      KL.ui.toast('该模式需要先选择知识库', 'error');
      return;
    }
    var history = chat.messages.filter(function (item) { return !item.streaming && !item.error; }).slice(-20).map(function (item) {
      return { role: item.role, content: item.content };
    });
    var stamp = Date.now();
    var user = { id: 'u' + stamp, role: 'user', content: text, ts: new Date().toISOString() };
    var assistant = {
      id: 'a' + stamp,
      role: 'assistant',
      content: '',
      reasoning: '',
      sources: [],
      model: settings.api.model,
      streaming: true,
      ts: new Date().toISOString()
    };
    chat.messages.push(user, assistant);
    chat.streaming = true;
    chat.controller = new AbortController();
    saveChat(chat.kbId, chat.messages);
    textarea.value = '';
    KL.app.renderView();
    var liveScroll = byId('chat-scroll');
    if (liveScroll) liveScroll.scrollTop = liveScroll.scrollHeight;

    var body = {
      message: text,
      history: history,
      mode: chat.mode,
      stream: true
    };
    if (chat.kbId) body.kbId = chat.kbId;

    var onEvent = function (name, data) {
      if (name === 'meta') {
        assistant.model = data.model || assistant.model;
      } else if (name === 'tool') {
        var note = byId('chat-tool');
        if (note) {
          if (data.status === 'started') note.textContent = data.tool === 'search' ? '联网：正在搜索…' : '联网：正在抓取网页…';
          else if (data.status === 'done') note.textContent = data.tool === 'search' ? '联网：找到 ' + (data.count || 0) + ' 条结果' : '联网：已抓取 ' + (data.ok || 0) + ' 篇';
          else if (data.status === 'error') note.textContent = '联网失败：' + (data.message || '');
        }
      } else if (name === 'sources') {
        assistant.sources = data.sources || [];
        var holder = byId('sources-' + assistant.id);
        if (holder) holder.outerHTML = sourceChips(assistant) || '<div id="sources-' + assistant.id + '"></div>';
      } else if (name === 'reasoning') {
        assistant.reasoning += data.text || '';
        var reason = byId('reason-' + assistant.id);
        if (reason) reason.textContent = assistant.reasoning;
        else {
          var live = byId('live-' + assistant.id);
          if (live) {
            live.insertAdjacentHTML('beforebegin', '<details class="reasoning" open id="reasoning-' + assistant.id + '"><summary>思考过程</summary><div id="reason-' + assistant.id + '">' + esc(assistant.reasoning) + '</div></details>');
          }
        }
      } else if (name === 'delta') {
        assistant.content += data.text || '';
        var node = byId('live-' + assistant.id);
        if (node) node.textContent = assistant.content;
        var scroller = byId('chat-scroll');
        if (scroller) scroller.scrollTop = scroller.scrollHeight;
      } else if (name === 'error') {
        assistant.error = data.message || '请求失败';
        assistant.streaming = false;
      } else if (name === 'done') {
        assistant.streaming = false;
        if (!assistant.content && !assistant.error) assistant.content = '（模型没有返回内容）';
        if (data.finishReason === 'error' && !assistant.error) assistant.error = '生成中断';
      }
    };

    KL.api.stream('/api/chat', body, onEvent, chat.controller.signal)
      .then(function () {
        chat.streaming = false;
        assistant.streaming = false;
        saveChat(chat.kbId, chat.messages);
        KL.app.renderView();
      })
      .catch(function (err) {
        chat.streaming = false;
        assistant.streaming = false;
        if (err && err.name === 'AbortError') {
          assistant.content += '\n\n_（已停止）_';
        } else {
          assistant.error = err.message || '请求失败';
        }
        saveChat(chat.kbId, chat.messages);
        KL.app.renderView();
      });
  }

  function openSource(ref, messages, context) {
    var source = null;
    (messages || []).forEach(function (message) {
      (message.sources || []).forEach(function (item) { if (item.ref === ref) source = item; });
    });
    if (!source) { KL.ui.toast('没有找到这条来源', 'error'); return; }
    var sameReader = !!(context && source.pathId && source.pathId === context.pathId);
    var body = '<div class="source-meta">'
      + '<div><b>编号</b>' + esc(source.ref) + '</div>'
      + '<div><b>类型</b>' + (source.kind === 'kb' ? '知识库资料' : '联网网页') + '</div>'
      + (source.pathName ? '<div><b>路径</b>' + esc(source.pathName) + '</div>' : '')
      + (source.file ? '<div><b>文件</b>' + esc(source.file) + '</div>' : '')
      + (source.heading ? '<div><b>章节</b>' + esc(source.heading) + '</div>' : '')
      + (source.url ? '<div><b>链接</b>' + (safeHref(source.url) ? '<a href="' + val(safeHref(source.url)) + '" target="_blank" rel="noreferrer noopener">' + esc(source.url) + '</a>' : esc(source.url)) + '</div>' : '')
      + (source.date ? '<div><b>日期</b>' + esc(source.date) + '</div>' : '')
      + '</div>'
      + '<div class="source-text">' + esc(source.text || '（无正文）') + '</div>';
    if (source.kind === 'kb' && source.pathId) {
      body += '<div class="row" style="margin-top:18px"><button class="btn btn-primary" id="source-open">' + icon('file') + (sameReader ? '在阅读区打开' : '打开原文件') + '</button></div>';
    }
    var drawer = KL.ui.drawer({ title: source.ref + ' · ' + (source.title || '来源'), body: body });
    var open = byId('source-open');
    if (open) open.addEventListener('click', function () {
      drawer.close();
      if (sameReader && source.file) {
        loadPathFile(context.kbId, context.pathId, source.file);
        var doc = byId('doc-view');
        if (doc) doc.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
      KL.state.openFile = { pathId: source.pathId, name: source.file };
      KL.app.go('#/library/' + encodeURIComponent(KL.state.chat.kbId) + '/' + encodeURIComponent(source.pathId));
    });
  }

  // ------------------------------------------------------------- reading chat
  function currentReaderName() {
    return (KL.state.reader && KL.state.reader.name) || '';
  }

  function readerChatTab() {
    return '<button type="button" class="reader-chat-tab" id="reader-chat-tab" title="展开阅读助手" aria-label="展开阅读助手">'
      + '<span>阅读助手</span>' + icon('chevron') + '</button>';
  }

  function readerChatInner(kb, path) {
    ensureChatFor(kb.id);
    var chat = KL.state.chat;
    var settings = KL.state.settings || {};
    var hasKey = !!(settings.api && settings.api.hasApiKey);
    var mode = chat.mode || 'kb';
    var current = currentReaderName();
    var inner = chat.messages.length
      ? chat.messages.map(messageHtml).join('')
      : '<div class="reader-empty"><div class="mono small muted">READING ASSISTANT</div>'
        + '<p>边读边问。正在阅读的文件会自动作为上下文。</p>'
        + '<div class="suggestion">'
        + '<button type="button" data-suggest="这一讲最重要的三个概念是什么？">提炼重点</button>'
        + '<button type="button" data-suggest="用更简单的例子解释这一讲。">换个例子</button>'
        + '<button type="button" data-suggest="给我 5 道自测题。">出几道题</button>'
        + '</div></div>';
    var seg = '<div class="seg seg--compact" id="reader-chat-mode">' + MODES.map(function (item) {
      return '<button type="button" data-reader-mode="' + item[0] + '" class="' + (item[0] === mode ? 'is-on' : '') + '">' + esc(item[1]) + '</button>';
    }).join('') + '</div>';
    var thinking = chat.thinking || 'off';
    var toolsOn = chat.enableTools !== false;
    var deepseek = !!(settings.api && settings.api.provider === 'deepseek');
    var thinkingOn = thinking !== 'off';
    var editLocked = !!(deepseek && ((thinkingOn && mode !== 'kb_web') || (!thinkingOn && mode === 'kb_web')));
    var toolsChecked = toolsOn && !editLocked;
    var options = '<div class="reader-chat-options">'
      + '<label class="reader-opt"><span>思考</span>' + thinkingOptions(thinking) + '</label>'
      + '<label class="reader-check' + (editLocked ? ' is-disabled' : '') + '"><input type="checkbox" id="reader-edit-tools"' + (toolsChecked ? ' checked' : '') + (editLocked ? ' disabled' : '') + '> 编辑工具</label>'
      + '</div>';
    var editLockNote = editLocked
      ? (mode === 'kb_web'
        ? '<div class="reader-chat-warning">联网增强 + 关闭思考时编辑工具不可用；打开思考，或切回「仅知识库」后编辑。</div>'
        : '<div class="reader-chat-warning">DeepSeek 思考模式下编辑工具暂停；关闭思考或切到「联网增强」后可用。</div>')
      : '';
    var disabled = !hasKey;
    var composer = '<div class="reader-composer' + (disabled ? ' is-disabled' : '') + '">'
      + '<div class="composer-box"><textarea id="chat-input" rows="1" placeholder="' + (disabled ? '先在设置中配置 API Key' : '就这一讲讲点什么…') + '"' + (disabled ? ' disabled' : '') + '></textarea>'
      + (chat.streaming
        ? '<button class="btn btn-danger btn-sm" id="chat-stop">' + icon('stop') + '停止</button>'
        : '<button class="btn btn-accent btn-sm" id="chat-send"' + (disabled ? ' disabled' : '') + '>' + icon('send') + '发送</button>')
      + '</div>'
      + '<div class="reader-composer-foot"><span id="reader-composer-current" title="' + val(current) + '">' + esc(current || '未选择文件') + '</span><span>' + (mode === 'kb_web' ? '含联网检索' : '') + '</span></div>'
      + '</div>';

    return '<div class="reader-chat-head">'
      + '<div><div class="eyebrow">READING ASSISTANT</div><strong>阅读助手</strong></div>'
      + '<div class="row" style="gap:4px">'
      + '<button class="btn btn-ghost btn-icon btn-sm" id="reader-chat-clear" title="清空对话" aria-label="清空对话">' + icon('trash') + '</button>'
      + '<button class="btn btn-ghost btn-icon btn-sm" id="reader-chat-toggle" title="收起侧栏" aria-label="收起侧栏">' + icon('chevron') + '</button>'
      + '</div></div>'
      + '<div class="reader-chat-sub" id="reader-current">' + (current ? '正在读：' + esc(current) : '选择左侧文件开始阅读') + '</div>'
      + '<div class="reader-chat-controls">' + seg + options + '</div>'
      + editLockNote
      + (hasKey ? '' : '<div class="reader-chat-warning">尚未配置 API Key，<a href="#/settings">去设置</a>。</div>')
      + '<div class="reader-chat-body">'
      + '<div class="reader-chat-scroll" id="reader-chat-scroll"><div class="reader-chat-inner" id="reader-chat-inner">' + inner + '</div></div>'
      + '<div class="chat-note" id="chat-tool"></div>'
      + composer + '</div>';
  }

  function readerChatPanel(kb, path) {
    var collapsed = readerChatCollapsed();
    return '<aside class="reader-chat' + (collapsed ? ' is-collapsed' : '') + '" id="reader-chat">'
      + readerChatTab()
      + '<div class="reader-glass" aria-hidden="true"></div>'
      + '<div class="reader-chat-main">' + readerChatInner(kb, path) + '</div></aside>';
  }

  function setReaderChatCollapsed(panel, collapsed) {
    var layout = byId('reader-layout');
    if (panel) panel.classList.toggle('is-collapsed', collapsed);
    if (layout) layout.classList.toggle('is-chat-collapsed', collapsed);
    try { localStorage.setItem('kl.readerChat', collapsed ? '1' : '0'); } catch (_) { /* ignore */ }
    if (!collapsed) {
      var scroller = byId('reader-chat-scroll');
      if (scroller) scroller.scrollTop = scroller.scrollHeight;
      var input = byId('chat-input');
      if (input) setTimeout(function () { input.focus(); }, 80);
    }
  }

  function refreshReaderChat(kb, path) {
    var panel = byId('reader-chat');
    if (!panel) return;
    var collapsed = panel.classList.contains('is-collapsed');
    panel.innerHTML = readerChatTab() + '<div class="reader-glass" aria-hidden="true"></div>'
      + '<div class="reader-chat-main">' + readerChatInner(kb, path) + '</div>';
    if (collapsed) panel.classList.add('is-collapsed');
    bindReaderChat(kb, path);
    var scroller = byId('reader-chat-scroll');
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }

  function bindReaderChat(kb, path) {
    ensureChatFor(kb.id);
    var chat = KL.state.chat;
    var panel = byId('reader-chat');
    if (!panel) return;
    var scroller = byId('reader-chat-scroll');
    if (scroller) scroller.scrollTop = scroller.scrollHeight;

    panel.querySelectorAll('[data-reader-mode]').forEach(function (button) {
      button.addEventListener('click', function () {
        if (chat.streaming) { KL.ui.toast('请先停止当前回答', 'error'); return; }
        chat.mode = button.getAttribute('data-reader-mode');
        try { localStorage.setItem('kl.mode', chat.mode); } catch (_) { /* ignore */ }
        refreshReaderChat(kb, path);
      });
    });

    var thinking = byId('reader-thinking');
    if (thinking) thinking.addEventListener('change', function () {
      if (chat.streaming) {
        thinking.value = chat.thinking || 'off';
        KL.ui.toast('请先停止当前回答', 'error');
        return;
      }
      chat.thinking = thinking.value;
      try { localStorage.setItem('kl.thinking', chat.thinking); } catch (_) { /* ignore */ }
      refreshReaderChat(kb, path);
    });
    var editTools = byId('reader-edit-tools');
    if (editTools) editTools.addEventListener('change', function () {
      chat.enableTools = !!editTools.checked;
      try { localStorage.setItem('kl.editTools', chat.enableTools ? '1' : '0'); } catch (_) { /* ignore */ }
    });

    var clear = byId('reader-chat-clear');
    if (clear) clear.addEventListener('click', async function () {
      if (chat.streaming) { KL.ui.toast('请先停止当前回答', 'error'); return; }
      if (!chat.messages.length) return;
      if (!(await KL.ui.confirm({ title: '清空对话', message: '只清空这段阅读会话，不删除知识库文件。', confirmText: '清空' }))) return;
      chat.messages = [];
      saveChat(chat.kbId, chat.messages);
      refreshReaderChat(kb, path);
    });

    var collapse = byId('reader-chat-toggle');
    if (collapse) collapse.addEventListener('click', function () { setReaderChatCollapsed(panel, true); });
    var tab = byId('reader-chat-tab');
    if (tab) tab.addEventListener('click', function () { setReaderChatCollapsed(panel, false); });

    var textarea = byId('chat-input');
    if (textarea) {
      textarea.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          sendReaderChat(kb, path);
        }
      });
      textarea.addEventListener('input', function () {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(110, textarea.scrollHeight) + 'px';
      });
    }
    var send = byId('chat-send');
    if (send) send.addEventListener('click', function () { sendReaderChat(kb, path); });
    var stop = byId('chat-stop');
    if (stop) stop.addEventListener('click', function () { if (chat.controller) chat.controller.abort(); });

    panel.querySelectorAll('[data-suggest]').forEach(function (button) {
      button.addEventListener('click', function () {
        var input = byId('chat-input');
        if (input) { input.value = button.getAttribute('data-suggest'); input.focus(); }
      });
    });
    panel.querySelectorAll('[data-cite]').forEach(function (button) {
      button.addEventListener('click', function () {
        openSource(button.getAttribute('data-cite'), chat.messages, { kbId: kb.id, pathId: path.id });
      });
    });
    panel.querySelectorAll('[data-copy]').forEach(function (button) {
      button.addEventListener('click', function () {
        var msg = chat.messages.filter(function (item) { return item.id === button.getAttribute('data-copy'); })[0];
        if (msg) KL.ui.copy(msg.content);
      });
    });
  }

  function sendReaderChat(kb, path) {
    ensureChatFor(kb.id);
    var chat = KL.state.chat;
    if (chat.streaming) return;
    var textarea = byId('chat-input');
    if (!textarea) return;
    var text = textarea.value.trim();
    if (!text) return;
    var settings = KL.state.settings || {};
    if (!settings.api || !settings.api.hasApiKey) {
      KL.ui.toast('请先在设置中配置 API Key', 'error');
      return;
    }
    var history = chat.messages.filter(function (item) { return !item.streaming && !item.error; }).slice(-20).map(function (item) {
      return { role: item.role, content: item.content, reasoning: item.reasoning || '' };
    });
    var stamp = Date.now();
    var user = { id: 'u' + stamp, role: 'user', content: text, ts: new Date().toISOString() };
    var assistant = {
      id: 'a' + stamp,
      role: 'assistant',
      content: '',
      reasoning: '',
      sources: [],
      toolLog: [],
      thinking: chat.thinking,
      model: settings.api.model,
      streaming: true,
      ts: new Date().toISOString()
    };
    chat.messages.push(user, assistant);
    chat.streaming = true;
    chat.controller = new AbortController();
    saveChat(chat.kbId, chat.messages);
    textarea.value = '';
    refreshReaderChat(kb, path);
    var liveScroll = byId('reader-chat-scroll');
    if (liveScroll) liveScroll.scrollTop = liveScroll.scrollHeight;

    var body = {
      message: text,
      history: history,
      mode: chat.mode,
      thinking: chat.thinking || 'off',
      enableTools: chat.enableTools !== false,
      stream: true,
      kbId: kb.id
    };
    var reader = KL.state.reader || {};
    if (reader.pathId === path.id && reader.name) {
      body.pathId = path.id;
      body.file = reader.name;
    }

    var onEvent = function (name, data) {
      if (name === 'meta') {
        assistant.model = data.model || assistant.model;
        assistant.thinking = data.thinking || assistant.thinking;
        if (data.toolsSuppressed) {
          var suppressed = byId('chat-tool');
          if (suppressed) {
            suppressed.textContent = data.suppressReason === 'web'
              ? '联网增强 + 关闭思考时编辑工具已暂停；打开思考后可编辑。'
              : '思考模式下编辑工具已暂停；关闭思考后可编辑。';
          }
        }
      } else if (name === 'tool') {
        var note = byId('chat-tool');
        var labels = { search: '联网搜索', fetch: '抓取网页', web: '联网', read: '读取', edit: '编辑', write: '新建', highlight: '高亮' };
        var label = labels[data.tool] || data.tool;
        if (note) {
          if (data.status === 'started') note.textContent = label + '：进行中…';
          else if (data.status === 'done') {
            var extra = data.tool === 'search' ? ' · ' + (data.count || 0) + ' 条'
              : data.tool === 'fetch' ? ' · ' + (data.ok || 0) + ' 篇'
                : data.file ? ' · ' + data.file : '';
            note.textContent = label + '：完成' + extra;
          } else if (data.status === 'error') note.textContent = label + '：失败' + (data.message ? ' · ' + data.message : '');
        }
        if (data.status === 'done' || data.status === 'error') {
          assistant.toolLog.push({ tool: label, file: data.file || '', message: data.status === 'error' ? (data.message || '') : '' });
        }
        if (data.status === 'done' && (data.tool === 'edit' || data.tool === 'write')) {
          KL.ui.toast(data.tool === 'edit' ? '已更新 ' + (data.file || '文档') : '已新建 ' + (data.file || '文档'), 'ok');
        }
      } else if (name === 'doc') {
        if (data.action === 'edit' && data.file) {
          loadPathFile(kb.id, path.id, data.file);
        } else if (data.action === 'write' && data.file) {
          refreshFileStrip(kb, path).then(function () { loadPathFile(kb.id, path.id, data.file); });
        }
      } else if (name === 'highlight') {
        applyHighlight(data.file, data.quote, data.note, path.id, true);
        KL.ui.toast('已高亮：' + (data.file || ''), 'ok');
      } else if (name === 'sources') {
        assistant.sources = data.sources || [];
        var holder = byId('sources-' + assistant.id);
        if (holder) holder.outerHTML = sourceChips(assistant) || '<div id="sources-' + assistant.id + '"></div>';
      } else if (name === 'reasoning') {
        assistant.reasoning += data.text || '';
        var reason = byId('reason-' + assistant.id);
        if (reason) reason.textContent = assistant.reasoning;
        else {
          var live = byId('live-' + assistant.id);
          if (live) {
            live.insertAdjacentHTML('beforebegin', '<details class="reasoning" open id="reasoning-' + assistant.id + '"><summary>思考过程</summary><div id="reason-' + assistant.id + '">' + esc(assistant.reasoning) + '</div></details>');
          }
        }
      } else if (name === 'delta') {
        assistant.content += data.text || '';
        var node = byId('live-' + assistant.id);
        if (node) node.textContent = assistant.content;
        var scroller = byId('reader-chat-scroll');
        if (scroller) scroller.scrollTop = scroller.scrollHeight;
      } else if (name === 'error') {
        assistant.error = data.message || '请求失败';
        assistant.streaming = false;
      } else if (name === 'done') {
        assistant.streaming = false;
        if (!assistant.content && !assistant.error) assistant.content = '（模型没有返回内容）';
        if (data.finishReason === 'error' && !assistant.error) assistant.error = '生成中断';
      }
    };

    KL.api.stream('/api/chat', body, onEvent, chat.controller.signal)
      .then(function () {
        chat.streaming = false;
        assistant.streaming = false;
        saveChat(chat.kbId, chat.messages);
        refreshReaderChat(kb, path);
      })
      .catch(function (err) {
        chat.streaming = false;
        assistant.streaming = false;
        if (err && err.name === 'AbortError') {
          assistant.content += '\n\n_（已停止）_';
        } else {
          assistant.error = err.message || '请求失败';
        }
        saveChat(chat.kbId, chat.messages);
        refreshReaderChat(kb, path);
      });
  }

  // ------------------------------------------------------------- web tools
  function renderWeb() {
    var web = KL.state.web;
    var results = '';
    if (web.searchError) results = '<div class="form-error">' + esc(web.searchError) + '</div>';
    else if (web.results) {
      results = '<div class="card" style="margin-top:16px"><div class="card-head"><div><h2 class="card-title"><span class="no">RESULT</span>搜索结果</h2><p class="card-sub">来自 DeepSeek Search · ' + web.results.length + ' 条</p></div></div>'
        + web.results.map(renderWebResult).join('') + '</div>';
    }
    var searchCard = '<div class="card"><div class="card-head"><div><h2 class="card-title"><span class="no">01</span>WebSearch</h2><p class="card-sub">按 query 检索网页，返回标题、摘要与正文。</p></div></div>'
      + field('搜索词', textarea('web-query', web.searchQuery, '例如：DeepSeek V4 发布 ｜ 支持 site:、after: 等语法', 3))
      + '<div class="form-grid" style="margin-top:14px">'
      + field('返回条数（1..50）', input('web-count', web.searchCount, '5', 'number'))
      + field('原始问题（可选）', input('web-ori', web.oriQuestion, '帮助理解搜索意图'))
      + '</div>'
      + '<label class="row small" style="margin-top:12px"><input type="checkbox" id="web-need-content"' + (web.needContent ? ' checked' : '') + '> 在结果中返回网页正文（need_content）</label>'
      + '<div class="row" style="margin-top:14px"><button class="btn btn-primary" id="web-search-btn">' + icon('search') + '执行搜索</button><span class="hint">会调用 1 次 DeepSeek Search</span></div>'
      + results
      + '<div id="web-raw"></div></div>';
    var preview = '';
    if (web.fetchError) preview = '<div class="form-error" style="margin-top:14px">' + esc(web.fetchError) + '</div>';
    if (web.fetchResult) {
      preview = '<div class="fetch-preview"><div class="row" style="justify-content:space-between"><span class="mono small muted">FETCHED · ' + esc(web.fetchResult.url || '') + '</span>'
        + '<button class="btn btn-ghost btn-sm" id="web-copy">' + icon('copy') + '复制</button></div><div class="md" style="margin-top:12px">' + KL.md.render(web.fetchResult.content || '') + '</div></div>';
    }
    var fetchCard = '<div class="card"><div class="card-head"><div><h2 class="card-title"><span class="no">02</span>WebFetch</h2><p class="card-sub">抓取单个网页，返回 Markdown 正文。</p></div></div>'
      + field('网页 URL', input('web-url', web.fetchUrl, 'https://example.com/article'))
      + '<div class="row" style="margin-top:14px"><button class="btn btn-primary" id="web-fetch-btn">' + icon('globe') + '抓取网页</button><span class="hint">会调用 1 次 DeepSeek Fetch</span></div>'
      + preview + (web.rawFetch ? '<div class="raw-json"><details><summary>原始 JSON</summary><pre>' + esc(JSON.stringify(web.rawFetch, null, 2)) + '</pre></details></div>' : '') + '</div>';

    return page(pageHead('WEB TOOLS / 03', '联网', '直接调试 DeepSeek WebSearch 与 WebFetch；对话页也可以开启联网模式。',
      '<a class="btn btn-ghost" href="#/settings">' + icon('settings') + '接口设置</a>')
      + '<div class="web-grid">' + searchCard + fetchCard + '</div>');
  }

  function renderWebResult(item) {
    return '<div class="result">'
      + '<div class="row"><span class="score">SCORE ' + esc(item.rerank_score == null ? '—' : item.rerank_score) + '</span><span class="spacer"></span><span class="url">' + esc(item.site_name || item.url || '') + '</span></div>'
      + '<h4>' + (safeHref(item.url) ? '<a href="' + val(safeHref(item.url)) + '" target="_blank" rel="noreferrer noopener">' + esc(item.title || item.url) + '</a>' : esc(item.title || item.url || '（无链接）')) + '</h4>'
      + '<div class="url">' + esc(item.url || '') + '</div>'
      + '<div class="snippet">' + esc(item.snippet || '') + '</div>'
      + (item.summary ? '<details><summary>长摘要</summary><div class="content">' + esc(item.summary) + '</div></details>' : '')
      + (item.content ? '<details><summary>网页正文</summary><div class="content">' + esc(item.content) + '</div></details>' : '')
      + (item.date ? '<div class="hint">' + esc(item.date) + '</div>' : '')
      + '</div>';
  }

  function bindWeb(root) {
    var web = KL.state.web;
    byId('web-query').addEventListener('input', function () { web.searchQuery = byId('web-query').value; });
    byId('web-count').addEventListener('input', function () { web.searchCount = Number(byId('web-count').value) || 5; });
    byId('web-count').addEventListener('focus', function () { this.select(); });
    byId('web-ori').addEventListener('input', function () { web.oriQuestion = byId('web-ori').value; });
    byId('web-need-content').addEventListener('change', function () { web.needContent = byId('web-need-content').checked; });
    byId('web-url').addEventListener('input', function () { web.fetchUrl = byId('web-url').value; });
    byId('web-search-btn').addEventListener('click', async function () {
      var button = byId('web-search-btn');
      web.searchQuery = byId('web-query').value.trim();
      web.searchCount = Number(byId('web-count').value) || 5;
      web.oriQuestion = byId('web-ori').value.trim();
      web.needContent = byId('web-need-content').checked;
      if (!web.searchQuery) { KL.ui.toast('请填写搜索词', 'error'); return; }
      button.disabled = true;
      web.searchError = '';
      try {
        var data = await KL.api.post('/api/web/search', {
          query: web.searchQuery,
          count: web.searchCount,
          ori_question: web.oriQuestion || null,
          need_content: web.needContent
        });
        web.results = data.results || [];
        web.rawSearch = data;
        KL.app.renderView();
      } catch (err) {
        web.searchError = err.message;
        KL.app.renderView();
      } finally { button.disabled = false; }
    });
    byId('web-fetch-btn').addEventListener('click', async function () {
      var button = byId('web-fetch-btn');
      web.fetchUrl = byId('web-url').value.trim();
      web.fetchError = '';
      if (!web.fetchUrl) { KL.ui.toast('请填写 URL', 'error'); return; }
      button.disabled = true;
      try {
        web.fetchResult = await KL.api.post('/api/web/fetch', { url: web.fetchUrl });
        web.rawFetch = web.fetchResult;
        KL.app.renderView();
      } catch (err) {
        web.fetchError = err.message;
        KL.app.renderView();
      } finally { button.disabled = false; }
    });
    var copy = byId('web-copy');
    if (copy) copy.addEventListener('click', function () { KL.ui.copy(web.fetchResult.content || ''); });
  }

  // ------------------------------------------------------------- dispatch
  function renderView() {
    var root = byId('view-root');
    if (!root) return;
    var route = KL.state.route || { name: 'library' };
    try {
      if (route.name === 'settings') {
        root.innerHTML = KL.views.renderSettings();
        KL.views.bindSettings(root);
      } else if (route.name === 'kb') {
        root.innerHTML = KL.views.renderKb();
        KL.views.bindKb(root);
      } else if (route.name === 'path') {
        root.innerHTML = renderPath();
        bindPath(root);
      } else {
        root.innerHTML = KL.views.renderLibrary();
        KL.views.bindLibrary(root);
      }
    } catch (err) {
      root.innerHTML = page('<div class="form-error">页面渲染失败：' + esc(err.message) + '</div>');
    }
    if (KL.app && KL.app.updateRail) KL.app.updateRail();
  }

  KL.views.renderPath = renderPath;
  KL.views.startGeneration = startGeneration;
  KL.views.renderView = renderView;
})(window.KL = window.KL || {});
