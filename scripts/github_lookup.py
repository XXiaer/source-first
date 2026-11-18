"""项目线辅助：只读查询 GitHub 仓库并按课设适配维度整理字段。

用法：
    python github_lookup.py "图书管理 springboot" --language Java
    python github_lookup.py "library management" --pushed-after 2025-01-01 --min-stars 5
    python github_lookup.py "图书管理" --check-files --json

只读。不写文件、不读凭据文件、不起子进程。
退出码：
    0  成功找到仓库
    1  正常返回但无结果
    2  参数错误（如检索词为空）
    3  请求失败（如触发 403 限流或网络异常）
"""

import argparse
import concurrent.futures
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "source-first-github-lookup/1.3 (read-only)"
TIMEOUT = 20

KEY_FILENAMES = {
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


def inspect_repo_files(full_name, default_branch="main"):
    """优先使用 GitHub Trees API 探测根目录与前后端子目录的关键文件（单次请求）。"""
    tree_url = f"https://api.github.com/repos/{full_name}/git/trees/{urllib.parse.quote(default_branch, safe='')}?recursive=1"
    try:
        data = fetch_json(tree_url)
        tree = data.get("tree")
        if isinstance(tree, list):
            found = []
            for node in tree:
                if node.get("type") != "blob":
                    continue
                path = node.get("path") or ""
                # 只看根目录和最多两层子目录（如 backend/pom.xml、frontend/package.json）
                if path.count("/") > 2:
                    continue
                fname = path.rsplit("/", 1)[-1].lower()
                if fname in KEY_FILENAMES or fname.endswith(".sql"):
                    found.append(path)
            return sorted(found)[:20]
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise
    except Exception:
        pass

    # 降级：仅查根目录 contents
    contents_url = f"https://api.github.com/repos/{full_name}/contents"
    data = fetch_json(contents_url)
    if not isinstance(data, list):
        return []
    found = []
    for item in data:
        name = item.get("name") or ""
        low = name.lower()
        if low in KEY_FILENAMES or low.endswith(".sql"):
            found.append(name)
    return sorted(found)


def search_repos(keywords, language, pushed_after, min_size, min_stars, sort, limit, check_files, check_limit):
    """构造查询条件并拉取仓库元信息。"""
    parts = [keywords.strip(), "archived:false"]
    if min_size > 0:
        parts.append(f"size:>={min_size}")
    if min_stars > 0:
        parts.append(f"stars:>={min_stars}")
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
    raw_items = data.get("items", [])
    for repo in raw_items:
        lic = (repo.get("license") or {}).get("spdx_id") or ""
        size_kb = repo.get("size") or 0
        items.append(
            {
                "full_name": repo.get("full_name") or "",
                "default_branch": repo.get("default_branch") or "main",
                "url": repo.get("html_url") or "",
                "description": (repo.get("description") or "").strip(),
                "language": repo.get("language") or "未知",
                "stars": repo.get("stargazers_count") or 0,
                "pushed_at": (repo.get("pushed_at") or "")[:10],
                "size_kb": size_kb,
                "size_mb": round(size_kb / 1024, 2),
                "too_small": size_kb < 100,
                "oversize": size_kb > 51200,
                "license": lic if lic and lic != "NOASSERTION" else "无",
            }
        )

    if check_files and items:
        targets = items[: max(1, min(check_limit, 5))]
        rate_limited = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
            fut_map = {
                pool.submit(inspect_repo_files, row["full_name"], row["default_branch"]): row
                for row in targets
            }
            for fut in concurrent.futures.as_completed(fut_map):
                row = fut_map[fut]
                if rate_limited:
                    row["key_files_error"] = "已熔断跳过（触发 GitHub 403 限流）"
                    continue
                try:
                    row["key_files"] = fut.result()
                except urllib.error.HTTPError as exc:
                    if exc.code == 403:
                        rate_limited = True
                        row["key_files_error"] = "HTTP 403 限流，已熔断后续检查"
                    else:
                        row["key_files_error"] = f"HTTP {exc.code}"
                except Exception as exc:
                    row["key_files_error"] = f"{type(exc).__name__}: {exc}"

    return total, items


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="查询 GitHub 仓库并输出课设适配信息")
    p.add_argument("keywords", nargs="?", default="", help="检索词，如 '图书管理 springboot'")
    p.add_argument("--language", default="", help="限定主语言，如 Java / Python / Vue")
    p.add_argument("--pushed-after", default="", help="限定最近提交日期，如 2025-01-01")
    p.add_argument("--min-size", type=int, default=10, help="最小仓库体积（KB，默认 10 过滤空壳仓，传 0 关闭）")
    p.add_argument("--min-stars", type=int, default=0, help="最小 Star 数（默认 0）")
    p.add_argument("--sort", default="updated", choices=["updated", "stars", "forks"], help="排序方式，默认 updated")
    p.add_argument("--limit", type=int, default=5, help="返回条数（1-10），默认 5")
    p.add_argument("--check-files", action="store_true", help="探测前 2 个候选的关键文件（含前后端子目录）")
    p.add_argument("--check-limit", type=int, default=2, help="开启 --check-files 时最多检查几个候选（1-5），默认 2")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    if not args.keywords.strip():
        print("检索词为空。请给一个具体题目或技术关键词再查。")
        return 2

    limit = max(1, min(args.limit, 10))
    try:
        total, items = search_repos(
            args.keywords,
            args.language,
            args.pushed_after,
            max(0, args.min_size),
            max(0, args.min_stars),
            args.sort,
            limit,
            args.check_files,
            args.check_limit,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 403 and "rate limit" in body.lower():
            kw_q = args.keywords.strip()
            print("GitHub API 触发限流（403 rate limit exceeded），请停止重试。")
            print(f"替代路径：用网页搜索 `site:github.com {kw_q}` 或打开 Gitee 网页检索。")
        else:
            print(f"GitHub API 请求失败：HTTP {exc.code} {body[:200]}")
        return 3
    except Exception as exc:
        print(f"请求失败：{type(exc).__name__}: {exc}")
        return 3

    if args.json:
        print(json.dumps({"total_count": total, "items": items}, ensure_ascii=False, indent=2))
    else:
        print(f"命中总数：{total}（展示前 {len(items)} 条）")
        if total > 10000:
            print("提示：关键词过宽（命中超 1 万条），建议补充技术栈或核心业务词缩小范围。")
        print()
        for it in items:
            flags = []
            if it["oversize"]:
                flags.append("[体积>50MB]")
            elif it["too_small"]:
                flags.append("[体积<100KB-可能未推全代码]")
            lic_note = "[无许可证-仅参考思路]" if it["license"] == "无" else f"[{it['license']}]"
            flags.append(lic_note)
            print(f"- {it['full_name']} | {it['language']} | {it['size_mb']}MB | 提交:{it['pushed_at']} | 星:{it['stars']} {' '.join(flags)}")
            print(f"  地址: {it['url']}")
            if it["description"]:
                print(f"  简介: {it['description'][:100]}")
            if "key_files" in it:
                print(f"  关键文件: {', '.join(it['key_files']) if it['key_files'] else '未发现常见 README/依赖清单'}")
            elif "key_files_error" in it:
                print(f"  文件检查失败: {it['key_files_error']}")
            print()

    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
