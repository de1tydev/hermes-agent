# 建模指引

## 目录

- 建模输入决策
- 地区选择规则
- 场景选择规则
- 风光与负荷曲线生成
- 缺失参数与字段补充
- 启动前选择仿真场景
- 完整示例
- 校验与停止条件

## 建模输入决策

开始创建设备和时序前，先从用户需求中确定：

1. 建模地区：省、地级市或区县；
2. 仿真年份和时间范围；
3. 场景名称；
4. 风电、光伏、负荷设备及其绑定关系；
5. 计算模式和 `job_type`。

创建任何时序前必须运行 `teap -o json timeseries types "$CASE_PATH" --sheet <设备表>`，并从返回的 `types[].value` 选择限定类型。完整规则和当前 Core 清单见 [timeseries-types.md](timeseries-types.md)。不能把前端显示文字（例如“指定出力率”）写入 `timeseries.type`。

地区或场景未给出时按本文的明确默认规则继续，不要生成空场景曲线。用户给出地区或场景时优先采用用户值，不得用默认值覆盖。

## 地区选择规则

### 用户没有指定地区

风电、光伏和负荷曲线统一使用江苏省：

- 风光 TMY：`--province 江苏省`，不指定市和区县；
- 负荷预测：`--area 江苏省`。

在完成汇报中说明采用了默认地区江苏省。不要根据设备名称、服务地址或当前工作目录猜其他地区。

### 用户指定省份

风光和负荷都使用该省。先通过服务端配置取得精确名称：

```bash
teap -o json timeseries tmy config --province 江苏
teap -o json timeseries load-forecast areas
```

TMY 命令接受省名简称并规范化，但建模记录和汇报应使用服务端返回的完整名称，例如 `江苏省`。

### 用户指定地级市或区县

风光 TMY 支持省、市、区县层级；负荷预测只按服务端支持的省级区域生成。因此必须执行不同的地区处理：

1. 根据中国行政区划解析该地市或区县所属省份；
2. 用 `timeseries tmy config --province <省>` 验证风光的父子层级；
3. 风光使用尽可能精确的省/市/区县；
4. 用 `timeseries load-forecast areas` 验证所属省份；
5. 负荷使用所属省份，不要把地市名传给 `--area`。

示例：

| 用户地区 | 风光 TMY 参数 | 负荷预测参数 |
| --- | --- | --- |
| 南京市 | `--province 江苏省 --city 南京市` | `--area 江苏省` |
| 深圳市 | `--province 广东省 --city 深圳市` | `--area 广东省` |
| 成都市 | `--province 四川省 --city 成都市` | `--area 四川省` |

不要维护或猜测一份不经验证的完整城市表。可先依据稳定的行政区划知识解析父省，再用 TMY 区域树和负荷区域列表验证。简称有歧义、行政归属不确定或目标服务不支持该省时，停止写入并请求用户确认；不要静默退回江苏或删除市级条件重试。

## 场景选择规则

每条用于本次计算的风、光、负荷时序都必须带非空 `scenario`，并且三类曲线使用同一个场景名称，除非用户明确要求分别建模。

按以下顺序选择场景：

1. 用户给出场景时，使用用户指定的精确名称；
2. 修改已有 case 且用户未指定时，先读取 `timeseries`；只有一个非空场景时复用它；
3. 已有 case 包含多个非空场景时，让用户选择，不擅自选第一个；
4. 新 case 或没有非空场景时，使用默认场景 `base`。

不要使用空字符串或展示占位值 `（空）` 作为新曲线场景。场景名称必须在以下位置完全一致：

- 风电 TMY 的 `--scenario`；
- 光伏 TMY 的 `--scenario`；
- 负荷预测的 `--scenario`；
- 其他参与计算的时序行的 `scenario`；
- `case_info.scenario_selected`。

大小写、空格和中文字符差异都会形成不同场景。不要把结果查询中的 `--scenario` 选项、典型运行方式分类和建模时序场景混为一谈。

## 风光与负荷曲线生成

先取得实际可用地区和设备 row ID：

```bash
teap -o json timeseries tmy config --province "$PROVINCE"
teap -o json timeseries load-forecast areas
teap -o json case sheet get "$CASE_PATH" wind
teap -o json case sheet get "$CASE_PATH" solar
teap -o json case sheet get "$CASE_PATH" load
teap -o json timeseries types "$CASE_PATH" --sheet wind
teap -o json timeseries types "$CASE_PATH" --sheet solar
teap -o json timeseries types "$CASE_PATH" --sheet load
```

生成曲线时始终显式传场景并绑定目标设备：

```bash
teap -o json timeseries tmy insert "$CASE_PATH" \
  --type wind --province "$PROVINCE" --city "$CITY" \
  --quantile 0.5 --period D --scenario "$SCENARIO" --bind-index "$WIND_ID"

teap -o json timeseries tmy insert "$CASE_PATH" \
  --type solar --province "$PROVINCE" --city "$CITY" \
  --quantile 0.5 --period D --scenario "$SCENARIO" --bind-index "$SOLAR_ID"

teap -o json timeseries load-forecast insert "$CASE_PATH" \
  --area "$LOAD_PROVINCE" --pred-year "$YEAR" \
  --scenario "$SCENARIO" --bind-index "$LOAD_ID"
```

省级风光建模时省略 `--city`，不要传空变量形成含糊命令。规划风光设备使用对应的 `--bind-plan-index`。

同一设备已绑定同类型、同场景曲线时，后端可能要求覆盖确认。先读取现有 `timeseries` 和设备绑定，明确替换、保留或新建场景；不要在失败后自动添加 `--confirm`。

## 缺失参数与字段补充

任务因参数或字段缺失而失败时，先使用 TEAP 的内置参数补充能力；只有内置补充不适用或补充后仍不满足计算要求，才依据模型语义做最小人工填写。不要看到缺失字段后立即猜值，也不要在 case 未发生有效变化时重复启动任务。

按以下顺序修复：

1. 读取失败任务和分析日志，定位精确的参数路径或 `sheet/row/field`：

   ```bash
   teap -o json task status --task-record-id "$TASK_ID"
   teap -o json log analyze --task-id "$TASK_ID"
   ```

2. 判断缺失项属于设备 row 字段、参数树，还是必须由用户决定的业务输入。
3. 新建设备且目标表支持模板初始化时，优先使用默认开启的 `--fill-defaults`。需要让修复意图清晰时显式写出该选项：

   ```bash
   teap -o json case row create "$CASE_PATH" gen \
     --name G1 --type coal --bus 0 --max_p_mw 600 --fill-defaults
   ```

4. 记录返回的 row ID，确认结构化输出包含 `"autofill": true`，再回读精确 row 并校验：

   ```bash
   teap -o json case row get "$CASE_PATH" gen "$ROW_ID"
   teap -o json case validate "$CASE_PATH"
   ```

5. 若内置补充后仍缺字段，或目标表没有稳定的补充处理器，先运行 `case structure`，并参考同类有效行、TEAP Core 模型语义、失败日志和用户目标，仅填写能可靠确定的最小字段集合。写后回读同一 row，再运行 `case validate`。

当前内置参数补充绑定在受支持模板表的 `case row create` 流程中，不存在通用的 `case autofill` 或 `case parameter autofill` 命令。对于导入的已有不完整 row，先读取该 row 及其引用关系；不得为了触发补全而直接删除并重建，以免产生重复设备或破坏 row ID 关系。确认缺失值后使用 `case row update` 做最小修复：

```bash
teap -o json case row get "$CASE_PATH" gen "$ROW_ID"
teap -o json case structure "$CASE_PATH"
teap -o json case row update "$CASE_PATH" gen "$ROW_ID" -j '{"min_p_rate":0.35}'
teap -o json case row get "$CASE_PATH" gen "$ROW_ID"
teap -o json case validate "$CASE_PATH"
```

参数树缺失时，先读取目标分支，再通过 `case parameter set` 精确写入；该命令是人工 patch，不是自动补全：

```bash
teap -o json case parameter get "$CASE_PATH" --path mid_term.simulation
teap -o json case parameter set "$CASE_PATH" \
  --path mid_term.simulation.max_solving_time --value-json '3600'
teap -o json case parameter get "$CASE_PATH" \
  --path mid_term.simulation.max_solving_time
```

内置默认值只是可计算初始化值，不证明模型满足用户的业务要求。容量、拓扑引用、成本、投退产日期、技术出力和运行边界无法从 schema、有效样例或用户要求可靠推出时，停止写入并询问用户。不要用 `--no-fill-defaults` 规避缺失错误，不要原样重试同一个失败任务，也不要把服务端仍然报告缺失的 case 送入下一次计算。

## 启动前选择仿真场景

`teap task start` 当前没有 `--scenario` 选项。TEAP Core 从 case 参数 `case_info.scenario_selected` 读取本次运行场景，并用它筛选设备绑定的时序。因此每次启动前都要显式设置并回读：

```bash
teap -o json case parameter set "$CASE_PATH" \
  --path case_info.scenario_selected --value-json '"base"'

teap -o json case parameter get "$CASE_PATH" \
  --path case_info.scenario_selected
```

脚本变量形式需要生成合法 JSON 字符串，不要直接把未转义的 shell 值拼入 `--value-json`。Agent 已知场景名称时，优先使用内联 JSON 的明确字面量；复杂字符先按命令帮助准备受控 JSON 输入。

确认回读值与曲线的 `scenario` 完全一致后再启动：

```bash
teap -o json case validate "$CASE_PATH"
teap -o json task start "$CASE_PATH" --job-type 4 --wait
```

一个 case 有多个场景时，每个场景分别执行“设置 `scenario_selected` -> 回读 -> 校验 -> 启动”，并记录各自 task ID。对同一 `.tc` 串行修改；不要并发切换场景参数。

## 完整示例

用户要求为南京市建立 2030 年风光荷基础场景时：

```bash
# 南京属于江苏；风光保留南京市精度，负荷使用江苏省。
teap -o json timeseries tmy config --province 江苏省 --city 南京市
teap -o json timeseries load-forecast areas

teap -o json timeseries tmy insert "$CASE_PATH" \
  --type wind --province 江苏省 --city 南京市 \
  --scenario base --bind-index 0
teap -o json timeseries tmy insert "$CASE_PATH" \
  --type solar --province 江苏省 --city 南京市 \
  --scenario base --bind-index 0
teap -o json timeseries load-forecast insert "$CASE_PATH" \
  --area 江苏省 --pred-year 2030 --scenario base --bind-index 0

teap -o json case parameter set "$CASE_PATH" \
  --path case_info.scenario_selected --value-json '"base"'
teap -o json case parameter get "$CASE_PATH" \
  --path case_info.scenario_selected
teap -o json case validate "$CASE_PATH"
teap -o json task start "$CASE_PATH" --job-type 4 --wait
```

示例 row ID 仅说明命令顺序。实际操作必须使用创建设备或读取 sheet 后得到的 ID，不要照抄 `0`。

## 校验与停止条件

启动前至少确认：

1. TMY 省/市/区县层级已由服务端区域树验证；
2. 负荷 `--area` 是服务端返回的省级区域；
3. 风、光、负荷曲线均有非空且一致的 `scenario`；
4. 曲线 ID 已绑定到正确设备；
5. `case_info.scenario_selected` 与目标曲线场景完全一致；
6. `case validate` 成功。

区域、绑定或场景检查失败时停止启动，读取精确 sheet/parameter 修正。不要通过删除 `--scenario`、改用空场景、把地市直接传给负荷接口或换一个省份来规避错误。
