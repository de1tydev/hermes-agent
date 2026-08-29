---
name: zhipu-search
description: |
  智谱 Coding Plan MCP 工具集：联网搜索 + 网页读取 + 开源仓库。
  基于 Streamable HTTP MCP 协议，通过 Coding Plan Key 认证。
platforms: [linux]
prerequisites:
  commands: [python3]
  env_vars: [ZHIPU_API_KEY]
required_environment_variables:
  - name: ZHIPU_API_KEY
    prompt: Zhipu Coding Plan API key
---

# 智谱 Coding Plan MCP Skill

## 核心能力

| MCP Server | 工具 | 说明 |
|---|---|---|
| web-search-prime | `webSearchPrime` | 联网搜索，返回标题/URL/摘要 |
| web-reader | `webReader` | 网页内容抓取，返回 Markdown |
| zread | `search_doc` / `get_repo_structure` / `read_file` | GitHub 开源仓库文档/结构/代码 |

## 依赖

- Python 3（标准库，无第三方依赖）
- Coding Plan API Key（从当前 Hermes Profile 的 `ZHIPU_API_KEY` 读取）

## API Key 管理

Key 只存储在当前 Hermes Profile 的 `.env`，由 Skill 声明按需传给子进程。不要创建 `skill.env`，也不要在命令行或输出中传递 Key。

## 使用方式

### CLI 调用

```bash
# 联网搜索
python3 {baseDir}/scripts/search.py search -q '关键词'
python3 {baseDir}/scripts/search.py search -q '关键词' --recency oneWeek

# 网页读取
python3 {baseDir}/scripts/search.py read --url 'https://example.com'

# 开源仓库 - 搜索文档
python3 {baseDir}/scripts/search.py zread search --repo NousResearch/hermes-agent -q 'getting started'

# 开源仓库 - 目录结构
python3 {baseDir}/scripts/search.py zread structure --repo NousResearch/hermes-agent

# 开源仓库 - 读取文件
python3 {baseDir}/scripts/search.py zread read --repo NousResearch/hermes-agent -f README.md
```

## 文件路径

```
{baseDir}/
├── SKILL.md          ← 本文件
└── scripts/
    └── search.py     ← CLI 工具
```
