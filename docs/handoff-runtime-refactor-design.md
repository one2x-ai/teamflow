# Teamflow 内外 Loop 通信重构设计：从 phase 到 handoff

状态：设计定稿，未实施

适用范围：`.teamflow/` 运行时（CLI、extensions、skills、agents 契约）、外层协调循环的观察面、仓库契约测试与文档

## 0. 核心铁律

本设计只有一条不可协商的原则，所有实施决策都必须回到这条原则上校验：

> **状态变更与状态查询是程序式的；需求表达与任务编排走模型与提示词。**

两个平面的硬边界：

| 平面 | 载体 | 允许做 | 禁止做 |
|---|---|---|---|
| 机械平面（程序） | CLI、extensions、watchdog 进程 | 状态写入与迁移、事件投递、收据 schema 校验、活性探测、序号分配、原子落盘、冲突检测、超时判定 | 决定"委托什么、给谁、验收标准是什么"；解释失败原因的业务含义；改写模型产出的语义内容 |
| 语义平面（模型 + 提示词） | handoff 正文、agent 系统提示词、收据的叙述字段 | 需求分析、验收标准、任务拆分与编排、诊断假设、证据压缩与摘要 | 手工拼接状态文件名、直接写 `state.json`/事件文件、自报"我还活着"、绕过 CLI 维护任何状态 |

推论：

1. 任何状态如果需要模型"记得去写"才能保持正确，就是设计错误；状态写入必须挂在机械执行点上（CLI 子命令、extension 钩子、watchdog），模型忘了也不会漏。
2. 任何查询如果需要模型读散文再"理解"才能得到状态，就是设计错误；状态必须能被 `ls`、文件名解析或一次小 JSON 读取直接回答。
3. 反方向同样成立：程序不得替模型做编排决策。CLI 可以拒绝一次非法的状态迁移，但不能决定"下一步该委托谁"。

## 1. 背景与问题诊断

### 1.1 现状

内外 loop 通信目前由四条弱耦合的通道构成：

- **phase 收据**：`teamflow phase start/finish`（`.teamflow/skills/plan-change/scripts/phase_state.py`）写 `.teamflow/runs/code/<run-id>/phases/<name>.json`，用 `current.json` 单指针指向"当前 phase"。
- **观察梯子**：外层按 ≥30 秒间隔轮询 `teamflow probe` → `teamflow phase status --run-id` → `teamflow session list --format json`，并对 `.teamflow/runs/` 下期望工件做存在性检查。
- **handoff**：`write-handoff` skill 约定的 Markdown 结构，仅作为 `task`/`task_group` 的 prompt 字符串存在，不落盘。
- **失败收据**：test-runner 等角色在最终 assistant 文本里按 prompt 约定返回半结构化 JSON，由 planner 容错解析。

### 1.2 问题清单

1. **轮询浪费 token**。外层每次轮询都是一对"工具调用 + 结果"进入上下文，状态未变时这些调用纯属开销，且持续破坏 KV-cache。
2. **收据弱类型**。失败收据活在 assistant 文本里，无 schema、无校验器，可能退化为散文；planner 与外层都要花 token 容错。
3. **BLOCKED 原因分裂**。CLI `--block-reason` 只接受 `CONTEXT_BUDGET_EXCEEDED`/`RECALL_BUDGET_EXCEEDED` 两个预算类枚举；策略层要求的 `DELEGATION_ARTIFACT_MISSING`/`OUTPUT_TRUNCATED` 只能塞进 `summary` 自由文本；`observe-inner-loop` skill 描述的顶层 `block_reason` 字段实际不存在。
4. **handoff 不落盘**。外层"写 handoff、失败后带新 handoff 再委托"的职责缺少可核验载体；委托内容无法在不读 session 的前提下核对。
5. **`current.json` 是单游标**。它假设一个 run 任何时刻只有一个当前 phase，与并行任务（并发 `task_group`、多 planner 会话）根本冲突：并发 `start` 互相踩指针，同名 phase 收据互相覆盖。
6. **run-id 无生成器**。外层靠目录 mtime 猜当前 run；probe 全局扫描 `comm==pi`，多进程并发时活跃度无法归属。
7. **"phase" 语义错位**。它隐含一条由 planner 独占维护的全局流水线，而系统的真实结构是"工作单元在 agent 之间移交"；状态应由执行方自己维护，并对所有协作方可见。

### 1.3 业界参照

- **A2A 协议**：Task 是一等公民，带标准生命周期状态机（submitted → working → completed/failed/canceled），状态由执行方维护，产出以 Artifact 引用传递，支持推送免轮询。
- **Maildir tmp/new 投递模式**：写临时文件后 rename 进目标目录，rename 在同一文件系统上原子，监听者永远不会读到半个文件。
- **Hadoop/Spark `_SUCCESS` 哨兵**：用文件存在性而非内容表达完成态。
- **黑板架构 / 环境介导协作**：agent 不互发消息，而是把状态写到共享介质，其他 agent 按需读取。
- **事件溯源（Temporal 等）**：append-only 事件 + 单调序号 + 观察者带 offset 取增量；心跳与业务事件分离。
- **多 agent token 经济（Anthropic multi-agent research、Manus context engineering）**：传引用不传内容；观察面输出字节级稳定以保 KV-cache；文件系统即终极上下文。

## 2. 目标架构

### 2.1 一切皆 handoff

废除 `phase` 概念。协调的基本单元是 **handoff**：一方把业务的一部分连同目标、边界、验收标准移交给另一方，接收方自己维护该 handoff 的状态直至终态。

- 外层 → planner 的初始委托是**根 handoff**（`AGENTS.md` 中"outer loop may write the handoff"从此有落盘载体）。
- planner → 子角色的每次 `task`/`task_group` 是**子 handoff**，`lineage` 构成一棵树，替代扁平 phase 序列。
- planner 自身的规划工作不伪装成 handoff，以根 handoff 上的进度收据表达。
- 深度约束不变：只有 depth-0 角色（`delegates: true`）能开启子 handoff；depth-1 角色只维护自己那份 handoff 的状态。

### 2.2 目录布局

```text
.teamflow/runs/code/
├── _spool/                                  # run 级发现点（跨 run 并行）
│   ├── 00001--<run-id>--run_started
│   └── 00002--<run-id>--run_finished--PASS
└── <run-id>/
    ├── handoffs/
    │   └── <handoff-id>/
    │       ├── handoff.md                   # 委托方写：语义平面（Goal/Scope/Acceptance…）
    │       ├── state.json                   # 接收方经 CLI 维护：机械平面
    │       ├── receipt.json                 # 接收方经 CLI 写入的结构化结果收据
    │       └── title.txt                    # 可选：注册表标题（默认取 Goal 行，超长时异步压缩）
    ├── active/                              # 哨兵文件：每个进行中的 handoff 一个
    │   └── <handoff-id>
    ├── events/                              # 外层唯一监听面
    │   ├── 00001--<handoff-id>--handoff_opened--OPEN.json
    │   ├── 00002--<handoff-id>--handoff_finished--FAIL.json
    │   └── 00003--<run-id>--runner_exited--EXITED.json
    ├── tmp/                                 # 事件写入暂存，rename 进 events/ 完成投递
    ├── liveness/                            # 心跳与退出记录（纯程序维护，外层 LLM 不读）
    │   └── <pid>--<role>--<depth>.json
    └── runner.json                          # depth-0 进程 pid 与启动信息
```

`evidence/<run-id>/`、`test-patches/<run-id>/`、`task-receipts/<run-id>/`、`memory/` 等既有工件路径不变。

### 2.3 handoff 生命周期与统一失败枚举

`state.json` 的状态机（仅可经 `teamflow handoff` CLI 迁移）：

```text
open → running → done(PASS | FAIL)
              └→ blocked(reason)
```

`blocked.reason` 统一为单一顶层枚举，消除 CLI 与策略层的分裂：

```text
CONTEXT_BUDGET_EXCEEDED | RECALL_BUDGET_EXCEEDED |
DELEGATION_ARTIFACT_MISSING | OUTPUT_TRUNCATED |
PROVIDER_FAILURE | USER_CANCELLED
```

预算类原因保留现有 `budget_failure` 嵌套结构作为详情。外层判定规则维持既有不变量：**静默与墙钟时间不是失败；停机信号只有两个——`blocked` 状态，以及"未收尾即退出"（`runner_exited` 出现且其前最后一个业务事件不是终态）**。

### 2.4 事件文件协议

一事件一文件，文件名即元数据，外层监听器无需读内容即可获知"第几号事件、哪个主体、什么类型、什么结果"：

```text
<五位零填充序号>--<主体id>--<kind>--<status>.json
```

- **kind 枚举**：`run_started`、`run_finished`、`handoff_opened`、`handoff_finished`、`artifact_written`、`runner_exited`。
- **原子投递**：先写 `tmp/`，再 rename 进 `events/`；监听者只响应 rename 完成事件（inotify `IN_MOVED_TO`），杜绝半文件读取。
- **序号**：run 内单调，由 CLI 用 `flock` 计数器文件分配；零填充保证字典序即时间序。`_spool/` 用同样机制维护全局序号。
- **正文**：限制在几百字节，只放 `ref`（指向 `handoffs/<id>/receipt.json` 等详情文件）与最小上下文；大内容一律落 `evidence/` 传引用。
- **文件名清洗**：主体 id 只允许 `[a-z0-9-]`，整名 ≤ 255 字节。
- 事件由机械层投递：`teamflow handoff` CLI、`teamflow-task` extension、watchdog。模型永远不直接创建事件文件。

### 2.5 收据落盘与机械校验

收据从"子 agent 最终 assistant 文本"迁移为落盘工件：

1. `teamflow-task` extension 在委托时物化 `handoffs/<id>/` 目录（写入 `handoff.md`），并把 `TEAMFLOW_HANDOFF_ID` 注入子进程环境。
2. 子 agent 通过 `teamflow handoff finish --id <id> --status FAIL --receipt <file>` 写自己的收据；CLI 按 JSON Schema 校验（test-runner 收据字段：`status`、`command`、`exit_code`、`failed_checks`、`error_excerpt`（硬上限 2000 字符，超出强制落 `evidence/` 给 `ref`）、`reproduction`、`diagnosis`、`next_owner`、`expected_red`）。
3. extension 在子进程退出后机械校验 `receipt.json` 存在且通过 schema；缺失或输出截断（`stopReason=length`）时由 extension 代写 `blocked` 状态（`DELEGATION_ARTIFACT_MISSING` / `OUTPUT_TRUNCATED`），不做静默重试。
4. `task` 工具的返回值退化为指针（handoff-id + 状态 + 收据路径），planner 上下文不再承载收据正文；planner 需要细节时按需读收据文件的具体字段。

### 2.6 agent 注册表与协同看板：派生视图、拉取式、紧凑

"所有 agent 都能知道别人在干嘛"通过一张 **agent 注册表**实现。三条设计约束按铁律卡死：

1. **注册表是派生视图，不是第二份可变状态。** 不存在需要各 agent 维护的 `registry.json`；`teamflow agents list` 由 CLI 现场拼装三个既有事实源——`liveness/`（pid、role、depth、心跳年龄，watchdog 维护）× `active/`（进行中的 handoff）× `handoffs/<id>/`（title、scope）。任何时刻这张表都能从磁盘事实推导出来，没有任何东西需要"记得去更新"，也就没有失同步与并发写锁问题。
2. **title 是注册表里唯一的语义字段，压缩模型是兜底而非必经路径。** write-handoff 契约本就强制 "Goal: one observable outcome" 单行——委托方写 handoff 时已免费产出标题，注册表默认直接取该行（预算 ≤ 80 字符）。仅当 Goal 缺失或超长时，才触发一次 MiMo 2.5 Pro 压缩（与 `command`/`supervisor` 同款廉价模型）。该压缩调用必须**异步、分离、可失败**：由机械层拉起分离子进程，结果写回 `handoffs/<id>/title.txt`；未完成或失败时注册表降级显示截断的 Goal 原文。状态写入永远不等待模型调用返回——机械平面不得被语义平面阻塞。
3. **拉取查询，永不自动注入。** agent 需要全局视野时调用 `teamflow agents list`，返回字节级稳定的紧凑 JSON（每行：role、depth、心跳年龄、handoff-id、title、scope 文件列表、状态）。成本是 O(活跃 agent 数) 的小行，10 个并行 agent 的全表约几百 token。禁止把注册表按轮自动注入 agent 上下文——那会使上下文开销随进程数线性增长，恰好复现"进程多了上下文爆炸"的问题。title 就是上下文的压缩边界：agent 之间共享的是一行标题与 scope，而不是彼此的上下文。

配套机制：

- `teamflow handoff list --active` 保留为 handoff 维度的同类查询（按工作单元而非按 agent 实例聚合）。
- `handoff.md` 的 Scope 段结构化（frontmatter 或专用字段）后，CLI 在开启新 handoff 时做**并发冲突检测**：两个活跃 handoff 的 scope 文件集相交即警告——这是并行 `task_group` 的关键护栏。
- 委托方在 handoff 正文里附上与本任务相关的看板摘录（语义平面的编排职责）；接收方默认只看到自己的 handoff，需要时再主动查表。

### 2.7 并行支持

- **run 内并行**：`current.json` 废弃。"当前在干什么" = `ls active/`（活跃集合，每个 handoff 只碰自己的哨兵文件，天然无锁）；"发生过什么" = 折叠 `events/` 序列。`teamflow handoff status --run-id` 不带 `--id` 时返回全部活跃 handoff 的数组。
- **实例唯一性**：handoff-id 由 CLI 生成（含序号成分），并发同名任务不会互相覆盖；`lineage.split_scope` 留在收据内容里。
- **跨 run 并行**：run 级生命周期事件投递到 `_spool/`；`teamflow wait` 不带 `--run-id` 时监听 `_spool/` 做发现，发现后对具体 run 的 `events/` 挂子监听。注意 `_spool/` 与 agent 注册表（2.6）不冗余：注册表是拉取式状态快照，服务内层 agent 的协同视野；`_spool/` 是可被 inotify 阻塞等待、带序号可续读的事件通知，服务外层的免轮询发现（非外层启动的 run、外层重启后按 `--since` 水位补读错过的 run 结束事件）。事件可折叠出状态，状态推导不回事件，两者是"active/ 哨兵 vs events/ 事件流"在 run 层级的同构复制。外层自己启动的 run 无需发现——run-id 直接来自 `teamflow run` 的机器可读输出行。
- **run-id 生成**：`teamflow run` 启动时分配 run-id，注入 `TEAMFLOW_RUN_ID` 环境变量（子进程经 `...process.env` 自动继承），并以机器可读固定格式输出一行。外层不再靠 mtime 猜。

### 2.8 活性：独立纯程序流程

活性与业务事件是两条正交通道；外层 LLM 在活性上花费的 token 为零。

- **点火**：新 extension `agent-watchdog` 在每个 pi 进程首个 `before_agent_start`（或 `session_start`）时 spawn 一个分离的微型 watchdog 进程（`detached: true, stdio: "ignore"`, `unref()`），传入被监护 pid、role、depth、run-id。一次性标志保证每进程只点火一次。覆盖率由 extension 加载机制保证，不依赖 prompt。
- **监视**：watchdog 是纯程序，每 60 秒刷新 `liveness/<pid>--<role>--<depth>.json` 心跳；进程退出检测优先用 `pidfd_open` + poll（即时），降级为数秒一次查 `/proc/<pid>`。watchdog 同时监视自身父进程，孤儿化时一并处理。
- **回执**：被监视进程消失时，由 watchdog（仍然活着的独立进程）写退出记录——插件活在 pi 进程内部，进程被 SIGKILL/OOM 杀死时插件一起死，**写"我死了"回执的必须是监视者而非被监视者**。
- **信噪分流**：depth-1 子角色的退出本就被父进程 `teamflow-task` 观察（`delegation_result`），其退出只记入 `liveness/`；**仅 depth-0 进程退出**向 `events/` 投递 `runner_exited`，因为只有它的死亡意味着内层瘫痪且无人能代为报告。

### 2.9 外层监听：`teamflow wait`

外层的全部感知收敛为一条阻塞命令：

```text
teamflow wait [--run-id <id>] [--since <seq>] [--kind <k>,...] [--timeout 600]
```

- Linux 用 inotify 监听 `events/`（或 `_spool/`）的 `IN_MOVED_TO`；macOS/NFS 自动降级为工具内部 stat 轮询——降级对调用方透明，外层永远只是"一次调用，挂起到有事或超时"。
- 返回新事件文件名解析出的 JSON 数组；超时无事件返回空数组 + 当前 `seq` 水位。`--since` 保证重连后只取增量，天然幂等。
- 输出字段顺序与格式字节级稳定，保护外层 KV-cache。
- 现有 30 秒轮询梯子（probe → status → session list）整体退役；`teamflow probe` 保留为人工诊断工具，不再进入外层契约。

### 2.10 外层观察契约（更新后）

- 外层唯一动作：`teamflow wait` + 事件指向的收据/工件存在性检查（必要时读事件正文与 `receipt.json` 的枚举字段）。
- 依旧禁止读 session 文件、prompt、reasoning、模型响应、原始 provider 错误、配置与凭证。`events/`、`state.json`、`receipt.json` 是设计给外层读的元数据面，与 session 隔离。
- "metadata only"的经济学不变：事件文件名承载判断所需的绝大部分信息，读正文是例外而非常态。

## 3. 迁移计划

按依赖序拆为七个独立可验收的委托，每项都有可证伪的验收标准，随时可停：

| # | 委托 | 内容 | 依赖 |
|---|---|---|---|
| ① | 统一失败枚举 | `blocked.reason` 顶层枚举六值落地，`budget_failure` 保留为详情；修正 observe skill 的字段描述 | 无 |
| ② | handoff 注册表 CLI | `teamflow handoff open/finish/status/list` 与 `teamflow agents list` 派生视图；目录布局、`active/` 哨兵、`_spool/`、事件投递（tmp/rename + flock 序号）；吸收 `phase_state.py` 迁移；`teamflow phase` 保留为过渡别名一个版本 | ① |
| ③ | `teamflow-task` 接入 | 物化 handoff、注入 `TEAMFLOW_HANDOFF_ID`、收据 schema 校验、缺失/截断代写 blocked、返回值指针化 | ② |
| ④ | run-id 注入 | `teamflow run` 生成 run-id、注入 `TEAMFLOW_RUN_ID`、机器可读输出行 | 无 |
| ⑤ | `agent-watchdog` | extension 点火 + 分离 watchdog 程序（心跳、退出检测、`runner_exited`、信噪分流） | ②④ |
| ⑥ | `teamflow wait` | inotify + 轮询降级、`--since`、超时语义、字节级稳定输出 | ② |
| ⑦ | 契约面全量迁移 | `planner.md`、`test-runner.md`、`supervisor.md`、`observe-inner-loop`、`write-handoff`（与 CLI 合流：skill 写正文，CLI 注册状态）、`.teamflow/AGENTS.md`、根 `AGENTS.md`、README、`tests/` 与 `tests/runtime/` 契约测试、`scripts/clean.py` 清理策略 | ②③⑤⑥ |
| ⑧（可选） | title 压缩钩子 | Goal 缺失/超长时异步拉起 MiMo 压缩、写回 `title.txt`；失败降级为截断 Goal；不阻塞任何状态写入 | ②③ |

实施约束：

- 每项委托走仓库标准流程（write-handoff → test-first → teamflow 内层实施 → 门禁），并按维护规则跑 `./scripts/doctor.sh`、核对 `teamflow debug` 输出、对一次性 Git 项目 dry-run `./scripts/install.sh`。
- ②③ 完成前旧 `phase` 契约保持可用，避免半迁移状态下内层 agent 的既有提示词调用失败。
- `scripts/clean.py` 对已完结 run 的 `events/`、`tmp/`、`liveness/` 视为可再生产物；`handoffs/` 收据与既有收据同级保留。

## 4. 非目标

- 不引入消息总线、HTTP/SSE 服务或任何常驻 daemon（watchdog 随被监护进程生灭，不是服务）。
- 不改变 test-first 顺序、角色模型分工、depth 约束与记忆管线。
- 不替换 Pi 的 session backend；不触碰 memory-context extension 的上下文接管机制。
- 不做跨机器分布式；一切以单机文件系统语义（原子 rename、flock、inotify）为边界。
