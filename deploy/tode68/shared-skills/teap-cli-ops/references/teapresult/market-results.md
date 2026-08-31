# 市场模拟结果

## 目录

- 结果来源与对象边界
- 为什么任务 ID 查询曾失败
- 推荐查询顺序
- 节点电价及分量
- 机组经济性
- 分电压等级价格统计
- 与滚动和时序模拟的区别
- 大结果与导出
- 失败处理
- 已验证契约

## 结果来源与对象边界

市场模拟的 job type 是 `301`。它有两种输入工作流：

1. 输入 `.tc`，以市场模式执行仿真并生成 `.tr`；
2. 输入已有滚动/短期 `.tr`，对既有运行结果执行市场定价，生成追加电价数据的新 `.tr`。

两种工作流最终都必须按 `task/result` 查询。不要把市场结果当作可编辑 case，也不要为了分析结果先下载 `.tr` 再读取原始字节。

市场定价后的 `.tr` 通常保留原滚动结果结构，并增加市场专属曲线和统计。旧结果可能仍保存 `short_term` 来源信息，core 会在检测到母线 `lmp` 曲线后把有效展示模式识别为 `market`；新结果也可能直接保存 `market`。因此应按实际可用 group 判断，不要只依赖某一个 parameter 字段。

## 为什么任务 ID 查询曾失败

teap3 的 `/backend/teap_api_v3/get_sim_task_result/` 支持两种互斥来源：

- `task_record_id`
- `result_file_path`

在当前 core 基线中，`task_record_id` 分支只允许短期、中期和长期类型，因而会对 `301` 返回：

```text
本接口不支持查询该任务类型[301]结果数据！
```

这不表示市场 `.tr` 无法解析，也不表示任务不存在。`result_file_path` 分支会使用同一个通用 `.tr` handler，并能读取市场结果。

当前 CLI 会把数字任务 ID 解析为该任务的 `result_path`，再以 `result_file_path` 查询。不要在遇到上述错误后重复更换 task ID；升级 CLI 或使用明确的服务端结果路径。

## 推荐查询顺序

先确认任务完成且有结果路径：

```bash
teap task status --task-record-id 4776
```

再发现该 `.tr` 实际包含的市场能力：

```bash
teap result market groups 4776
```

`groups` 返回：

- 可用的 `lmp`、能量分量和阻塞分量；
- 可选的机组收入、成本和毛利；
- 是否存在价格统计；
- 该文件实际具有的价格类型和电压等级。

必须先发现再读取。机组经济性和价格统计都可能缺失，电压等级也只返回算例中实际存在的分组。

通用 group 仍可直接读取，并接受任务 ID 或服务端 `.tr` 路径：

```bash
teap result get 4776 -g _result.result_dict.bus.lmp
teap result get /server/path/result.tr -g _result.result_dict.bus.lmp
```

## 节点电价及分量

市场母线曲线如下：

| CLI component | result group | 含义 | JSON/导出单位 |
| --- | --- | --- | --- |
| `lmp` | `_result.result_dict.bus.lmp` | 节点边际电价 | 元/MWh |
| `energy` | `_result.result_dict.bus.lmp_energy` | 能量电价分量 | 元/MWh |
| `congestion` | `_result.result_dict.bus.lmp_congestion` | 阻塞电价分量 | 元/MWh |

读取指定母线，`--bus-index` 可重复：

```bash
teap result market prices 4776 --component lmp --bus-index 0
teap result market prices 4776 --component energy --bus-index 0 --bus-index 3
teap result market prices 4776 --component congestion --bus-index 0
```

不传 `--bus-index` 会返回全部母线和全部时点，可能产生很大的 JSON。Agent 分析应先取少量明确母线，或直接导出 CSV。

通常满足：

```text
lmp = lmp_energy + lmp_congestion
```

浮点展示经过三位小数舍入，做等式核验时允许相应舍入误差。

## 机组经济性

当市场计算产生机组经济性时，可用：

| CLI metric | result group | 含义 |
| --- | --- | --- |
| `revenue` | `_result.result_dict.gen.revenue_gen` | 机组收入 |
| `cost` | `_result.result_dict.gen.cost_gen` | 机组成本 |
| `profit` | `_result.result_dict.gen.profit_gen` | 机组毛利 |

```bash
teap result market generator-economics 4776 \
  --metric profit --generator-index 0
```

`--generator-index` 可重复。不传时读取全部机组。

这些字段是可选的。必须以 `market groups` 的 `generator_economics` 为准，不能因为存在 LMP 就假定一定存在收入、成本和毛利。

单位需要区分输出方式：

- JSON 曲线是 `.tr` 中存储的原始金额，单位为元；
- CSV/表格导出按照 core 的结果元数据乘以 `0.0001`，展示单位为万元。

不要把 JSON 原值直接标成万元，也不要再次对 CSV 数值重复换算。

## 分电压等级价格统计

不带筛选时只发现可用维度，不返回全部统计表：

```bash
teap result market price-stats 4776
```

服务端可能返回的价格类型包括：

- `LMP`
- `Energy_comp`
- `Congestion_comp`

可能的电压分组包括 `<10kV`、`10-30kV`、`30-200kV`、`200-500kV`、`>=500kV`，但具体 `.tr` 可能只有其中一部分。必须使用 discovery 返回的精确字符串。

读取某一价格类型和电压组：

```bash
teap result market price-stats 4776 \
  --price-type LMP --voltage-group '>=500kV'
```

`--voltage-group` 依赖 `--price-type`。统计结果可能包含：

- `avg_price_timeseries`：平均电价时序；
- `price_boxplot`：价格箱线统计；
- `price_vs_total_load`：价格与总负荷关系；
- `price_vs_net_load`：价格与净负荷关系；
- `time`：完整仿真时间轴。

不同统计表行数可以不同，不要把关系抽样表强行与完整 8760 时间轴逐行拼接。

## 与滚动和时序模拟的区别

滚动/短期结果关注机组出力、启停、备用、负荷平衡、断面和新能源消纳等运行量。市场结果复用这些物理运行结果，并增加价格和收益侧信息。

分析时分开处理：

| 问题 | 优先结果 |
| --- | --- |
| 系统是否供需平衡、机组如何运行 | 滚动结果中的 balance/result curves |
| 各节点的边际价格 | `bus.lmp` |
| 价格由系统能量还是网络阻塞形成 | `lmp_energy` 与 `lmp_congestion` |
| 单台机组收入、成本和毛利 | 可选的 `gen.*_gen` |
| 不同电压等级价格分布和负荷关系 | `_result.price_stats_df` |

不要用 `_result.key_summaries` 代替市场价格分析。它仍可用于新能源利用率等滚动结果摘要，但不是市场专属结果入口。

## 大结果与导出

单条市场曲线通常覆盖完整时序。优先按设备 index 限定 JSON：

```bash
teap result market prices 4776 -c lmp -b 0
```

需要完整数据时导出：

```bash
teap result market prices 4776 -c lmp --to-csv-format -o ./lmp.csv
teap result market generator-economics 4776 -m profit \
  --to-csv-format -o ./profit.csv
```

不得使用 `cat`、`head`、`strings` 或 `grep` 检查 `.tr` 原始内容。

## 失败处理

- “任务类型 301 不支持”：这是 task-ID 分支限制；使用当前 CLI 的 `result market` 或 result path 查询，不要盲目换任务。
- “没有 result path”：任务尚未完成、失败或没有生成结果；查 `task status`，失败时查 `log analyze`。
- 市场曲线不存在：该 `.tr` 不是已定价市场结果，或定价没有写入；不要把普通滚动 `.tr` 伪装成市场结果。
- 机组经济性不存在：这是允许情况；先读 `market groups`，只请求返回的字段。
- 统计维度不存在：重新运行 discovery 并使用服务端返回的精确 price type 和 voltage group。
- JSON 过大：限定母线/机组 index，或切换 CSV 导出；不要重复请求全量 JSON。

## 已验证契约

本参考基于 teap3 `develop` commit `f8031da8ea9350013706768596fe2673030e20be` 的代码和 develop 服务只读验证。

验证确认：

- job type 301 可通过 `result_file_path` 进入通用 `.tr` handler；
- `result_dict_st` 可用于发现实际市场曲线；
- LMP、能量分量、阻塞分量使用同一时间轴；
- 机组经济性存在性取决于计算输出；
- `price_stats_df --to-list` 返回实际价格类型和电压等级；
- 精确统计组返回各统计表和时间轴，不保证所有表行数相同。
