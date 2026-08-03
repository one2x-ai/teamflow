# Teamflow 记忆遗忘机制设计

状态：设计稿（未实现；实现须按 AGENTS.md 走 teamflow 多 Agent 流程）

适用范围：`~/.teamflow/memory/` 下的策展知识层（Basic Memory notes）、文件级冷存储（TurnBlock / TurnIndex）、规划经验，以及未来的云端记忆后端。

## 1. 背景与问题

Teamflow 已发布到云上运行，但跨项目记忆仍存储在本地 `~/.teamflow/memory/`。在讨论记忆上云之前，必须先解决一个更基础的缺陷：**当前记忆系统只有写入和逐出，没有任何主动遗忘路径**。

对当前实现的核查结论（证据见 §2）：

1. 自动捕获管道对 `update` / `supersede` 候选一律 `deferred`——只记录进 `50-apply.json`，没有任何后续流程消费这个队列。旧记忆永远保持"现行"状态。
2. 策展层唯一的防增长手段是 formatter 的 `skip` 判定，而 formatter 只能看到本次任务显式召回的笔记（evidence capsule 中的 `--source`）。凡未被召回的旧笔记对去重完全不可见，形成盲区复制。
3. 去重 digest 基于 casefold 后的语义原文精确哈希；任何换一种说法的等价知识都会生成新笔记。
4. `recall` 默认路径分页扫描全部搜索结果再本地过滤，成本随记忆总量线性增长；记忆越多，召回越慢、越噪，反过来又降低 formatter 的去重质量——恶性循环。
5. `memory context` 的 `build-context --timeframe 30d` 已经在悄悄"遗忘"超过 30 天的图关系——这是一处未经设计的隐式遗忘，与其他路径"永不遗忘"不一致。

结论：不遗忘的记忆库最终既不可信（陈旧结论持续参与规划）也不可用（召回被稀释）。遗忘机制是记忆上云的前置条件——同步一个只增不减的库只会把问题复制到云端。

## 2. 现有记忆系统评估

以下发现基于对当前仓库写入/读取路径的逐行核查，按严重程度排序。修复项 F1–F3 是遗忘机制的前置依赖（§13 Phase 0）。

### F1 — 原子类型串不一致，原子保护实际失效

`run_pipeline.py` 的 apply 阶段写入笔记时使用 `--type "teamflow-memory"`（连字符），而原子来源检测正则、formatter prompt 与 `contracts.md` 全部约定 `type: teamflow_memory`（下划线）：

```311:313:.teamflow/skills/extract-memory/scripts/run_pipeline.py
                "basic-memory", "tool", "write-note", "--title", title, "--folder", folder,
                "--content", content, "--tags", f"teamflow,curated,{item.get('type', 'fact')}",
                "--type", "teamflow-memory", "--project", memory_project,
```

```241:241:.teamflow/skills/extract-memory/scripts/run_pipeline.py
                if re.search(r"(?m)^type:\s*(?:teamflow|workflow)_memory\s*$", head):
```

后果：捕获管道自己写出的笔记在后续 run 中不被识别为原子来源，"拒绝替换原子来源"的确定性护栏对它们不生效，formatter 也可能把它们误判为可 supersede 的旧监护对象。遗忘机制的核心前提是"能可靠区分原子笔记与遗留巨石笔记"，必须先统一类型串（含存量数据迁移）。

### F2 — 持久化笔记的证据与谱系是运行期悬空引用

apply 写入的笔记正文中，`[evidence]` 是 capsule 内部 ID（`NOTE-1` / `RECEIPT-1`），`[lineage]` 是 extraction 内部 ID（`fact-001`），且不含 run_id 与写入时间：

```301:307:.teamflow/skills/extract-memory/scripts/run_pipeline.py
        content = (
            f"# {title}\n\n{semantic}\n\n## Observations\n\n"
            f"- [type] {item.get('type', 'fact')}\n"
            f"- [status] {item.get('status', 'verified')}\n"
            f"- [scope] {scope}\n"
            f"- [evidence] {', '.join(item.get('evidence_refs', []))}\n"
            f"- [lineage] {', '.join(item.get('derived_from', []))}\n"
```

run 结束后这些 ID 无从解析。这违背了"Include the reason and evidence in the memory text"的仓库规则精神，也使一切基于证据新旧程度的遗忘决策失去依据。手工 `remember` 路径反而记录了 `[recorded]` 时间戳，自动路径没有。

### F3 — deferred 队列有去无回

`apply_candidates` 对 `update` / `supersede` 的处理是记录后放弃；`50-apply.json` 虽被 `clean.py` 当作证据保留，但没有任何命令、Agent 或 Skill 消费它。被更强证据取代或已被证伪的记忆永远以 `active` 姿态参与召回。

### F4 — 缺少任何生命周期元数据

策展笔记的 frontmatter 只有 title/type/permalink/tags；正文只有 type/status/scope/evidence/lineage 五行观察。没有 `recorded_at`、`last_verified_at`、召回命中记录、强化/矛盾计数。文件 mtime 是唯一的时间信号，而 mtime 在同步、备份恢复、git 检出后全部失真。**没有元数据就没有可计算的遗忘。**

### F5 — 去重范围受限于召回质量

formatter 只能对 evidence capsule 中的笔记做 `skip` / `update` 判定，而 capsule 只包含本次任务 `related_memory` 传入的召回结果。近义改写 + 召回盲区共同作用下，同一知识的变体会持续累积，每个变体的标题是"前 52 字符 + 12 位 digest"，人工浏览也难以合并。

### F6 — 召回与图遍历的退化

`recall` 默认路径最多翻 1000 页（每页 8 条）拉取全部命中再过滤；`list` 用 `recent-activity --timeframe 365d`；`context` 用 `--timeframe 30d`。三条读取路径对"库会变大"的假设互相矛盾，且都没有基于记忆价值的排序——只有 upstream 文本相关度。

### F7 — 冷存储无保留策略

`FileColdStore` 将每轮 TurnBlock 永久写入 `state/cold-store/`，设计文档明确"遗忘是逐出，不是删除"（对热区正确），但对冷存储本身没有任何年龄、任务完成度或容量维度的保留策略。TurnIndex 可重建，TurnBlock 不可变，长期运行必然膨胀。

### F8 — 次要问题

- 手工 `remember` 用 `shasum` 生成标题哈希，精简 Linux 镜像上可能缺失（应使用 `sha256sum` 回退）。
- 手工 finding 标题为 `Verified finding <hash>`，标题本身零检索价值。
- `MAX_CREATES_PER_RUN=8`、目标 3–5 条/任务：高频使用一个月即数百条笔记，而系统没有与之配平的任何回收路径。

### 对已有记忆数据的审计与整备建议

记忆数据在运行 teamflow 的机器上（本仓库工作区没有 `~/.teamflow/memory/`），审计必须在数据所在处执行。建议按以下顺序做一次性整备（planner 主导，遵循 memory-curate 的安全规则）：

1. **规模与构成**：统计 `knowledge/` 下笔记总数、按 folder（`projects/<slug>/curated`、`global/`、遗留目录）与 `type:` 值（`teamflow-memory` / `teamflow_memory` / `workflow_memory` / `workflow_finding` / `teamflow-finding`）的分布。类型分布直接量化 F1 的存量影响面。
2. **悬空证据面**：统计正文含 `- [evidence] NOTE-` / `RECEIPT-` / `EVIDENCE-` 的笔记比例，即 F2 影响的存量。这些笔记在整备时应从 git 历史 / 文件时间回填 `recorded_at`，无法回填的降为 `hypothesis`。
3. **重复簇**：把标题去掉末尾 `[digest]` 后按前缀聚类，再对正文 casefold 相似度复核；每簇保留证据最强的一条，其余进入 §6 的 `deprecated` 状态（首批遗忘对象）。
4. **陈旧结论**：对照当前仓库逐条验证 `status: verified` 声明（memory-recall 本来就要求使用前验证）；被现实推翻的立即 `deprecated` 并注明 superseded 证据。
5. **遗留巨石**：`workflow_finding` 巨石笔记按现有 formatter 规则提炼为原子笔记后整体 `deprecated`。
6. **可检索性抽样**：用近期真实任务关键词跑 `teamflow memory recall`，检查 top-8 是否被低价值笔记稀释——这是遗忘机制上线后的基线对照指标。

## 3. 目标与非目标

### 3.1 目标

- 让每条策展记忆有显式生命周期状态与可计算的强度，默认召回只返回高置信、现行、经使用验证的记忆。
- 把"更强证据取代旧结论"从被搁置的提案变成受控执行的一等操作。
- 提供周期性提炼（consolidation）：把同主题的零散原子笔记合并为更少、更强、带完整谱系的笔记——记忆总量随使用**收敛**而非发散。
- 为冷存储 TurnBlock 建立基于任务完成度与年龄的分级保留。
- 所有遗忘动作可审计、可逆（在窗口期内）、可同步（tombstone），为记忆上云铺路。

### 3.2 非目标

- 不改变"只有验证 PASS 才写记忆"的写入门槛。
- 不引入云服务、MCP、账号或 API Key；机制本身保持完全本地可运行。
- 不让模型直接删除任何文件：模型只产生候选，确定性 runner 执行（与 capture 管道同构）。
- 不做基于嵌入的自动语义合并作为首期依赖（可作为后续增强）。
- 不改动准则 cache（rule cache）——它已有 supersede/retire 语义，是本设计权限阶梯的参照物，不是改造对象。

## 4. 核心原则

1. **遗忘是状态迁移，不是物理删除。** 延续热区"逐出而非删除"的哲学：记忆沿 `active → deprecated → archived → purged(tombstone)` 单向迁移，每级之间有最短驻留期，窗口内可恢复。
2. **遗忘的首要单位是检索可见性。** 系统优化的是"默认召回返回什么"，而不是"磁盘上有什么"。降低可见性先于回收空间。
3. **提炼优先于丢弃。** 对高价值主题，遗忘表现为 N 条零散笔记 → 1 条提炼笔记 + N 条 deprecated 源；只对确证陈旧、重复或长期无用的记忆做纯粹降级。
4. **证据驱动，决策留痕。** 每次状态迁移必须携带 reason 与 evidence（新笔记 permalink、矛盾的验证收据、使用统计），与写入侧"包含理由与证据"的规则对称。
5. **决策与执行分离。** 模型（formatter / 新的 consolidation 阶段）只写提案 JSON 到 `.teamflow/runs/memory/`；确定性 runner 校验谱系与安全约束后执行，与 capture 管道完全同构。
6. **权限阶梯。** 复用准则 cache 的权限序：用户手工写入（`remember` / `remember-global`）的记忆只能被自动流程**提名**降级，执行需要用户确认；自动捕获的记忆可以被自动降级。用户的显式恢复指令高于一切自动决策。
7. **存储无关、云可同步。** 全部生命周期状态表达为 Markdown frontmatter + 独立 tombstone 文件，任何未来的同步层（对象存储、云端 Basic Memory）都能复制状态而不需要理解决策过程；tombstone 保证"删除"这一事实本身可同步。

## 5. 生命周期状态机

```text
            强化(召回命中+验证引用)
              ┌─────────┐
              ▼         │
  create ─> active ─────┘
              │ supersede 执行 / 矛盾证据 / 衰减低于阈值
              ▼
          deprecated ──(驻留 ≥ D_dep 且无恢复)──> archived ──(驻留 ≥ D_arc 且无恢复)──> purged
              │                                     │                                   │
              └––––––– restore（任何人）–––––––––––––┘                          tombstone 永久保留
```

| 状态 | 默认召回 | `read`/审计 | 物理位置 | 进入条件 |
| --- | --- | --- | --- | --- |
| `active` | 参与 | 可见 | `knowledge/` | 创建；或从 deprecated 恢复 |
| `deprecated` | 排除 | 可见（带状态标注） | `knowledge/`（frontmatter 标记） | supersede 执行、矛盾证据、衰减阈值、整备判定 |
| `archived` | 排除 | 显式请求可见 | `state/archive/`（移出 knowledge 树，索引不含） | deprecated 驻留期满且期间零命中 |
| `purged` | 排除 | 仅 tombstone | `state/tombstones/<id>.json` | archived 驻留期满；或用户显式授权 |

规则：

- 状态只能逐级向下迁移；`restore` 可从 deprecated/archived 一步回 active，并记录恢复原因（恢复本身是强烈的价值信号，重置强度）。
- `purged` 永不自动发生于用户手工写入的笔记；自动捕获笔记的 purge 也默认关闭（`TEAMFLOW_MEMORY_PURGE_ENABLED=false`），打开后仍受驻留期与每次清扫上限约束。
- tombstone 记录：permalink、title、content sha256、最终状态、迁移链（每步 reason/evidence/时间）、操作者（runner run_id 或用户）。

## 6. 记忆强度模型

每条笔记维护一个确定性可重算的强度分 `strength ∈ [0, 1]`，只由以下信号构成（全部可审计，不含模型主观打分）：

```text
strength = clamp(
    base(status, authority)              # verified=0.6, hypothesis=0.35; 用户手工 +0.2
  + Σ reinforcement                      # 每次"召回命中且出现在后续 PASS receipt 的 related_memory" +0.15（封顶 +0.3）
  - Σ contradiction                      # 每次出现在 receipt conflicts / 验证失败关联 −0.4
  - pending_supersede                    # 存在未执行的 supersede 提案 −0.2
  - decay(days_since_last_signal)        # 无任何信号后按半衰期 H 指数衰减，最多 −0.4
)
```

- **强化信号**来自现有闭环：capture receipt 的 `related_memory` 字段已经记录了本任务召回并验证过的 permalink——这就是"这条记忆帮任务少走了弯路"的直接证据，只需在 apply 阶段回写。
- **矛盾信号**来自 compression 阶段本已要求输出的 `conflicts`（"newer receipt invalidates old statement must appear in conflicts"），同样只需回写。
- **衰减**是慢信号，半衰期 `H` 默认 180 天；衰减只能把 active 压到 deprecated 候选，从不直接 archived。
- 阈值：`strength < S_dep`（默认 0.25）进入 deprecated 候选清单，由清扫任务执行（§8.4）。

使用记录不直接改写笔记文件（避免每次 recall 制造 git noise 与 mtime 污染），而是追加到 `state/usage-ledger/`（按月分片 JSONL），由清扫任务定期结算进 frontmatter。

## 7. 元数据 Schema 扩展

### 7.1 笔记 frontmatter（新增字段，全部可缺省，缺省视为遗留笔记）

```yaml
type: teamflow_memory          # F1 修复后统一为下划线
state: active                  # active | deprecated | archived
strength: 0.72
recorded_at: 2026-08-03T12:00:00Z
recorded_run: 20260803T120000Z # capture run_id，证据可回溯到 runs/memory/<run_id>/
last_verified_at: 2026-08-03T12:00:00Z
last_recalled_at: 2026-09-10T08:00:00Z
recall_count: 4
reinforced_count: 2
contradicted_count: 0
authority: captured            # captured | user
superseded_by: []              # permalink 列表；deprecated 时必填其一（或 reason）
```

### 7.2 正文观察（替代 F2 的悬空引用）

`[evidence]` 行改为持久引用：召回来源写 permalink，收据来源写 `runs/task-receipts/<run-id>/receipt.json` 相对路径 + 收据内命令摘要。`[lineage]` 保留 extraction ID 但必须同时带 `recorded_run`，使谱系可回溯。

### 7.3 状态目录

```text
~/.teamflow/memory/state/
├── usage-ledger/2026-09.jsonl      # recall 命中、强化、矛盾事件（append-only）
├── proposals/                       # 未执行的 supersede/deprecate/consolidate 提案（取代有去无回的 deferred）
│   └── <proposal-id>.json
├── archive/                         # archived 笔记原文（保持原相对路径）
├── tombstones/<note-id>.json
└── cold-store/                      # 既有
```

## 8. 四条遗忘路径

### 8.1 写时遗忘（capture 内嵌）

把 F3 的 deferred 队列变成受控执行。`apply_candidates` 的新行为：

- `create`：不变，另回写 §7 元数据；对 receipt `related_memory` 中的每个 permalink 追加强化事件。
- `update`：仅当目标是原子笔记且新证据严格更强时，向目标笔记**追加**新的 `[evidence]` 观察与 `last_verified_at`（不改语义正文）；语义变化一律降级为 supersede 提案。
- `supersede`：写入 `state/proposals/`，并在**同一 run 内**由确定性 runner 执行安全部分——把被取代笔记置为 `deprecated` + `superseded_by` 指向新笔记；笔记原文一字不动。执行条件（全部满足才执行，否则留在 proposals 等待清扫或人工）：
  1. 新笔记本次已成功 create 且验证 PASS；
  2. 被取代笔记 `authority: captured`（用户笔记只提名，不执行）；
  3. 被取代笔记不在最短保护期内（创建 < 14 天的不动）；
  4. 本 run 已执行的 deprecate 数 < `TEAMFLOW_MEMORY_MAX_DEPRECATES_PER_RUN`（默认 4）。

### 8.2 召回时遗忘（读路径过滤与排序）

- `recall` 默认过滤 `state != active`，输出按 `upstream 相关度 × strength` 重排；每条结果附带 `state/strength/recorded_at/last_verified_at`，让 planner 对低强度命中保持怀疑（对齐"stale notes are leads, not authority"）。
- 显式 `TEAMFLOW_MEMORY_RECALL_STATES=active,deprecated` 可扩大范围（审计、整备用）。
- 每次 recall 的命中写 usage-ledger 一行；不修改笔记文件。
- `context` 的 `--timeframe` 从隐式 30 天改为显式配置并默认放宽到 365 天——图关系的时效交给状态机管理，不再用时间窗一刀切（消除 F6 的隐式遗忘）。

### 8.3 周期性巩固（consolidation，"睡眠期"提炼）

新命令 `teamflow memory consolidate [--topic <query>] [--apply]`，每 N 次 capture 后或人工触发：

1. 确定性预聚类：按 folder + tags + 标题前缀 + 正文关键词把 active 笔记分簇，选出超过 `CLUSTER_MIN`（默认 4）条的主题簇。
2. 模型提炼阶段（复用 capture 管道基建，GLM 执行、extract-memory 语义规则约束）：输入一个簇的全部原文，输出提案——`create` 一条合并笔记（谱系必须覆盖簇内全部被合并源的 permalink）+ 对每个源的 `deprecate` 提案。
3. 确定性校验：合并笔记谱系完整、无新增语义（受 opaque-identifier 校验约束）、簇内用户笔记只提名；校验通过且 `--apply` 时执行，单次 consolidate 至多处理 `MAX_CLUSTERS_PER_RUN`（默认 3）个簇。

这是"记忆更提炼"的主要来源：知识密度上升、笔记数量下降、每条提炼笔记携带全部源谱系。

### 8.4 衰减清扫（sweep，纯确定性）

新命令 `teamflow memory sweep [--apply]`，默认 dry-run 输出完整报告：

1. 结算 usage-ledger → 更新各笔记 frontmatter 统计与 strength。
2. `strength < S_dep` 的 active 笔记 → deprecated（每次上限 `MAX_SWEEP_DEPRECATES`，默认 8；受 14 天保护期约束）。
3. deprecated 驻留 ≥ `D_dep`（默认 60 天）且期间零命中 → archived（移入 `state/archive/`，上限 20/次）。
4. archived 驻留 ≥ `D_arc`（默认 180 天）→ 仅当 `TEAMFLOW_MEMORY_PURGE_ENABLED=true` 时 purge（写 tombstone 后删除原文）。
5. 消费 `state/proposals/` 中因条件不满足而滞留的提案：条件已满足的执行，超过 90 天仍不满足的在报告中列为需人工裁决。

sweep 不调用任何模型，输出结构化报告到 `.teamflow/runs/memory-sweep/<run-id>/report.json`。

## 9. 冷存储保留策略（TurnBlock / TurnIndex）

热区语义不变（逐出不是删除）；遗忘只作用于冷存储的长期保留：

| 层 | 保留规则 | 依据 |
| --- | --- | --- |
| 活跃 session 的 TurnBlock | 全量保留 | 恢复与召回依赖完整原文 |
| 已结束 session，任务收尾完成（存在对应 verified receipt / planning-experience / 策展笔记） | 保留 `COLD_RETENTION_DISTILLED`（默认 90 天）后可打包压缩，压缩包再保留 `COLD_RETENTION_PACKED`（默认 180 天）后 purge + tombstone | 精华已提炼进策展层，原文价值随时间衰减 |
| 已结束 session，无任何提炼产物 | 保留 `COLD_RETENTION_RAW`（默认 180 天），到期进入 sweep 报告等待人工裁决，不自动 purge | 未提炼即原文是唯一载体，宁可提醒不可静删 |
| TurnIndex | 随时可删可重建；对应 TurnBlock purge 时同步删除 | 设计既有约定 |

TurnBlock purge 的 tombstone 记录 block id、sequence、content hash 与 session 归属，保证审计链完整（hash 校验语义不受影响：tombstone 证明"曾存在且未被篡改地删除"）。Pi JSONL 归 Pi 管理，不在本策略范围内。

## 10. 安全约束

1. 模型永远不直接执行任何状态迁移或删除；一切执行走确定性 runner，且默认 dry-run。
2. 用户手工写入（`authority: user`）的笔记：自动流程只能生成提名，进入 sweep 报告；执行需要用户对该提名的显式确认。
3. 每条自动执行路径都有硬上限（§8 各处）与 14 天新笔记保护期。
4. deprecate 必须携带 reason + evidence（superseded_by 或矛盾收据引用）；无证据的降级只能来自衰减阈值，且衰减降级永不跨级。
5. purge 默认关闭；打开后每一次 purge 必须先写 tombstone，写失败则中止（对称于 `MEMORY_PERSISTENCE_FAILED` 的不伪造原则）。
6. 提炼合并笔记未通过确定性校验时，源笔记保持 active，绝不出现"源已降级而合并未落地"的中间态（先 create 后 deprecate，同 run 原子顺序）。
7. 遗忘决策与执行事件全部落 `.teamflow/runs/`，外层协调按既有规则只观察元数据。

## 11. CLI 面

```bash
teamflow memory audit                    # §2 审计：分布、悬空证据、重复簇、老化、状态统计（只读）
teamflow memory sweep [--apply]          # §8.4 衰减清扫（默认 dry-run）
teamflow memory consolidate [--topic q] [--apply]   # §8.3 主题提炼
teamflow memory restore <permalink>      # 从 deprecated/archived 恢复并重置强度
teamflow memory proposals                # 列出滞留提案与用户笔记提名
```

`recall` / `list` / `context` / `read` 行为按 §8.2 调整；`remember` / `remember-global` 写入时补齐 §7 元数据并标记 `authority: user`。

## 12. 云端演进兼容性

本设计刻意不依赖存储位置：

- 状态、强度、谱系全部内嵌于 Markdown frontmatter——同步 = 复制文件。
- 删除通过 tombstone 物化为可同步的事实，避免"本地删了、云端复活"的经典同步缺陷。
- usage-ledger 按月分片、append-only，多设备合并时按事件时间去重合并即可，不需要冲突解决协议。
- 只读 `teamflow server` 可直接展示 state/strength 徽标与 tombstone 审计视图，不需要写路径。

记忆上云（远端 Basic Memory 或对象存储后端 + 多设备 ledger 合并）作为独立设计在遗忘机制落地后再行展开。

## 13. 实施阶段

### Phase 0 — 前置修复（遗忘机制的依赖）

- 统一类型串为 `teamflow_memory`（写入侧 + 存量迁移脚本 + 检测正则兼容期同时接受两种）。（F1）
- apply 写入持久 provenance：permalink / 收据路径 / `recorded_run` / `recorded_at`。（F2）
- `remember` 的 `shasum` 改为 `sha256sum` 回退。（F8）

### Phase 1 — 元数据与账本

- frontmatter 扩展、usage-ledger 写入、recall 命中记录与 `related_memory` 强化回写、`memory audit`。

### Phase 2 — 写时遗忘

- proposals 存储取代 deferred 黑洞；同 run 安全执行 supersede → deprecated；recall 默认过滤非 active。

### Phase 3 — 清扫与恢复

- strength 结算、`memory sweep`、`memory restore`、deprecated→archived 迁移、tombstone。

### Phase 4 — 提炼

- `memory consolidate` 聚类 + 模型提炼 + 确定性校验执行。

### Phase 5 — 冷存储保留

- §9 分级保留、打包压缩、TurnBlock tombstone。

每个 Phase 按 AGENTS.md 流程实现：test-writer 先写失败测试，coder 最小实现，test-runner 回执，test-writer 审查。

## 14. 验收标准

- 存量与新写入笔记的原子类型可被检测正则统一识别；formatter 对策展笔记不再产生误判 supersede。
- 任一策展笔记可回答：何时写入、依据什么证据、最近何时被召回/验证、当前状态与强度。
- 一次带 supersede 候选的 capture 结束后，被取代笔记立即退出默认召回，且 `read` 显示 superseded_by。
- `sweep --apply` 在构造的老化数据集上完成 active→deprecated→archived 迁移，且用户笔记只出现在提名清单。
- `consolidate --apply` 后：簇内源笔记全部 deprecated、合并笔记谱系覆盖全部源、默认召回该主题只返回合并笔记。
- 全程无一次物理删除发生在 tombstone 缺失的情况下；`restore` 可在窗口期内恢复任意降级。
- 召回基线对照（§2 审计第 6 项）在整备 + 遗忘上线后 top-8 信噪比不降低。

## 15. 待确认参数

1. 半衰期 `H`、阈值 `S_dep` 与各驻留期（`D_dep`/`D_arc`）的默认值是否合适——建议先以 dry-run 报告运行一个月后定标。
2. 强化封顶（+0.3）与矛盾扣分（−0.4）的相对权重。
3. consolidate 预聚类的确定性算法是否需要本地嵌入辅助（首期不依赖）。
4. deprecated 笔记是否参与 formatter 的去重比对（倾向：参与，防止被取代的说法换皮重生）。
5. 冷存储打包格式与压缩粒度（按 session 还是按月）。
6. 用户笔记提名的确认交互形态（CLI 确认 vs server 界面）。
