---
name: service-status-timeline
description: 查看 TODE 服务最近一段时间的运行状态图和简要分析。当用户说“看一下所有服务/内网服务/公司服务/公网服务 最近xx小时/天/7d/30d/90d 的状态”“服务状态截图”“uptime timeline”时触发。
platforms: [linux]
prerequisites:
  commands: [python3]
required_environment_variables:
  - name: STATUS_HUB_BASE_URL
    prompt: TODE Status Hub base URL override
    required_for: non-default endpoint
---
<!--
Role: local-control
Scope: skill:service-status-timeline
Authority: high
Canonical: true
-->

# TODE 服务状态 Timeline

## 目标

按需拉取 TODE Status Hub 的 uptime timeline，生成状态图，并给出简短分析。默认查看“所有服务最近 24 小时”。

底层数据来自生产 Status Hub 公网/内网入口：

```text
https://status.tode.ltd/api/timeline
```

历史上 TODE80 的 `:8092` 曾保留过旧实例，不再作为生产数据源使用；看到它返回数据也只能当作残留副本。不要凭印象编状态；必须先运行本 skill 的脚本。

## 默认调用

当用户说“看一下所有服务/内网服务/公司服务最近 xx 时间的状态”时，直接把原话传给脚本：

```bash
python3 scripts/service_status_timeline.py \
  --text '<用户原话>'
```

```bash
python3 {baseDir}/scripts/service_status_timeline.py \
  --text '<用户原话>'
```

脚本会自动解析：

- scope：`all` / `internal` / `company` / `public`
- 时间：最近 N 小时、最近 N 天、`24h`、`7d`、`30d`、`90d`

如果没有说范围，默认 `all`；如果没有说时间，默认最近 `24h`。

也可以显式调用：

```bash
python3 scripts/service_status_timeline.py --scope internal --hours 24
python3 scripts/service_status_timeline.py --scope all --range 7d
```

## scope 规则

| 用户说法 | scope | 目标 |
|---|---|---|
| 所有服务 / 全部服务 / all | `all` | Status Hub 全部 target |
| 内网服务 / 内部服务 / 基础设施 | `internal` | `internal-infra`、`service-docker` |
| 公司服务 | `company` | 当前按全部公司监控目标处理，等同 `all` |
| 公网服务 / 外网服务 / 对外服务 | `public` | `external-public`、`tode20-public` |

如果用户说法含混，按最安全的宽口径处理：`company → all`。

## 输出规则

脚本 stdout 是 JSON，schema 为 `service_status_timeline.v1`。发送给用户时：

1. 不要在最终回复里写 `MEDIA:<path>`。Feishu 群聊里自动回复的本地媒体会作为 reply 图片发送，当前会显示 `Media failed`。
2. 必须先用 `message send` 显式发送图片到当前群聊；图片路径优先用 `media.png_path`，如果 PNG 为空或生成失败再用 `media.svg_path`。
3. `chat_id` 来自本轮系统注入的 Conversation info，例如 `chat:oc_xxx`；原样传给 `--target`。
4. 图片发送命令成功后，再用最终回复给 3～6 条简短分析：窗口、目标数、异常/降级目标、最差目标、是否有维护或 unknown。最终回复不要再包含 `MEDIA:` 行。
5. 不要输出完整 JSON。
6. 不要泄露任何 token、cookie、Authorization 或 webhook。
7. 如果脚本失败，直接报告失败命令、退出码和 stderr 摘要，不要伪造分析。

推荐发送图片命令：

```bash
使用 Hermes 当前会话提供的媒体发送工具，把 `<media.png_path>` 发送回当前聊天。
```

推荐最终回复形态：

```text
最近 24h，范围：内网服务，共 8 个目标。
- 当前异常：0 个；窗口内出现过异常：1 个。
- 最差目标：xxx，uptime 99.31%，主要原因：HTTP 状态异常。
- 其余目标整体正常。
```

## 注意

- 公司代理连通性监控（HTTP/HTTPS/SOCKS5 代理、JMS 出口、US-Home 出口、x.com/facebook.com/google.com/openai.com 探测）详见 `references/proxy-connectivity-monitoring.md`。代理目标应归入 `internal-infra` / 公司内部基础设施服务，不要单独建 UI 分组。
- Status Hub 页面或 `/metrics` 打开慢时，按 `references/status-hub-performance-triage.md` 排查：先测本机/公网端点，再测 SQLite 大表查询与 VM 查询；优先索引、汇总表或短 TTL 进程内缓存，只有多实例/跨进程共享缓存需求明确时再引入 Redis。代码修改默认交 Codex CLI，Saber 负责统筹与验收。
- Status Hub 生产 render 接口本身可用于全量截图，但本脚本会从 JSON 生成过滤后的图，以支持“内网/公网/公司”和任意小时窗口。脚本生成 SVG/PNG 时，必须把 JSON 里的 raw group key（如 `external-public`、`service-docker`、`tode20-public`）映射成网页一致的中文分组名，只在分析 JSON 中保留原始 group 值。
- 时间窗口不是自然日；“最近 12 小时”就是以当前触发时刻为结束点向前 12 小时。
- 对于小于 24h 的窗口，脚本会拉取 `24h` 数据后按 bucket 过滤；对于 2～7 天拉 `7d`，8～30 天拉 `30d`，31～90 天拉 `90d`。

## 全局 unknown / 监控中断排查

当用户问“只有某台服务器故障，为什么所有服务都 unknown/未知”时，不要直接判定业务全挂。先按下面区分 **服务故障** 与 **监控采样中断**：

1. 拉 `/api/timeline?range=24h` 或 `7d`，检查异常时段每个 bucket 的 `sample_count`。
   - 如果所有 target 在同一时段 `sample_count=0` 且 bucket `status=unknown`，含义是 Status Hub 没有采样数据，不是所有服务同时故障。
   - 如果 `sample_count>0` 且存在 `unknown_count` / `down_count` / `failures`，再按具体 failure label 判断业务或依赖故障。
2. 对 Status Hub 自身查 `/metrics` 里的 `status_hub_uptime_seconds` 与当前时间反推进程启动时间；若启动时间紧贴 unknown 结束点，优先结论为 `status-hub`/探测循环在该窗口停摆或重启。
3. 结合部署事实：生产 `status.tode.ltd` / Status Hub 已运行在 TODE41；TODE80 上的旧 `:8092` 残留实例已退役。若看到 TODE80 返回 Status Hub 数据，应先当作陈旧副本排查，不要作为生产状态结论。
4. 给用户的措辞要明确：`unknown` = “监控系统那段时间没有数据，无法判断状态”，不是“服务已宕机”。建议修法优先包括采样器去单点、timeline 增加 `monitoring outage` 语义、展示最后已知状态但标注监控中断。


## 媒体路径注意

输出文件默认写入 `/tmp/hermes-service-status/`。不要依赖最终回复里的 `MEDIA:` 行；飞书聊天必须使用 Hermes 当前会话的媒体发送工具显式发送。
