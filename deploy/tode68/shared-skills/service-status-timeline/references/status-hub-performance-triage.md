# Status Hub 性能排查与加速

用于 Status 页面打开慢、`/metrics` 慢、timeline API 慢等问题。

## 关键判断

Status Hub 首页不是纯静态页。请求路径会组装状态视图：

- 读取 active targets。
- 读取每个 target 的 latest probe snapshot。
- 聚合每个 target 的最后一次成功时间。
- 旧实现曾对 docker workload target 实时查询 VictoriaMetrics；P0 后应只读取 probe loop 写入的 snapshot。

因此页面慢时，先把瓶颈分清：SQLite 大表查询、缓存是否命中、是否误回退到旧 VM 实时查询路径、Nginx/TLS/公网链路、还是渲染/JSON 体积。

## 低风险排查顺序

1. 在生产机本机分别测 `/`、`/metrics`、`/api/timeline?range=24h`、`/api/timeline/render?range=24h`，再测公网域名。
   - 本机也慢：优先查应用/SQLite/VM。
   - 只有公网慢：优先查 Nginx/TLS/网络。
2. 查 SQLite 数据规模与索引：
   - `probe_snapshots` 行数。
   - `PRAGMA index_list(probe_snapshots)`。
   - 重点 benchmark：
     ```sql
     SELECT target_id, MAX(checked_at)
     FROM probe_snapshots
     WHERE status = 'up'
     GROUP BY target_id;
     ```
   - 另测 latest snapshot 查询：
     ```sql
     SELECT *
     FROM probe_snapshots
     WHERE id IN (SELECT MAX(id) FROM probe_snapshots GROUP BY target_id);
     ```
3. 代表性测 VictoriaMetrics instant query。若单次 VM 查询只有毫秒级，先不要把锅扣给 VM。
4. 任何索引/DDL 先复制生产 SQLite 到 `/tmp` 副本上 benchmark，不要直接改生产 DB。

## 已验证的索引收益

一次生产副本测试中：

- `probe_snapshots` 约 210 万行。
- `LastSuccessfulProbeTimes` 类查询无合适索引时约 3.2～5.1 秒。
- 在副本上新增：
  ```sql
  CREATE INDEX IF NOT EXISTS idx_probe_snapshots_status_target_checked_at
  ON probe_snapshots(status, target_id, checked_at);
  ```
- 同查询降到约 0.29 秒。

可同时测试：

```sql
CREATE INDEX IF NOT EXISTS idx_probe_snapshots_target_id
ON probe_snapshots(target_id, id);
```

它主要改善 latest snapshot 的 `GROUP BY target_id` 路径；收益要用副本实测确认。

## 当前 P0 实现

生产 Status Hub 已采用第一阶段优化：

- `probe_snapshots(status, target_id, checked_at)`：加速 `LastSuccessfulProbeTimes`。
- `probe_snapshots(target_id, id)`：辅助 latest snapshot 路径。
- 首页 `publicTargets` 与 `/metrics` 使用 10s 进程内 TTL cache；默认 `STATUS_HUB_VIEW_CACHE_TTL=10s`，应低于 `status_hub` scrape interval（当前 15s）。
- `docker_workload` 首页与 `/metrics` 不再在请求路径实时查询 VictoriaMetrics，而是读取 probe loop 写入的最新 snapshot。
- Docker snapshot raw 保留 `running`、`expected`、`last_oom_unix`，页面展示运行数、最近 OOM、延迟、最近检查、最近成功和非 up 原因。

上线后典型验证口径：

- 本机首页首开约 1～1.5s，TTL 内二次打开约毫秒级。
- `LastSuccessfulProbeTimes` 查询在 210 万行级别约 0.27～0.30s。
- 公网首页应约 1～2s；若又回到 6～10s，先查索引是否存在、cache TTL 是否为 0、以及是否误回退到旧 release。

## Timeline 查询优化现状

`/api/timeline` 与 `/api/timeline/render` 已采用两级优化：

- 先把 Go 侧读取 200 万 raw snapshot 再分桶，改成 SQLite 侧 `TimelineBucketStats` 按 bucket 聚合，只把聚合结果交给 Go 渲染。
- 因 `checked_at` 是 RFC3339Nano 文本，不能直接依赖文本顺序处理精确秒/小数秒边界；代码注册 deterministic SQLite scalar `status_hub_unix_nanos(checked_at)`，用纳秒整数保证 `[start,end)` 语义。
- 生产索引：
  ```sql
  CREATE INDEX IF NOT EXISTS idx_probe_snapshots_checked_at_nanos_id
  ON probe_snapshots(status_hub_unix_nanos(checked_at), id);

  CREATE INDEX IF NOT EXISTS idx_probe_snapshots_target_checked_at_nanos
  ON probe_snapshots(target_id, status_hub_unix_nanos(checked_at));
  ```
- timeline JSON/render 均走 `STATUS_HUB_VIEW_CACHE_TTL` 的进程内 keyed cache；生产当前设为 60s，默认示例仍为 10s。

生产验证口径：

- 24h cold JSON/render 约 0.47s；cache hit 约 1～20ms。
- 7d cold JSON/render 约 3s；cache hit 约 1～20ms。
- 30d/90d cold 仍可能 8～11s，主要受长窗口全量历史聚合与响应渲染影响；cache hit 约 1～20ms。
- 若要求 30d/90d cold 也稳定亚秒级，下一刀不是 Redis，而是写入时维护 `timeline_bucket_rollup` 一类汇总表，让长窗口查询退化为读取已聚合 bucket。

## 加速方案优先级

1. **索引 / schema migration**：首页先补 `(status, target_id, checked_at)`；timeline 再补 `status_hub_unix_nanos(checked_at)` expression index。
2. **请求路径短 TTL 缓存**：首页、`/metrics`、timeline JSON/render 可加进程内 TTL cache；探测周期通常远大于此，状态新鲜度损失可接受。`/metrics` 缓存 TTL 必须低于 scrape interval。
3. **汇总表**：若数据继续增大或长窗口 cold query 必须亚秒，维护 `latest_probe_snapshot` / `target_probe_summary` / `timeline_bucket_rollup`，在 `RecordProbeSnapshot` 写入时 upsert latest、last_success 与各级 timeline bucket。
4. **Redis**：只有多实例 Status Hub、跨进程共享缓存、或缓存失效需要集中管理时再引入。单实例 + SQLite 下，Redis 会增加 compose 依赖、持久化和失效复杂度，不应作为第一刀。

## 用户偏好与工作流

代码修改类工作由 Saber 统筹和验收，具体代码修改默认交 Codex CLI 执行；Saber 负责给 Codex 明确 prompt、检查 diff、跑测试、部署和线上验证。

Status Hub 所在仓库根没有 Go module；Go 测试应在 `services/status-hub` 下运行：

```bash
cd /home/liao/code/tode-monitoring-mvp/services/status-hub
go test ./...
```

生产机不安装 Go。生产只做运行态验证：health、build info、endpoint latency、metrics/timeline 语义。