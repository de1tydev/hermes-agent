#!/usr/bin/env python3
"""
智谱搜索 Skill - Coding Plan MCP Client
支持三个 MCP Server：
  - web-search-prime: 联网搜索
  - web-reader: 网页读取
  - zread: 开源仓库文档/代码
"""

import json
import argparse
import sys
import os
import urllib.request
import urllib.error

MCP_SERVERS = {
    "web-search-prime": "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp",
    "web-reader": "https://open.bigmodel.cn/api/mcp/web_reader/mcp",
    "zread": "https://open.bigmodel.cn/api/mcp/zread/mcp",
}


def load_api_key() -> str:
    key = os.environ.get("ZHIPU_API_KEY")
    if key:
        return key
    raise ValueError("未找到 ZHIPU_API_KEY，请在当前 Hermes Profile 中配置环境变量")


def mcp_call(server_name: str, tool_name: str, arguments: dict, api_key: str) -> dict:
    """通过 Streamable HTTP MCP 协议调用工具（带 session 管理）"""
    url = MCP_SERVERS[server_name]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }

    # Step 1: initialize（获取 session id）
    init_payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "zhipu-search-skill", "version": "2.0"}
        }
    }
    init_result, session_id = _post_mcp(url, init_payload, headers)
    if "error" in init_result:
        return init_result

    # Step 2: 带 session id 调用 tools/call
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    call_payload = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments}
    }
    result, _ = _post_mcp(url, call_payload, headers)
    return result


def _post_mcp(url: str, payload: dict, headers: dict) -> tuple:
    """发送 MCP 请求并解析 SSE 响应，返回 (result, session_id)"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    session_id = None
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            session_id = resp.headers.get("Mcp-Session-Id", "")
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            if "text/event-stream" in content_type:
                return _parse_sse(body), session_id
            elif "application/json" in content_type:
                return json.loads(body), session_id
            else:
                return {"error": f"未知 Content-Type: {content_type}", "body": body[:500]}, session_id
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}: {body[:500]}"}, session_id
    except Exception as e:
        return {"error": str(e)}, session_id


def _parse_sse(body: str) -> dict:
    """解析 SSE 响应，提取 JSON-RPC result"""
    for line in body.split("\n"):
        if line.startswith("data:"):
            data_str = line[5:].strip()
            if data_str:
                try:
                    msg = json.loads(data_str)
                    if "result" in msg:
                        return msg["result"]
                    elif "error" in msg:
                        return {"error": msg["error"]}
                except json.JSONDecodeError:
                    pass
    return {"error": "未能从 SSE 响应中提取结果", "raw": body[:500]}


def extract_text(result: dict) -> str:
    """从 MCP 工具返回中提取文本内容"""
    if "error" in result:
        return f"错误: {result['error']}"
    content = result.get("content", [])
    if isinstance(content, list):
        texts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                texts.append(c.get("text", ""))
        return "\n".join(texts) if texts else str(result)
    return str(content)


# === 搜索 ===

def web_search(query: str, recency: str = None, content_size: str = "medium",
               location: str = "cn", api_key: str = None) -> str:
    params = {"search_query": query, "content_size": content_size, "location": location}
    if recency:
        params["search_recency_filter"] = recency
    result = mcp_call("web-search-prime", "web_search_prime", params, api_key)
    return extract_text(result)


# === 网页读取 ===

def web_read(url: str, api_key: str = None) -> str:
    result = mcp_call("web-reader", "webReader", {
        "url": url, "return_format": "markdown", "retain_images": False
    }, api_key)
    return extract_text(result)


# === 开源仓库 ===

def zread_search(repo_name: str, query: str, api_key: str = None) -> str:
    result = mcp_call("zread", "search_doc", {
        "repo_name": repo_name, "query": query
    }, api_key)
    return extract_text(result)


def zread_structure(repo_name: str, dir_path: str = None, api_key: str = None) -> str:
    args = {"repo_name": repo_name}
    if dir_path:
        args["dir_path"] = dir_path
    result = mcp_call("zread", "get_repo_structure", args, api_key)
    return extract_text(result)


def zread_file(repo_name: str, file_path: str, api_key: str = None) -> str:
    result = mcp_call("zread", "read_file", {
        "repo_name": repo_name, "file_path": file_path
    }, api_key)
    return extract_text(result)


def format_search_results(raw_text: str) -> str:
    """格式化搜索结果为可读文本"""
    try:
        items = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return raw_text

    if not isinstance(items, list):
        return raw_text

    lines = []
    for i, item in enumerate(items[:10], 1):
        if not isinstance(item, dict):
            lines.append(f"{i}. {item}")
            continue
        title = item.get("title", "")
        link = item.get("link", item.get("url", ""))
        snippet = item.get("content", item.get("summary", ""))
        lines.append(f"{i}. **{title}**")
        if link:
            lines.append(f"   {link}")
        if snippet:
            lines.append(f"   {snippet[:300]}")
        lines.append("")
    return "\n".join(lines) if lines else raw_text


def main():
    parser = argparse.ArgumentParser(description="智谱 Coding Plan MCP 工具集")
    sub = parser.add_subparsers(dest="command")

    # 搜索
    p_search = sub.add_parser("search", help="联网搜索")
    p_search.add_argument("--query", "-q", required=True, help="搜索关键词")
    p_search.add_argument("--recency", choices=["oneDay", "oneWeek", "oneMonth", "oneYear", "noLimit"])
    p_search.add_argument("--content-size", "-c", choices=["medium", "high"], default="medium")
    p_search.add_argument("--location", "-l", choices=["cn", "us"], default="cn")
    p_search.add_argument("--raw", action="store_true", help="输出原始 JSON")

    # 网页读取
    p_read = sub.add_parser("read", help="读取网页内容")
    p_read.add_argument("--url", "-r", required=True, help="目标 URL")

    # 开源仓库
    p_zread = sub.add_parser("zread", help="开源仓库工具")
    zread_sub = p_zread.add_subparsers(dest="zread_cmd")

    p_zs = zread_sub.add_parser("search", help="搜索仓库文档")
    p_zs.add_argument("--repo", required=True, help="owner/repo")
    p_zs.add_argument("--query", "-q", required=True, help="搜索关键词")

    p_zst = zread_sub.add_parser("structure", help="获取仓库结构")
    p_zst.add_argument("--repo", required=True, help="owner/repo")
    p_zst.add_argument("--dir", default=None, help="子目录路径")

    p_zf = zread_sub.add_parser("read", help="读取仓库文件")
    p_zf.add_argument("--repo", required=True, help="owner/repo")
    p_zf.add_argument("--file", "-f", required=True, help="文件路径")

    args = parser.parse_args()

    try:
        api_key = load_api_key()
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "search":
        print(f"[智谱搜索] {args.query}", file=sys.stderr)
        raw = web_search(args.query, args.recency, args.content_size, args.location, api_key)
        if args.raw:
            print(raw)
        else:
            print(format_search_results(raw))

    elif args.command == "read":
        print(f"[网页读取] {args.url}", file=sys.stderr)
        print(web_read(args.url, api_key))

    elif args.command == "zread":
        if args.zread_cmd == "search":
            print(f"[仓库搜索] {args.repo}: {args.query}", file=sys.stderr)
            print(zread_search(args.repo, args.query, api_key))
        elif args.zread_cmd == "structure":
            print(f"[仓库结构] {args.repo}", file=sys.stderr)
            print(zread_structure(args.repo, args.dir, api_key))
        elif args.zread_cmd == "read":
            print(f"[文件读取] {args.repo}/{args.file}", file=sys.stderr)
            print(zread_file(args.repo, args.file, api_key))
        else:
            p_zread.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
