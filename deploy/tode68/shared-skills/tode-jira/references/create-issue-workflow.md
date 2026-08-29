# 创建 Issue 工作流

## 目标

把“先查重、再确认、后创建”固定成稳定流程，避免重复或冲突 Issue。

## 0. 先准备输入

至少先明确：

- 拟创建的标题
- 问题背景 / 需求背景
- 期望效果或当前问题
- 影响范围
- 预计 `type`
- 必填 `component`

在这一步就先从标题和正文里提炼 4~8 个关键词；必要时补充同义词、模块名、页面名、接口名、算法名、错误现象等词。

## 1. 先做查重

优先使用脚本：

```bash
python3 {baseDir}/scripts/search_duplicate_issues.py \
  --project TEAP \
  --title "拟创建的标题" \
  --description-file /tmp/issue_body.jira \
  --keyword 关键词1 \
  --keyword 关键词2
```

### 查重脚本的使用要求

- 不要只传标题；若已有较完整描述，优先把描述也传入
- 关键词不要只放泛词；优先放最能区分业务语义的词
- 若第一轮结果看起来过窄，补充关键词后再跑一轮
- 若第一轮结果过宽，增加更具体的模块词、场景词、异常词，再跑一轮

## 2. 判断是否需要停下确认

若查重结果里出现以下任一情况，先停下并汇报，再等待确认：

1. 标题或正文语义明显相同的现有 Issue
2. 同一模块、同一现象、同一目标的历史 Issue
3. 虽不是完全重复，但会与拟创建 Issue 产生冲突、覆盖或拆分不清的候选项

汇报时至少列出：

- Issue Key
- 标题
- 当前状态
- 为什么怀疑它重复 / 冲突 / 高度相关

默认展示最相关的 3~5 条即可。

## 3. 确认后的分支处理

### A. 确认“不新建”

根据确认结果执行其一：

- 直接转为更新已有 Issue
- 给已有 Issue 补 comment
- 链接相关 Issue
- 关闭这次创建动作

### B. 确认“仍需新建”

只有在这时才继续创建新 Issue。

创建前：

1. 先从 `issue-body-templates.md` 选择正确模板
2. 用 Jira Wiki Markup 生成正文
3. 若正文里包含公式 / 约束 / 伪公式，先改写成 ASCII 可读形式并放进 `{code}` 块，不要保留 LaTeX / MathJax
4. 运行 `python3 {baseDir}/scripts/lint_jira_markup.py /tmp/issue_body.jira`，未通过则先修正文
5. 若已有相关旧 Issue，在正文或后续 comment 中说明区别与关系

## 4. 创建 Issue

推荐把正文先落为本地文件，再通过 `--template` 创建：

```bash
python3 {baseDir}/scripts/lint_jira_markup.py /tmp/issue_body.jira
jira issue create \
  -p TEAP \
  -t "新功能" \
  -s "标题" \
  -y 高 \
  -C 算法 \
  --template /tmp/issue_body.jira \
  --no-input
```

## 5. 创建后补 comment / link（按需）

若存在相关旧 Issue，但确认后仍需新建：

### 补 comment

```bash
jira issue comment add TEAP-XXXX --template /tmp/comment.jira --no-input
```

### 建 link

```bash
jira issue link NEW-ISSUE OLD-ISSUE Relates
```

若明确是重复关系，可用更明确的 link type（如 `Duplicate` / `Duplicates`），但要以当前 Jira 实例实际可用类型为准。

## 6. 切到“开发中”前的前置动作

当该 Issue 后续从“待开发”切到“开发中”时：

1. 先把完整开发方案写成 Jira Wiki Markup comment
2. 再执行状态流转

不要先切状态，再补方案。
