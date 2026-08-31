# 算法预处理

## 目录

- 能力边界
- 年内机组检修
- 跨年机组检修
- 运行方式筛选
- 与仿真算法的关系
- 校验与失败处理

## 能力边界

`teap maintenance` 调用 teap3 的机组检修计划预处理算法，产物用于修改或辅助修改可编辑 `.tc`。它不是 task，不会生成 `.tr`，也不代表平衡、规划或潮流计算已经执行。

年内算法的表单字段随 teap3 配置演进，因此先读取服务端 config，再按返回字段构造 JSON；不要从旧示例补默认值。跨年算法具有稳定的强类型参数，由 CLI 直接校验。

## 年内机组检修

先查询服务端字段、枚举与约束：

```bash
teap maintenance annual config
```

仅生成 CSV：

```bash
teap maintenance annual run "$CASE_PATH" \
  --operation generate --data-file ./annual-maintenance.json \
  -o ./annual-maintenance.csv
```

其他操作：

| operation | 行为 | CSV 参数 |
| --- | --- | --- |
| `generate` | 生成并可下载计划，不修改 case | 禁止 |
| `insert` | 生成并写入 case | 禁止 |
| `generate-insert` | 生成、写入并可下载计划 | 禁止 |
| `upload-insert` | 上传已有计划并写入 case | 必须 `--csv` |

示例：

```bash
teap maintenance annual run "$CASE_PATH" \
  --operation upload-insert --data-file ./annual-maintenance.json \
  --csv ./reviewed-maintenance.csv
```

`--data-json` 与 `--data-file` 互斥。JSON 中不要重复或覆盖 `tc_filename`；CLI 使用位置参数中的 case path。只有产生下载文件的 operation 才使用 `-o`。

## 跨年机组检修

查询配置：

```bash
teap maintenance years config
```

预览 CSV：

```bash
teap maintenance years generate "$CASE_PATH" \
  --gen-type coal --gen-type nuclear --a-maint-rate 20 \
  --arrange-type 2 -o ./maintenance-years.csv
```

直接写入 case：

```bash
teap maintenance years insert "$CASE_PATH" \
  --gen-type coal --gen-type nuclear --a-maint-rate 20 \
  --arrange-type 2
```

`a-maint-rate` 范围为 0-100。`arrange-type` 只能为 0、1、2，具体业务含义以同一服务的 `years config` 为准，不从数字猜策略。

## 与仿真算法的关系

普通 `.tc` 任务类型 `3/4/5/6/101/102/103/105/301` 通过 `task start --job-type` 执行；`104` 短路计算使用 Core 的 BPA 专用入口，当前 CLI 尚未封装。完整输入和类型边界见 [general-operation/computation-modes.md](../general-operation/computation-modes.md)，任务操作见 [general-operation/task-lifecycle.md](../general-operation/task-lifecycle.md) 与 [teapresult/result-lifecycle.md](../teapresult/result-lifecycle.md)。滚动模拟的爬坡率、断面限额，规划的 DSM、机组最小在线数量、光热最小技术出力、供需预检查以及风光时变成本等能力由设备表、参数和时序表达，不应为每个算法字段创建临时 CLI 命令。

相关入口：

- 设备模型： [teapcase/device-tables.md](../teapcase/device-tables.md)
- 参数： [teapcase/parameters.md](../teapcase/parameters.md)
- 时序： [teapcase/timeseries-write.md](../teapcase/timeseries-write.md)
- 执行与结果： [general-operation/task-lifecycle.md](../general-operation/task-lifecycle.md)

## 运行方式筛选

运行方式筛选是已完成短期 `.tr` 的后处理算法。先获取服务端当前支持的完整场景分类：

```bash
teap result operation-modes scenarios
```

将所需场景按返回的全部分类键写入 JSON。后端要求分类键集合完整，即使某一类不选择场景也应传空数组：

```bash
teap result operation-modes run "$TASK_ID_OR_RESULT_PATH" \
  --scenarios-file ./operation-mode-scenarios.json \
  --summary-output ./typical-scenarios.xlsx \
  --detail-output ./typical-scenarios-detail.xlsx
```

命令返回结构化 `web_res` 和时间列表；两个 XLSX 下载选项均可省略。该算法仅支持 short-term 结果，不能对中长期或规划 `.tr` 反复尝试。

## 校验与失败处理

插入检修计划后回读受影响的机组/计划表；完成同一建模批次后，按 [SKILL.md 的校验节奏](../../SKILL.md#校验节奏) 在启动任务前执行一次提交门禁。

生成成功但插入失败时，不要把生成命令当作已修改 case。读取错误 `hints` 和 config，修正明确字段后再执行；同一 case 的算法写操作必须串行。
