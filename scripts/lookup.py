"""一次并发查四个文献源，合并成一张表。

用法：
    python lookup.py "retrieval augmented generation"
    python lookup.py "图书管理" --sources crossref,openalex
    python lookup.py "RAG" --limit 3 --json
    python lookup.py "RAG" --limit 2 --abstract --json

输出：
    每个源一段，每条一行：年份 | 标题 | 编号或 DOI
    查不到的源写一行 skipped 并说明原因。

可选环境变量：
    OPENALEX_API_KEY   OpenAlex 免费 API Key，提升每日额度
    CROSSREF_MAILTO    联系邮箱，放入 User-Agent 进入 Crossref 礼貌池

只读。不写文件、不读凭据文件、不起子进程。
退出码：0 至少一个源有结果，1 四个源全部失败。

这个脚本是可选的。跑不了 Python 也不影响技能可用，
直接按 references/sources.md 里的地址自己发请求即可。
"""

import argparse
import concurrent.futures
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TIMEOUT = 20
TAG_RE = re.compile(r"<[^>]+>")


def build_user_agent():
    """构造请求头 User-Agent，如设置了 CROSSREF_MAILTO 则附加礼貌池信息。"""
    mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
    if mailto:
        return f"source-first-lookup/1.2 (academic citation lookup; mailto:{mailto})"
    return "source-first-lookup/1.2 (academic citation lookup; read-only)"


def fetch(url):
    """发送只读 GET 请求并返回响应字节。"""
    req = urllib.request.Request(url, headers={"User-Agent": build_user_agent()})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def clean_text(text):
    """剥离 HTML/JATS 标签、反转义 HTML 实体并折叠多余空白。"""
    if not text:
        return ""
    unescaped = html.unescape(text)
    stripped = TAG_RE.sub("", unescaped)
    return " ".join(stripped.split())


def strip_doi_prefix(url):
    """去掉 https://doi.org/ 前缀，保留纯 DOI。"""
    prefix = "https://doi.org/"
    return url[len(prefix):] if url.startswith(prefix) else url


def join_authors(pairs):
    """pairs 是 (姓, 名) 列表。中文文献两者常相同，去掉重复。"""
    out = []
    for fam, giv in pairs:
        fam = (fam or "").strip()
        giv = (giv or "").strip()
        name = giv if fam and fam == giv else f"{fam} {giv}".strip()
        if name and name not in out:
            out.append(name)
    return ", ".join(out)


def decode_openalex_abstract(inv_idx):
    """将 OpenAlex 的倒排索引摘要还原为纯文本。"""
    if not isinstance(inv_idx, dict) or not inv_idx:
        return ""
    max_pos = max((pos for positions in inv_idx.values() for pos in positions), default=-1)
    if max_pos < 0:
        return ""
    words = [""] * (max_pos + 1)
    for token, positions in inv_idx.items():
        for pos in positions:
            if 0 <= pos <= max_pos:
                words[pos] = token
    return " ".join(w for w in words if w)


def query_crossref(kw, limit, include_abstract=False):
    """查询 Crossref 书目接口。"""
    fields = "title,author,issued,DOI,container-title,volume,issue,page,type"
    if include_abstract:
        fields += ",abstract"
    url = (
        "https://api.crossref.org/works?"
        + urllib.parse.urlencode(
            {
                "query.bibliographic": kw,
                "rows": str(limit),
                "select": fields,
            }
        )
    )
    data = json.loads(fetch(url))
    out = []
    for it in data.get("message", {}).get("items", []):
        pairs = [
            (a.get("family", ""), a.get("given", ""))
            for a in (it.get("author") or [])[:4]
        ]
        year = None
        parts = it.get("issued", {}).get("date-parts") or [[None]]
        if parts and parts[0]:
            year = parts[0][0]
        doi = it.get("DOI") or ""
        row = {
            "source": "crossref",
            "year": year,
            "title": clean_text((it.get("title") or [""])[0]),
            "authors": join_authors(pairs),
            "journal": clean_text((it.get("container-title") or [""])[0]),
            "volume": it.get("volume") or "",
            "issue": it.get("issue") or "",
            "page": it.get("page") or "",
            "id": doi,
            "url": f"https://doi.org/{doi}" if doi else "",
            "type": it.get("type", ""),
        }
        if include_abstract:
            row["abstract"] = clean_text(it.get("abstract"))
        out.append(row)
    return out


def query_openalex(kw, limit, include_abstract=False):
    """查询 OpenAlex 文献接口。"""
    fields = "title,publication_year,doi,authorships,open_access,primary_location,cited_by_count"
    if include_abstract:
        fields += ",abstract_inverted_index"
    params = {
        "search": kw,
        "per-page": str(limit),
        "select": fields,
    }
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = json.loads(fetch(url))
    out = []
    for it in data.get("results", []):
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in (it.get("authorships") or [])[:4]
        ]
        oa = (it.get("open_access") or {}).get("oa_url") or ""
        source_obj = (it.get("primary_location") or {}).get("source") or {}
        doi_url = it.get("doi") or ""
        row = {
            "source": "openalex",
            "year": it.get("publication_year"),
            "title": clean_text(it.get("title")),
            "authors": ", ".join(a for a in authors if a),
            "journal": clean_text(source_obj.get("display_name")),
            "volume": "",
            "issue": "",
            "page": "",
            "id": strip_doi_prefix(doi_url),
            "url": doi_url,
            "oa_url": oa,
            "cited_by_count": it.get("cited_by_count"),
            "type": "open access" if oa else "",
        }
        if include_abstract:
            row["abstract"] = decode_openalex_abstract(it.get("abstract_inverted_index"))
        out.append(row)
    return out


def query_arxiv(kw, limit, include_abstract=False):
    """查询 arXiv Atom 接口。"""
    terms = [t for t in re.split(r"\s+", kw.strip()) if t]
    sq = " AND ".join(f"all:{t}" for t in terms) if len(terms) > 1 else f"all:{kw.strip()}"
    query = urllib.parse.urlencode(
        {
            "search_query": sq,
            "start": "0",
            "max_results": str(limit),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    raw = fetch("https://export.arxiv.org/api/query?" + query)
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        ident = (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
        title = clean_text(e.findtext("a:title", "", ns))
        published = e.findtext("a:published", "", ns) or ""
        authors = [
            a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)[:4]
        ]
        row = {
            "source": "arxiv",
            "year": published[:4],
            "title": title,
            "authors": ", ".join(a for a in authors if a),
            "journal": "arXiv",
            "volume": "",
            "issue": "",
            "page": "",
            "id": ident,
            "url": f"https://arxiv.org/abs/{ident}" if ident else "",
            "type": "preprint",
        }
        if include_abstract:
            row["abstract"] = clean_text(e.findtext("a:summary", "", ns))
        out.append(row)
    return out


def query_doaj(kw, limit, include_abstract=False):
    """查询 DOAJ 开放获取期刊接口。"""
    url = (
        "https://doaj.org/api/search/articles/"
        + urllib.parse.quote(kw)
        + f"?pageSize={limit}"
    )
    data = json.loads(fetch(url))
    out = []
    for it in data.get("results", []):
        b = it.get("bibjson", {})
        authors = [a.get("name", "") for a in (b.get("author") or [])[:4]]
        doi = ""
        for ident in b.get("identifier") or []:
            if ident.get("type", "").lower() == "doi":
                doi = ident.get("id", "")
        journal_obj = b.get("journal") or {}
        journal = clean_text(journal_obj.get("title", ""))
        start_p = b.get("start_page") or ""
        end_p = b.get("end_page") or ""
        page = f"{start_p}-{end_p}" if start_p and end_p else (start_p or end_p)
        row = {
            "source": "doaj",
            "year": b.get("year"),
            "title": clean_text(b.get("title")),
            "authors": ", ".join(a for a in authors if a),
            "journal": journal,
            "volume": journal_obj.get("volume") or "",
            "issue": journal_obj.get("number") or "",
            "page": page,
            "id": doi,
            "url": f"https://doi.org/{doi}" if doi else "",
            "type": journal,
        }
        if include_abstract:
            row["abstract"] = clean_text(b.get("abstract"))
        out.append(row)
    return out


QUERIES = {
    "crossref": query_crossref,
    "openalex": query_openalex,
    "arxiv": query_arxiv,
    "doaj": query_doaj,
}


def run_concurrent_queries(wanted, keywords, limit, include_abstract):
    """并发查询所选文献源并保持原有顺序返回 (results, errors)。"""
    results_by_source = {}
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        future_map = {
            pool.submit(QUERIES[name], keywords, limit, include_abstract): name
            for name in wanted
        }
        for future in concurrent.futures.as_completed(future_map):
            name = future_map[future]
            try:
                results_by_source[name] = future.result()
            except Exception as exc:
                errors.append((name, f"{type(exc).__name__}: {exc}"))
                results_by_source[name] = []

    results = []
    for name in wanted:
        results.extend(results_by_source.get(name, []))
    return results, errors


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="一次并发查多个文献源，合并输出")
    p.add_argument("keywords", help="检索词，中英文都可")
    p.add_argument(
        "--sources",
        default="crossref,openalex,arxiv,doaj",
        help="逗号分隔，可选 crossref/openalex/arxiv/doaj",
    )
    p.add_argument("--limit", type=int, default=3, help="每个源取几条，默认 3")
    p.add_argument("--abstract", action="store_true", help="同时提取摘要（默认关闭）")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    wanted = [s.strip() for s in args.sources.split(",") if s.strip() in QUERIES]
    if not wanted:
        print("没有可用的源，检查 --sources 参数")
        return 1

    results, errors = run_concurrent_queries(
        wanted, args.keywords, args.limit, args.abstract
    )

    if args.json:
        print(json.dumps({"results": results, "errors": errors}, ensure_ascii=False, indent=2))
    else:
        for name in wanted:
            rows = [r for r in results if r["source"] == name]
            print(f"## {name}  ({len(rows)} 条)")
            if not rows:
                reason = next((e[1] for e in errors if e[0] == name), "无结果")
                print(f"skipped  {reason}")
            for r in rows:
                tag = r["type"] or ""
                print(f"  {r['year'] or '----'} | {r['title'][:60]} | {r['id'] or r['url']}")
                if r["authors"]:
                    print(f"         {r['authors'][:70]}" + (f"  [{tag}]" if tag else ""))
                if args.abstract and r.get("abstract"):
                    print(f"         摘要: {r['abstract'][:140]}...")
            print()

    if not results:
        print("所选源都没有结果。换个关键词再试一次，或者按第三档处理。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
