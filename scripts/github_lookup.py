"""项目线辅助：只读查询 GitHub 仓库并按课设适配维度整理字段。

用法：
    python github_lookup.py "图书管理 springboot" --language Java
    python github_lookup.py "library management" --pushed-after 2025-01-01 --min-stars 5
    python github_lookup.py "图书管理" --check-files --readme --json
    python github_lookup.py "图书管理" --include-forks

只读。不写文件、不读凭据文件、不起子进程。
退出码：
    0  成功找到仓库
    1  正常返回但无结果
    2  参数错误（如检索词为空）
    3  请求失败（如触发 403/429 限流或网络异常）
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "source-first-github-lookup/1.4 (read-only)"
TIMEOUT = 20

KEY_FILENAMES = {
    "readme.md",
    "readme_zh.md",
    "readme",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "package.json",
    "pnpm-lock.yaml",
    "requirements.txt",
    "pyproject.toml",
    "environment.yml",
    "setup.py",
    "go.mod",
    "cargo.toml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "dockerfile",
    "application.yml",
    "application.yaml",
    "application.properties",
    "vite.config.js",
    "vite.config.ts",
}

RUN_GUIDE_KEYWORDS = (
    "安装",
    "运行",
    "启动",
    "部署",
    "快速开始",
    "使用说明",
    "环境要求",
    "quick start",
    "getting started",
    "installation",
    "how to run",
    "usage",
)

MIDDLEWARE_KEYWORDS = (
    "redis",
    "rabbitmq",
    "kafka",
    "elasticsearch",
    "mongodb",
    "nacos",
    "zookeeper",
    "docker",
)
MIDDLEWARE_RE = {
    mw: re.compile(rf"\b{re.escape(mw)}\b", re.IGNORECASE)
    for mw in MIDDLEWARE_KEYWORDS
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


def read_http_error_body(exc):
    """安全读取 HTTPError 响应体文本并缓存，避免重复读取耗尽流。"""
    if hasattr(exc, "_cached_body_text"):
        return exc._cached_body_text
    try:
        raw = exc.read()
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    except Exception:
        text = ""
    exc._cached_body_text = text
    return text


def is_rate_limit_error(exc, body_text=None):
    """识别 GitHub 403/429 限流异常。"""
    if exc.code == 429:
        return True
    if exc.code != 403:
        return False
    headers = getattr(exc, "headers", None) or {}
    remaining = None
    if hasattr(headers, "get"):
        remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
    if str(remaining).strip() == "0":
        return True
    body = body_text if body_text is not None else read_http_error_body(exc)
    return "rate limit" in body.lower()


def classify_license(license_obj):
    """规范化许可证名称并返回 (license_label, license_risk)。"""
    spdx = ((license_obj or {}).get("spdx_id") or "").strip()
    if not spdx:
        return "无", "none_prohibited"
    if spdx.upper() == "NOASSERTION":
        return "自定义/需人工核对", "manual_check"
    if "GPL" in spdx.upper():
        return spdx, "copyleft"
    return spdx, "permissive"


def inspect_repo_files(full_name, default_branch="main"):
    """优先使用 GitHub Trees API 探测根目录与前后端子目录的关键文件及 src/ 目录。"""
    tree_url = (
        f"https://api.github.com/repos/{full_name}/git/trees/"
        f"{urllib.parse.quote(default_branch, safe='')}?recursive=1"
    )
    try:
        data = fetch_json(tree_url)
        tree = data.get("tree")
        if isinstance(tree, list):
            found = []
            for node in tree:
                node_type = node.get("type")
                path = node.get("path") or ""
                if not path:
                    continue
                if node_type == "blob":
                    if path.count("/") > 2:
                        continue
                    fname = path.rsplit("/", 1)[-1].lower()
                    if fname in KEY_FILENAMES or fname.endswith(".sql"):
                        found.append(path)
                elif node_type == "tree":
                    if path.count("/") <= 1 and path.rsplit("/", 1)[-1].lower() == "src":
                        found.append(f"{path}/")
            return sorted(set(found))[:25]
    except urllib.error.HTTPError as exc:
        if is_rate_limit_error(exc) or exc.code in (403, 429):
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
        if item.get("type") == "dir" and low == "src":
            found.append(f"{name}/")
        elif low in KEY_FILENAMES or low.endswith(".sql"):
            found.append(name)
    return sorted(set(found))


def fetch_repo_readme(full_name):
    """获取仓库 README 摘要，检测运行说明与常见中间件依赖线索。"""
    readme_url = f"https://api.github.com/repos/{full_name}/readme"
    try:
        data = fetch_json(readme_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"readme_excerpt": "", "has_run_guide": False, "middleware_hints": []}
        raise

    raw_content = data.get("content") or ""
    if (data.get("encoding") or "").lower() == "base64" and raw_content:
        text = base64.b64decode(raw_content).decode("utf-8", errors="replace")
    else:
        text = raw_content

    lower_text = text.lower()
    has_run_guide = any(kw in lower_text for kw in RUN_GUIDE_KEYWORDS)
    middleware_hints = [mw for mw in MIDDLEWARE_KEYWORDS if MIDDLEWARE_RE[mw].search(text)]
    excerpt = re.sub(r"\s+", " ", text).strip()[:500]
    return {
        "readme_excerpt": excerpt,
        "has_run_guide": has_run_guide,
        "middleware_hints": middleware_hints,
    }


def search_repos(
    keywords,
    language,
    pushed_after,
    min_size,
    min_stars,
    sort,
    limit,
    check_files,
    check_limit,
    include_forks=False,
    fetch_readme=False,
):
    """构造查询条件并拉取仓库元信息，可选串行探测关键文件与 README。"""
    parts = [keywords.strip(), "archived:false"]
    if not include_forks:
        parts.append("fork:false")
    if min_size > 0:
        parts.append(f"size:>={min_size}")
    if min_stars > 0:
        parts.append(f"stars:>={min_stars}")
    if language:
        lang = language.strip()
        if " " in lang and not (lang.startswith('"') and lang.endswith('"')):
            lang = f'"{lang}"'
        parts.append(f"language:{lang}")
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
        lic_label, lic_risk = classify_license(repo.get("license"))
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
                "license": lic_label,
                "license_risk": lic_risk,
            }
        )

    if (check_files or fetch_readme) and items:
        targets = items[: max(1, min(check_limit, 5))]
        for row in targets:
            rate_limited = False
            if check_files:
                try:
                    row["key_files"] = inspect_repo_files(row["full_name"], row["default_branch"])
                except urllib.error.HTTPError as exc:
                    if is_rate_limit_error(exc):
                        row["key_files_error"] = f"HTTP {exc.code} 限流，已熔断后续检查"
                        rate_limited = True
                    else:
                        row["key_files_error"] = f"HTTP {exc.code}"
                except Exception as exc:
                    row["key_files_error"] = f"{type(exc).__name__}: {exc}"
            if rate_limited:
                break

            if fetch_readme:
                try:
                    row.update(fetch_repo_readme(row["full_name"]))
                except urllib.error.HTTPError as exc:
                    if is_rate_limit_error(exc):
                        row["readme_error"] = f"HTTP {exc.code} 限流，已熔断后续检查"
                        rate_limited = True
                    else:
                        row["readme_error"] = f"HTTP {exc.code}"
                except Exception as exc:
                    row["readme_error"] = f"{type(exc).__name__}: {exc}"
            if rate_limited:
                break

    return total, items


def emit_error(error_msg, args, fallback=""):
    """按 JSON/文本模式输出错误信息，保证 --json 时 stdout 合法。"""
    if args.json:
        print(
            json.dumps(
                {
                    "error": error_msg,
                    "fallback": fallback,
                    "total_count": 0,
                    "items": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(error_msg)
        if fallback:
            print(f"替代路径：{fallback}")


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
    p.add_argument("--check-files", action="store_true", help="探测前 N 个候选的关键文件与 src/ 目录")
    p.add_argument("--readme", action="store_true", help="拉取前 N 个候选的 README 摘要与中间件线索")
    p.add_argument("--check-limit", type=int, default=2, help="开启文件/README 检查时最多检查几个候选（1-5），默认 2")
    p.add_argument("--include-forks", action="store_true", help="包含 Fork 仓库（默认排除）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    if not args.keywords.strip():
        emit_error("检索词为空。请给一个具体题目或技术关键词再查。", args)
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
            include_forks=args.include_forks,
            fetch_readme=args.readme,
        )
    except urllib.error.HTTPError as exc:
        body = read_http_error_body(exc)
        if is_rate_limit_error(exc, body):
            kw_q = args.keywords.strip()
            emit_error(
                f"GitHub API 触发限流（HTTP {exc.code} rate limit exceeded），请停止重试。",
                args,
                fallback=f"用网页搜索 `site:github.com {kw_q}` 或打开 Gitee 网页检索。",
            )
        else:
            emit_error(f"GitHub API 请求失败：HTTP {exc.code} {body[:200]}", args)
        return 3
    except Exception as exc:
        emit_error(f"请求失败：{type(exc).__name__}: {exc}", args)
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

            risk = it.get("license_risk")
            if risk == "none_prohibited":
                lic_note = "[无许可证-仅参考思路]"
            elif risk == "manual_check":
                lic_note = "[自定义许可证-需人工核对]"
            elif risk == "copyleft":
                lic_note = f"[{it['license']}-Copyleft强传染协议]"
            else:
                lic_note = f"[{it['license']}]"
            flags.append(lic_note)

            print(
                f"- {it['full_name']} | {it['language']} | {it['size_mb']}MB | 提交:{it['pushed_at']} | 星:{it['stars']} {' '.join(flags)}"
            )
            print(f"  地址: {it['url']}")
            if it["description"]:
                print(f"  简介: {it['description'][:100]}")
            if "key_files" in it:
                print(
                    f"  关键文件: {', '.join(it['key_files']) if it['key_files'] else '未发现常见 README/依赖清单'}"
                )
            elif "key_files_error" in it:
                print(f"  文件检查失败: {it['key_files_error']}")
            if "readme_excerpt" in it:
                guide_label = "含运行说明" if it.get("has_run_guide") else "未检测到明显运行说明"
                mw_label = f" | 疑似中间件: {', '.join(it['middleware_hints'])}" if it.get("middleware_hints") else ""
                print(f"  README 探测: {guide_label}{mw_label}")
                if it["readme_excerpt"]:
                    print(f"  README 摘要: {it['readme_excerpt'][:160]}...")
            elif "readme_error" in it:
                print(f"  README 检查失败: {it['readme_error']}")
            print()

    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
