# Teamflow 记忆遗忘机制设计

状态：设计稿 v3（未实现；实现须按 AGENTS.md 走 teamflow 多 Agent 流程）

v2 修订依据：2026-08-03 三路评审结论 GO WITH REVISIONS——GLM-5.2 planner（session 019fc94c-bd05-72c0-b809-f3ee162ee5ac）、K3（session 019fc94e-d479-7911-89d8-17bd3c8a0120）、以及用户对当前仓库与 Basic Memory 0.22.1 源码的人工复核。主要修订：F1 更正为误报；新增 §4 前置契约；实施阶段重排为 Phase -1 起步、只读 audit 为最窄首切片、冷存储保留明确阻塞。

v3 修订依据：对 v2（commit b701c36）的独立只读复审（G1–G8）。主要修订：G1——slug 三处实现并非字节等价且漏列真正写笔记的 Python 实现，§4.2 改为冻结单一 slug 契约 + 跨实现金标测试；G2——消除"create 不变"与 journaled commit 的内部矛盾，create 改为 journal 内带 commit 标记的步骤；G3——新增 §4.8 笔记形状统一契约（手工 `teamflow-finding` 路径的原子保护盲区、authority 识别、memory_id 落地）；G4——显式声明存量库 bootstrapping 限制与解锁门槛；G5——Phase 0B 解析手工笔记 `[recorded]` 行回填而非降级；G6——`context` timeframe 变更单列为 Phase 2 带测试交付物；G7——补充账本 seq 计数器的崩溃持久化规则；G8——写侧改为直接输出规范下划线类型串，消除对外部归一化的依赖。

适用范围：`~/.teamflow/memory/` 下的策展知识层（Basic Memory notes）、文件级冷存储（TurnBlock / TurnIndex）、规划经验，以及未来的云端记忆后端。

## 1. 背景与问题

Teamflow 已发布到云上运行，但跨项目记忆仍存储在本地 `~/.teamflow/memory/`。在讨论记忆上云之前，必须先解决一个更基础的缺陷：**当前记忆系统只有写入和逐出，没有任何主动遗忘路径**。

对当前实现的核查结论（证据见 §2）：

1. 自动捕获管道对 `update` / `supersede` 候选一律 `deferred`——只记录进 `50-apply.json`，没有任何后续流程消费这个队列。旧记忆永远保持"现行"状态。
2. 策展层唯一的防增长手段是 formatter 的 `skip` 判定，而 formatter 只能看到本次任务显式召回的笔记（evidence capsule 中的 `--source`）。凡未被召回的旧笔记对去重完全不可见，形成盲区复制。
3. 去重 digest 基于 casefold 后的语义原文精确哈希；任何换一种说法的等价知识都会生成新笔记。
4. `recall` 默认路径分页扫描全部搜索结果再本地过滤，成本随记忆总量线性增长；记忆越多，召回越慢、越噪，反过来又降低 formatter 的去重质量——恶性循环。
5. `memory context` 的 `build-context --timeframe 30d` 在收窄超过 30 天的图关系的查询可见性——数据仍完整在盘，但这是一处未经设计、与其他读取路径不一致的隐式可见性策略。

结论：不遗忘的记忆库最终既不可信（陈旧结论持续参与规划）也不可用（召回被稀释）。遗忘机制是记忆上云的前置条件——同步一个只增不减的库只会把问题复制到云端。

## 2. 现有记忆系统评估

以下发现基于对当前仓库写入/读取路径的逐行核查与三路评审复核，按严重程度排序。F2/F3 是遗忘机制的前置依赖；F1 经复核为误报。

### F1 — 类型串不一致（误报，已更正）

初版判断 apply 写入 `--type "teamflow-memory"`（连字符）与检测正则约定的 `type: teamflow_memory`（下划线）不一致、原子保护失效。复核结论：**误报**。Basic Memory 的 `NoteType` 在 schema 层通过 `to_snake_case` 归一化（0.22.1 `basic_memory/schemas/base.py`，用户人工核验），落盘 frontmatter 实为 `type: teamflow_memory`，与检测正则一致；现存策展笔记也全部如此：

```241:241:.teamflow/skills/extract-memory/scripts/run_pipeline.py
                if re.search(r"(?m)^type:\s*(?:teamflow|workflow)_memory\s*$", head):
```

处理：**无需存量迁移**。残余风险（复审 G8）：免责依赖仓库外、未被测试固定的归一化不变式，一旦 Basic Memory 变更 `to_snake_case` 行为，原子源检测将静默失配。因此 Phase 0B 做两件事：写侧改为直接传入规范下划线串 `--type teamflow_memory`（归一化从此是无操作，写入串与检测正则固定在同一契约内）；同时保留 round-trip 兼容测试（连字符写入 → 读回下划线 → 正则命中）覆盖存量与第三方写入路径。

### F2 — 持久化笔记的证据与谱系是运行期悬空引用（确认，且比初判更严重）

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

评审进一步指出：即便补上 `recorded_run` 与 `runs/task-receipts/...` 路径，它们仍是本机、项目相对、gitignored 的运行产物——跨项目召回、换机器或删除项目后同样无法解析。**"持久证据"必须是 durable evidence URI + 内容摘要 + 内联自足摘要**（§4.2），而不是运行产物的相对路径。

### F3 — deferred 队列有去无回（确认）

`apply_candidates` 对 `update` / `supersede` 的处理是记录后放弃（`run_pipeline.py` 第 272 行起）；没有任何命令、Agent 或 Skill 消费 `50-apply.json`（措辞更正：`clean.py` 是按后缀泛化保留所有 `.json`，并非专门保留该文件——它幸存是巧合而非契约）。被更强证据取代或已被证伪的记忆永远以 `active` 姿态参与召回。

### F4 — 缺少任何生命周期元数据（确认，范围限定为自动 curated 笔记）

自动 apply 写入的策展笔记 frontmatter 只有 title/type/permalink/tags；正文只有 type/status/scope/evidence/lineage 五行观察。没有稳定身份标识、`recorded_at`、`last_verified_at`、召回命中记录、强化/矛盾计数。文件 mtime 是唯一的时间信号，而 mtime 在同步、备份恢复、git 检出后全部失真。**没有元数据就没有可计算的遗忘。**

范围更正（复审 G5）：手工 `remember` 路径（`write_finding`）是另一种笔记形状，其正文含 `- [recorded] <iso8601>` 时间行——Phase 0B 迁移应解析该行回填 `recorded_at`，而不是因"无法回填"降级为 `hypothesis`。两种形状的全面对齐见 §4.8。

### F5 — 去重范围受限于召回质量（确认）

formatter 只能对 evidence capsule 中的笔记做 `skip` / `update` 判定，而 capsule 只包含本次任务 receipt `related_memory` 传入的召回结果。近义改写 + 召回盲区共同作用下，同一知识的变体会持续累积，每个变体的标题是"前 52 字符 + 12 位 digest"，人工浏览也难以合并。

### F6 — 读取路径的可见性策略互相矛盾（主体确认，措辞修订）

`recall` 默认路径最多翻 1000 页（每页 8 条）拉取全部命中再过滤；`list` 用 `recent-activity --timeframe 365d`；`context` 用 `--timeframe 30d`。三条读取路径对"库会变大"的假设互相矛盾。修订两点：

1. 30 天 timeframe 收窄的是**查询可见性**，不是删除——数据完整在盘。问题在于它是隐式的、与状态机无关的一刀切，应改为显式配置并交由生命周期状态管理。
2. 排序修正：Basic Memory 的 FTS 相关度分数在 SQLite FTS5 后端为**负值、升序**，Postgres 后端为**正值、降序**——符号与方向依后端而变。初版设想的 `relevance × strength` 直接组合不成立。强度只能作为独立的过滤 / 分桶 / 降级维度，或在把 upstream 结果先归一化为名次后再组合（§9.2）。

### F7 — 冷存储无保留策略（确认，且分级实现被阻塞）

`FileColdStore` 将每轮 TurnBlock 永久写入 `state/cold-store/`，设计文档明确"遗忘是逐出，不是删除"（对热区正确），但对冷存储本身没有任何年龄、任务完成度或容量维度的保留策略。且按任务完成度分级目前**无法实现**：

```490:490:.teamflow/extensions/memory-context/index.ts
				taskId: process.env.TEAMFLOW_TASK_ID || "adhoc",
```

产品路径没有任何环节设置 `TEAMFLOW_TASK_ID`，TurnBlock 实际全部落在 `adhoc`；TurnBlock 也没有到 receipt、任务完成、session 结束或提炼产物的反向关联。冷存储分级保留（§10）明确阻塞在这些关联键落地之后。

### F8 — 次要问题（确认，非前置 blocker）

- 手工 `remember` 用 `shasum` 生成标题哈希，精简 Linux 镜像上可能缺失（应回退 `sha256sum`）。
- 手工 finding 标题为 `Verified finding <hash>`，标题本身零检索价值。
- `MAX_CREATES_PER_RUN=8`、目标 3–5 条/任务：高频使用一个月即数百条笔记，而系统没有与之配平的任何回收路径。

以上属于工程优化项，随相邻阶段顺带处理，不作为遗忘机制的前置依赖。

### 对已有记忆数据的审计与整备建议

记忆数据在运行 teamflow 的机器上（本仓库工作区没有 `~/.teamflow/memory/`），审计必须在数据所在处执行，且首期以只读 `teamflow memory audit --format json`（§14 Phase 0A）落地为工具，不靠人工 grep。审计维度：

1. **规模与构成**：笔记总数、按 folder（`projects/<slug>/curated`、`global/`、遗留目录）与 `type:` 值的分布。类型分布用于定位遗留 `workflow_finding` 巨石与异常笔记（不再作为 F1 迁移依据）。
2. **悬空证据面**：统计正文含 `- [evidence] NOTE-` / `RECEIPT-` / `EVIDENCE-` 的笔记比例，即 F2 影响的存量。整备时从可用信号（git 历史、Basic Memory DB、文件时间）回填 `recorded_at`，无法回填的降为 `hypothesis`。
3. **重复候选**：标题去掉末尾 `[digest]` 后按前缀聚类，正文 casefold 相似度复核；只产出候选清单，合并留给 consolidation 阶段。
4. **陈旧结论**：对照当前仓库逐条验证 `status: verified` 声明；被现实推翻的进入 deprecated 提案。
5. **遗留巨石**：`workflow_finding` 按现有 formatter 规则提炼后整体 deprecated（提案）。
6. **参数模拟**：在候选半衰期/阈值参数下对全库做强度 dry-run 直方图，为 Phase 3 校准提供真实分布，而不是拍脑袋定阈值。
7. **可检索性抽样**：用近期真实任务关键词跑 `teamflow memory recall`，记录 top-8 构成作为遗忘上线前的信噪比基线。

## 3. 目标与非目标

### 3.1 目标

- 让每条策展记忆有稳定身份、显式生命周期状态与可计算的强度，默认召回只返回高置信、现行、经使用验证的记忆。
- 把"更强证据取代旧结论"从被搁置的提案变成受控执行的一等操作。
- 提供周期性提炼（consolidation）：把同主题的零散原子笔记合并为更少、更强、带完整谱系的笔记——记忆总量随使用**收敛**而非发散。
- 为冷存储 TurnBlock 建立基于任务完成度与年龄的分级保留（阻塞项解除后）。
- 所有遗忘动作可审计、可逆（在窗口期内）、可同步（tombstone），为记忆上云铺路。

### 3.2 非目标

- 不改变"只有验证 PASS 才写记忆"的写入门槛。
- 不引入云服务、MCP、账号或 API Key；机制本身保持完全本地可运行。
- 不让模型直接删除任何文件：模型只产生候选，确定性 runner 执行（与 capture 管道同构）。
- 不做基于嵌入的自动语义合并作为首期依赖（可作为后续增强）。
- 不改动准则 cache（rule cache）——它已有 supersede/retire 语义，是本设计权限阶梯的参照物，不是改造对象。

## 4. 前置契约（Phase -1 冻结项）

以下八项契约必须在任何实现开始前冻结。它们是三路评审与独立复审识别出的结构性缺口：跳过任何一项，后续阶段都会在错误的地基上返工。

### 4.1 稳定身份：`memory_id`

- 每条笔记在创建时获得不可变 `memory_id`（ULID），写入 frontmatter，终身不变——跨状态迁移、跨标题/目录变更、跨设备同步保持。
- **permalink 是检索地址，不承担身份。** 标题重写、目录调整、（后期的）物理归档都会使 permalink 失效或不可由普通 `read` 解析。
- 事件账本、`superseded_by`、tombstone、journal、`memory_feedback` 一律以 `memory_id` 为主键；permalink 仅作为便利线索冗余记录。
- 存量笔记在 Phase 0B 迁移中幂等补发 `memory_id`（重复运行不得重发），并记录 permalink → memory_id 映射供旧引用解析。

### 4.2 durable evidence

运行产物路径不是持久证据。证据引用契约为"来源身份 + 工件地址 + 内容摘要 + 内联自足摘要"四元组：

```yaml
evidence:
  - uri: teamflow-evidence://<origin-slug>/<capture-run-id>/receipt.json
    digest: sha256:...
    origin_remote: https://github.com/one2x-ai/teamflow   # slug 派生的原始 remote，跨机器可比对
    summary: "python -m pytest tests -q PASS（587 passed）证明 X"
```

- **slug 派生必须先统一为单一契约（复审 G1）。** v2 断言 origin-slug 与 bash/TS "完全相同"不成立，且漏列了真正写笔记的 Python 实现。当前三处实现并非字节等价：Python `run_pipeline.py`（`.strip("-")`，去全部首尾连字符）、bash `.teamflow/bin/memory`（`${VAR%-}`，只去一个尾部、不去首部）、TS `scope.ts`（`/-+$/`，去全部尾部、不去首部）。可复现分歧：`@foo.git` → Python 得 `foo`，bash/TS 得 `-foo`；`repo--` → bash 去 1 个、Python/TS 去全部。常规 GitHub 仓名下三者重合，故历史上未暴露；但 durable evidence、permalink→memory_id 映射与 §13 同步全部建立在"同一远程解析为同一 slug"之上，写侧（Python）与读侧（TS server）分叉会造成跨机器证据解析静默失配。Phase -1 冻结单一 slug 规范（小写 → 去 `.git` 后缀 → 非 `[a-z0-9._-]` 连续段折叠为 `-` → 去全部首尾 `-`），四个实现点（Python 写侧、bash CLI、TS server、未来同步层）共享同一组金标向量（golden vectors）测试，任何一处漂移即测试失败。
- 解析语义分级：本机工件存在 → 解引用并用 digest 校验；工件不存在（换机、删项目）→ 笔记内联 summary 仍自足可用，digest 作为"引用的是哪份证据"的指纹。
- 笔记正文 `[evidence]` 观察行写人类可读摘要（命令 + 结论），保证笔记脱离一切运行产物后仍能独立回答"凭什么信这条记忆"。

### 4.3 遗留权限默认值

- 缺少 `authority` 字段的存量笔记默认 `authority: unknown`，**享受与 `user` 同级保护**：自动流程只能提名，执行需用户确认。不得默认 `captured`。
- audit 可依据自动写入者签名（digest 后缀标题 + curated 目录 + `teamflow,curated` tags）生成 `captured` 重分类提案；重分类本身也是需确认的提案，不自动生效。

### 4.4 记忆反馈事件分类

receipt 的 `related_memory` 只被校验为字符串数组并转为管道 `--source` 输入：

```28:30:.teamflow/skills/memory-capture/scripts/capture_memory.py
    related = value.get("related_memory", [])
    if not isinstance(related, list) or not all(isinstance(item, str) for item in related):
        fail("related_memory must be an array of memory permalinks")
```

它最多证明"笔记曾作为 capture 输入被读取"，不能证明召回命中、规划采用、重新验证或对 PASS 有贡献。信号必须拆分为四类事件（弱 → 强）：

| 事件 | 语义 | 生产者 |
| --- | --- | --- |
| `recalled` | 出现在 recall 结果且被读取 | recall 读路径自动记账 |
| `used` | planner 在计划或 handoff 中显式采用该记忆 | receipt 新增 `memory_feedback` 字段 |
| `verified` | 笔记声明对当前仓库重新验证通过（memory-recall 规则本要求验证，现在把结果落账） | receipt `memory_feedback`，必须附验证证据 |
| `contradicted` | 重新验证失败，或被更强反向证据推翻 | receipt `memory_feedback`，必须附反向证据 |

receipt schema 扩展：

```json
"memory_feedback": [
  {
    "memory_id": "01JD...",
    "permalink": "projects/teamflow/curated/...",
    "event": "verified",
    "evidence": "pytest tests/runtime -q PASS on current main",
    "note": "约束仍然成立"
  }
]
```

更正初版的一个错误：初版称"矛盾信号来自 receipt 的 conflicts"——**receipt 当前没有 conflicts 字段**；`conflicts` 是 compression/formatting 管道产物的字段。矛盾信号以 receipt `memory_feedback` 的 `contradicted` 事件为准；管道 conflicts 产物在能解析回 `memory_id` 时仅作旁证。

### 4.5 事件账本契约

append-only 不等于自动可靠。每条账本事件：

```json
{
  "event_id": "01JD8...",            // ULID，全局幂等键
  "actor": {"device": "mac-wsq", "runner": "recall|capture|sweep|restore"},
  "run_id": "20260803T120000Z",
  "seq": 42,                          // actor 内单调递增，缺口可检测
  "memory_id": "01JD...",
  "event": "recalled|used|verified|contradicted|state_change|restore",
  "occurred_at": "2026-08-03T12:00:00Z",
  "payload": {"...": "..."},
  "payload_digest": "sha256:..."
}
```

- **幂等应用**：结算以 `event_id` 去重，重复应用无副作用。
- **结算 watermark**：`state/usage-ledger/watermark.json` 记录各 actor 已结算位置；结算中途崩溃后从 watermark 重放。
- **单写者锁**：`state/locks/` 文件锁；拿不到锁的 runner 显式报错退出，不静默并发写。
- **seq 计数器的崩溃持久化（复审 G7）**：watermark 只跟踪结算水位，不管发号。每个 actor 的 seq 计数器持久化在 `state/usage-ledger/actors/<device>.json`，发号顺序固定为"原子推进计数器（临时文件 + rename）→ 追加事件"。计数器推进后、事件落盘前崩溃会产生一个缺口——因此缺口的唯一语义是"此处曾有一次崩溃的追加"，永远不表示乱序，也永远不会因重启回退发出重复 seq。
- **崩溃恢复**：结算 = 读事件 → 更新 frontmatter → 推进 watermark，三步中任一步崩溃后重跑收敛到同一终态。
- **多设备合并** = 按 `event_id` 做幂等并集，无需冲突解决协议；`seq` 缺口只用于诊断，不阻塞合并。

### 4.6 事务与恢复

"先 create 后 deprecate 的同 run 顺序"只是顺序执行，不是原子性。状态迁移必须是 journaled、幂等、可续跑的状态机：

- `state/journal/<txn-id>.json` 先写完整意图（全部步骤 + 参数），每步完成写入 commit 标记。
- 崩溃重启时扫描未完成 txn：create 已 commit → 前滚完成剩余 deprecate；create 未 commit → 放弃整个 txn。deprecate 永远排在 create 验证提交之后，因此不需要补偿逻辑。
- 每个写步骤幂等：重复执行到达相同终态（frontmatter 置位、账本 event_id 去重）。
- txn 完成后 journal 归档入 run 产物，供审计。

### 4.7 归档与 tombstone 契约（修订）

- **首期 `archived` 仅是 frontmatter 可见性状态，不移动文件。** 移动文件会破坏 Basic Memory 索引一致性并使普通 read 无法解析。物理归档（移入 `state/archive/`）推迟到独立后期阶段，且必须伴随索引处理方案与按 `memory_id` 的读取支持。
- tombstone 是**删除决策的审计记录与内容指纹**：`memory_id`、title、content sha256、完整迁移链（每步 reason/evidence/时间/操作者）。它不证明"未篡改地删除"，也不能恢复内容——恢复能力只来自 purge 前的驻留期。若未来需要 purge 后恢复，须另行设计内容托管（escrow），不属于 tombstone 职责。

### 4.8 笔记形状统一（复审 G3）

当前存在两种互不对齐的笔记形状，生命周期机制必须覆盖两者：

| 路径 | type | folder | tags | 时间戳 |
| --- | --- | --- | --- | --- |
| 自动 apply | `teamflow-memory`（落盘 `teamflow_memory`） | `…/curated` | `teamflow,curated,<type>` | 无 |
| 手工 `write_finding` | `teamflow-finding` | `projects/<slug>` / `global`（无 `/curated`） | `coding,teamflow,verified` | 有（`[recorded]` 行） |

由此产生三个必须在契约层解决的盲区：

1. **原子保护盲区**：原子源检测正则只匹配 `*_memory`，`teamflow-finding` 笔记不受"拒绝替换原子来源"护栏保护。契约：形状注册表显式枚举全部受管 type（`teamflow_memory`、`workflow_memory`、`teamflow_finding`、`workflow_finding`）及各自的原子性与保护等级；检测逻辑以注册表为准，不再依赖单一正则的巧合覆盖。
2. **authority 识别盲区**：§4.3 的 `captured` 重分类签名（digest 后缀标题 + curated 目录）不匹配手工 finding；`user` 的识别规则不能只靠"签名不匹配"这种否定推断。契约：手工写入路径在写入时显式落 `authority: user`；存量手工 finding 依据 `teamflow-finding` type + `[recorded]` 行的正向签名在 Phase 0B 迁移中重分类为 `user`（此重分类是提升保护等级，可自动执行；降级方向才需要用户确认）。
3. **手工路径的元数据落地**：§12 承诺 `remember` 补齐 §8 元数据，但纯 bash 的 `write_finding` 没有生成 ULID 或结构化 frontmatter 的机制。契约：两条写入路径收敛到同一个规范形状——统一经由一个共享的确定性写入 helper（生成 memory_id、durable evidence、authority、时间戳后落盘），bash 只做参数收集。写入机制需在 Phase -1 一并冻结：自定义 frontmatter 字段若超出 `write-note` CLI 能力，则采用"直接写 knowledge/ 文件 + 触发 sync 索引"的文件优先路径（Basic Memory 本身是 file-first 设计），并以形状一致性测试固定。

## 5. 核心原则

1. **遗忘是状态迁移，不是物理删除。** 延续热区"逐出而非删除"的哲学：记忆沿 `active → deprecated → archived → purged(tombstone)` 单向迁移，每级之间有最短驻留期，窗口内可恢复。
2. **遗忘的首要单位是检索可见性。** 系统优化的是"默认召回返回什么"，而不是"磁盘上有什么"。降低可见性先于回收空间；首期所有降级都只动 frontmatter。
3. **提炼优先于丢弃。** 对高价值主题，遗忘表现为 N 条零散笔记 → 1 条提炼笔记 + N 条 deprecated 源；只对确证陈旧、重复或长期无用的记忆做纯粹降级。
4. **证据驱动，决策留痕。** 每次状态迁移必须携带 reason 与 evidence（新笔记 memory_id、`contradicted` 事件、使用统计），与写入侧"包含理由与证据"的规则对称。
5. **决策与执行分离。** 模型（formatter / consolidation 阶段）只写提案 JSON 到 `.teamflow/runs/memory/`；确定性 runner 校验谱系与安全约束后按 §4.6 事务执行，与 capture 管道同构。
6. **权限阶梯。** 复用准则 cache 的权限序：`user` 与 `unknown` 笔记只能被自动流程**提名**降级，执行需用户确认；`captured` 笔记可被自动降级。用户的显式恢复指令高于一切自动决策。
7. **先观测后动刀。** 账本与提案先以 shadow 模式运行，可见性变更、阈值启用一律在真实 telemetry 校准之后（§14 Phase 1/3）。
8. **存储无关、云可同步。** 生命周期状态表达为 frontmatter + tombstone 文件，事件账本按 `event_id` 幂等合并；任何未来的同步层都能复制状态而不需要理解决策过程。

## 6. 生命周期状态机

```text
            强化(used / verified 事件)
              ┌─────────┐
              ▼         │
  create ─> active ─────┘
              │ supersede 执行 / contradicted / 衰减低于阈值
              ▼
          deprecated ──(驻留 ≥ D_dep 且无恢复)──> archived ──(驻留 ≥ D_arc 且无恢复)──> purged
              │                                     │                                   │
              └––––––– restore（任何人）–––––––––––––┘                          tombstone 永久保留
```

| 状态 | 默认召回 | `read`/审计 | 物理位置 | 进入条件 |
| --- | --- | --- | --- | --- |
| `active` | 参与 | 可见 | `knowledge/` | 创建；或从 deprecated/archived 恢复 |
| `deprecated` | 排除 | 可见（带状态标注） | `knowledge/`（frontmatter 标记） | supersede 执行、`contradicted` 事件、衰减阈值、整备提案获确认 |
| `archived` | 排除 | 显式请求可见 | 首期仍在 `knowledge/`（仅 frontmatter；物理移动是后期独立阶段，见 §4.7） | deprecated 驻留期满且期间零 `used`/`verified` 事件 |
| `purged` | 排除 | 仅 tombstone | `state/tombstones/<memory_id>.json` | archived 驻留期满且 purge 显式启用；或用户显式授权 |

规则：

- 状态只能逐级向下迁移；`restore` 可从 deprecated/archived 一步回 active，记录恢复原因（恢复是强价值信号，重置强度）。
- `purged` 永不自动发生于 `user`/`unknown` 笔记；`captured` 笔记的 purge 也默认关闭（`TEAMFLOW_MEMORY_PURGE_ENABLED=false`），打开后仍受驻留期与每次清扫上限约束。
- 一切迁移经 §4.6 journal 执行、落 §4.5 账本 `state_change` 事件。

## 7. 记忆强度模型

每条笔记维护确定性可重算的强度分 `strength ∈ [0, 1]`，只由 §4.4 的四类事件与时间构成（全部可审计，不含模型主观打分）：

```text
strength = clamp(
    base(status, authority)              # verified=0.6, hypothesis=0.35; user/unknown +0.2
  + Σ recalled   × 0.02（封顶 +0.06）     # 最弱信号：只证明被读到
  + Σ used       × 0.10（封顶 +0.20）     # 规划显式采用
  + Σ verified   × 0.15（封顶 +0.30）     # 对当前仓库重新验证通过
  - Σ contradicted × 0.40                 # 重新验证失败或被推翻
  - pending_supersede × 0.20              # 存在未执行的 supersede 提案
  - decay(days_since_last_signal)         # 半衰期 H 指数衰减，最多 −0.40
)
```

- 权重与半衰期 `H`（初值 180 天）、降级阈值 `S_dep`（初值 0.25）在 Phase 3 之前**只参与 dry-run 报告，不驱动任何迁移**；用 audit 的参数模拟（§2 审计第 6 项）在真实分布上校准后才启用。
- 衰减是慢信号，只能把 active 压入 deprecated 候选清单，从不直接 archived。
- 事件记录进 `state/usage-ledger/`（按月分片 JSONL，契约见 §4.5），由 sweep 结算进 frontmatter；不在 recall 时改写笔记文件（避免 git noise 与 mtime 污染）。

## 8. 元数据 Schema 扩展

### 8.1 笔记 frontmatter（新增字段；缺省值见 §4.3 遗留规则）

```yaml
type: teamflow_memory
memory_id: 01JD8QWERTY...        # §4.1 不可变身份
state: active                    # active | deprecated | archived
strength: 0.72                   # sweep 结算写入；Phase 3 前仅报告
recorded_at: 2026-08-03T12:00:00Z
last_verified_at: 2026-08-03T12:00:00Z
last_event_at: 2026-09-10T08:00:00Z
event_counts: {recalled: 4, used: 2, verified: 1, contradicted: 0}
authority: captured              # captured | user | unknown（遗留默认 unknown）
superseded_by: []                # memory_id 列表；deprecated 时必填其一（或 reason）
evidence:                        # §4.2 durable evidence 数组
  - uri: teamflow-evidence://teamflow/20260803T120000Z/receipt.json
    digest: sha256:...
    origin_remote: https://github.com/one2x-ai/teamflow
    summary: "python -m pytest tests -q PASS 证明 X"
```

### 8.2 正文观察（替代 F2 的悬空引用）

`[evidence]` 行写人类可读摘要（命令 + 结论），与 frontmatter `evidence[].summary` 一致；`[lineage]` 保留 extraction ID 但必须伴随 frontmatter 的 capture run 引用，使谱系可回溯。笔记必须在脱离一切运行产物后自足。

### 8.3 状态目录

```text
~/.teamflow/memory/state/
├── usage-ledger/2026-09.jsonl      # §4.5 事件账本（append-only 分片）
├── usage-ledger/watermark.json     # 结算水位
├── locks/                           # 单写者文件锁
├── journal/                         # §4.6 迁移事务日志
├── proposals/                       # 未执行的 supersede/deprecate/consolidate 提案（取代有去无回的 deferred）
│   └── <proposal-id>.json
├── tombstones/<memory_id>.json
├── archive/                         # 后期物理归档阶段才启用
└── cold-store/                      # 既有
```

## 9. 四条遗忘路径

### 9.1 写时遗忘（capture 内嵌）

把 F3 的 deferred 队列变成受控执行。`apply_candidates` 的新行为：

- `create`：语义候选的判定规则不变，但**执行方式改变**（复审 G2 消除 v2 内部矛盾）：create 是 §4.6 journal 内带 commit 标记的第一个步骤——先写 txn 意图，write-note 成功且校验通过后写 create commit 标记，随后的 deprecate 步骤才允许执行。§4.6 的崩溃恢复分叉（create 已 commit → 前滚；未 commit → 放弃）以该标记为准。create 同时回写 §8 元数据（memory_id、durable evidence），并对 receipt `memory_feedback` 中的每个事件落账。
- `update`：仅当目标是原子笔记且新证据严格更强时，向目标笔记**追加**新的 evidence 条目与 `last_verified_at`（不改语义正文）；语义变化一律降级为 supersede 提案。
- `supersede`：写入 `state/proposals/`，并在同一 run 内由确定性 runner 按 §4.6 journal 执行安全部分——被取代笔记置 `deprecated` + `superseded_by` 指向新笔记 memory_id；笔记原文一字不动。执行条件（全部满足才执行，否则留在 proposals 等待 sweep 或人工）：
  1. 新笔记本次已成功 create 且 journal 确认提交；
  2. 被取代笔记 `authority: captured`（`user`/`unknown` 只提名）；
  3. 被取代笔记不在最短保护期内（创建 < 14 天不动）；
  4. 本 run 已执行的 deprecate 数 < `TEAMFLOW_MEMORY_MAX_DEPRECATES_PER_RUN`（默认 4）。

### 9.2 召回时遗忘（读路径过滤与标注）

- `recall` 默认过滤 `state != active`（Phase 2 起生效）；显式 `TEAMFLOW_MEMORY_RECALL_STATES=active,deprecated` 可扩大范围（审计、整备用）。
- **排序首期不动**：保持 upstream 排序，只对每条结果附加 `state/strength/recorded_at/last_verified_at` 标注，让 planner 对低强度命中保持怀疑（对齐"stale notes are leads, not authority"）。禁止任何 `relevance × strength` 直接组合——FTS 分数符号与方向依后端而变（§2 F6）；若 Phase 3 校准后需要重排，只允许"名次归一化后组合"或"强度分桶降级"两种后端无关方案。
- 每次 recall 的命中作为 `recalled` 事件写账本一行；不修改笔记文件。
- `context` 的 `--timeframe` 从隐式 30 天改为显式配置并默认放宽到 365 天——图关系的时效交给状态机管理，不再用时间窗一刀切。注意（复审 G6）：这是对既有 30 天消费者的**即刻行为变更**，不得埋在读路径改造里顺带发生；它是 Phase 2 中单列的带测试小交付物，与 deprecated 过滤同批启用、同批验收。

### 9.3 周期性巩固（consolidation，"睡眠期"提炼）

新命令 `teamflow memory consolidate [--topic <query>] [--apply]`，每 N 次 capture 后或人工触发：

1. 确定性预聚类：按 folder + tags + 标题前缀 + 正文关键词把 active 笔记分簇，选出超过 `CLUSTER_MIN`（默认 4）条的主题簇。
2. 模型提炼阶段（复用 capture 管道基建，GLM 执行、extract-memory 语义规则约束）：输入一个簇的全部原文，输出提案——`create` 一条合并笔记（谱系必须覆盖簇内全部被合并源的 memory_id）+ 对每个源的 `deprecate` 提案。
3. 确定性校验：合并笔记谱系完整、无新增语义（受 opaque-identifier 校验约束）、簇内 `user`/`unknown` 笔记只提名；校验通过且 `--apply` 时按 §4.6 journal 执行，单次至多处理 `MAX_CLUSTERS_PER_RUN`（默认 3）个簇。

这是"记忆更提炼"的主要来源：知识密度上升、笔记数量下降、每条提炼笔记携带全部源谱系。

### 9.4 衰减清扫（sweep，纯确定性）

新命令 `teamflow memory sweep [--apply]`，默认 dry-run 输出完整报告：

1. 结算 usage-ledger → 按 §4.5 幂等规则更新各笔记 frontmatter 统计与 strength，推进 watermark。
2. `strength < S_dep` 的 active `captured` 笔记 → deprecated（每次上限 `MAX_SWEEP_DEPRECATES`，默认 8；受 14 天保护期约束；Phase 3 校准前不启用）。
3. deprecated 驻留 ≥ `D_dep`（默认 60 天）且期间零 `used`/`verified` → archived（首期仅 frontmatter，上限 20/次）。
4. archived 驻留 ≥ `D_arc`（默认 180 天）→ 仅当 `TEAMFLOW_MEMORY_PURGE_ENABLED=true` 时 purge（先写 tombstone，写失败中止）。
5. 消费 `state/proposals/` 滞留提案：条件已满足的执行，超过 90 天仍不满足的列为需人工裁决；`user`/`unknown` 提名单独成节等待用户确认。

sweep 不调用任何模型，输出结构化报告到 `.teamflow/runs/memory-sweep/<run-id>/report.json`。

## 10. 冷存储保留策略（TurnBlock / TurnIndex）——阻塞中

热区语义不变（逐出不是删除）；遗忘只作用于冷存储的长期保留。但本节**当前无法实现**（§2 F7）：TurnBlock 的 `taskId` 在产品路径下恒为 `adhoc`，且没有到 receipt、任务完成、session 结束或提炼产物的任何反向关联。前置条件：

1. 产品路径（`teamflow run` / phase 包装）为每次委派设置真实 `TEAMFLOW_TASK_ID`；
2. TurnBlock 或伴随元数据建立到 verified receipt / planning-experience / 策展笔记的关联键；
3. session 结束事实可靠可查（session-ended 标记）。

关联键落地后按下表执行（Phase 6）：

| 层 | 保留规则 | 依据 |
| --- | --- | --- |
| 活跃 session 的 TurnBlock | 全量保留 | 恢复与召回依赖完整原文 |
| 已结束 session，任务收尾完成（存在关联的 verified receipt / planning-experience / 策展笔记） | 保留 `COLD_RETENTION_DISTILLED`（默认 90 天）后可打包压缩，压缩包再保留 `COLD_RETENTION_PACKED`（默认 180 天）后 purge + tombstone | 精华已提炼进策展层 |
| 已结束 session，无任何提炼产物 | 保留 `COLD_RETENTION_RAW`（默认 180 天），到期进入 sweep 报告等待人工裁决，不自动 purge | 未提炼即原文是唯一载体 |
| TurnIndex | 随时可删可重建；对应 TurnBlock purge 时同步删除 | 设计既有约定 |

TurnBlock purge 的 tombstone 记录 block id、sequence、content hash 与 session 归属——同 §4.7，它是审计记录与指纹，不是防篡改证明。Pi JSONL 归 Pi 管理，不在本策略范围内。

## 11. 安全约束

1. 模型永远不直接执行任何状态迁移或删除；一切执行走确定性 runner + §4.6 journal，默认 dry-run。
2. `authority: user` 与 `authority: unknown` 的笔记：自动流程只能生成提名，进入 sweep 报告；执行需要用户对该提名的显式确认。
3. 每条自动执行路径都有硬上限（§9 各处）与 14 天新笔记保护期。
4. deprecate 必须携带 reason + evidence（`superseded_by` 或 `contradicted` 事件引用）；无证据的降级只能来自衰减阈值，且衰减降级永不跨级。
5. purge 默认关闭；打开后每一次 purge 必须先写 tombstone，写失败则中止（对称于 `MEMORY_PERSISTENCE_FAILED` 的不伪造原则）。
6. 提炼合并未通过校验或 journal 未确认 create 提交时，源笔记保持 active——不存在"源已降级而合并未落地"的中间态。
7. 账本、journal、提案、报告全部落 `.teamflow/runs/` 或 `state/` 约定位置，外层协调按既有规则只观察元数据。

## 12. CLI 面

```bash
teamflow memory audit [--format json]    # §2 审计（只读，最窄首切片）：分布、悬空证据、重复候选、老化、参数模拟
teamflow memory sweep [--apply]          # §9.4 衰减清扫（默认 dry-run）
teamflow memory consolidate [--topic q] [--apply]   # §9.3 主题提炼
teamflow memory restore <memory-id|permalink>       # 从 deprecated/archived 恢复并重置强度
teamflow memory proposals                # 列出滞留提案与 user/unknown 提名
```

`recall` / `list` / `context` / `read` 行为按 §9.2 调整；`remember` / `remember-global` 改经 §4.8 统一写入 helper 落盘（生成 memory_id、durable evidence、`authority: user` 与时间戳），bash 入口只做参数收集。

## 13. 云端演进兼容性

本设计刻意不依赖存储位置：

- 身份（memory_id）、状态、强度、谱系、证据全部内嵌于 Markdown frontmatter——同步 = 复制文件。
- 删除通过 tombstone 物化为可同步的审计事实，避免"本地删了、云端复活"的经典同步缺陷。
- 事件账本按 `event_id` 幂等并集合并（§4.5），多设备不需要冲突解决协议。
- 只读 `teamflow server` 可直接展示 state/strength 徽标与 tombstone 审计视图，不需要写路径。

记忆上云（远端 Basic Memory 或对象存储后端 + 多设备 ledger 合并）作为独立设计在遗忘机制落地后再行展开。

## 14. 实施阶段

每个阶段按 AGENTS.md 流程实现：test-writer 先写失败测试，coder 最小实现，test-runner 回执，test-writer 审查。

### Phase -1 — 契约冻结

冻结 §4 全部八项：memory_id、事件 schema（含 seq 发号持久化）、durable evidence URI 与**统一 slug 规范**（G1，含四实现点金标向量）、遗留默认值（authority: unknown）、账本契约、事务与恢复契约、归档/tombstone 契约、**笔记形状注册表与统一写入 helper 机制**（G3）。交付契约级测试骨架与 schema 样例，不写任何运行时行为。复审明确要求 G1、G2 在本阶段冻结前解决：G2 已在 §9.1 消除（create 为 journaled 步骤），G1 以 slug 规范冻结落地。

### Phase 0A — 只读 audit（最窄首切片）

只实现 `teamflow memory audit --format json`：真实分布、悬空证据面、重复候选、老化统计、候选参数下的强度 dry-run 直方图，以及作为一等输出的存量 captured 重分类清单（G4 解锁门槛的输入）。零写入、零可见性变更。测试落点：

- `tests/runtime/bin/test_memory_audit.py`
- `tests/runtime/skills/test_memory_candidate_content.py`
- `tests/runtime/skills/test_memory_pipeline_receipts.py`

### Phase 0B — provenance 修复与存量整备

- 新写入落 memory_id + durable evidence（§4.1/§4.2），两条写入路径收敛到 §4.8 统一 helper；写侧 `--type` 改传规范下划线串（G8）。
- 存量笔记幂等补发 memory_id 与 `authority: unknown`；手工 finding 依 §4.8 正向签名重分类为 `authority: user`，并解析 `[recorded]` 行回填 `recorded_at`（G5），仅在完全无时间信号时才降为 `hypothesis`。
- F1 只加 round-trip 兼容测试，无类型串存量迁移。
- **存量库 bootstrapping 门槛（复审 G4）**：§9.1 supersede 自动执行要求 `authority: captured`，而存量一律默认 `unknown`——因此在用户对 audit 产出的 captured 重分类清单做一次性批量确认之前，自动遗忘对整个历史库只能提名、不能执行；Phase 2 的自动能力在此之前仅覆盖 Phase 0B 之后新捕获的笔记。audit 必须把该清单作为一等输出，重分类确认是解锁存量自动遗忘的显式门槛。

### Phase 1 — shadow ledger 与 proposal-only

recall/capture 开始记账（§4.5 完整契约：event_id、watermark、锁、崩溃恢复）；supersede 提案写入 `state/proposals/`。**全程不改变召回可见性。** receipt 增加 `memory_feedback` 字段并开始采集 used/verified/contradicted。

### Phase 2 — 唯一迁移：active → deprecated

按 §4.6 journal 执行 supersede → deprecated（仅 frontmatter，保留文件位置）；recall 默认过滤 deprecated；`context` timeframe 30d→365d 作为单列带测试交付物同批启用（G6）。不实现 archived/purged。

范围限制（G4）：在存量 captured 重分类获用户确认之前，本阶段的自动降级只作用于 Phase 0B 之后新捕获的 `captured` 笔记；存量 `unknown` 笔记只进提名清单。

### Phase 3 — restore 与 dry-run sweep

`memory restore`、sweep 的结算与 dry-run 报告；用 Phase 0A/1 积累的真实 telemetry 校准半衰期 `H` 与阈值 `S_dep`，校准完成后才允许衰减驱动的 deprecate。

### Phase 4 — consolidation

§9.3 主题提炼（依赖 journal 与提案基建已稳定）。

### Phase 5 — archive 与 purge

archived 的物理归档方案（索引一致性 + 按 memory_id 读取）与默认关闭的 purge。

### Phase 6 — 冷存储保留（阻塞中）

等待 §10 前置条件（TEAMFLOW_TASK_ID 产品路径落地、TurnBlock↔receipt/session 关联键）解除后最后实施。

## 15. 验收标准

- 任一策展笔记在任何生命周期状态下都可由 `memory_id` 定位；permalink 变更不影响账本、tombstone、superseded_by 的解析。
- 新写入笔记携带 durable evidence（uri + digest + 内联摘要），脱离运行产物后仍能独立回答"凭什么信"。
- 遗留笔记补发 memory_id 与 `authority: unknown` 的迁移可重复运行且结果幂等；`unknown` 笔记在全部自动路径中获得与 `user` 同级保护。
- 写侧直接输出规范下划线类型串；round-trip 兼容测试证明连字符输入落盘为下划线 frontmatter 且被检测正则命中（覆盖存量与第三方路径）。
- 四个 slug 实现点通过同一组金标向量测试（含 `@foo.git`、`repo--` 等已知分歧用例），任何一处漂移即失败。
- 两条写入路径产出同一规范笔记形状（形状一致性测试）；`teamflow-finding` 存量被形状注册表识别并获得原子级保护。
- 账本结算在人为注入的中途崩溃后重跑收敛：无重复计数、watermark 正确、并发第二写者被锁显式拒绝；seq 计数器在"推进后、事件落盘前"崩溃点上重启后不发重复号，缺口被诊断为崩溃追加而非乱序。
- journal 状态机在 create 已提交/未提交两种崩溃点上分别前滚/放弃，最终不存在"源已降级而新笔记未落地"状态；create 本身携带 commit 标记（不存在绕过 journal 的直接 write-note 路径）。
- 存量库在 captured 重分类确认前，全部自动降级路径对 `unknown` 笔记只产生提名——用构造的存量数据集验证零执行。
- 一次带 supersede 候选的 capture 结束后，被取代笔记退出默认召回，`read` 显示 `superseded_by`，且文件未移动。
- Phase 5 之前不发生任何笔记文件移动；任何 purge 之前 tombstone 先落盘。
- `audit --format json` 是第一个交付物，且在只读前提下产出 §2 全部七个维度与存量 captured 重分类清单。
- 召回基线对照（§2 审计第 7 项）在整备 + 遗忘上线后 top-8 信噪比不降低。

## 16. 待确认参数

1. 四类事件权重与封顶、半衰期 `H`、阈值 `S_dep`、驻留期 `D_dep`/`D_arc`——一律以 Phase 0A/1 真实 telemetry 校准，初值仅供 dry-run。
2. `actor.device` 的派生方式（hostname 易冲突；建议首次运行生成并持久化随机 device id）。
3. `memory_feedback` 由 planner 填写的执行率如何保障（skill 提示 vs receipt 校验强制 related_memory 全覆盖）。
4. deprecated 笔记是否参与 formatter 的去重比对（倾向：参与，防止被取代的说法换皮重生）。
5. 物理归档阶段的索引方案：单独 Basic Memory project、独立 SQLite，还是纯文件 + memory_id 索引。
6. 是否需要 purge 前内容托管（escrow）以支持 tombstone 之外的恢复（倾向：不需要，驻留期已覆盖）。
7. 冷存储打包格式与压缩粒度（按 session 还是按月）。
8. `user`/`unknown` 提名与存量 captured 重分类清单的确认交互形态（CLI 确认 vs server 界面；G4 的批量确认与单条提名是否同一入口）。
9. §4.8 统一写入 helper 的落地形态：直接写 `knowledge/` 文件 + 触发 sync（file-first，倾向），还是 `write-note` + `edit-note` 组合——取决于 Basic Memory CLI 对自定义 frontmatter 字段的写入与保留能力，Phase -1 以实测定案。
10. 统一 slug 规范定案后，bash/TS 读侧对存量"按旧规则派生"的 permalink 前缀是否需要兼容映射（倾向：常规仓名无分歧，不做映射，仅金标测试防新分歧）。
