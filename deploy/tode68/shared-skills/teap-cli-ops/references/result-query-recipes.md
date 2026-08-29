# 结果快速查询知识库

## 强制查询顺序

分析 `.tr` 数据时按以下顺序执行：

1. 用最小分页定位目标完成任务。
2. 动态发现该 `.tr` 实际包含的 result key、设备曲线和分区 bus-index。
3. 优先查询工作位置图的系统/分区聚合曲线；需要设备明细时再查询精确设备曲线并在服务端过滤设备行。
4. 只对已返回的有限 JSON 数据做本地极值、求和、比率或时间索引计算。
5. 结构化接口无法满足查询时，报告已尝试的 group/curve 和具体限制；只有用户明确要求本地原始文件时才下载 `.tr`。

某个猜测 group 失败不等于数据不存在，也不构成下载 `.tr` 的理由。先运行：

```bash
teap -o json result groups <task-id-or-result-path>
```

返回的 `groups` 是该文件实际存储的 `_result.*` key；`work_position_groups` 只给出 inventory 证实存在、可直接查询的普通/情景系统或分区聚合 group；`curve_inventory_groups` 和 `curve_inventories` 分别展示普通 `result_dict`、场景结果或两者，`curve_units` 给出每条实际曲线由 Core 返回的精确单位、系数和类型，兼容字段 `curve_inventory_group/device_curves` 指向首个 inventory；`zones` 给出设备曲线分区查询所需的精确 bus-index。不同 job type 和算法的结果结构可能不同，不要猜 element、curve、单位、设备索引、聚合 zone 后缀或 bus-index。

## 单位发现与换算

Core 的 JSON 数据是未换算原值。平衡表响应在 `structure.unit` 返回单位映射，例如功率可能返回 `{兆瓦: 1, 万千瓦: 0.1, 千瓦: 1e3}`，电量可能返回 `{兆瓦时: 1, 万千瓦时: 0.1, 亿千瓦时: 0.00001}`；指定单位下的值严格为 `原始值 × coefficient`。设备曲线使用 `structure.<curve>.unit[]` 表达同一契约。

Agent 必须先动态发现再精确选择：设备曲线从 `result groups <source>` 的 `curve_units` 读取；平衡表先执行目标查询并读取 `available_units`。随后在同一条有界查询上增加 `--unit <exact-unit>`，并确认响应的 `unit.applied=true`：

```bash
teap -o json result work-position <source> --unit 兆瓦
teap -o json result balance-hour <source> 750 --unit 万千瓦
teap -o json result curve <source> wind Pwind --device-index 7 --unit 万千瓦
teap -o json result get <source> -g _result.balance_df.neps_electricity_monthly -l --unit 兆瓦时
```

不要让 Agent 自行复制系数做无标注换算，也不要假定所有功率曲线都是 MW。CLI 会跳过时间/索引、布尔状态和 Core 标记为 `fixed_unit` 的字段。未指定 `--unit` 时 `unit.applied=false`，数据仍是 Core 原值；`raw-json` 保持服务端原始响应且不能与 `--unit` 合用。

## 最新完成结果

后端完成任务按结束时间升序分页，`finished_page=-1` 表示最后一页。只取最新一条：

```bash
teap -o json task status \
  --finished-only --finished-page -1 --finished-page-size 1
```

需要按算例名、备注或任务类型缩小范围时增加 `--filter <text>` 和可重复的 `--job-type <id>`。不要为了找最新结果先下载所有完成任务页。

## 月度电力平衡与任意时刻

月度默认表：

```bash
teap -o json result get <source> -g _result.balance_df.neps_power -l
```

默认表不是固定月初、月末或简单最大负荷时刻。TEAP Core 从每个月完整时序中选择一行，主排序是四位小数舍入后的 `power_balance` 升序；全系统主值相同时，再按 `p_deficiency_total` 降序、负荷及运行特征排序。业务上它代表当月最严重的电力缺口；没有缺口时代表盈余/平衡裕度最小的时刻，通常是用户最关心、风险特征最明显的时刻。

默认行不限制其他时刻查询。第一个仿真时刻和第 751 个仿真时刻分别为：

```bash
teap -o json result balance-hour <source> 0
teap -o json result balance-hour <source> 750
```

时间参数是零基仿真索引，不一定等于自然小时编号。命令精确映射到 `_result.balance_df.neps_alltime_power.<index>`，返回该时刻各分区/系统的电力平衡表。超出范围时停止原样重试，先从结果时间轴确认长度。

## 工作位置图聚合曲线

系统级工作位置曲线：

```bash
teap -o json result work-position <source>
teap -o json result work-position <source> --unit 兆瓦
```

分区聚合曲线先从 `result groups <source>` 的 `work_position_groups` 取得 `_result.balance_df.zone_power.` 后的精确 zone 后缀，再运行：

```bash
teap -o json result work-position <source> \
  --zone "华东"
```

它们对应 `_result.balance_df` 和 `_result.balance_df.zone_power.<zone>`。zone 是 `.tr` 中存储的分区键，可能是中文名称；它与设备曲线使用的 bus-index 不是同一个值。先检查实际返回字段，再选择功率、缺口、备用等序列；不要假定所有结果类型都返回同一组标签。

若 `work_position_groups` 返回 `_result.scenario_balance_df` 或其 `.zone_power.<zone>`，查询情景聚合曲线时增加 `--scenario`：

```bash
teap -o json result work-position <source> --scenario
teap -o json result work-position <source> --scenario --zone "华东"
```

不要仅因通用 group 列表展示某种结果就假定当前 `.tr` 支持它；当前文件可用能力以动态 `work_position_groups` 为准。

## 设备级时序

动态 inventory 会列出可查询的 element 和 curve。常见配置包括：

- `wind`: `Pwind`（消纳功率）、`Pwind_curt`（弃电功率）
- `solar`: `Psolar`（消纳功率）、`Psolar_curt`（弃电功率）
- `load`: `Pload`、`Pl_increase`、`Dr_direct`、`Dr_shift`
- `gen`: `Pgen`、`Ug`、`Rgen_up`、`Rgen_emer`
- `stogen`: `Pstogen`、`Rstogen_up`、`Ucharge`
- `feedin`: `Pfeedin`、`Pfeedin_increase`

示例：

```bash
teap -o json result curve <source> wind Pwind
teap -o json result curve <source> wind Pwind_curt --device-index 7
teap -o json result curve <source> wind Pwind_curt --device-index 7 --unit 万千瓦
teap -o json result curve <source> solar Psolar
teap -o json result curve <source> solar Psolar_curt
```

先轻量获取某条曲线的设备名称和索引，不返回任何时序样本：

```bash
teap -o json result curve <source> solar Psolar --metadata-only
```

再用可重复的 `--device-index` 查询目标设备。索引必须来自 metadata-only 响应，不能按 case 行号或名称猜测。该选项通过 `extra_param.filtered_index_list` 在服务端裁剪矩阵，查询少量设备时不要先拉取全部设备。若 `curve_inventory_group` 是 `_result.scenario_result_dict_st`，查询对应场景曲线时增加 `--scenario`。分区设备查询使用 inventory 返回的精确 bus-index。

## 常见极值查询

以下流程都先用工作位置图聚合曲线，避免拉取每台设备的完整矩阵。实际字段名以响应为准。

### 风电、光伏与新能源消纳极值

1. 查询系统或目标分区工作位置曲线。
2. 从实际响应识别风电/光伏消纳与弃电的聚合序列。
3. 每个时刻按业务口径计算 `消纳率 = 消纳 / (消纳 + 弃电)`；分母为零的时刻标为不可计算，不当作 0%。
4. 对风、光分别求最低/最高时刻；合并新能源时先逐时刻合计风光分子和分母，再计算比率，不能简单平均两个百分比。
5. 返回时间索引和值；需要该时刻完整平衡表时再调用 `result balance-hour <source> <index>`。

若聚合曲线缺少所需分子或分母，先从动态 inventory 精确获取 `Pwind/Pwind_curt` 或 `Psolar/Psolar_curt`，再对 API 返回的设备矩阵按时刻求和。不要直接下载 `.tr`。

### 负荷峰谷

优先从工作位置图识别系统/分区负荷序列并求最大、最小索引。只有需要单个负荷设备贡献时，才查询 `load Pload`，并用 `--device-index` 限定设备。

### 储能充放电极值

先检查聚合曲线中的储能充电、放电序列及其正负号约定。需要设备明细时，动态确认 `stogen` 的 `Pstogen`、`Ucharge` 等曲线；分别筛选充电/放电状态后求极值，不凭曲线名猜符号。

### 机组出力、启停与备用

系统总量优先使用聚合曲线。设备分析按需读取 `gen Pgen`、`gen Ug`、`gen Rgen_up`、`gen Rgen_emer`，用服务端设备过滤缩小响应。启停 `Ug` 是状态序列，不能当成功率相加。

### 缺电与备用不足

从工作位置图识别缺口、负荷削减或备用序列，先定位极值索引，再用 `balance-hour` 获取该时刻完整平衡构成。月度风险概览优先用默认 `neps_power` 表；精确复核才查询任意时刻。

## 结构化查询停止条件

以下情况应停止结构化查询并说明限制，不得自动下载：

- `result groups <source>` 成功，但 inventory 明确不含目标数据；
- 后端明确报告该历史/异常 `.tr` 无法通过结构化接口解析；
- 已确认的后端接口缺陷需要受控离线诊断。

记录尝试过的 group/curve 后，让用户决定是否需要取得本地文件。只有用户明确要求下载 `.tr` 后才可执行；离线检查必须使用受控解析器、限定 key 和输出大小，禁止用 `cat/head/tail`、无界 `strings/grep` 把二进制内容送入模型上下文。
