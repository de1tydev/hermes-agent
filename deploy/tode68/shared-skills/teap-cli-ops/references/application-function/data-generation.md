# 风光荷数据与曲线的生成

## 目录

- 风光 TMY
- 负荷预测
- 风光极端曲线
- 内置时序生成

分工：本文件覆盖使用 `teap timeseries ...` 生成风光荷数据并绑定到算例；在 `timeseries` sheet 中手工写入/更新曲线见 [teapcase/timeseries-write.md](../teapcase/timeseries-write.md)。

## 风光 TMY

用户未指定地区时使用 `--province 江苏省`。用户指定地市时先解析并验证所属省份，再同时传省和市；不要只传城市或猜父省。每次插入都必须指定非空 `--scenario`。

先检查区域树：

```bash
teap timeseries tmy config
teap timeseries tmy config --province 四川
teap timeseries tmy config --province 四川 --city 成都
```

预览曲线：

```bash
teap timeseries tmy preview --type wind --province 四川 --quantile 0.5 --period D
```

生成并绑定：

```bash
teap timeseries tmy insert "$CASE_PATH" \
  --type wind --province 四川 --city 成都 --quantile 0.5 --period D \
  --scenario base --bind-index 2
```

`--bind-index` 绑定现有 `wind/solar`，`--bind-plan-index` 绑定相应 plan 表。CLI 会严格校验省/市/区父子层级；区域错误不可通过删掉层级后盲目重试，应先读 config 返回的有效节点。

## 负荷预测

负荷预测使用省级区域，不支持精确到地市。用户指定地市或区县时，自动解析其所属省份并以该省作为 `--area`；例如南京市使用 `--area 江苏省`。用户未指定地区时同样使用江苏省。先运行 `areas` 验证服务端支持的完整省名。

列出可用区域：

```bash
teap timeseries load-forecast areas
```

仅预测/下载：

```bash
teap timeseries load-forecast predict --area 江苏省 --pred-year 2030
```

插入并绑定负荷：

```bash
teap timeseries load-forecast insert "$CASE_PATH" \
  --area 江苏省 --pred-year 2030 --bind-index 5 --scenario base
```

历史年份、行业电量、极值比例等专业选项以子命令 `-h` 为准。不要把地市名直接传给负荷接口，不要编造区域名；先用 `areas`。

## 风光极端曲线

基于 case 中已有场景生成极端风电或光伏曲线。先预览并按需下载 CSV：

```bash
teap timeseries extreme preview "$CASE_PATH" \
  --type wind --scenario base --new-scenario extreme-low \
  --confidence-level 95 --bind-index 2 -o ./extreme.csv
```

确认后直接插入并绑定正常或规划设备：

```bash
teap timeseries extreme insert "$CASE_PATH" \
  --type solar --scenario base --new-scenario extreme-high \
  --confidence-level 95 --bind-plan-index 3 --max-p-rate 0.9
```

`confidence-level` 范围为 0-100，`max-p-rate` 范围为 0-1，且至少提供一个 `--bind-index` 或 `--bind-plan-index`。后端要求二次确认时命令返回 `code=2`；读取消息后再决定是否加 `--confirm`，不要自动确认。

## 内置时序生成

除上述功能外，`teap timeseries ...` 还提供其他内置时序生成器（如机组指定出力等模板化曲线）。具体子命令以 `teap timeseries -h` 与各子命令 `-h` 为准；生成后仍需按 [teapcase/timeseries-write.md](../teapcase/timeseries-write.md) 的更新与回读节奏定点校验曲线与绑定。
