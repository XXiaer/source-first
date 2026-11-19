"""只读环境与接口连通性自检脚本。

用法：
    python check_environment.py
    python check_environment.py --offline
    python check_environment.py --strict   # 任一必需源不可达时退出码为 1（用于 CI 巡检）

只读。绝不回显任何环境变量密钥值，仅报告是否已设置。
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import urllib.parse
import urllib.request

TIMEOUT = 15
UA = "source-first-healthcheck/1.4 (read-only)"

ENV_VARS = [
    ("GITHUB_TOKEN", "强烈建议（提升 GitHub API 额度至 5000 次/小时）"),
    ("OPENALEX_API_KEY", "可选（提升 OpenAlex 每日免费额度）"),
    ("CROSSREF_MAILTO", "可选（进入 Crossref 礼貌池）"),
    ("S2_API_KEY", "可选（激活 Semantic Scholar）"),
]

SECRET_ENV_NAMES = ("GITHUB_TOKEN", "OPENALEX_API_KEY", "S2_API_KEY")

ENDPOINTS = [
    ("Crossref", "https://api.crossref.org/works?query.bibliographic=RAG&rows=1", True),
    ("OpenAlex", "https://api.openalex.org/works?search=RAG&per-page=1", False),
    ("arXiv", "https://export.arxiv.org/api/query?search_query=all:RAG&max_results=1", True),
    ("DBLP", "https://dblp.org/search/publ/api?q=RAG&h=1&format=json", False),
    ("DOAJ", "https://doaj.org/api/search/articles/RAG?pageSize=1", False),
    ("GitHub", "https://api.github.com/search/repositories?q=RAG&per_page=1", True),
]

S2_ENDPOINT = (
    "Semantic Scholar",
    "https://api.semanticscholar.org/graph/v1/paper/search?query=RAG&limit=1&fields=title",
    False,
)


def sanitize_error_detail(detail):
    """清理异常信息中可能附带的 URL 参数或环境变量密钥值。"""
    cleaned = re.sub(r"(api_key=)[^&\s\"']+", r"\1***", str(detail))
    for name in SECRET_ENV_NAMES:
        val = os.environ.get(name, "").strip()
        if val:
            cleaned = cleaned.replace(val, "***")
    return cleaned


def build_probe_endpoints():
    """构建本次探活的目标端点列表；配置 S2_API_KEY 时动态加入 Semantic Scholar。"""
    targets = list(ENDPOINTS)
    if os.environ.get("S2_API_KEY", "").strip():
        targets.append(S2_ENDPOINT)
    return targets


def probe_endpoint(name, url, required):
    """探测单个公开端点是否返回 HTTP 200，按端点透传对应环境变量。"""
    headers = {"User-Agent": UA}
    req_url = url

    if name == "Crossref":
        mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
        if mailto:
            headers["User-Agent"] = f"source-first-healthcheck/1.4 (mailto:{mailto})"
    elif name == "OpenAlex":
        oa_key = os.environ.get("OPENALEX_API_KEY", "").strip()
        if oa_key:
            sep = "&" if "?" in req_url else "?"
            req_url = f"{req_url}{sep}{urllib.parse.urlencode({'api_key': oa_key})}"
    elif name == "GitHub":
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif name == "Semantic Scholar":
        s2_key = os.environ.get("S2_API_KEY", "").strip()
        if s2_key:
            headers["x-api-key"] = s2_key

    req = urllib.request.Request(req_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return name, required, r.status == 200, f"HTTP {r.status}"
    except Exception as exc:
        return name, required, False, sanitize_error_detail(f"{type(exc).__name__}: {exc}")


def evaluate_overall_status(py_ok, offline, net_results):
    """根据 Python 版本与探活结果判定总体运行状态。"""
    if not py_ok:
        return "unavailable"
    if offline:
        return "partial"
    req_ok = all(r["ok"] for r in net_results if r["required"])
    all_ok = all(r["ok"] for r in net_results)
    any_ok = any(r["ok"] for r in net_results)
    if req_ok and all_ok:
        return "ready"
    if req_ok:
        return "degraded"
    if any_ok:
        return "needs_setup"
    return "unavailable"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="检查运行环境、可选环境变量与接口连通性")
    p.add_argument("--offline", action="store_true", help="仅检查本地 Python 与环境变量，不发网络请求")
    p.add_argument("--strict", action="store_true", help="必需接口失败时返回退出码 1（用于 CI）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    py_ok = sys.version_info >= (3, 8)
    env_status = {
        name: {"configured": bool(os.environ.get(name, "").strip()), "note": note}
        for name, note in ENV_VARS
    }

    net_results = []
    if not args.offline:
        targets = build_probe_endpoints()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futs = [pool.submit(probe_endpoint, n, u, req) for n, u, req in targets]
            for f in concurrent.futures.as_completed(futs):
                n, req, ok, detail = f.result()
                net_results.append({"name": n, "required": req, "ok": ok, "detail": detail})
        net_results.sort(key=lambda x: x["name"])

    overall = evaluate_overall_status(py_ok, args.offline, net_results)

    if args.json:
        print(
            json.dumps(
                {
                    "status": overall,
                    "python_version": sys.version.split()[0],
                    "python_ok": py_ok,
                    "env_vars": env_status,
                    "endpoints": net_results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"总体状态：{overall} | Python {sys.version.split()[0]}")
        print("环境变量（只检查是否配置，不读取值）：")
        for k, v in env_status.items():
            mark = "已配置" if v["configured"] else "未配置"
            print(f"  - {k}: {mark}（{v['note']}）")
        if net_results:
            print("接口连通性：")
            for r in net_results:
                tag = "必需" if r["required"] else "可选"
                mark = "OK" if r["ok"] else "FAIL"
                print(f"  - [{tag}] {r['name']}: {mark} ({r['detail']})")

    if args.strict and not args.offline:
        if not (py_ok and all(r["ok"] for r in net_results if r["required"])):
            return 1
    return 0 if py_ok else 1


if __name__ == "__main__":
    sys.exit(main())
