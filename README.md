# Teamflow

这是一个可持续迭代的多 Agent 编码工作流：GLM-5.2 负责需求分析、规划与测试编写，Kimi K3 专注代码实现，MiMo 2.5 Pro 负责执行测试并返回错误回执，Basic Memory 提供完全本地的跨项目记忆。

工作项目只看到统一的本地目录 `.teamflow/`。不会向业务仓库根目录写入配置 JSON、Agent 目录或脚本；唯一的仓库级改动是在 `.gitignore` 中声明 `.teamflow/`。用户只使用 `teamflow` 命令，不需要感知底层 Agent harness。

## 工作流

```text
用户需求
  -> planner (GLM-5.2): 召回线索、分析需求、定义验收标准
  -> test-writer (GLM-5.2): 先写测试并给出精确执行命令
  -> test-runner (MiMo 2.5 Pro): 执行测试并返回结构化失败回执
  -> coder (Kimi K3): 完成最小实现并让测试通过
  -> test-runner (MiMo 2.5 Pro): 执行回归并返回 PASS/FAIL/BLOCKED 回执
  -> test-writer (GLM-5.2): 检查断言与最终 diff
  -> planner (GLM-5.2): 仅在 PASS 后写入已验证记忆并汇总
```

`planner` 是默认主 Agent，`coder`、`test-writer` 和 `test-runner` 是受限子 Agent。当前在同一工作区中顺序执行，不自动创建 worktree，不自动提交或推送。

## 安装

要求：macOS 或 Linux、Git、curl、Node.js 20+、Kimi K3 API Key、MiMo API Key、DeepSeek API Key、智谱 GLM Coding Plan API Key。

```bash
git clone git@github.com:wenshiqi0/teamflow.git
cd teamflow
./scripts/bootstrap.sh
```

`bootstrap.sh` 会安装或升级底层运行时、uv、Basic Memory，并把统一入口安装到 `~/.local/bin/teamflow`。如该目录不在 `PATH`，将它加入 shell 配置：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

在全局的 `~/.teamflow/.env` 中配置模型密钥，或直接使用 shell 环境变量：

```dotenv
KIMI_API_KEY=your-kimi-api-key
ZHIPU_API_KEY=your-zhipu-coding-plan-api-key
MIMO_API_KEY=your-mimo-api-key
DEEPSEEK_API_KEY=your-deepseek-api-key
```

shell 环境变量优先级最高；业务项目的 `.teamflow/.env` 可作为少数项目的本地覆盖，但默认不需要创建。

初始化本地跨项目记忆并检查模板：

```bash
./scripts/setup-memory.sh
./scripts/doctor.sh
```

## 初始化工作项目

先预览：

```bash
./scripts/init-project.sh --dry-run /path/to/project
```

再安装：

```bash
./scripts/init-project.sh /path/to/project
cd /path/to/project
teamflow
```

无头运行：

```bash
teamflow run --agent planner "为当前项目增加一个健康检查接口"
```

安装器遵循最小 Git 侵入原则：

- 所有运行文件只写入项目的 `.teamflow/`。
- `.gitignore` 只增加 `.teamflow/` 及一行中性说明，便于团队共享这一目录约定。
- 业务项目已有的 `AGENTS.md`、配置、脚本和源码完全保留。
- manifest 位于 `.teamflow/manifest.json`，用于幂等更新和冲突检测。
- 用户修改过的受管文件不会被静默覆盖；`--force` 会先备份到 `~/.teamflow/backups/`。

除 `.gitignore` 的标准目录规则外，安装前后业务仓库的 `git status --short` 应保持不变。

## 目标项目布局

```text
.teamflow/                    # 整个目录仅本地存在
├── models.json               # Pi provider/model 注册表（唯一 provider 配置）
├── manifest.json             # 安装器校验信息
├── AGENTS.md                 # Pi 从 agentDir 自动追加的 Teamflow 共享约束
├── agents/
│   ├── planner.md
│   ├── test-writer.md
│   ├── test-runner.md
│   └── coder.md
├── skills/
├── bin/                      # 仅正式流程入口
│   ├── teamflow              # 项目内入口
│   ├── memory                # 本地记忆适配器
│   ├── memory-capture        # 已验证任务的正式记忆链路
│   ├── test-patch            # 测试补丁门禁
│   └── server                # 只读本地记忆浏览服务
├── extensions/
│   └── teamflow-task/        # task(agent, prompt) 角色启动器（仅 planner 深度 0 注册）
├── server/                   # Bun + TypeScript HTTP 服务源码
├── experiments/bin/          # 显式调用的临时实验，不由 teamflow 命令暴露
└── runs/                     # 临时运行产物
```

全局 `teamflow` 命令只负责定位当前 Git 项目，再调用 `.teamflow/bin/teamflow`。包装器通过显式配置路径加载 Agents 和 Skills，因此业务根目录不需要任何 harness 配置或目录。

### 公共 CLI 映射

包装器导出 `PI_CODING_AGENT_DIR` 指向项目 `.teamflow/`，并把角色路由到底层运行时：

```bash
teamflow run --agent planner "分析需求"     # 按 agents/<role>.md frontmatter 解析 provider/model/system prompt
teamflow command "列出当前分支"             # command 角色的快捷入口
teamflow debug agent [名称]                 # 查看项目 .teamflow/agents/ 中的 Agent 元数据
teamflow debug skill                        # 列出项目 .teamflow/skills/ 中已安装的 Skill
teamflow session list --format json         # 仅输出会话元数据（id/model/provider/时间/message_count）
```

角色身份由 `agents/<role>.md` 的 Markdown frontmatter 唯一确定：`model`（`<provider>/<model>`）映射到 provider 与模型，文件正文即系统提示；`test-runner` 通过 frontmatter 的 `tools` 排除 edit 保持只读。Pi 会从 `PI_CODING_AGENT_DIR=.teamflow` 自动追加 `.teamflow/AGENTS.md`，并继续追加业务项目根目录已有的 `AGENTS.md`；运行器不再显式追加同一文件，避免重复注入。会话目录默认读取 `TEAMFLOW_PI_SESSION_DIR`，未设置时回退到 `$PI_CODING_AGENT_DIR/sessions`。

`pi-runtime run` 会通过 `--extension` 加载 `.teamflow/extensions/teamflow-task/index.ts` 并导出 `TEAMFLOW_AGENT_ROLE`/`TEAMFLOW_AGENT_DEPTH=0`。该扩展仅在深度 0（planner）注册 `task(agent, prompt)` 工具：按文件名在 `.teamflow/agents/` 中解析角色 Markdown，用 frontmatter 的 `model`（`<provider>/<model>`）与可选 `tools` 启动隔离的 pi 子进程（JSON 模式），子进程环境为 `TEAMFLOW_AGENT_ROLE=<role>`、`TEAMFLOW_AGENT_DEPTH=1`，未知角色、非零退出、取消以及 `stopReason` 为 `error`/`aborted`/`length` 时显式失败。

## 模型配置

| Agent | Model | 权限 |
| --- | --- | --- |
| `planner` | GLM-5.2 | 需求分析、规划和调用指定子 Agent；不修改业务代码 |
| `test-writer` | GLM-5.2 | 只负责测试设计、测试文件和最终断言审查 |
| `test-runner` | MiMo 2.5 Pro | 只执行测试并返回结构化错误回执；禁止修改文件 |
| `coder` | Kimi K3 | 专注修改代码、构建和测试；禁止危险 Git 操作 |
| `command` | MiMo 2.5 Pro | 快速执行明确的 Shell、Git、GitHub 操作；禁止修改代码和启动子 Agent |

记忆候选生成使用四个隔离阶段：`emotional-salience-sensor`（MiMo 2.5 Pro）探测可观察信号与记忆显著性，`memory-compressor`（DeepSeek V4 Pro）压缩原始长记忆，`memory-extractor`（GLM-5.2）发现概念与经验，`memory-formatter`（GLM-5.2）生成原子化候选。正式 formatter 固定使用 GLM-5.2，作为稳定输出骨架；其他模型只通过实验目录临时对比。Emotion 只提供注意力元数据，不进行心理诊断、不主动追问，也不能作为事实证据或直接写入记忆。

当前模型端点：

- Kimi Code：`https://api.kimi.com/coding/v1`
- MiMo OpenAI-compatible：`https://token-plan-cn.xiaomimimo.com/v1`
- DeepSeek：`https://api.deepseek.com`（`deepseek/deepseek-v4-pro`）
- 智谱 GLM Coding Plan：`https://open.bigmodel.cn/api/coding/paas/v4`

底层由 Pi runtime 读取 `.teamflow/models.json` 和 Agent Markdown frontmatter，角色解析不依赖额外兼容配置。

明确的命令式任务不启动 GLM planner 与 K3 coder，直接使用快速命令模式：

```bash
teamflow command "检查当前 diff，提交到 feat/example 并创建 PR"
```

该模式由 MiMo 2.5 Pro 执行，仅适用于无需修改业务内容的状态检查、测试执行、分支、提交、push 和 PR 操作；危险清理、强制推送、代码编辑和子 Agent 委派均被禁止。

Pi 以流式方式消费模型响应。明确的 provider timeout、认证失败、额度不足、overload、传输失败、用户取消或进程退出必须结束当前阶段并返回真实的 `BLOCKED`；不能把错误折叠为空结果或静默重试整轮。`teamflow phase status --run-id <id>` 查看当前阶段，增加 `--phase <name>` 可读历史阶段回执；其中 stale 只表示观察时间较长，不会终止模型。K3 每批编辑后必须运行 `teamflow source-check`，它会拒绝 NUL、ESC、DEL 等误入源码的非打印控制字节。

记忆 Agent 默认同样无限等待 provider。只有显式设置正整数 `TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS` 才启用本地 wall-time；零、负数和非整数会被拒绝。若显式 timeout 或 provider 错误发生在 extraction 之后，使用 `teamflow memory-capture --receipt <file> --resume-formatting <run-id>`，不重跑已完成阶段；启用 timeout 时仍会终止整个子进程组，避免后台孤儿继续执行 apply。

外层协调只观察元数据：用 `teamflow phase status --run-id <id>` 读取阶段收据，用 `teamflow session list --format json` 读取会话概要，并检查 `.teamflow/runs/` 下约定产物是否存在。它不读取会话文件、prompt、reasoning、response、原始错误或凭证，也不因终端静默自行终止内层运行。

单次任务默认最多自动创建 8 条新记忆；超出时 deterministic validation/apply 会在任何写入前整体拒绝。可通过 `TEAMFLOW_MEMORY_MAX_CREATES_PER_RUN` 显式调整，但不建议常态放宽。

## 本地跨项目记忆

默认目录：

```text
~/.teamflow/memory/
├── knowledge/    # Markdown source of truth
└── state/        # Basic Memory 配置、SQLite、日志和缓存
```

默认 Basic Memory project 为 `teamflow`。全部操作强制使用本地模式，不需要账号、邮箱、云 API 或 MCP。

```bash
teamflow memory status
teamflow memory recall "Agent 权限配置"
teamflow memory list
teamflow memory read "memory://<note-permalink>"
teamflow memory context "memory://<topic>/*"
teamflow memory remember "已验证的项目事实；证据：相关测试 PASS。"
teamflow memory remember-global "已在多个项目验证的通用实践。"
```

`recall` 默认只检索当前仓库的命名空间（`projects/<slug>/*`）加上全局命名空间（`global/*`），合并结果时保留上游排序、按 permalink 去重，其他仓库的记忆不会出现；如需跨全部记忆的无范围检索，显式设置 `TEAMFLOW_MEMORY_RECALL_SCOPE=all`。

`remember`/`remember-global` 仅保留给显式手工写入；编码任务收尾禁止直接调用，必须生成 verified-task receipt 后运行 `teamflow memory-capture`。

### 只读本地记忆浏览服务

```bash
teamflow server [--host 127.0.0.1] [--port 7324] [--dir <仓库路径>]
```

`teamflow server` 启动一个只读、仅本地的 HTTP 服务，用于浏览 Basic Memory 中的记忆。默认绑定 `127.0.0.1`，默认端口 `7324`；也可用环境变量 `TEAMFLOW_SERVER_HOST` / `TEAMFLOW_SERVER_PORT` 配置，优先级为 CLI 参数 > 环境变量 > 默认值。

使用 `--dir` 可按仓库范围浏览：

```bash
teamflow server --dir ../try/mcap
```

`<path>` 相对于进程当前工作目录解析（也接受绝对路径），且必须是一个已存在的 Git 工作树；校验失败时服务在绑定端口前直接退出。仓库 slug 的推导规则与 `.teamflow/bin/memory` 一致：取 `remote.origin.url` 的 basename（去掉 `.git` 后缀），转小写，非 `[a-z0-9._-]` 字符折叠为 `-` 并去掉末尾的 `-`；无 remote 时回退为 Git 顶层目录的 basename。`--dir` 模式下先分页读取共享项目，再按 `teamflow/projects/<slug>/` 精确前缀在本地过滤；页面头部会显示当前仓库 slug。不带 `--dir` 时读取全部记忆。

端点：

- `GET /`：交互式只读浏览页面（HTML）。在浏览器打开 `http://127.0.0.1:7324/`（或配置的 host/port）即可使用。
- `GET /memory?permalink=<记忆标识>`：只读记忆详情页；列表标题会生成该站内链接，不显示内部 permalink 路径。
- `GET /health`、`GET /api/health`：返回 `{"status": "ok"}`。
- `GET /api/memories?page=1&page_size=20&query=<可选>`：返回 `{items, page, page_size, total, total_pages, query}`；`page >= 1`，`1 <= page_size <= 100`；无 query 时读取最近活动，有 query 时执行全文搜索。
- `GET /api/memory?permalink=<记忆标识>`：通过本地 `basic-memory read-note` 读取单条记忆；`--dir` 模式下只允许当前仓库前缀内的记忆。

`GET /` 页面能力：

- 默认以卡片形式浏览最近记忆活动（每页 12 条）。
- 点击卡片标题打开可读详情，并可返回列表；内部 permalink 和文件路径不会作为页面文本展示。
- 全文搜索，提交或清空搜索时自动重置到第 1 页。
- 上一页/下一页分页，并显示“第 N 页 / 共 M 页”指示。
- 具有可见的加载、空结果与错误状态。
- 页面为响应式布局，适配桌面与移动端浏览器。

该页面严格只读：不提供任何编辑、创建或删除控件；记忆数据全部通过 `textContent` / `setAttribute` 安全渲染，不做原始 HTML 插值；实现为零 npm 运行时依赖（仅 Bun 内置与浏览器标准 API）。

服务不提供任何写入、编辑或删除端点；非 GET 请求一律返回 405。

实验性运行完整记忆候选流程（只生成候选，不写 Basic Memory）：

```bash
.teamflow/experiments/bin/memory-experiment \
  --source memory://teamflow/projects/example/finding-a \
  --source memory://teamflow/projects/example/finding-b
```

每次运行产物位于 `.teamflow/runs/memory/<run-id>/`：证据胶囊、Emotion 输入与信号、DeepSeek 压缩结果、GLM 抽取与格式化结果、阶段日志与确定性校验报告均独立保存。四个阶段固定串行运行，以避免本地状态锁。Emotion 的高强度或高显著性只要求压缩阶段保留目标或说明排除理由，不会自动升级为知识。若下游阶段的可解析 JSON 违反结构或谱系约束，runner 最多把精确错误回执交给同一阶段修复一次；第二次仍失败则整轮停止。

任务通过测试与最终审查后，Planner 写入 `.teamflow/runs/task-receipts/<run-id>/receipt.json` 并运行：

```bash
teamflow memory-capture --receipt .teamflow/runs/task-receipts/<run-id>/receipt.json
```

安全 apply 只自动写入新的原子候选；`update`、`supersede` 和冲突保留在 `50-apply.json`，不会覆盖旧记忆或打断用户询问。

需要比较某个阶段的模型效果时使用临时模型覆盖，不新增固定 Agent，也不执行 apply：

```bash
.teamflow/experiments/bin/memory-compare \
  --run-id <existing-run-id> \
  --stage formatting \
  --model zhipuai-coding-plan/glm-5.2 \
  --label glm52
```

比较产物与基线并存，报告候选数量、动作分布、类型、谱系校验和 atomic source retain 情况。`compression`、`extraction`、`formatting` 均可按需比较。

测试由 GLM 生成统一补丁到 `.teamflow/runs/test-patches/`。`teamflow test-patch check` 确认改动仅位于普通测试文件或 Rust `#[cfg(test)] mod ...` 后，K3 才可用 `teamflow test-patch apply` 机械应用。

`test-writer` 采用产物优先的检查点：完成一次聚焦代码检查和一次代表性测试惯例检查后，先写 `tests.patch`，再继续校验和精炼。Planner 会在委派返回后独立检查该补丁；`finish=length` 或缺少强制产物会将当前 phase 明确结束为 `BLOCKED`，不会把空返回当成功或在同一 phase 内静默重试。

`teamflow server` 的实现是 Bun + TypeScript，源码位于仓库根目录 `server/`（"实现集中在 `.teamflow/`" 原则的唯一文档化例外）；`scripts/init-project.sh` 会把它安装到目标项目的 `.teamflow/server/` 下。服务使用 `Bun.serve`，运行时只依赖 Bun 内置与标准 API（零 npm 运行时依赖），并通过 `--local` 调用本机已有的 `basic-memory` CLI 读取记忆；不使用 MCP、云同步、账号或密钥。开发期类型检查：`cd server && bun install && bun run typecheck`。

可配置项：

- `TEAMFLOW_HOME`：默认 `$HOME/.teamflow`
- `TEAMFLOW_BIN_DIR`：默认 `$HOME/.local/bin`
- `TEAMFLOW_MEMORY_HOME`：默认 `$TEAMFLOW_HOME/memory`
- `TEAMFLOW_MEMORY_PROJECT`：默认 `teamflow`
- `TEAMFLOW_MODEL_STAGE_TIMEOUT_SECONDS`：默认不设置，即不启用本地 wall-time。

记忆策略：先搜索、后验证，只在全部质量门 PASS 后写入；禁止保存密钥、隐私数据、原始对话、完整日志或未验证猜测。

## 维护与诊断

查看工作流实际发现的 Agent 和 Skill：

```bash
teamflow debug agent planner
teamflow debug agent coder
teamflow debug agent test-writer
teamflow debug agent test-runner
teamflow debug skill
```

更新 Basic Memory 官方 Skills 的 CLI-only 适配：

```bash
./scripts/update-basic-memory-skills.sh \
  --ref main \
  --instruction "保留新增的本地知识图谱能力，继续禁止云端与 MCP"
```

模板仓库结构：

```text
.
├── AGENTS.md
├── README.md
├── .env.example
├── .teamflow/             # 可安装运行模板
│   ├── models.json
│   ├── AGENTS.md
│   ├── agents/
│   ├── skills/
│   └── bin/
├── server/                # Bun + TypeScript 只读记忆浏览服务源码（仓库级例外）
└── scripts/
    ├── bootstrap.sh
    ├── doctor.sh
    ├── init-project.sh
    ├── setup-memory.sh
    ├── update-basic-memory-skills.sh
    └── teamflow           # 全局入口模板
```

## 迭代原则

1. 对业务仓库保持最小 Git 侵入：只声明 `.teamflow/` 忽略规则，所有实现集中在该目录。
2. 先修改工作流规则、Agent 或 Skill，再用真实任务验证。
3. 测试必须在实现前证明缺失行为，最终必须重新运行。
4. Agent 不得自行推送、强制重置或清理用户工作区。
5. 调整模型、权限、交接协议或安装链路时，同步 README 和 `AGENTS.md`。
6. 跨项目记忆保留来源且使用前重新验证，不能替代当前仓库事实。
