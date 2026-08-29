# 端到端工作流

## 目录

- 发现或创建 case
- 构建新 case
- 修改已有 case
- 时序与参数
- 执行和结果
- 校验强度

## 发现或创建 case

```bash
teap -o json auth status
teap -o json case list -f "$KEYWORD"
```

没有目标 case 时：

```bash
teap -o json case create "$CASE_NAME"
```

记录返回的服务端 `path`。已有本地文件时使用 `case import`，不要复制到猜测的服务目录。

## 构建新 case

按依赖顺序串行写入：

```bash
teap -o json case row create "$CASE_PATH" zone --name Z1
teap -o json case row create "$CASE_PATH" bus --name B1 --zone 0
teap -o json case row create "$CASE_PATH" gen \
  --name G1 --type coal --bus 0 --max_p_mw 600
teap -o json case row create "$CASE_PATH" load \
  --name L1 --bus 0 --max_p_mw 400
```

信任成功返回的 row ID。不要在每次创建后读取全表；完成一类对象后定点读取该 sheet。

储能、线路和其他设备的字段及 companion 规则见 [device-tables.md](device-tables.md)。

## 修改已有 case

1. 读取目标 sheet 或 row；
2. 确认 index、当前值和关系；
3. 用 row update 做最小 patch；
4. 回读同一 row；
5. 若删除，先检查引用关系。

```bash
teap -o json case row get "$CASE_PATH" gen 3
teap -o json case row update "$CASE_PATH" gen 3 --max_p_mw 650
teap -o json case row get "$CASE_PATH" gen 3
```

服务端拒绝字段时再运行 `case structure`，不要在失败命令上轮换猜测字段名。

## 时序与参数

风光优先用 TMY，负荷优先用 load forecast，本地抽象曲线使用紧凑模板。先创建设备，再绑定返回的 row ID。

风光荷地区和仿真场景必须在写曲线前确定。用户未指定地区时统一使用江苏省；指定地市时，风光使用服务端支持的省/市层级，负荷自动改用该地市所属省份。新曲线必须写非空场景，启动前必须设置同名 `case_info.scenario_selected`。完整决策和命令见 [modeling-guide.md](modeling-guide.md)。

先查询目标设备表的标准类型；只使用返回的 `types[].value`，不能写前端显示名：

```bash
teap -o json timeseries types "$CASE_PATH" --sheet gen
```

通用曲线创建并绑定：

```bash
teap -o json case row create "$CASE_PATH" timeseries \
  -j '{"period":"year","value":0.8,"type":"gen.p_rate","value_type":"multiply","scenario":"base"}' \
  --bind-sheet gen --bind-index 3
```

机组指定出力的 `-1/[0,1]` 语义及 match-on/off 参数见 [timeseries.md](timeseries.md) 和 [parameters.md](parameters.md)。

参数单点更新：

```bash
teap -o json case parameter get "$CASE_PATH" --path mid_term.simulation
teap -o json case parameter set "$CASE_PATH" \
  --path mid_term.simulation.max_solving_time --value-json '3600'
```

## 执行和结果

```bash
teap -o json case parameter set "$CASE_PATH" \
  --path case_info.scenario_selected --value-json '"base"'
teap -o json case parameter get "$CASE_PATH" \
  --path case_info.scenario_selected
teap -o json case validate "$CASE_PATH"
teap -o json task start "$CASE_PATH" --job-type 4 --wait
```

成功后记录 task ID，再查精确结果：

```bash
teap -o json result get "$TASK_ID" -g _result.key_summaries
```

失败后不重复 start：

```bash
teap -o json log analyze --task-id "$TASK_ID"
```

修正 finding 指向的最小对象，重新校验，再启动一个新任务。若 finding 指出设备参数或字段缺失，新建设备应优先使用受支持模板默认开启的 `case row create --fill-defaults` 内置补充，回读 row 并运行 `case validate`；只有补充能力不适用或仍不满足时，才依据 schema、同类有效行、Core 模型语义和用户要求做最小人工填写。已有 row 和参数树的安全修复方式及停止条件见 [modeling-guide.md](modeling-guide.md)，不要虚构通用 autofill 命令或原样重试失败任务。

## 校验强度

新建空 case 且使用常见模板：

- 创建前不读所有空 sheet；
- 使用 row.create 返回 ID；
- 每类设备完成后读一次触达 sheet；
- 最后 `case validate`。

修改已有或导入 case：

- 写前读目标 row；
- 涉及关系时读关联 sheet；
- 写后回读目标 row；
- 任务前完整 CLI 校验。

只有命令失败、返回缺字段或业务关系不明确时升级探查。不要用大量只读命令替代明确的建模步骤，也不要省略高风险写操作前的必要读取。
