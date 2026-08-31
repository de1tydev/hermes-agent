# 常见结果分析套路

## 目录

- 强制查询顺序
- 定位目标结果
- 单位发现与换算
- 新能源消纳
- 月度电力平衡与风险时刻
- 工作位置与设备曲线
- 常见极值查询
- 停止条件

本文给出“拿到一个计算结果后怎么分析”的常用套路汇总。命令细节与 group 清单见 [teapresult/result-lifecycle.md](../teapresult/result-lifecycle.md) 与 [teapresult/query-recipes.md](../teapresult/query-recipes.md)。

## 强制查询顺序

分析 `.tr` 数据时按以下顺序执行：

1. 用最小分页定位目标完成任务；
2. 动态发现该 `.tr` 实际包含的 result key、设备曲线和分区 bus-index；
3. 优先查询工作位置图的系统/分区聚合曲线；需要设备明细时再查询精确设备曲线并在服务端过滤设备行；
4. 只对已返回的有限 JSON 数据做本地极值、求和、比率或时间索引计算；
5. 结构化接口无法满足查询时，报告已尝试的 group/curve 和具体限制；只有用户明确要求本地原始文件时才下载 `.tr`。

某个猜测 group 失败不等于数据不存在，也不构成下载 `.tr` 的理由。先运行 `teap result groups <task-id-or-result-path>`。

返回的 `groups` 是该文件实际存储的 `_result.*` key；`work_position_groups` 只给出 inventory 证实存在、可直接查询的普通/情景系统或分区聚合 group；`curve_inventory_groups` 和 `curve_inventories` 分别展示普通 `result_dict`、场景结果或两者，`curve_units` 给出每条实际曲线由 Core 返回的精确单位、系数和类型；`zones` 给出设备曲线分区查询所需的精确 bus-index。不同 job type 和算法的结果结构可能不同，不要猜 element、curve、单位、设备索引、聚合 zone 后缀或 bus-index。

## 定位目标结果

“最新完成”“计算完成的算例”从 task/result 路由：

```bash
teap task status --finished-only --finished-page -1 --finished-page-size 1
teap result search --filter 华东 --page-size 20
```

得到 `result_path` 或 task ID 后，用 `result groups <source>` 动态发现。

## 单位发现与换算

Core 的 JSON 数据是未换算原值。平衡表响应在 `structure.unit` 返回单位映射，例如功率可能返回 `{兆瓦: 1, 万千瓦: 0.1, 千瓦: 1e3}`，电量可能返回 `{兆瓦时: 1, 万千瓦时: 0.1, 亿千瓦时: 0.00001}`；指定单位下的值严格为 `原始值 × coefficient`。设备曲线使用 `structure.<curve>.unit[]` 表达同一契约。

Agent 必须先动态发现再精确选择：设备曲线从 `result groups <source>` 的 `curve_units` 读取；平衡表先执行目标查询并读取 `available_units`。随后在同一条有界查询上增加 `--unit <exact-unit>`，并确认响应的 `unit.applied=true`：

```bash
teap result work-position <source> --unit 兆瓦
teap result balance-hour <source> 750 --unit 万千瓦
teap result curve <source> wind Pwind --device-index 7 --unit 万千瓦
teap result get <source> -g _result.balance_df.neps_electricity_monthly -l --unit 兆瓦时
```

不要让 Agent 自行复制系数做无标注换算，也不要假定所有功率曲线都是 MW。CLI 会跳过时间/索引、布尔状态和 Core 标记为 `fixed_unit` 的字段。未指定 `--unit` 时 `unit.applied=false`；`raw-json` 保持服务端原始响应且不能与 `--unit` 合用。

## 新能源消纳

优先读 `_result.key_summaries` 的 `data.renewable_util_rate.wind/solar/renewable`（0-1 小数，面向用户转百分比）。需要逐时口径时：

1. 查询系统或目标分区工作位置曲线；
2. 从实际响应识别风电/光伏消纳与弃电的聚合序列；
3. 每个时刻按业务口径计算 `消纳率 = 消纳 / (消纳 + 弃电)`；分母为零的时刻标为不可计算，不当作 0%；
4. 合并新能源时先逐时刻合计风光分子和分母，再计算比率，不能简单平均两个百分比。

## 月度电力平衡与风险时刻

月度默认表：

```bash
teap result get <source> -g _result.balance_df.neps_power -l
```

默认表不是固定月初、月末或简单最大负荷时刻。TEAP Core 从每个月完整时序中选择一行，主排序是四位小数舍入后的 `power_balance` 升序；全系统主值相同时，再按 `p_deficiency_total` 降序、负荷及运行特征排序。业务上它代表当月最严重的电力缺口；没有缺口时代表盈余/平衡裕度最小的时刻。

任意零基仿真时刻：

```bash
teap result balance-hour <source> 0
teap result balance-hour <source> 750
```

时间参数是零基仿真索引，不一定等于自然小时编号。超出范围时停止原样重试，先从结果时间轴确认长度。

## 工作位置与设备曲线

系统级工作位置曲线：

```bash
teap result work-position <source> --unit 兆瓦
```

分区聚合曲线先从 `result groups <source>` 的 `work_position_groups` 取得 `_result.balance_df.zone_power.` 后的精确 zone 后缀，再运行 `result work-position <source> --zone "华东"`。zone 是 `.tr` 中存储的分区键，可能是中文名称；它与设备曲线使用的 bus-index 不是同一个值。inventory 返回情景聚合 group 时增加 `--scenario`。

设备级曲线先轻量取名称/索引，再按需查询：

```bash
teap result curve <source> solar Psolar --metadata-only
teap result curve <source> solar Psolar_curt --device-index 7 --unit 万千瓦
```

索引必须来自 metadata-only 响应，不能按 case 行号或名称猜测。该选项在服务端裁剪矩阵，查询少量设备时不要先拉取全部设备。常见配置：`wind` `Pwind/Pwind_curt`、`solar` `Psolar/Psolar_curt`、`load` `Pload/Pl_increase/Dr_direct`、`gen` `Pgen/Ug/Rgen_up/Rgen_emer`、`stogen` `Pstogen/Ucharge/Rstogen_up`、`feedin` `Pfeedin`。

## 常见极值查询

以下流程都先用工作位置图聚合曲线，避免拉取每台设备的完整矩阵。实际字段名以响应为准。

- **负荷峰谷**：从工作位置图识别系统/分区负荷序列并求最大、最小索引；只有需要单个负荷设备贡献时才查 `load Pload` 并 `--device-index` 限定。
- **储能充放电极值**：先检查聚合曲线中的储能充电、放电序列及其正负号约定；需要设备明细时按需读取 `stogen` 的 `Pstogen`、`Ucharge`，筛选充/放状态后求极值，不凭曲线名猜符号。
- **机组出力、启停与备用**：系统总量优先聚合曲线；设备分析按需读取 `gen Pgen`、`gen Ug`、`gen Rgen_up`、`gen Rgen_emer`。启停 `Ug` 是状态序列，不能当成功率相加。
- **缺电与备用不足**：从工作位置图识别缺口、负荷削减或备用序列，先定位极值索引，再用 `balance-hour` 获取该时刻完整平衡构成。

## 停止条件

以下情况应停止结构化查询并说明限制，不得自动下载：

- `result groups <source>` 成功，但 inventory 明确不含目标数据；
- 后端明确报告该历史/异常 `.tr` 无法通过结构化接口解析；
- 已确认的后端接口缺陷需要受控离线诊断。

记录尝试过的 group/curve 后，让用户决定是否需要取得本地文件。只有用户明确要求下载 `.tr` 后才可执行；离线检查必须使用受控解析器、限定 key 和输出大小，禁止用 `cat/head/tail`、无界 `strings/grep` 把二进制内容送入模型上下文。
