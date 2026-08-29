---
name: tode-jira
description: TODE Jira 项目管理工具集。当用户要求在自建 Jira 上查询、创建、更新、关闭、搜索、关联或流转 Jira Issue 时使用。覆盖创建前查重确认、Jira Wiki Markup 描述规范、Jira CLI 调用约束与参与人字段编辑。TEAP PR 巡检与催办已拆到独立 skill `teap-pr-check`。
platforms: [linux]
prerequisites:
  commands: [jira, python3]
  env_vars: [JIRA_API_TOKEN, JIRA_CONFIG_FILE, JIRA_AUTH_TYPE]
required_environment_variables:
  - name: JIRA_API_TOKEN
    prompt: TODE Jira API token
  - name: JIRA_CONFIG_FILE
    prompt: Jira CLI config path
  - name: JIRA_AUTH_TYPE
    prompt: Jira authentication type
---
<!--
Role: local-control
Scope: skill:tode-jira
Authority: high
Canonical: true
-->

# TODE Jira

## 核心用法

### 查询、查看、编辑、流转 Jira Issue

先读取 `references/auth-and-setup.md`，再按 `references/jira-cli-cheatsheet.md` 执行对应 CLI 命令。

### 创建新 Issue

严格按下面顺序执行：

1. 先读取 `references/create-issue-workflow.md`
2. 先做查重，不要直接创建
3. 若查到疑似重复或冲突 Issue，先向 Master 汇报候选项并等待确认
4. 只有在确认应新建后，才按 `references/issue-body-templates.md` 生成 Jira Wiki Markup 正文
5. 创建前必须先用 `scripts/lint_jira_markup.py` 校验正文；未通过不得提交

### TEAP PR 巡检与催办

这部分**已从 `tode-jira` 拆出**，当前归独立 skill：`skills/teap-pr-check/`。

原因很简单：
- PR 巡检本质上是 **TEAP + Gitea pull request workflow**
- 它可能展示 Jira issue 链接，但不属于“通用 Jira 操作”本身

因此：
- 通用 Jira issue 操作 → 留在 `tode-jira`
- TEAP PR 巡检 / 催办 / 汇总卡片 → 改走 `teap-pr-check`

## 当前工作区的硬规则

- 创建前必须查重
- 发现疑似重复或冲突 Issue 时，先报出候选项并确认，不要擅自继续创建
- Issue 描述必须使用 Jira Wiki Markup，不要使用 Markdown
- 当前 Jira 实例里，issue/comment 正文默认不要使用 `#` 编号列表；统一改用 `*` 列表
- 普通 issue 正文默认不要使用 `h1.`；只用 `h2.` / `h3.` 做结构标题
- Jira 正文默认不要写 LaTeX / MathJax 公式；若需要表达公式、约束或伪公式，统一改写成 ASCII 可读形式，并放进 `{code}` 块，避免 `^`、`$`、`\frac`、`\sum`、`\begin` 等语法在 Jira Wiki Markup 里乱渲染
- 创建前正文必须通过 `scripts/lint_jira_markup.py`
- 当用户要求设置“参与人”时，优先直接编辑 Jira 的参与人字段；当前实例里它是 `customfield_10203`，不要误降级成 watcher 或“先发评论等确认”
- 合并关闭的 Issue 状态设为“取消”，不要设为“Done”
- 当 Issue 从“待开发”切到“开发中”时，先把完整开发方案以评论形式补到该 Issue，再继续后续开发流程

## 参考资料

- [认证与环境](references/auth-and-setup.md) — 当前工作区的 Jira CLI、server、token 加载与自检方式
- [创建 Issue 工作流](references/create-issue-workflow.md) — 查重、确认、创建、补 comment/link 的完整流程
- [Issue 正文模板](references/issue-body-templates.md) — Bug / 新功能 / 改进 的 Jira Wiki Markup 模板
- [Jira 数学公式 ASCII 写法速查表](references/jira-formula-ascii-cheatsheet.md) — 公式 / 约束 / 目标函数的 ASCII 改写规则与示例
- [Jira CLI 速查](references/jira-cli-cheatsheet.md) — 常用命令、参数与脚本调用示例
- TEAP PR 巡检 / 催办 / 汇总卡片 → 改看 `skills/teap-pr-check/`
