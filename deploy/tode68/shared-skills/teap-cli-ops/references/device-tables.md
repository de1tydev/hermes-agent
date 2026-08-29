# 设备表与行操作

## 目录

- 表分类和依赖
- Sheet 读取与写入
- Row 读取与创建
- Row 更新与删除
- 字段模板
- 储能双表关系
- 建模校验

## 表分类和依赖

常用 TEAP sheet：

| 类别 | Sheet | 关键关系 |
| --- | --- | --- |
| 区域拓扑 | `zone`, `bus` | `bus.zone -> zone.index` |
| 常规机组 | `gen`, `gen_plan` | `bus`, `type`, 容量、技术出力 |
| 新能源 | `wind`, `solar`, `wind_plan`, `solar_plan` | `bus`, 容量、`timeseries` |
| 负荷/交换 | `load`, `feedin` | `bus/zone`, `timeseries` |
| 储能 | `stogen`, `stogen_plan`, `storage`, `storage_plan` | 功率单元引用能量单元 |
| 网络 | `ac_line`, `dc_line`, `trafo`, `interface` 及 plan 表 | 起止节点、容量/限额 |
| 水电 | `hydropower`, `reservoir` 及 plan 表 | 水库、机组和时序关系 |
| 光热/氢能 | `csp`, `hydrogen_tank` 及 plan 表 | 类型、容量、时序 |
| 抽蓄 | `hydro_acc`, `hydro_acc_plan` | 发电/抽水能力、库容与计划关系 |
| 需求响应 | `dsm`, `dsm_plan` | 节点、响应能力、成本与时序 |
| 曲线 | `timeseries` | 被设备行的 `timeseries` 列引用 |
| 参数 | `parameter` | 嵌套对象，使用 parameter 命令 |

服务端结构可能随算例类型变化。非常见表先运行 `case structure`，不要从上表推断其一定存在。

推荐创建顺序：`zone -> bus -> gen/wind/solar/load/feedin/storage/network -> timeseries -> parameter`。同一 case 的写操作串行执行。

## Sheet 读取与写入

读取整表：

```bash
teap -o json case sheet get "$CASE_PATH" bus
```

整表写入：

```bash
teap -o json case sheet write "$CASE_PATH" zone \
  -j '[{"index":-1,"name":"华东","in_service":true}]' -y
```

`sheet write` 适合批量替换或已有结构化数据。它的 blast radius 大于 row 命令；单行增删改优先使用 row 命令。写前保留现有行和 index，不要无意覆盖整表。

## Row 读取与创建

读取单行：

```bash
teap -o json case row get "$CASE_PATH" gen 3
```

仅在定点检查外部关系时加 `--include-relations`：

```bash
teap -o json case row get "$CASE_PATH" gen 3 --include-relations
```

字段式创建是常用路径：

```bash
teap -o json case row create "$CASE_PATH" zone --name 华东
teap -o json case row create "$CASE_PATH" bus --name BUS-1 --zone 0
teap -o json case row create "$CASE_PATH" gen \
  --name G1 --type coal --bus 0 --max_p_mw 600
```

字段名使用 TEAP schema key，而不是中文显示名。短横线会归一化为下划线；为避免歧义，文档示例使用 schema 原名。

复杂对象可用 JSON：

```bash
teap -o json case row create "$CASE_PATH" ac_line \
  -j '{"name":"L1","from_bus":0,"to_bus":1,"max_p_mw":500}'
```

不要同时传 `--data-json/--data-file` 和字段式参数。

## Row 更新与删除

先读取当前行，再做最小 patch：

```bash
teap -o json case row update "$CASE_PATH" gen 3 --max_p_mw 650
```

复杂 patch：

```bash
teap -o json case row update "$CASE_PATH" gen 3 \
  -j '{"min_p_rate":0.35,"max_p_rate":1.0}'
```

按 ID 删除：

```bash
teap -o json case row delete "$CASE_PATH" gen --row-id 3 -y
```

删除前检查其他表是否引用目标 row。不要猜删除会级联清理关系。

## 字段模板

CLI 对常见设备创建提供最小默认值和后端参数补全，包括 `gen/load/wind/solar/feedin/csp`、线路、储能、水电、抽蓄、需求响应、变压器、断面及相应 plan 表。`csp_plan` 同样支持后端初始化字段。

- 默认开启 `--fill-defaults`；后端按设备类型补齐未提供字段。
- 需要保留纯输入时使用 `--no-fill-defaults`。
- `gen` 未提供 type 时默认 `coal`；专业模型应显式给出 type。
- `wind/solar/load` 默认初始化空 `timeseries` 关系。
- 不要依赖模板猜测拓扑引用、容量或业务参数；这些必须明确给出。

若新建模型因设备参数或字段缺失而计算失败，先确认失败指向的 sheet 和 row，再对受支持表使用默认开启的 `--fill-defaults` 创建完整初始化行；回读返回的 row ID，并在 `case validate` 成功后才重新计算。内置补全仍不满足时，才按 schema、同类有效行、Core 模型语义和用户要求补充最小业务字段。已有不完整 row 不得为触发补全而盲目重建；先检查引用关系，再用 `case row update` 精确修复。完整修复顺序和停止条件见 [modeling-guide.md](modeling-guide.md)。

创建返回的 row ID 是后续关系绑定依据。仅当返回缺少 ID 或后续失败时，才重读 sheet 定位新行。

`integrated`、`interface_add`、`branch_add`、`coordinated_add`、`powerbase_add` 等专业表可通过 `-j/-F` 显式写入，但当前后端没有统一稳定的初始化字段处理器。先用 `case structure` 读取目标服务 schema，完整提供业务字段，不要依赖 `--fill-defaults` 猜值。

## 储能双表关系

TEAP 将储能功率单元与能量单元分开：

- `stogen`：充放电功率、所在节点、运行状态；
- `storage`：能量容量、初始能量、损耗、寿命等。

便捷创建：

```bash
teap -o json case row create "$CASE_PATH" stogen \
  --name ES1 --bus 0 --type battery --max-p-mw 100 --max-e-mwh 200
```

`--max-p-mw` 同时映射到 `max_p_discharge_mw` 和 `max_p_charge_mw`。未显式给出 `storage` 时，CLI 创建 companion `storage` 行并绑定，输出包含 `companion`。

也可分别提供充、放电功率；两者都不得为空。仅给时长时可用 `--duration-h`，CLI 在有放电功率时计算能量容量。修改已有储能前同时读取 `stogen` 和关联 `storage`。

## 建模校验

完成后只复核触达的表：

- 名称和 index；
- 母线/分区/线路端点引用；
- 机组和新能源容量、技术出力；
- 储能充放电功率与能量容量；
- `timeseries` ID、scenario 和设备绑定；
- plan 表的投产/退役窗口。

最后运行 `case validate`。服务端字段错误时先用 `case structure` 查实际 schema，不要把同一个未知字段重复提交。
