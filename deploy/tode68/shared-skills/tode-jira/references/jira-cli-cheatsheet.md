# Jira CLI 速查

Binary: `jira`

实际调用时，认证变量由当前 Hermes Profile 和 Skill 的环境变量声明注入。

## 创建前查重

优先先跑多轮查重脚本：

```bash
python3 {baseDir}/scripts/search_duplicate_issues.py \
  --project TEAP \
  --title "拟创建的标题" \
  --description-file /tmp/issue_body.jira \
  --keyword 关键词1 \
  --keyword 关键词2
```

- 标题、描述、关键词可以一起参与多轮查询
- 关键词优先放模块名、功能名、页面名、接口名、算法名、错误现象等高区分度词
- 若命中疑似重复或冲突项，先确认，再创建

## 查询 Issue

```bash
# JQL 搜索（不要在 JQL 里手写 ORDER BY）
jira issue list -p TEAP -q 'project = TEAP AND text ~ "关键词"' --paginate 0:100 --raw

# 查看详情（纯文本）
jira issue view TEAP-XXXX --plain

# 查看详情（原始 JSON）
jira issue view TEAP-XXXX --raw

# 带评论
jira issue view TEAP-XXXX --comments 5
```

## 创建 Issue

```bash
# 推荐：正文先写本地文件，再 lint，再用 --template 创建
python3 {baseDir}/scripts/lint_jira_markup.py /tmp/issue_body.jira
jira issue create -p TEAP -t "新功能" -s "标题" -y 高 -C 算法 --template /tmp/issue_body.jira --no-input

# 也可直接用 -b 传单行或较短正文
jira issue create -p TEAP -t "新功能" -s "标题" -y 高 -C 算法 -b "正文" --no-input
```

## 编辑 Issue

```bash
# 修改标题和描述
jira issue edit TEAP-XXXX -s "新标题" -b "新描述" --no-input

# 修改负责人、优先级、组件
jira issue edit TEAP-XXXX -a "新负责人" -y 中 -C 前端 --no-input
```

## 状态流转

```bash
# 查看可用状态
jira issue move TEAP-XXXX

# 切换状态
jira issue move TEAP-XXXX "开发中"
```

## 参与人（不是 watcher）

当前 Jira 实例里：
- `参与人` = `customfield_10203`
- 字段类型 = multi-user picker
- 与 `watchers` 不是一回事
- Bearer token + `robot` 账号在 `editmeta` 允许时可以直接编辑这个字段

推荐直接用脚本，不要把“参与人”误降级成 watcher/comment：

```bash
python3 {baseDir}/scripts/set_issue_participants.py TEAP-XXXX 黄叶飞 杨洋
```

若只想追加、不覆盖原参与人：

```bash
python3 {baseDir}/scripts/set_issue_participants.py TEAP-XXXX 黄叶飞 杨洋 --mode add
```

## 评论

```bash
# 直接加评论
jira issue comment add TEAP-XXXX "评论内容" --no-input

# 从文件加载评论内容
jira issue comment add TEAP-XXXX --template /tmp/comment.jira --no-input
```

## 链接 Issue

```bash
# 建立关联
jira issue link TEAP-1 TEAP-2 Relates

# 明确标记重复关系（以当前实例支持的 link type 为准）
jira issue link TEAP-1 TEAP-2 Duplicate
```

## 注意事项

- `--no-input` 用于非交互模式，脚本调用时优先加上
- Issue 描述必须使用 Jira Wiki Markup，不要使用 Markdown
- 长正文优先使用 `--template /tmp/issue_body.jira`
- 在当前 Jira 实例里，正文默认不要使用 `#` 编号列表；统一改用 `*` 列表
- 普通 issue 正文默认不要使用 `h1.`；用 `h2.` / `h3.` 做标题即可
- 正文若涉及公式 / 约束 / 伪公式，统一改成 ASCII 可读形式并放进 `{code}` 块；不要直接写 LaTeX / MathJax
- 创建前先跑 `python3 {baseDir}/scripts/lint_jira_markup.py /tmp/issue_body.jira`
- 当用户要求设置“参与人”时，优先使用 `scripts/set_issue_participants.py` 写 `customfield_10203`，不要误用 watcher 或评论代替
- 合并关闭的 Issue 状态设为“取消”，不要设为“Done”
