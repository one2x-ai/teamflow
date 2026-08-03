# 容器 Sidecar 部署设计（单 Pod 双容器）

本文档描述 Teamflow OpenCode Web 运行时的单 Pod / 双容器部署模型。README 只保留操作者快速上手；本文档承载完整的部署与运行时设计。

## 1. 总体拓扑

一个 Kubernetes Pod 内运行两个容器，共享同一个网络命名空间（network namespace）：

- **Teamflow 容器**：直接以 exec-form 运行 `opencode web`，只绑定回环地址 `127.0.0.1:13000`（loopback only）。容器内没有 shell supervisor、没有 Node 健康网关、没有第二个进程；进程与生命周期完全由 Kubernetes 管理（重启策略、探针、资源限制都由 Pod spec 声明）。
- **Caddy sidecar 容器**：占用公开端口 `:3000`（port 3000），反向代理到同 Pod 内 Teamflow 容器的 `127.0.0.1:13000`。因为两个容器共享网络命名空间，Caddy 通过 loopback 即可到达 OpenCode，无需 Service 或额外网络配置。

Caddy 不进 Teamflow 镜像；它是独立的 sidecar 镜像，由部署清单（Deployment/StatefulSet）装配。

## 2. 健康探针合成（Caddy sidecar 负责，Teamflow 不重实现）

公开端口的健康探针合成完全属于 Caddy sidecar 的职责，Teamflow 代码不再实现该行为：

- Caddy 只对**精确的根路径** `GET /` 与 `HEAD /` 请求做 User-Agent 匹配：
  - UA 前缀匹配 `kube-probe`（Kubernetes 探针）。
  - UA 前缀匹配 `ELB-HealthChecker`（AWS ELB 健康检查）。
- 匹配命中时，Caddy 直接返回合成的最小化 HTTP 200 响应，正文为字面量 `ok`（`text/plain`）。该响应**不转发**给上游 OpenCode，也**不注入任何凭证**——伪造健康检查 UA 的请求方只会得到字面量 `ok`，永远拿不到需要认证的 OpenCode HTML。
- 其余一切普通 HTTP 流量与 WebSocket 升级请求（`Connection: Upgrade`）都**原样转发**到 `127.0.0.1:13000`，包括 Basic Auth 头与 SSE/WebSocket 流式语义，不缓冲、不改写。

## 3. 认证模型

Basic Auth 保留在 OpenCode 自身：Caddy 只透传 `Authorization` 头，不校验也不合成认证。

- `OPENCODE_SERVER_USERNAME` 与 `OPENCODE_SERVER_PASSWORD` 两者都是**必需的运行时环境变量**，注入到 Teamflow 容器。
- 两者缺省时 OpenCode 每次启动随机生成不可预测的凭证，无法获得稳定登录；因此部署清单必须通过 Secret（如 `envFrom: secretRef`）显式提供这两个变量。
- 凭证只在运行时注入，绝不写入镜像、Dockerfile、文档或命令历史。

## 4. 进程与生命周期

- Teamflow 容器的唯一进程是 `opencode web --hostname 127.0.0.1 --port 13000`，PID 1 语义、信号处理、崩溃重启全部由 Kubernetes 负责；容器内**不允许**再引入 supervisor 或包装脚本。
- 健康探针分两层，两个容器各自独立声明：
  - **Caddy 容器**：HTTP liveness/readiness 探针指向公开端口 `:3000` 的合成健康路由（kube-probe UA 命中时由 sidecar 直接返回 `ok`，不转发到上游），避免未认证探针打到 loopback 上的 OpenCode。
  - **Teamflow（OpenCode）主容器**：带认证的 exec liveness/readiness 探针，在容器内对 `127.0.0.1:13000` 发起认证请求，只有 OpenCode 进程真实存活且认证通过才算就绪。
- 两层探针缺一不可：当 OpenCode 宕掉时，即使 Caddy 仍在合成 `ok`，主容器 exec 探针失败也会让整个 Pod 变为 NotReady——Caddy 单层探针无法在上游故障时维持 Pod Ready。

## 5. 持久化

一个 PVC 同时承载两类状态，挂载到 `/workspace`：

- `/workspace/opencode`：OpenCode 自身状态（会话、配置、缓存）。
- `/workspace/teamflow`：Teamflow Git 工作区（镜像构建时检出的工作树，运行时 `git pull` 或 Agent 产生的改动）。

Pod 替换或重启后，由于 PVC 重新挂载到相同路径，上述数据全部**保留**（preserve / persist）；镜像层是可抛弃的，状态只活在卷上。

### init container 与 seed-once 语义

镜像构建时已在 `/workspace/teamflow` 内含一份 seed 检出（`git clone` + checkout 的完整工作树）。首次部署时把 PVC 挂载到 `/workspace` 会**遮蔽**镜像内的这份 seed，因此使用一个 **init container**（`init-workspace.sh`）完成 **seed-once** 初始化：

- 仅当持久卷上的 `/workspace/teamflow` 缺少 `.git` 时，才把镜像内的 seed 检出（`/workspace/teamflow/.`）复制到持久工作树；已存在 `.git` 则原样跳过，保留卷上用户与 Agent 的既有数据。
- 旧版本遗留的 OpenCode home 条目只做一次性迁移（migrate once），完成后不再重复执行。
- 复制前检查目标状态，发现不安全的覆盖风险时拒绝执行，绝不强行覆盖卷上已有内容。
- init container 只在 Pod 启动时运行一次；后续容器重启（restart）不会重放 seed，卷上状态原样保留。

注意：init **不在运行时克隆仓库**，也**不播种任何初始 OpenCode 配置**——seed 内容全部来自镜像层。

### 独立（standalone）运行

不带 sidecar 独立运行镜像时（standalone `docker run`），需要区分两种网络契约：

- **默认命令是 loopback 工作负载契约**：镜像 CMD 为 `opencode web --hostname 127.0.0.1 --port 13000`，只绑定容器内回环地址。这是为 sidecar 拓扑设计的——同 Pod 的 Caddy 经共享网络命名空间访问它。此时即使 `docker run -p 13000:13000` 发布端口，流量指向容器网络接口，也到不了回环地址上的进程。
- **本地直接访问**必须显式覆盖命令，放开绑定地址：

```bash
docker run -e OPENCODE_SERVER_USERNAME=$OPENCODE_SERVER_USERNAME -e OPENCODE_SERVER_PASSWORD=$OPENCODE_SERVER_PASSWORD -p 13000:13000 \
  teamflow-opencode-web opencode web --hostname 0.0.0.0 --port 13000
```

持久化方面，镜像本身不会创建外部持久卷。如需持久化，必须挂载（volume / 挂载）一个已按相同目录布局（`/workspace/opencode` 与 `/workspace/teamflow`）初始化的 volume，或提供等价的初始化步骤；否则容器重建后状态丢失。

## 6. 发布策略

### rollout 顺序

PVC 是 ReadWriteOnce（RWO），同一时刻只能被单个 Pod 挂载，因此 Deployment 采用单副本 **`Recreate`** 策略，HPA 固定为 1 副本，**不做滚动重叠**：

1. 先确认 PVC 与 Secret（`OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD`）已就绪。
2. rollout 时 `Recreate` 策略先终止旧 Pod，再创建新 Pod；不存在“新 Pod 就绪后旧 Pod 才终止”的重叠窗口，每次发布都有一段短暂的替换停机时间（brief replacement downtime），属于预期行为。新版本 Teamflow 容器与 Caddy sidecar 作为一个原子单元一起替换。
3. 新 Pod 就绪依赖两层探针同时通过：Caddy 的 HTTP 探针（端口 `:3000` 合成 `ok`）与主容器对 `127.0.0.1:13000` 的认证 exec 探针；init container 的 seed-once 逻辑保证数据不被重置。
4. rollout 完成后验证：经公开端口带凭证访问返回 OpenCode HTML，匿名访问返回 401，伪造健康 UA 只返回 `ok`。

### rollback 检查

rollback 前确认：卷上的 `/workspace/teamflow` 检出与目标版本兼容（必要时先记录当前 ref）；Secret 未随发布变更。rollback 只替换 Pod 模板镜像，不触碰 PVC；回滚后重复第 4 步验证，并确认 WebSocket 与普通 HTTP 流量均恢复正常。
