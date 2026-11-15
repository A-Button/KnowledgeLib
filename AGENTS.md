# AGENTS.md

本文件是本仓库对自动化编码代理的硬性约定。开始修改前先读一遍。

## 项目概览

KnowledgeLib 是本地运行的「学习路径生成 + 阅读工作台」：

- 后端：Python 3.11+、FastAPI、Uvicorn、httpx，使用 uv 管理环境。
- 前端：原生 HTML / CSS / JavaScript，无构建步骤；`theme-init.js` 在 `<head>` 预置主题，主体脚本按 `md.js` → `api.js` → `ui.js` → `highlight.js` → `theme.js` → `views.js` → `app.js` 顺序加载。
- 测试：pytest + 本地 Python Mock 上游，禁止使用真实 API Key 或访问外部模型服务。
- 配置：保存在用户目录（Windows `%APPDATA%\KnowledgeLib\settings.json`），不要提交到仓库。
- 运行时数据：`data/`（知识库、回收站、缓存）不进入版本控制。
- Git 同步：每个知识库可初始化独立仓库；remote 存在 `.git/config`，同步偏好存在用户配置目录的 `git-sync.json`。

## 硬性收尾流程

任何代码、测试、前端资源、配置或文档改动完成后，必须按顺序执行：

1. 运行完整测试：

   ```bash
   uv run pytest -q
   ```

   必须全部通过。**测试失败时禁止 commit、禁止 push**；先修复，再从头重跑。

2. 运行应用自检：

   ```bash
   uv run python app.py --selftest
   ```

   必须输出 `SELFTEST OK (19 checks)`。

3. 运行项目总检查：

   ```bash
   uv run python tools/selfcheck.py
   ```

   必须输出 `ALL CHECKS PASSED`（包含 AST、`node --check` 与全量 pytest）。

4. 只有第 1–3 步全部通过，才允许提交并推送：

   ```bash
   git status --short
   git add <本次改动的具体文件>
   git commit -m "<简洁的英文祈使句，说明本次改动>"
   git push origin main
   ```

   提交前检查 `git status`：
   - 不得包含 `.venv/`、`data/`、`.pytest_cache/`、`__pycache__/`、日志或临时文件；
   - 不得包含任何 API Key、Token、用户配置或真实知识库内容；
   - 新增文件要明确加入，不要用未检查的 `git add -A` 把运行态带进去。

5. 推送失败时：
   - 不要使用 `git push --force` 或改写历史；
   - 保留本地 commit，报告完整错误；
   - 若只是网络问题，等网络恢复后重试 `git push origin main`。

## 测试纪律

- 所有测试必须离线运行，只使用 `tests/mock_upstream.py`、内存临时目录或临时本地 Git 裸仓库。
- 不把真实 API Key 写进测试、命令、提交信息或日志。
- 修 bug 时先补能复现问题的测试，再改实现；不允许通过删除/跳过测试让套件变绿。
- 当前基线是 **88 个 pytest 用例**；新增功能应同步增加测试并更新 README 中的数量。
- 前端改动至少要被 `tests/test_static.py` 的 `node --check` 与 `byId` 检查覆盖。

## 版本控制纪律

- 编译产物、打包产物、虚拟环境、运行时数据一律不入库。
- `.gitignore` 已排除 `.venv/`、`data/`、`__pycache__/`、`.pytest_cache/`、`*.pyc`、`*.log` 等。
- 一个 commit 只做一件逻辑上的事；提交信息使用英文祈使句，例如：
  - `Add rename for knowledge bases and learning paths`
  - `Fix DeepSeek thinking-mode tool combination`
- 不在未获用户明确要求时改写历史、强推或删除远端分支。

## 关键实现约束

- 学习资料文件名必须是 `NN-标题.md`，首行必须是 `# NN · 标题`；`00` 只用于总览。
- DeepSeek 函数工具只允许两种组合：
  - 联网 + 思考开启（服务端 `deepseek_web_search` 伴随函数工具）；
  - 非联网 + 思考关闭（不声明服务端工具）。
  其余组合必须在后端丢弃函数工具，不能直接发给 DeepSeek。
- 知识库/学习路径重命名只改 `.kb.json` / `.path.json` 的 `name`，不改目录名、slug 与文件内容。
- 前端静态资源改动后，提醒用户重启后端（若改 Python）并 Ctrl+F5；后端改动只刷新页面不会生效。
- 主题/液态玻璃/无衬线开关只存浏览器 `localStorage`；liquidGL 本地 vendored 且为 MIT，CSP 需要 `worker-src 'self' blob:`。
- Git 同步设置（自动同步、间隔、方向、冲突策略、自定义命令）只存用户配置目录的 `git-sync.json`；remote 存在知识库的 `.git/config`，不得写进 `.kb.json` 或提交进仓库。
- 自定义 Git 命令必须以 `git` 开头、不经 shell 执行，并拒绝 `-C` / `--git-dir` / `--work-tree` 等切仓参数；错误信息中的 remote URL 要脱敏。
- 后台自动同步线程只在服务 lifespan 内启动；测试用临时裸仓库，不得访问 GitHub 等外部远端。

## 本机环境备注

- 本机 `gh` 不在 PATH，位置是 `C:\Program Files\GitHub CLI\gh.exe`。
- 远端使用 HTTPS：`https://github.com/A-Button/KnowledgeLib.git`；已配置 `gh auth setup-git`。
- 如果 `git push` 因网络无法连接 `github.com:443`，不要强推；可先用 `gh api` 检查网络/仓库状态，再重试。
