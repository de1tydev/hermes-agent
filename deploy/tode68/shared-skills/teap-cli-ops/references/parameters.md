# 仿真参数操作

## 目录

- 参数对象
- 读取
- 单点设置
- Patch 与 Replace
- 选择仿真场景
- 最新机组指定出力参数
- 修改纪律

## 参数对象

`parameter` 是多层嵌套对象，不是设备 row。使用 `case parameter` 命令，不要用 `case row` 修改参数表。

不同 job type 使用不同分支，例如 `mid_term`、`long_term`、`short_term`、`market`；具体结构以目标 case 返回为准。

## 读取

读取全部参数：

```bash
teap -o json case parameter get "$CASE_PATH"
```

读取 dotted path：

```bash
teap -o json case parameter get "$CASE_PATH" \
  --path mid_term.simulation.max_solving_time
```

修改已有算例时先读取目标 path，确认分支存在和当前值类型。路径不存在时不要猜另一分支；读取完整参数或检查目标 job type。

## 单点设置

JSON 标量可保持布尔、数字和 null 类型：

```bash
teap -o json case parameter set "$CASE_PATH" \
  --path mid_term.simulation.max_solving_time --value-json '3600'

teap -o json case parameter set "$CASE_PATH" \
  --path mid_term.simulation.separate_zone --value-json 'true'
```

字符串必须是合法 JSON 字符串，例如 `--value-json '"CPLEX"'`。不要把布尔值写成字符串 `"true"`。

涉及日期范围或服务端覆盖确认时按帮助使用 `--confirm/-y`，不要在失败后自动加确认。

## Patch 与 Replace

同时更新多个同层字段时使用 JSON patch：

```bash
teap -o json case parameter set "$CASE_PATH" \
  -j '{"mid_term":{"simulation":{"max_solving_time":3600,"mipgap":5}}}'
```

默认采用深度合并，保留未提供的兄弟字段。只有用户明确要求整体替换且已读取完整对象时才使用 replace 语义；replace 可能删除未包含字段。

不要同时提供 dotted path 和 JSON patch 文件。大 patch 使用 `--data-file`/命令帮助中对应的文件选项，避免 shell 引号错误。

## 选择仿真场景

TEAP Core 使用 `case_info.scenario_selected` 选择本次仿真读取的时序场景。启动前设置精确场景名并回读：

```bash
teap -o json case parameter set "$CASE_PATH" \
  --path case_info.scenario_selected --value-json '"base"'
teap -o json case parameter get "$CASE_PATH" \
  --path case_info.scenario_selected
```

该值必须与设备绑定时序行的 `scenario` 完全一致。不要把空值或 `（空）` 当作新建模场景，也不要虚构 `task start --scenario`；完整地区与场景建模流程见 [modeling-guide.md](modeling-guide.md)。

## 最新机组指定出力参数

最新 TEAP3 在中长期、短期/滚动等 simulation 分支公开：

```text
simulation.gen_p_rate_match_onoff
```

默认 `false`：机组 `p_rate` 曲线约束指定出力，但不强制匹配机组开停状态。

设为 `true`：正目标匹配开机，零/近零目标匹配停机；若同时存在冲突的 `gen_on_off` 曲线，服务端会拒绝任务。

示例：

```bash
teap -o json case parameter set "$CASE_PATH" \
  --path mid_term.simulation.gen_p_rate_match_onoff --value-json 'true'
```

实际分支必须来自目标 case。旧键 `gen_p_bounds_forced_must_on` 仅由 teap3 做兼容，不要在新算例继续写旧键。

机组 `p_rate` 曲线创建与绑定见 [timeseries.md](timeseries.md)。

## 修改纪律

1. 根据任务类型选择参数分支。
2. 读取当前 path 和类型。
3. 做最小 set/patch。
4. 回读同一 path。
5. 提交任务前运行 `case validate`。

参数更新失败且 `retryable=false` 时，执行错误 `hints`；不要改用默认分支、旧键或全局配置掩盖错误。
