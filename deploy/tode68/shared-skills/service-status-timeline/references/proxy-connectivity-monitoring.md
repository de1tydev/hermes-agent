# Status Hub 代理连通性监控

## 适用场景

当 Master 要求把公司代理、HTTP/HTTPS/SOCKS5 代理、外部网站连通性、JMS 出口、US-Home 出口纳入 Status Hub 时，按这里处理。

## 生产事实

- Status Hub 生产入口：`https://status.tode.ltd`
- 生产主机：TODE41（`status.tode.ltd -> 192.168.50.41`）
- Status Hub 容器本地监听：`127.0.0.1:8092`
- 公司代理地址：`192.168.50.12:7890`
- 代理 URL 配置：`STATUS_HUB_PROXY_URL=http://192.168.50.12:7890`
- 代理出口目标类型：`proxy_exit`
- 代理目标 source：`proxy-seed`
- 代理目标分组：`internal-infra` / 公司内部基础设施服务
- 聚合探测轮数：`STATUS_HUB_PROXY_EXIT_ROUNDS=3`

不要把代理目标单独建成 `proxy-connectivity` UI 分组。若历史配置里出现 `proxy-connectivity`，应兼容映射到 `internal-infra`。

## 代表性探测目标

| target_id | 展示名 | 目标 URL | 出口语义 | 分组 |
|---|---|---|---|---|
| `proxy-jms-exit` | 代理 JMS 出口 | `https://x.com/`, `https://www.facebook.com/` | JMS / 非 US-Home | `internal-infra` |
| `proxy-us-home-exit` | 代理 US-Home 出口 | `https://www.google.com/generate_204`, `https://openai.com/` | US-Home | `internal-infra` |

## 配置格式

`STATUS_HUB_PROXY_TARGETS` 使用 env seed。生产优先使用聚合出口格式：

```text
id|display_name|url1,url2[|group][|visibility][|enabled];...
```

单 URL 旧格式仍兼容：

```text
id|display_name|scheme|host|path[|group][|visibility][|enabled];...
```

生产示例：

```text
proxy-jms-exit|代理 JMS 出口|https://x.com/,https://www.facebook.com/|internal-infra;proxy-us-home-exit|代理 US-Home 出口|https://www.google.com/generate_204,https://openai.com/|internal-infra
```

## 实现规则

- 出口级监控应使用 `proxy_exit`，一个出口只暴露一个状态；不要再为同一出口的两个 URL 分别暴露多个 public target。
- `proxy_exit` 的 `Path` 存放逗号分隔的完整 URL 列表，每个 URL 按 `STATUS_HUB_PROXY_EXIT_ROUNDS` 多轮探测，`LatencyMS` 取成功样本平均值。
- 聚合出口只在所有样本都失败时标记 `down`；存在部分成功样本时保持 `up`，并在 raw 中记录 `attempts`、`success_count`、`failure_count`，避免单个站点/轮次抖动误报整个出口挂掉。
- 新 proxy seed 生效时要软删除不再出现在 `STATUS_HUB_PROXY_TARGETS` 中的旧 `proxy-seed` target，确保状态页只保留出口级状态。
- `STATUS_HUB_PROXY_URL` 支持 `http` / `https` / `socks5`；裸 `host:port` 可默认补 `http://`。
- 配置了 proxy targets 但未配置 proxy URL 时，target 应显式 down，failure reason 用 `proxy_not_configured`，不要静默跳过。
- HTTP 2xx / 3xx / 401 / 403 都可视为目标可达；openai/facebook 这类站点可能返回 403，但仍能证明代理链路可用。
- metrics 应为 `proxy_exit` 输出 `status_target_up`、`status_target_probe_duration_seconds`、`status_target_failure_reason`。
- vmalert 应有 `StatusProxyExitDown` P2 规则；代理出口全部样本失败或代理无法联通时，经 Alertmanager / Feishu adapter 发送飞书 webhook 告警。通用 `StatusTargetDown` 应排除 `proxy_exit`，避免重复告警。
- timeline failure label 应覆盖 `proxy_not_configured` 与 `invalid_proxy_config`。

## 验证清单

本地：

```bash
cd /home/liao/code/tode-monitoring-mvp/services/status-hub && go test ./...
cd /home/liao/code/tode-monitoring-mvp && scripts/build-custom-services.sh --check
cd /home/liao/code/tode-monitoring-mvp && scripts/smoke-status-hub.sh
```

线上：

```bash
curl -fsS http://127.0.0.1:8092/healthz
curl -fsS http://127.0.0.1:8092/metrics | grep 'proxy-'
curl -fsS 'http://127.0.0.1:8092/api/timeline?range=24h'
curl -fsS 'https://status.tode.ltd/api/timeline?range=24h'
```

检查点：

- `status_hub_build_info` commit 与发布 commit 一致。
- 只存在两个 `proxy-*` active target：`proxy-jms-exit` 与 `proxy-us-home-exit`。
- target type 为 `proxy_exit`。
- group 为 `internal-infra`。
- metrics 中 `status_target_probe_duration_seconds` 是多 URL、多轮成功样本平均延迟。
- raw snapshot 包含 `rounds=3`、`url_count=2`、`attempts=6`、`success_count`、`failure_count`。
- `StatusProxyExitDown` vmalert rule 存在且 health 为 `ok`。
- latest bucket 为 `up` 或能解释的降级状态。

## 生产部署注意

- 生产 release 使用 `/opt/tode-monitoring/current -> /opt/tode-monitoring/releases/<release>`，不是 git checkout 工作树。
- 预构建 Go 二进制 `services/status-hub/status-hub` 需要提交，Dockerfile 直接 COPY。
- 生产机不一定安装 Go；不要为了 smoke 在生产安装构建工具链。本地完成源码/单测/smoke，生产只做运行态验证。
- `.env` 中 token/password/webhook/app secret 等敏感值一律不要输出。代理地址不是 secret。
