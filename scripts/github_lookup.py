"""项目线辅助：只读查询 GitHub 仓库或直查单个仓库并按课设适配维度整理字段。

用法：
    python github_lookup.py "图书管理 springboot" --language Java
    python github_lookup.py "library management" --pushed-after 2025-01-01 --min-stars 5
    python github_lookup.py "图书管理" --check-files --readme --json
    python github_lookup.py "图书管理" --include-forks
    python github_lookup.py --repo https://github.com/owner/repo --json

只读。不写文件、不读凭据文件、不起子进程。
退出码：
    0  成功找到仓库
    1  正常返回但无结果（含单仓 404）
    2  参数错误（如检索词为空或仓库标识非法）
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

UA = "source-first-github-lookup/1.6 (read-only)"
TIMEOUT = 20
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

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


def _parse_readme_text(text):
    """从 README 纯文本提取运行说明、中间件线索与摘要。"""
    lower_text = text.lower()
    has_run_guide = any(kw in lower_text for kw in RUN_GUIDE_KEYWORDS)
    middleware_hints = [mw for mw in MIDDLEWARE_KEYWORDS if MIDDLEWARE_RE[mw].search(text)]
    excerpt = re.sub(r"\s+", " ", text).strip()[:500]
    return {
        "readme_excerpt": excerpt,
        "has_run_guide": has_run_guide,
        "middleware_hints": middleware_hints,
    }


def fetch_raw_readme(full_name, default_branch="main"):
    """通过 raw.githubusercontent.com 免配额读取仓库 README 文本。"""
    branches = [default_branch or "main"]
    if "master" not in branches:
        branches.append("master")
    for branch in branches:
        for fname in ("README.md", "readme.md", "README_zh.md"):
            raw_url = f"https://raw.githubusercontent.com/{full_name}/{urllib.parse.quote(branch, safe='')}/{fname}"
            req = urllib.request.Request(raw_url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    text = r.read().decode("utf-8", errors="replace")
                    return _parse_readme_text(text)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                raise
    return {"readme_excerpt": "", "has_run_guide": False, "middleware_hints": []}


def fetch_repo_readme(full_name, default_branch="main", prefer_raw=False):
    """获取仓库 README 摘要，检测运行说明与常见中间件依赖线索（限流时自动降级 Raw 通道）。"""
    if prefer_raw:
        return fetch_raw_readme(full_name, default_branch)
    readme_url = f"https://api.github.com/repos/{full_name}/readme"
    try:
        data = fetch_json(readme_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"readme_excerpt": "", "has_run_guide": False, "middleware_hints": []}
        if is_rate_limit_error(exc):
            return fetch_raw_readme(full_name, default_branch)
        raise

    raw_content = data.get("content") or ""
    if (data.get("encoding") or "").lower() == "base64" and raw_content:
        text = base64.b64decode(raw_content).decode("utf-8", errors="replace")
    else:
        text = raw_content

    return _parse_readme_text(text)


def compute_suitability_score(item):
    """按课设可运行性、体量黄金区间与许可证合规度计算启发式评分。"""
    score = 0
    size_kb = item.get("size_kb") or 0
    if 200 <= size_kb <= 35840:
        score += 30
    elif (100 <= size_kb < 200) or (35840 < size_kb <= 51200):
        score += 10
    elif item.get("too_small"):
        score -= 30
    elif item.get("oversize"):
        score -= 25

    risk = item.get("license_risk")
    if risk == "permissive":
        score += 25
    elif risk == "copyleft":
        score += 5
    elif risk == "none_prohibited":
        score -= 20

    stars = item.get("stars") or 0
    if 1 <= stars <= 500:
        score += 15
    elif 500 < stars <= 3000:
        score += 10

    if item.get("description"):
        score += 5
    if item.get("archived"):
        score -= 50
    if item.get("fork"):
        score -= 20
    return score


def normalize_repo_item(repo):
    """将 GitHub 仓库 JSON 规范化为课设适配字典。"""
    lic_label, lic_risk = classify_license(repo.get("license"))
    size_kb = repo.get("size") or 0
    item = {
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
        "archived": bool(repo.get("archived")),
        "fork": bool(repo.get("fork")),
        "license": lic_label,
        "license_risk": lic_risk,
    }
    item["suitability_score"] = compute_suitability_score(item)
    return item


def enrich_repo_items(items, check_files, fetch_readme, check_limit):
    """串行探测候选仓库的关键文件与 README，触发 403/429 时立即熔断。"""
    if not ((check_files or fetch_readme) and items):
        return
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
                row.update(fetch_repo_readme(row["full_name"], row.get("default_branch", "main")))
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


def parse_repo_identifier(raw):
    """解析 owner/repo 或 GitHub 仓库链接，返回规范化的 'owner/repo'。"""
    s = (raw or "").strip()
    if not s:
        raise ValueError("仓库标识为空，请传入 owner/repo 或 GitHub 仓库链接。")
    s = re.split(r"[?#]", s, maxsplit=1)[0].strip()
    if s.lower().startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(s)
        if parsed.netloc.lower() not in ("github.com", "www.github.com"):
            raise ValueError("仅支持 github.com 仓库链接或 owner/repo。")
        s = parsed.path
    elif s.lower().startswith(("github.com/", "www.github.com/")):
        s = s.split("/", 1)[1]
    parts = [p for p in s.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError("仓库标识格式非法，请传入 owner/repo 或 GitHub 仓库链接。")
    owner, repo = parts[0], parts[1]
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    full_name = f"{owner}/{repo}"
    if not REPO_NAME_RE.match(full_name):
        raise ValueError("仓库标识格式非法，请传入 owner/repo 或 GitHub 仓库链接。")
    return full_name


def inspect_single_repo(full_name, check_files=True, fetch_readme=True):
    """直接体检单个 GitHub 仓库；核心接口限流时自动降级走 Search API + Raw README。"""
    url = f"https://api.github.com/repos/{full_name}"
    try:
        repo = fetch_json(url)
        items = [normalize_repo_item(repo)]
        enrich_repo_items(items, check_files, fetch_readme, check_limit=1)
        return 1, items
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 0, []
        if not is_rate_limit_error(exc):
            raise

    # 降级：走独立限流池的 Search API + 免配额 Raw README
    search_url = (
        "https://api.github.com/search/repositories?"
        + urllib.parse.urlencode({"q": f"repo:{full_name}"})
    )
    try:
        data = fetch_json(search_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 422:
            return 0, []
        raise
    matched = next(
        (
            r for r in data.get("items", [])
            if (r.get("full_name") or "").lower() == full_name.lower()
        ),
        None,
    )
    if matched is None:
        return 0, []
    row = normalize_repo_item(matched)
    row["degraded_from_rate_limit"] = True
    if check_files:
        row["key_files_error"] = "核心接口限流，已降级走 Search API + Raw README 通道"
    if fetch_readme:
        try:
            row.update(fetch_repo_readme(full_name, row["default_branch"], prefer_raw=True))
        except Exception as exc:
            row["readme_error"] = f"{type(exc).__name__}: {exc}"
    return 1, [row]


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
    """构造查询条件并拉取仓库元信息，可选按课设适配评分重排及探测关键文件与 README。"""
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

    api_sort = "updated" if sort == "capstone" else sort
    per_page = min(max(limit * 2, 10), 20) if sort == "capstone" else limit

    q = " ".join(parts)
    url = (
        "https://api.github.com/search/repositories?"
        + urllib.parse.urlencode(
            {
                "q": q,
                "sort": api_sort,
                "per_page": str(per_page),
            }
        )
    )
    data = fetch_json(url)
    total = data.get("total_count", 0)
    items = [normalize_repo_item(repo) for repo in data.get("items", [])]
    if sort == "capstone":
        items.sort(key=lambda r: r.get("suitability_score", 0), reverse=True)
        items = items[:limit]
    enrich_repo_items(items, check_files, fetch_readme, check_limit)
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
    p.add_argument("keywords", nargs="?", default="", help="检索词，如 '图书管理 springboot'（--repo 模式下可省略）")
    p.add_argument(
        "--repo",
        default="",
        help="直接体检单个 GitHub 仓库（支持 owner/repo 或完整 GitHub URL，自动探测关键文件与 README）",
    )
    p.add_argument("--language", default="", help="限定主语言，如 Java / Python / Vue")
    p.add_argument("--pushed-after", default="", help="限定最近提交日期，如 2025-01-01")
    p.add_argument("--min-size", type=int, default=10, help="最小仓库体积（KB，默认 10 过滤空壳仓，传 0 关闭）")
    p.add_argument("--min-stars", type=int, default=0, help="最小 Star 数（默认 0）")
    p.add_argument(
        "--sort",
        default="capstone",
        choices=["capstone", "updated", "stars", "forks"],
        help="排序方式，默认 capstone（按课设可运行与合规评分综合排序）",
    )
    p.add_argument("--limit", type=int, default=5, help="返回条数（1-10），默认 5")
    p.add_argument("--check-files", action="store_true", help="探测前 N 个候选的关键文件与 src/ 目录")
    p.add_argument("--readme", action="store_true", help="拉取前 N 个候选的 README 摘要与中间件线索")
    p.add_argument("--check-limit", type=int, default=2, help="开启文件/README 检查时最多检查几个候选（1-5），默认 2")
    p.add_argument("--include-forks", action="store_true", help="包含 Fork 仓库（默认排除）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    repo_mode = bool(args.repo.strip())
    if not repo_mode and not args.keywords.strip():
        emit_error("检索词为空。请给一个具体题目、技术关键词或使用 --repo <owner/repo>。", args)
        return 2

    target_repo = ""
    if repo_mode:
        try:
            target_repo = parse_repo_identifier(args.repo)
        except ValueError as exc:
            emit_error(str(exc), args)
            return 2

    limit = max(1, min(args.limit, 10))
    try:
        if repo_mode:
            total, items = inspect_single_repo(target_repo, check_files=True, fetch_readme=True)
        else:
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
            kw_q = target_repo if repo_mode else args.keywords.strip()
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

    not_found_msg = (
        "未找到目标 GitHub 仓库（HTTP 404），请核对链接真伪或是否为私有仓库。"
        if repo_mode and not items
        else ""
    )

    if args.json:
        payload = {"total_count": total, "items": items}
        if not_found_msg:
            payload["message"] = not_found_msg
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not_found_msg:
            print(not_found_msg)
        else:
            print(f"命中总数：{total}（展示前 {len(items)} 条）")
        if total > 10000:
            print("提示：关键词过宽（命中超 1 万条），建议补充技术栈或核心业务词缩小范围。")
        print()
        for it in items:
            flags = []
            if it.get("archived"):
                flags.append("[已归档-不建议直接用]")
            if it.get("fork"):
                flags.append("[Fork仓]")
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
