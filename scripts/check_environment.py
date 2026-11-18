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
import sys
import urllib.request

TIMEOUT = 15
UA = "source-first-healthcheck/1.3 (read-only)"

ENV_VARS = [
    ("GITHUB_TOKEN", "强烈建议（提升 GitHub API 额度至 5000 次/小时）"),
    ("OPENALEX_API_KEY", "可选（提升 OpenAlex 每日免费额度）"),
    ("CROSSREF_MAILTO", "可选（进入 Crossref 礼貌池）"),
    ("S2_API_KEY", "可选（激活 Semantic Scholar）"),
]

ENDPOINTS = [
    ("Crossref", "https://api.crossref.org/works?query.bibliographic=RAG&rows=1", True),
    ("OpenAlex", "https://api.openalex.org/works?search=RAG&per-page=1", True),
    ("arXiv", "https://export.arxiv.org/api/query?search_query=all:RAG&max_results=1", True),
    ("DBLP", "https://dblp.org/search/publ/api?q=RAG&h=1&format=json", False),
    ("DOAJ", "https://doaj.org/api/search/articles/RAG?pageSize=1", False),
    ("GitHub", "https://api.github.com/search/repositories?q=RAG&per_page=1", True),
]


def probe_endpoint(name, url, required):
    """探测单个公开端点是否返回 HTTP 200。"""
    headers = {"User-Agent": UA}
    if name == "GitHub":
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return name, required, r.status == 200, f"HTTP {r.status}"
    except Exception as exc:
        return name, required, False, f"{type(exc).__name__}: {exc}"


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
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ENDPOINTS)) as pool:
            futs = [pool.submit(probe_endpoint, n, u, req) for n, u, req in ENDPOINTS]
            for f in concurrent.futures.as_completed(futs):
                n, req, ok, detail = f.result()
                net_results.append({"name": n, "required": req, "ok": ok, "detail": detail})
        net_results.sort(key=lambda x: x["name"])

    if not py_ok:
        overall = "unavailable"
    elif args.offline:
        overall = "partial"
    else:
        req_ok = all(r["ok"] for r in net_results if r["required"])
        any_ok = any(r["ok"] for r in net_results)
        if req_ok and all(e["configured"] for e in env_status.values()):
            overall = "ready"
        elif any_ok:
            overall = "ready" if req_ok else "needs_setup"
        else:
            overall = "unavailable"

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
        if not all(r["ok"] for r in net_results if r["required"]):
            return 1
    return 0 if py_ok else 1


if __name__ == "__main__":
    sys.exit(main())
