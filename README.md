# Teamflow

Teamflow 是一个可持续迭代的多 Agent 编码工作流：GLM-5.2 负责需求分析、规划与测试编写，Kimi K3 专注代码实现，MiMo 2.5 Pro 执行测试并返回错误回执，Basic Memory 提供完全本地的跨项目记忆。底层 Agent 运行时是 Pi（pi-runtime），用户只使用统一的 `teamflow` 命令，不需要感知底层 Agent harness。

工作项目只看到本地目录 `.teamflow/`；唯一的仓库级改动是在 `.gitignore` 中声明 `.teamflow/`。

## 工作流

```text
用户需求
  -> planner (GLM-5.2): 召回记忆、分析需求、定义验收标准
  -> test-writer (GLM-5.2): 先写测试并给出精确执行命令
  -> test-runner (MiMo 2.5 Pro): 执行测试并返回结构化失败回执
  -> coder (Kimi K3): 最小实现并让测试通过
  -> test-runner (MiMo 2.5 Pro): 回归并返回 PASS/FAIL/BLOCKED 回执
  -> test-writer (GLM-5.2): 检查断言与最终 diff
  -> planner (GLM-5.2): 仅在 PASS 后写入已验证记忆并汇总
```

`planner` 是默认主 Agent，`coder`、`test-writer`、`test-runner` 等是受限子 Agent，在同一工作区顺序执行，不自动创建 worktree，不自动提交或推送。

## 安装

要求：macOS 或 Linux、Git、curl、Node.js 20+，以及 Kimi K3、MiMo、DeepSeek、智谱 GLM Coding Plan 的 API Key。

```bash
git clone git@github.com:wenshiqi0/teamflow.git
cd teamflow
./scripts/bootstrap.sh                       # 安装运行时、uv、Basic Memory 与全局入口
export PATH="$HOME/.local/bin:$PATH"         # 如 ~/.local/bin 不在 PATH
```

在全局 `~/.teamflow/.env` 或 shell 环境变量中配置密钥（shell 环境变量优先级最高）：

```dotenv
KIMI_API_KEY=your-kimi-api-key
ZHIPU_API_KEY=your-zhipu-coding-plan-api-key
MIMO_API_KEY=your-mimo-api-key
DEEPSEEK_API_KEY=your-deepseek-api-key
```

```bash
./scripts/setup.sh     # 初始化本地跨项目记忆并检查模板
./scripts/doctor.sh    # 环境与安装诊断
```

## 初始化工作项目

```bash
./scripts/install.sh --dry-run /path/to/project   # 预览
./scripts/install.sh /path/to/project
cd /path/to/project
teamflow                                               # 交互式
teamflow run --agent planner "为当前项目增加一个健康检查接口"   # 无头运行
```

安装器遵循最小 Git 侵入原则：所有运行文件只写入 `.teamflow/`；`.gitignore` 只增加 `.teamflow/`；业务项目已有的配置与源码完全保留；manifest 位于 `.teamflow/manifest.json`，支持幂等更新与冲突检测，用户改动过的受管文件不会被静默覆盖；只安装产品文件——Teamflow 自身的开发上下文（测试、`runs/`、`sessions/`、凭证、`docs/`、仓库级 README 与 AGENTS.md）不会进入业务项目。

## 卸载

```bash
./scripts/uninstall.sh --dry-run                          # 预览
./scripts/uninstall.sh                                    # 只清理全局命令
./scripts/uninstall.sh --project /path/to/project         # 同时清理该项目运行时
./scripts/uninstall.sh --project /path/to/project --memory  # 连本地记忆一起删除
```

`--memory` 才会删除 `~/.teamflow/memory/`；跨项目记忆是用户知识，不加该参数永不删除。

## 目标项目布局

```text
.teamflow/
├── models.json       # Pi provider/model 注册表（唯一 provider 配置）
├── manifest.json     # 安装器校验信息
├── AGENTS.md         # Teamflow 共享约束
├── agents/           # planner / test-writer / test-runner / coder / command / supervisor / memory-*
├── skills/
├── bin/              # teamflow / memory / memory-capture / test-patch / server
├── extensions/       # teamflow-task 委派扩展 + memory-context 上下文扩展
├── experiments/bin/  # 显式调用的临时实验（memory-experiment、memory-compare）
└── runs/             # 临时运行产物（不入 Git）
```

全局 `teamflow` 命令定位当前 Git 项目后调用 `.teamflow/bin/teamflow`；包装器通过显式配置路径加载 Agents 与 Skills，业务根目录不需要任何 harness 配置。

## 常用命令

```bash
teamflow run --agent planner "分析需求"     # 按 agents/<role>.md frontmatter 解析 provider/model/system prompt
teamflow command "列出当前分支"             # 明确命令式任务的快速入口（不启动 planner/coder）
teamflow debug agent [名称]                 # 查看 Agent 元数据
teamflow debug skill                        # 列出已安装 Skill
teamflow session list --format json         # 会话元数据概要（默认上限 10 条）
teamflow phase status --run-id <id>         # 阶段回执；stale 只表示观察时间较长
teamflow probe                              # 探测最近活跃运行（退出码 0=alive、1=exited、2=unknown）
teamflow source-check                       # 拒绝源码中的非打印控制字节
```

测试由 test-writer 生成统一补丁到 `.teamflow/runs/test-patches/`；`teamflow test-patch check` 校验后由 coder 用 `teamflow test-patch apply` 机械应用。

明确的 provider 超时、认证失败、额度不足、overload、传输失败或用户取消必须结束当前阶段并返回真实的 `BLOCKED`，不能折叠为空结果或静默重试。外层协调只观察元数据：先用 `teamflow probe`，再按需 `teamflow phase status` 与 `teamflow session list`，不读会话文件、prompt、response 或凭证，也不因终端静默终止内层运行（见 `.teamflow/skills/observe-inner-loop/`）。

## 模型配置

| Agent | Model | 权限 |
| --- | --- | --- |
| `planner` / `test-writer` | GLM-5.2 | 规划、测试设计与断言审查；不修改业务代码 |
| `test-runner` | MiMo 2.5 Pro | 只执行测试并返回结构化回执；禁止修改文件 |
| `coder` | Kimi K3 | 专注代码实现；禁止危险 Git 操作 |
| `command` / `supervisor` | MiMo 2.5 Pro | 明确的 shell/Git 操作 / 机械性校验 |

记忆候选生成是串行管道：emotional-salience-sensor（MiMo 2.5 Pro）→ memory-compressor（DeepSeek）→ memory-extractor（GLM-5.2）→ memory-formatter（GLM-5.2）。Emotion 只提供注意力元数据，不进行心理诊断、不主动追问。

当前模型端点：Kimi `https://api.kimi.com/coding/v1`；MiMo `https://token-plan-cn.xiaomimimo.com/v1`；DeepSeek `https://api.deepseek.com`；智谱 GLM `https://open.bigmodel.cn/api/coding/paas/v4`。

Agent frontmatter 字段：`description`（必需）、`model`（必需，`<provider>/<model>`）、`tools`（可选，逗号分隔）、`delegates`（严格布尔值 `true` 时授权 `task`/`task_group`）、`needs_project_rules`（`false` 时跳过 AGENTS.md 注入）。只有 depth-0 且声明 `delegates: true` 的角色可以委派；子角色运行在 depth 1，不能继续委派。

## 本地跨项目记忆

```text
~/.teamflow/memory/
├── knowledge/    # Markdown source of truth
└── state/        # Basic Memory 配置、SQLite、日志、缓存、冷存储
```

```bash
teamflow memory status
teamflow memory recall "Agent 权限配置"
teamflow memory list
teamflow memory read "memory://<note-permalink>"
teamflow memory context "memory://<topic>/*"
```

`recall` 默认只检索当前仓库命名空间加全局命名空间；显式设置 `TEAMFLOW_MEMORY_RECALL_SCOPE=all` 才做无范围检索。`remember`/`remember-global` 仅保留给显式手工写入；编码任务收尾必须生成 verified-task receipt 后运行 `teamflow memory-capture --receipt <file>`，安全 apply 只自动写入新的原子候选。单次任务默认最多自动创建 8 条新记忆（`TEAMFLOW_MEMORY_MAX_CREATES_PER_RUN` 可调整）。

### 只读本地记忆浏览服务

```bash
teamflow server [--host 127.0.0.1] [--port 7324] [--dir <仓库路径>]
```

`teamflow server` 是 Bun + TypeScript 实现的只读本地服务（源码在仓库根 `server/`，由 bootstrap/install 同步到全局 `~/.teamflow/server/`，不按项目安装），用于浏览 Basic Memory 记忆并代理 opencode 会话。运行时只依赖 Bun 内置与预构建静态资源（构建期依赖仅在仓库内），通过 `basic-memory` CLI 读取记忆；不使用 MCP、云同步、账号或密钥。服务严格只读：非 GET 请求一律返回 405。

`/api/oc/*` 是服务端反向代理：opencode 的 Basic Auth 凭证留在服务端进程内，绝不下发到浏览器；SSE 流式透传。未配置 `TEAMFLOW_OPENCODE_URL` 时 `/api/oc/*` 返回结构化 503，记忆浏览不受影响。

### 可配置项

- `TEAMFLOW_HOME`：默认 `$HOME/.teamflow`
- `TEAMFLOW_BIN_DIR`：默认 `$HOME/.local/bin`
- `TEAMFLOW_MEMORY_HOME`：默认 `$TEAMFLOW_HOME/memory`
- `TEAMFLOW_MEMORY_PROJECT`：默认 `teamflow`
- `TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS`：默认不设置（不启用本地 wall-time）
- `TEAMFLOW_OPENCODE_URL` / `TEAMFLOW_OPENCODE_USERNAME` / `TEAMFLOW_OPENCODE_PASSWORD`：默认不设置（`/api/oc/*` 返回 503）

## 运行时产物与清理

不入 Git、不被安装：`.teamflow/runs/`、`.teamflow/sessions/`、`.teamflow/auth.json`、`.teamflow/models-store.json`、`.teamflow/.env`。其他 AI harness 的配置（`openai.yaml`、`CLAUDE.md`、`.codex/` 等）在 `.teamflow/.gitignore` 中忽略，不会成为受管文件。

```bash
python3 scripts/clean.py --dry-run   # 预览
python3 scripts/clean.py             # 只删除 .teamflow/runs/ 下的一次性原始输出；证据类产物保留
```

## 测试布局

测试跟随被测代码：`tests/`（scripts/ 与仓库级契约）、`tests/runtime/`（运行时按主题分组）、`.teamflow/extensions/**/*.test.ts`（bun test）、`server/tests/`（Bun 服务）。这些目录都不会被安装到业务项目。

```bash
python -m pytest tests                 # 全部 Python 测试（含 tests/runtime/）
bun test ./.teamflow/extensions/       # 扩展纯逻辑测试
cd server && bun test                  # server 全部测试
```

## 维护与诊断

```bash
teamflow debug agent planner    # 查看工作流发现的 Agent
teamflow debug skill
./scripts/update.sh --ref main --instruction "保留本地知识图谱能力，继续禁止云端与 MCP"   # 刷新 Basic Memory Skills
```

调整模型、权限、交接协议或安装链路时，同步 README 与 `AGENTS.md`；跨项目记忆保留来源且使用前重新验证，不能替代当前仓库事实。

## 容器运行（Docker）

仓库根目录提供多阶段 `Dockerfile`：构建阶段安装 `opencode-ai`、`@earendil-works/pi-coding-agent` 与 `basic-memory`（经 uv），运行阶段以非 root 用户 `opencode` 在 `WORKDIR /workspace/teamflow`（镜像构建时从公开仓库检出的 Git 工作树，含 `.teamflow/` 运行时）下直接以 exec-form 运行 `opencode serve`（headless 服务器模式，仍对外提供 Web UI；`opencode web` 会尝试调用容器内不可用的 `xdg-open` 而崩溃），只绑定回环地址 `127.0.0.1:13000`。公开端口 3000 由同 Pod 内的 Caddy sidecar 占用并反向代理到该容器；健康探针合成与认证转发属于 Caddy sidecar 的职责，不在本镜像内实现。

- 凭据：模型与运行时密钥一律在 `docker run` 时通过环境变量注入；镜像不含任何凭据或 `.env` 内容。
- 登录凭证：`OPENCODE_SERVER_USERNAME` 和 `OPENCODE_SERVER_PASSWORD` 两者都是必需的（required for a stable known login）；缺省时 OpenCode 每次启动随机生成不可预测的凭证（random credentials），无法稳定登录。

```bash
docker build -t teamflow-opencode-web .
# 镜像默认命令只绑定容器内回环地址 127.0.0.1；不带 sidecar 独立运行时必须显式覆盖命令放开绑定，
# 否则 -p 发布的端口指向容器网络接口，流量到不了回环地址上的进程。
docker run -e OPENCODE_SERVER_USERNAME=$OPENCODE_SERVER_USERNAME -e OPENCODE_SERVER_PASSWORD=$OPENCODE_SERVER_PASSWORD -p 13000:13000 \
  teamflow-opencode-web opencode serve --hostname 0.0.0.0 --port 13000
# 推荐：用 --env-file 避免密钥进入 shell 历史
# docker run --env-file .env -p 13000:13000 teamflow-opencode-web opencode serve --hostname 0.0.0.0 --port 13000
```

检出源与版本由 `ARG TEAMFLOW_REPO_URL`（默认公开 GitHub 仓库）与 `ARG TEAMFLOW_REPO_REF`（默认 `main`）控制。单 Pod 双容器拓扑、健康探针合成、持久化、rollout/rollback 等完整设计见 `docs/container-sidecar-deployment.md`。

## 记忆与上下文架构

`.teamflow/extensions/` 中的 memory-context 扩展接管 Agent 上下文的生命周期；完整设计见 `docs/teamflow-memory-context-design.md`。要点：

- 冷记忆（cold-memory）：会话按 turn-block 片段归档到冷存储；文件级冷存储位于 `~/.teamflow/memory/state/` 下的 file-cold-store（cold-store），仅在召回时按需回填，不常驻上下文。
- Phase C 上下文注入：召回的记忆以可见 XML（visible XML）片段做 context 注入，Agent 与人都能看到注入了哪些记忆。
- Phase D 热区投影：上下文按热区（hot-zone）投影裁剪，受 CONTEXT_BUDGET 预算约束，超出预算的部分走 no-compact 降级路径而非静默压缩。
- Phase G 规划反馈：预算耗尽时发出 CONTEXT_BUDGET_EXCEEDED（预算失败）信号，并回写规划经验（planning-experience），供后续任务的 planner 召回。

## 设计文档

- `docs/container-sidecar-deployment.md` — 单 Pod 双容器（OpenCode + Caddy sidecar）部署设计
- `docs/teamflow-memory-context-design.md` — memory-context 扩展的上下文接管设计
- `docs/teamflow-web-console-design.md` — server/ 记忆浏览与 opencode 代理设计
- `docs/multi-agent-optimization-design.md` — 多 Agent 编排优化设计

## 迭代原则

1. 对业务仓库保持最小 Git 侵入：只声明 `.teamflow/` 忽略规则，所有实现集中在该目录。
2. 先修改工作流规则、Agent 或 Skill，再用真实任务验证。
3. 测试必须在实现前证明缺失行为，最终必须重新运行。
4. Agent 不得自行推送、强制重置或清理用户工作区。
5. 调整模型、权限、交接协议或安装链路时，同步 README 和 `AGENTS.md`。
6. 跨项目记忆保留来源且使用前重新验证，不能替代当前仓库事实。
