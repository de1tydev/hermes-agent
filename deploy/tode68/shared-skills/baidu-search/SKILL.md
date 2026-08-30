---
name: baidu-search
description: Search the web using Baidu AI Search Engine (BDSE). Use for live information, documentation, or research topics.
platforms: [linux]
prerequisites:
  commands: [python3]
  env_vars: [BAIDU_API_KEY]
required_environment_variables:
  - name: BAIDU_API_KEY
    prompt: Baidu AI Search API key
metadata:
  hermes:
    tags: [Baidu, Search, Web]
---

# Baidu Search

Search the web via Baidu AI Search API.

## Prerequisites

### API Key Configuration
This skill requires `BAIDU_API_KEY` in the current Hermes Profile `.env`.

If you don't have an API key yet, please visit:
**https://console.bce.baidu.com/ai-search/qianfan/ais/console/apiKey**

For detailed setup instructions, see:
[references/apikey-fetch.md](references/apikey-fetch.md)

## Usage

```bash
python3 {baseDir}/scripts/search.py '<JSON>'
```

脚本仅使用 Python 标准库，直接调用即可；无需安装 `requests`，也不要另写搜索客户端。
需要保存中间结果时，统一写入当前 Profile 的 `$HERMES_HOME/workspace/tmp`。

## Request Parameters

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| query | str | yes | - | Search query |
| count | int | no | 10 | Number of results to return, range 1-50 |
| freshness | str | no | Null | Time range, two formats: format one is ”YYYY-MM-DDtoYYYY-MM-DD“, and format two includes pd, pw, pm, and py, representing the past 24 hours, past 7 days, past 31 days, and past 365 days respectively |

## Examples

```bash
# Basic search
python3 {baseDir}/scripts/search.py '{"query":"人工智能"}'

# Freshness first format "YYYY-MM-DDtoYYYY-MM-DD" example
python3 {baseDir}/scripts/search.py '{
  "query":"最新新闻",
  "freshness":"2025-09-01to2025-09-08"
}'

# Freshness second format pd、pw、pm、py example
python3 {baseDir}/scripts/search.py '{
  "query":"最新新闻",
  "freshness":"pd"
}'

# set count, the number of results to return
python3 {baseDir}/scripts/search.py '{
  "query":"旅游景点",
  "count": 20,
}'
```

## Current Status

Fully functional.
