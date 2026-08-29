# 结果分析与模板 A 报告

## 目录

- 分析入口
- 数据收集
- 专属内容写作
- Word 渲染
- 质量要求
- 交付

## 分析入口

报告只能基于已完成 task/result。先确认：

```bash
teap -o json task status --task-record-id "$TASK_ID"
```

任务未完成时等待或报告状态；任务失败时先按 [tasks-results-logs.md](tasks-results-logs.md) 诊断，不生成看似完整的报告。

用户要求“规划类仿真分析报告”“模板 A”或未指定报告方向时，使用 `template/模板A_规划类仿真分析报告_公文版.docx`。最终交付物是 `.docx`，不是图片集合。

## 数据收集

运行确定性收集阶段：

```bash
python skills/teap-cli-ops/scripts/generate_template_report.py "$TASK_ID" \
  --server "$BASE_URL" --work-dir "$WORK_DIR" --collect-only
```

脚本通过 `teap` CLI 获取任务和结果，不直接请求后端。当前核心 group：

- `parameter`
- `_result.key_summaries`
- `_result.cost_and_penalty`
- `_result.balance_df.neps_power -l`
- `_result.balance_df.neps_electricity_monthly -l`
- 典型日所需的 `_result.balance_df.neps_alltime_power.<hour> -l`

检查 `$WORK_DIR/report_context.json` 和 `$WORK_DIR/figures/*.png`。缺失关键 group 时如实标记，不用虚构值填模板。

## 专属内容写作

基于收集到的实际 case/result 生成 `$WORK_DIR/content.json`，只提供 `paragraphs`。不要替换标题、图题、表题、章节标题或 6.3 节敏感性建议。

分析应覆盖：

- 供需平衡和缺额时段；
- 装机结构与新能源消纳；
- 灵活性和备用充裕度；
- 成本与惩罚项驱动；
- 结果机理、风险和可执行规划建议。

段落必须引用实际指标和时间/对象，不写“根据数据显示”等空泛占位文本。建议每段 120-220 个中文字符，并服从模板容量。

## Word 渲染

```bash
python skills/teap-cli-ops/scripts/generate_template_report.py "$TASK_ID" \
  --server "$BASE_URL" --work-dir "$WORK_DIR" \
  --content-json "$WORK_DIR/content.json" --output "$REPORT_PATH"
```

迭代文案或图形时使用 `--reuse-data`，避免重复查询 TEAP。表格默认由脚本确定性生成；只有用户明确要求且内容 schema 校验通过时使用 `--allow-content-tables`。

## 质量要求

- Word 所有用户可见文本和文件名不得包含 emoji 或彩色符号。
- 正文中文保留模板 `仿宋_GB2312`，标题中文保留 `黑体`，英文和数字优先 Times New Roman。
- 图像中文必须使用真实 CJK 字体；找不到字体时应失败，不得输出方框字图。
- 图像使用脚本生成并插入既有图题后，图内不要重复绘制标题。
- 表格保持三线表，正文首行缩进两个中文字符；标题、图题、表题和单元格不缩进。
- 不向 Word 正文加入“数据命令附录”；审计信息保留在工作目录或交付说明。
- 不重建模板主体；保留页面设置、样式、package parts、既有标题和表格结构。

生成后检查 `.docx` 可打开、关键章节非空、表图数量合理、可见文本无 emoji。模板可能自带 `styles.xml/settings.xml` 兼容警告，要与新生成的 `word/document.xml` 错误区分。

## 交付

汇报 task ID、最终状态、使用的 result group、报告绝对路径和仍缺失的数据。保留工作目录以便审计和迭代。若报告没有真正生成，不得只给预期路径。
