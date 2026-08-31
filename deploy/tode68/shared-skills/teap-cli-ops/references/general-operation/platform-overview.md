# TEAP 平台概览

## 目录

- TEAP 是什么
- 主要能力范围
- 常见研究类型
- 核心算例概念
- 为什么优先使用 CLI
- Agent 工作姿态

## TEAP 是什么

TEAP 是南京图德科技有限公司开发的电力系统规划与仿真平台，公司官网为 https://www.tode.cn/ 。在 `teap-cli` 和本 skill 的语境中，TEAP 应被视为一个面向电力系统算例建模、任务执行、结果分析和报告生成的专业系统。

TEAP 主要用于：

- 构建和维护电力系统算例。
- 关联负荷、风电、光伏等时序曲线。
- 配置中长期、潮流、市场等仿真参数。
- 执行规划或仿真任务。
- 通过结构化接口分析仿真结果。
- 基于仿真结果生成分析报告。

`teap-cli` 不是普通 REST 包装器，而是围绕算例编辑、任务执行和结果检查设计的任务型命令行入口。

## 主要能力范围

当前 CLI 覆盖的能力族与后端工作流对应：

- 算例创建与搜索。
- 通过 sheet、row 和 parameter 编辑算例。
- 通过 TMY 和 load forecast 模块生成后端原生时序。
- 任务启动、轮询和等待。
- 结构化结果读取；用户明确要求时导出本地文件。
- 模板 A 规划类仿真分析报告生成。

## 常见研究类型

当前正式配置包括仿真与规划、容量平衡、电网静态计算和市场模拟四组能力。完整 `job_type`、配置名、输入格式、普通/BPA 启动入口以及历史类型边界见 [computation-modes.md](computation-modes.md)。

不要假设每个算例支持所有研究类型，也不要把 CLI 能列出的类型理解为普通 `task start` 全部可用。根据用户的分析目标和输入类型选择匹配的作业类型。

## 核心算例概念

### Case（算例）

`case` 是 TEAP 的主建模对象，通常以服务端 `.tc` 文件形式保存。

### Sheet（逻辑表）

`sheet` 是算例内部的一张可编辑逻辑表。常见 sheet 包括：

- `parameter`
- `timeseries`
- `zone`
- `bus`
- `gen`
- `wind`
- `solar`
- `load`
- `storage`
- `stogen`

实际算例中还可能存在 subtype 或结构相关 sheet，例如 `gen.<subtype>`。

### Row（行）

`row` 是 sheet 中的一条设备或记录。设备 CRUD 通常通过 row 命令完成。

### Parameter（参数）

`parameter` 是嵌套仿真参数对象，不是简单的平铺 key-value。优先使用 dotted path 做单点更新。

### Timeseries（时序）

`timeseries` 保存时序曲线。后端通常需要 8760 类数据，CLI 通过下列方式降低命令复杂度：

- 本地紧凑模板扩展。
- 后端 TMY 风/光曲线生成。
- 后端负荷预测曲线生成。

### Task 和 Result（任务与结果）

`task` 是一次带具体 `job_type` 的算例运行记录。普通结果分析使用服务端结构化 result group。只有用户明确要求取得本地文件时，才下载 `.tr` 或把结果内嵌输入模型导出为本地 `.tc`；后者不是服务端算例复制或继续修改的入口。

## 为什么优先使用 CLI

使用 `teap-cli` 的原因：

- 输出紧凑，适合 Agent 自动解析。
- case-editing 工作流贴合后端 sheet-write 语义。
- 常见设备支持字段式 row create/update。
- 紧凑时序模板避免命令行塞入大量 8760 数据。
- 内置 TMY 和 load forecast 后端原生生成能力。
- 认证和 CAS token 刷新由 CLI 统一处理；用户/工作区隔离由宿主环境负责。

## Agent 工作姿态

使用本 skill 时：

- 以命令序列为中心推理，不绕开 CLI。
- 记录稳定的 case path、row index、timeseries index 和 task ID。
- 修改已有算例前先检查，刚新建且结构明确的空算例可先写后验。
- 按依赖顺序建模。
- 将结果分析与算例构建分成两个阶段。
- 生成 Word 报告时不写入 emoji，避免 Word 查看或转换时乱码。
