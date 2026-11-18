"""项目线辅助：只读查询 GitHub 仓库并按课设适配维度整理字段。

用法：
    python github_lookup.py "图书管理" --language Java
    python github_lookup.py "library management" --pushed-after 2025-01-01 --limit 5
    python github_lookup.py "图书管理 springboot" --check-files --json

只读。不写文件、不读凭据文件、不起子进程。
退出码：0 成功找到仓库，1 无结果或请求失败（如触发限流）。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "source-first-github-lookup/1.2 (read-only)"
TIMEOUT = 20

KEY_FILES = {
    "readme.md",
    "readme",
    "pom.xml",
    "build.gradle",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "cargo.toml",
    "docker-compose.yml",
    "dockerfile",
}


def fetch_json(url):
    """发送只读 GitHub API GET 请求并返回解析后的 JSON。"""
    headers = {
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def inspect_root_files(full_name):
    """获取仓库根目录文件名并筛选 README 与常见依赖/部署文件。"""
    url = f"https://api.github.com/repos/{full_name}/contents"
    data = fetch_json(url)
    if not isinstance(data, list):
        return []
    found = []
    for item in data:
        name = item.get("name") or ""
        low = name.lower()
        if low in KEY_FILES or low.endswith(".sql"):
            found.append(name)
    return sorted(found)


def search_repos(keywords, language, pushed_after, sort, limit, check_files):
    """构造查询条件并拉取仓库元信息。"""
    parts = [keywords.strip(), "archived:false"]
    if language:
        parts.append(f"language:{language.strip()}")
    if pushed_after:
        parts.append(f"pushed:>{pushed_after.strip()}")

    q = " ".join(parts)
    url = (
        "https://api.github.com/search/repositories?"
        + urllib.parse.urlencode(
            {
                "q": q,
                "sort": sort,
                "per_page": str(limit),
            }
        )
    )
    data = fetch_json(url)
    total = data.get("total_count", 0)
    items = []
    for idx, repo in enumerate(data.get("items", [])):
        lic = (repo.get("license") or {}).get("spdx_id") or ""
        size_kb = repo.get("size") or 0
        row = {
            "full_name": repo.get("full_name") or "",
            "url": repo.get("html_url") or "",
            "description": (repo.get("description") or "").strip(),
            "language": repo.get("language") or "未知",
            "stars": repo.get("stargazers_count") or 0,
            "pushed_at": (repo.get("pushed_at") or "")[:10],
            "size_kb": size_kb,
            "size_mb": round(size_kb / 1024, 2),
            "oversize": size_kb > 51200,
            "license": lic if lic and lic != "NOASSERTION" else "无",
        }
        if check_files and idx < 5:
            try:
                row["key_files"] = inspect_root_files(row["full_name"])
            except Exception as exc:
                row["key_files_error"] = f"{type(exc).__name__}: {exc}"
        items.append(row)
    return total, items


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="查询 GitHub 仓库并输出课设适配信息")
    p.add_argument("keywords", help="检索词，如 '图书管理 springboot'")
    p.add_argument("--language", default="", help="限定主语言，如 Java / Python / Vue")
    p.add_argument("--pushed-after", default="", help="限定最近提交日期，如 2025-01-01")
    p.add_argument("--sort", default="updated", choices=["updated", "stars", "forks"], help="排序方式，默认 updated")
    p.add_argument("--limit", type=int, default=5, help="返回条数（1-10），默认 5")
    p.add_argument("--check-files", action="store_true", help="检查前 5 个候选根目录是否有 README 和依赖清单")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    limit = max(1, min(args.limit, 10))
    try:
        total, items = search_repos(
            args.keywords,
            args.language,
            args.pushed_after,
            args.sort,
            limit,
            args.check_files,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and "rate limit" in body.lower():
            print("GitHub API 触发限流（403 rate limit exceeded），请停止重试，改用浏览器或通用搜索。")
        else:
            print(f"GitHub API 请求失败：HTTP {exc.code} {body[:200]}")
        return 1
    except Exception as exc:
        print(f"请求失败：{type(exc).__name__}: {exc}")
        return 1

    if args.json:
        print(json.dumps({"total_count": total, "items": items}, ensure_ascii=False, indent=2))
    else:
        print(f"命中总数：{total}（展示前 {len(items)} 条）\n")
        for it in items:
            warn = " [体积>50MB]" if it["oversize"] else ""
            lic_note = " [无许可证-仅参考思路]" if it["license"] == "无" else f" [{it['license']}]"
            print(f"- {it['full_name']} | {it['language']} | {it['size_mb']}MB{warn} | 提交:{it['pushed_at']} | 星:{it['stars']}{lic_note}")
            print(f"  地址: {it['url']}")
            if it["description"]:
                print(f"  简介: {it['description'][:100]}")
            if "key_files" in it:
                print(f"  根目录关键文件: {', '.join(it['key_files']) if it['key_files'] else '未发现常见 README/依赖清单'}")
            elif "key_files_error" in it:
                print(f"  根目录检查失败: {it['key_files_error']}")
            print()

    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
