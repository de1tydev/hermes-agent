# 时序写入与绑定

## 目录

- 曲线对象与同名语义
- 本地紧凑模板
- XLSX 批量导入
- 创建并通用绑定
- 机组指定出力率
- 更新与校验

分工：本文件覆盖在 `timeseries` sheet 中写入曲线并绑定设备；TMY、负荷预测、风光极端曲线等**数据生成**见 [application-function/data-generation.md](../application-function/data-generation.md)。

## 曲线对象与同名语义

`timeseries` 行通常包含 `name`、`type`、`value_type`、`scenario`、`value`。设备行通过自己的 `timeseries` ID 列表引用曲线。Core 字段名是 `value_type`；CLI 兼容输入名 `data_type` 并在写入前规范化，但两者同时出现时必须一致。

用于建模和仿真的新曲线必须显式填写非空 `scenario`。地区默认、地市到负荷省份的映射、场景选择和启动前 `case_info.scenario_selected` 设置见 [application-instructions/build-case-from-scratch.md](../application-instructions/build-case-from-scratch.md)。

标准类型发现、完整清单、限定格式与 `value_type` 契约见 [teapcase/timeseries-types.md](timeseries-types.md)。创建前必须运行 `timeseries types`；`case structure` 不返回这份类型清单。选择 `types[].value` 后同时检查该项的 `recommended_value_type`；不匹配允许写入，但会在普通 JSON 成功输出中返回结构化 warning。

同一个类型后缀 `p_rate` 在不同设备上含义不同：

| 绑定设备 | `p_rate` 含义 |
| --- | --- |
| `gen.p_rate` / `gen_plan.p_rate` | 指定机组出力率；`-1` 表示该时段不指定 |
| `wind.p_rate` / `solar.p_rate` 及 plan 限定类型 | 风光可用标幺出力 |
| `load.p_rate` | 负荷标幺功率 |
| `feedin.p_rate` | 电力流标幺功率 |
| `csp.p_rate` | 指定出力率；辐照输入使用 `csp.dni_pu` |

不要看到 `p_rate` 就套用统一业务解释。不要写前端 label，例如“指定出力率”；仿真只识别标准键。类型来源既包括 `extra_ts_type`，也包括标记为 `change_with_timeseries` 的设备字段。

## 本地紧凑模板

不要把 8760 点直接放进命令行。`case row create/update ... timeseries` 支持带 `period` 的模板 JSON。

全年固定值：

```bash
teap case row create "$CASE_PATH" timeseries \
  -j '{"period":"year","template":"fixed","value":0.8,"type":"wind.p_rate","value_type":"multiply","scenario":"base"}'
```

逐日 24 点重复 365 天：

```bash
teap case row create "$CASE_PATH" timeseries \
  -F ./daily-profile.json
```

文件示例：

```json
{"period":"day","template":"repeat","value":[0.7,0.65,0.6,0.58,0.56,0.6,0.72,0.85,0.9,0.92,0.9,0.88,0.86,0.84,0.85,0.88,0.95,1,0.98,0.92,0.86,0.82,0.78,0.74],"type":"load.p_rate","value_type":"multiply","scenario":"base"}
```

支持的模板：

| period | template | 输入点 | 展开规则 |
| --- | --- | ---: | --- |
| `year` | `fixed` | 1 | 全年 8760 点固定 |
| `day` | `repeat` | 24 | 日曲线重复 365 次 |
| `month` | `repeat` | 28-31 | 按每月天数重复/截断，再展开小时 |
| `quarter` | `fixed` | 4 | 按 90/91/92/92 天展开 |

新曲线必须显式提供标准限定 `type`；CLI 不再为无目标曲线猜测 `p_rate`。`p_rate` 紧凑模板默认 `value_type=multiply`，其他类型默认 `replace`，但专业任务应显式写 `type`、`scenario` 和 `value_type`。

## XLSX 批量导入

模板由 case 的起止时间动态生成，先下载再填写：

```bash
teap timeseries file template "$CASE_PATH" -o ./timeseries-template.xlsx
teap timeseries file inspect "$CASE_PATH" ./curves.xlsx
```

`inspect` 校验文件并返回可插入记录的 index。上传缓存最长保留 24 小时，但不要把缓存键当永久文件路径保存。

插入选中记录，并可同时绑定一个设备 row：

```bash
teap timeseries file insert "$CASE_PATH" ./curves.xlsx \
  --index 0 --index 2 --bind-sheet wind --bind-index 7
```

同名曲线会返回 `code=2`，不会静默决定处理方式。检查返回后明确重跑并选择一个策略：

```text
--overwrite       更新同名曲线，并插入非重名曲线
--skip-existing   保留同名曲线，只插入非重名曲线
--copy-duplicates 创建自动重命名的副本
```

三种策略互斥。`--bind-sheet` 与 `--bind-index` 必须同时提供。插入后回读 `timeseries` 和绑定设备；不要把设备 sheet 的 `import-xlsx` 当作此流程的替代。

## 创建并通用绑定

通过 `--bind-sheet` 和可重复的 `--bind-index`，在一个命令中创建曲线并绑定设备：

```bash
teap case row create "$CASE_PATH" timeseries \
  -j '{"period":"year","value":0.8,"type":"gen.p_rate","value_type":"multiply","scenario":"base"}' \
  --bind-sheet gen --bind-index 3 --bind-index 4
```

规则：

- 两个选项必须一起使用；
- 只允许在创建 `timeseries` 行时使用；
- CLI 在创建曲线前验证目标 row 存在；
- 绑定会保留设备已有的时序 ID，并避免重复追加；
- 成功 JSON 的 `bindings` 返回目标 sheet 和 row ID。

曲线创建和设备绑定包含连续的服务端写操作，不应视为数据库事务。命令失败时先读取 `timeseries` 和目标设备行确认实际状态；不要直接重放整条 create 命令，否则可能创建重复曲线。

一个命令只绑定一个 sheet。需要跨 `gen` 和 `gen_plan` 复用同一曲线时，先创建并绑定一类，再用 `case row update` 将返回的曲线 ID 加入另一类设备的 `timeseries` 列。

## 机组指定出力率

最新 TEAP3 支持 `gen/gen_plan` 的 `p_rate` 指定出力率曲线。CLI 在使用通用绑定指向这两个 sheet 时执行输入层校验：每个点只能是 `-1` 或 `[0,1]`。

- `-1`：该时段不指定，进入 teap3 后转换为未约束目标；
- `0`：指定零出力；
- `(0,1]`：指定相对于机组额定/最大功率的目标出力率；
- 其他负值和大于 1 的值会在写 case 前被 CLI 拒绝。

部分时段指定示例：

```bash
teap case row create "$CASE_PATH" timeseries \
  -j '{"period":"day","template":"repeat","type":"gen.p_rate","value_type":"multiply","scenario":"base","value":[-1,-1,-1,-1,-1,-1,0.4,0.5,0.6,0.7,0.8,0.8,0.8,0.8,0.7,0.6,0.5,0.4,0.3,0.2,-1,-1,-1,-1]}' \
  --bind-sheet gen --bind-index 3
```

服务端还会结合机组逐时最大/最小技术出力、`gen_on_off` 和参数 `simulation.gen_p_rate_match_onoff` 校验：

- 指定值超过逐时最大技术出力：硬错误；
- 正目标低于最小技术出力：硬错误；
- 曲线与指定启停冲突：根据 match 开关产生警告或硬错误；
- 只覆盖部分时段：服务端可能给出警告。

CLI 无法只凭曲线完成这些跨表校验；任务失败时按日志提示修正设备边界或启停曲线，不要反复启动同一 case。

## 更新与校验

更新模板曲线会先读取原始 row，再保留未修改字段：

```bash
teap case row update "$CASE_PATH" timeseries 9 \
  -j '{"period":"year","template":"fixed","value":0.9}'
```

完成后检查：

1. `case sheet get "$CASE_PATH" timeseries` 中的 ID、type、scenario、点数和关系摘要；
2. 设备 sheet 的 `timeseries` ID 列表；
3. 机组曲线同时检查 `gen_on_off` 和 `gen_p_rate_match_onoff`；
4. 完成本批次所有 case 修改并回读场景参数后，按 [SKILL.md 的校验节奏](../../SKILL.md#校验节奏) 通过一次统一提交门禁，修复 errors 并确认 warnings 后再启动任务。

启动前还必须把本次曲线场景写入并回读 `case_info.scenario_selected`；仅在曲线行写 `scenario` 不会自动选择运行场景。完整顺序见 [application-instructions/build-case-from-scratch.md](../application-instructions/build-case-from-scratch.md)。
