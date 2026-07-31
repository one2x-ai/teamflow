# Teamflow Web Console 详细设计

状态：Phase A 已实现并验证（669 tests / 965 subtests 通过）；Phase B–D 待实现

---

## 1. 背景与结论

`teamflow server` 目前是一个只读记忆浏览服务：Bun + `Bun.serve`，两个页面的 HTML/CSS/JS 按页面拆到 `server/src/ui/`，请求时装配返回，无构建步骤。

两点现实推动它演进：

1. **前端已到复杂度上限**。纯 JS + 手写 DOM 操作足以支撑"卡片列表 + 详情"，但无法支撑消息流、增量渲染、工具调用折叠这类有状态 UI。
2. **外层 loop 需要可视化**。`opencode` 是外层 loop，跟随持久化任务会话、自己管自己；内层任务调度由 pi 管理。它已暴露完整 REST API（162 端点）与 SSE 事件流，但其自带界面与 teamflow 的记忆视图是两个割裂的入口。

结论：把 `teamflow server` 升级为**单一入口的本地 Web Console**，用 Svelte 5 + Tailwind 4 + shadcn-svelte 重写前端，并通过服务端反向代理集成 opencode 的会话数据。

## 2. 目标与非目标

### 2.1 目标

- 前端全量 TypeScript，按模块组织，不再有裸 JS 与内联字符串模板。
- 渲染走 Svelte 5（runes），样式走 Tailwind 4，组件走 shadcn-svelte（复制式，零运行时依赖）。
- 后端按职责拆模块：`config` / `http` / `memory` / `opencode` / `static`。
- 提供两个页面：**Memory**（记忆浏览，替换现有两页）与 **Chat**（opencode 会话查看）。
- opencode 走**服务端反向代理**：浏览器只面对 teamflow 一个源，Basic Auth 凭证留在服务端进程内。
- 页面风格简洁明朗：中性灰阶、细边框、克制的动效。

### 2.2 非目标

- **不做登录**。本地放开访问；上线后接飞书登录，届时在 `http/` 层加中间件，不影响本设计。
- **不管理 opencode 进程**。opencode 是外层 loop，自己管自己；teamflow 只连接已有实例，不拉起、不停止。
- **不重写 opencode 的全部界面**。第一版只做会话列表与消息查看，不做 pty 终端、MCP 管理、provider OAuth。
- **不做写入**。Chat 第一版只读；发消息留到 Phase D 之后单独评估。
- **不引入前端路由库**。两个页面用 `$state` 驱动的视图切换即可。
- **不做虚拟滚动**。本机首会话 645 条消息 / 7.3 MB，用"最近 N 条 + 加载更早"分页处理。

## 3. 核心原则

### 3.1 服务路径无构建依赖，但允许预构建产物

`bin/server` 直接用 Bun 跑 TypeScript 源码，这一点不变。Svelte 必须构建，产物落在 `server/web/dist/`，由 `bootstrap.sh` 在安装时生成、`doctor.sh` 校验存在。运行时只读静态文件，不在请求路径上调用任何打包器。

README 现有的"零 npm 运行时依赖"措辞需要修正为准确表述：**运行时只依赖 Bun 内置与预构建静态资源；构建期依赖仅在 teamflow 仓库内需要。**

### 3.2 凭证不进浏览器

opencode 的 Basic Auth 凭证由 teamflow 服务端从环境变量或 CLI 参数读取，代理时附加到上游请求。浏览器侧的任何响应、任何 JS 变量都不包含凭证。这条约束在接飞书登录后依然成立，因此前端无需改动。

### 3.3 反代对 SSE 透明

opencode 的 `/event` 返回 `text/event-stream` 并已设 `x-accel-buffering: no`，说明上游预期被反代。代理必须流式转发、不缓冲、不改写 chunk 边界，并在客户端断开时关闭上游连接。

### 3.4 只读语义由框架保证，而非字符串检查

现有测试断言响应体含 `textContent`、不含 `innerHTML`。Svelte 编译后这些标识符不存在，但 `{value}` 默认转义，安全性更强。测试改为**行为验证**：注入 `<script>alert(1)</script>` 到记忆标题与消息文本，断言渲染结果被转义。

### 3.5 外层 loop 视角不越界

`observe-inner-loop` skill 规定外层 loop 只读 phase 元数据与产物存在性，不读 session 文件、prompt、reasoning、response。

Chat 页面展示的是 **opencode 自己的会话**，即外层 loop 自身的对话记录，不是 pi 内层角色的会话。这与该约束不冲突：外层 loop 读自己的历史是正当的。设计上明确区分：

- `.teamflow/sessions/`（pi 内层会话）— **Console 不读**
- opencode `/session`（外层 loop 会话）— Chat 页面数据源

## 4. 总体架构

```text
浏览器 (单一源 127.0.0.1:7324)
  │
  ├── GET /                     → web/dist/index.html (Svelte SPA)
  ├── GET /assets/*             → web/dist/assets/*
  │
  ├── GET /api/memories         → basic-memory CLI（现有）
  ├── GET /api/memory           → basic-memory CLI（现有）
  │
  └── /api/oc/*                 → 反向代理 ──┐
                                             │  + Basic Auth
                                             ▼
                                   opencode serve (外层 loop 自管)
                                     GET /session
                                     GET /session/{id}/message
                                     GET /event  (SSE)
```

## 5. 组件边界

### 5.1 后端负责

| 模块 | 职责 |
|------|------|
| `config.ts` | CLI 参数与环境变量解析；host/port/dir/opencode 连接信息 |
| `http/router.ts` | 路由表与分发 |
| `http/response.ts` | `json()` / `html()` / `notFound()` / `error()` |
| `memory/basic-memory.ts` | `basic-memory tool ... --local` 子进程封装 |
| `memory/scope.ts` | `--dir` 仓库 slug 推导与 permalink 前缀过滤 |
| `memory/routes.ts` | `/api/memories`、`/api/memory` |
| `opencode/config.ts` | 上游 URL 与凭证解析，缺失时的降级策略 |
| `opencode/proxy.ts` | `/api/oc/*` 透明代理，含 SSE 流式转发 |
| `opencode/types.ts` | `Session` / `Message` / `Part` 类型（与前端共享） |
| `pages.ts` | Phase A–B 过渡期的页面装配（读 `src/ui/` 资源包文档外壳）；Phase C 随 `src/ui/` 一并删除 |
| `static.ts` | 服务 `web/dist/`，SPA fallback 到 `index.html`（Phase B 新增） |

### 5.2 前端负责

| 模块 | 职责 |
|------|------|
| `lib/api.ts` | 类型化 fetch 客户端；SSE 订阅 |
| `lib/types.ts` | 与后端共享的 TS 类型 |
| `lib/stores.svelte.ts` | 视图状态（当前页、选中项、分页游标） |
| `routes/Layout.svelte` | 侧栏导航 + 内容区 |
| `routes/Memory.svelte` | 记忆列表、搜索、分页、详情 |
| `routes/Chat.svelte` | 会话列表、消息流、SSE 增量 |
| `lib/components/ui/*` | shadcn-svelte 组件（复制入库，非依赖） |

### 5.3 opencode 负责

会话持久化、消息生成、工具执行、事件广播。teamflow 不复制、不缓存、不改写其数据。

## 6. opencode API 契约（实测）

### 6.1 会话

```
GET  /session              → Session[]
GET  /session/{id}         → Session
GET  /session/{id}/message → MessageWithParts[]
GET  /event                → SSE (text/event-stream)
```

`Session` 字段：

```ts
interface Session {
  id: string            // "ses_..."
  slug: string
  projectID: string
  directory: string
  path: string
  title: string
  agent: string         // "build" 等
  version: string
  model: { id: string; providerID: string; variant: string }
  summary: { additions: number; deletions: number; files: number }
  tokens: { input: number; output: number; reasoning: number; cache: {...} }
  cost: number
  time: { created: number; updated: number }
}
```

### 6.2 消息与 Part

消息为 `{ info, parts }`。`info` 含 `id / sessionID / role / agent / model / time`。

实测 part 类型分布（本机首会话 645 条消息）：

| type | 计数 | 关键字段 | Phase D 渲染 |
|------|------|----------|--------------|
| `tool` | 639 | `callID`, `tool`, `state` | 折叠卡片 |
| `step-start` | 609 | `snapshot` | 忽略 |
| `step-finish` | 607 | `reason`, `cost`, `tokens` | 忽略 |
| `text` | 437 | `text` | 气泡 |
| `reasoning` | 214 | `text`, `time` | 折叠块 |
| `patch` | 151 | — | 忽略（Phase D 之后） |

第一版只渲染 `text` / `tool` / `reasoning`。

### 6.3 认证

Basic Auth，`www-authenticate: Basic realm="Secure Area"`。凭证由上游进程的 `OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD` 决定；**未设置时每次启动随机生成**，因此 teamflow 必须由用户显式提供，不能猜测。

### 6.4 数据量约束

首会话 645 条消息序列化后 7.3 MB。`GET /session/{id}/message` 无分页参数，因此分页在 **teamflow 代理层或前端**完成：默认取尾部 N 条，向前翻页时扩大窗口。

## 7. 连接配置

优先级从高到低：

1. `--opencode-url <url>` 与 `--opencode-user` / `--opencode-password`
2. `TEAMFLOW_OPENCODE_URL` / `TEAMFLOW_OPENCODE_USERNAME` / `TEAMFLOW_OPENCODE_PASSWORD`
3. 缺失 → Chat 页面显示"未连接 opencode"引导，Memory 页面正常工作

**降级不是失败**：记忆浏览不依赖 opencode，缺少连接信息时服务照常启动，只有 `/api/oc/*` 返回 503 并附结构化原因。

## 8. 分阶段实现

### Phase A — 后端 TS 模块化与 opencode 反代 ✅ 已完成

- 拆分 `server.ts` 为 `config` / `http` / `memory` / `opencode` / `static`
- 实现 `/api/oc/*` 反向代理：透明转发方法、路径、查询、body
- SSE 流式转发：不缓冲、客户端断开时关闭上游
- 凭证只在服务端；任何响应不含凭证
- 上游不可用时返回结构化 503，不是空响应
- 现有 `/api/memories`、`/api/memory` 行为不变

验收：现有 HTTP 行为测试全绿；新增反代契约测试（透明性、SSE 无缓冲、凭证隔离、503 降级）。

实现结果：`server.ts` 348 → 51 行；新增 10 个模块共 1027 行（含注释）。页面装配抽到 `pages.ts`，Phase C 时随 `src/ui/` 一并删除。端到端验证：反代真实 opencode 取回 100 个会话，SSE 保留 `x-accel-buffering: no`，响应头与响应体均无凭证与认证头。

### Phase B — 前端工程与静态服务

- `server/web/`：Svelte 5 + Tailwind 4 + shadcn-svelte + Vite
- `bootstrap.sh` 与 `install.sh` 构建 `web/dist/`；`doctor.sh` 校验产物
- `static.ts` 服务产物，SPA fallback
- 保留现有 `src/ui/` 直到 Phase C 完成后删除

验收：构建产物存在且可服务；`bun run typecheck` 覆盖前端；doctor 校验通过。

### Phase C — Memory 页面

- 列表：Card 网格、Input 搜索、Badge 类型、Skeleton 加载、分页
- 详情：可读正文，返回列表
- 替换 `src/ui/list.*` 与 `src/ui/detail.*` 并删除

验收：端点行为不变；XSS 行为验证（注入脚本被转义）；只读（无写入控件）；响应式布局。

### Phase D — Chat 页面

- 会话列表：标题、agent、模型、时间、token 概要
- 消息流：`text` / `tool` / `reasoning` 三类 part
- 分页：默认尾部 N 条，"加载更早"
- SSE：订阅 `/api/oc/event`，增量追加

验收：三类 part 正确渲染；分页不重复不丢失；SSE 断线重连；未连接 opencode 时显示引导而非报错。

## 9. 测试策略

测试跟随被测代码（仓库既有约定）：

| 位置 | 内容 |
|------|------|
| `server/tests/` | 后端模块、反代契约、静态服务、前端产物契约 |
| `tests/` | `install.sh` / `bootstrap.sh` 的构建接入与产物校验 |

**行为优先于文本**。现有 XSS 断言查源码字符串，Phase C 起改为注入渲染验证。反代测试用真实 SSE 上游（可用最小 Bun 服务模拟），不 mock fetch。

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 构建步骤进入安装链路 | 首次装机变慢、需网络 | 只在 `bootstrap.sh`/`install.sh` 构建；`doctor.sh` 明确报缺失产物 |
| opencode API 演进破坏契约 | Chat 页面失效 | 类型集中在 `opencode/types.ts`；仅依赖 `/session`、`/message`、`/event` 三组稳定端点 |
| 7.3 MB 单会话拖慢首屏 | 页面卡顿 | 分页取尾部 N 条；不做全量渲染 |
| "零运行时依赖"承诺失真 | 文档与实现不符 | Phase B 同步修正 README 措辞 |
| Svelte 编译使现有 XSS 断言失效 | 安全回归无人发现 | Phase C 改为行为验证，不保留失效的字符串断言 |
| 上游凭证泄漏到前端 | 本地服务被越权访问 | 凭证只在服务端读取与附加；测试断言响应体与产物不含凭证 |
