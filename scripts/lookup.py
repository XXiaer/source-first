"""一次并发查多个文献源，去重合并并支持 DOI 精准反查与 BibTeX 导出。

用法：
    python lookup.py "retrieval augmented generation"
    python lookup.py "图书管理" --sources crossref,openalex,dblp
    python lookup.py --doi 10.1145/3777378 --json
    python lookup.py "RAG" --limit 3 --bibtex
    python lookup.py "RAG" --limit 3 --abstract --json

可选环境变量：
    OPENALEX_API_KEY   OpenAlex 免费 API Key，提升每日额度
    CROSSREF_MAILTO    联系邮箱，放入 User-Agent 进入 Crossref 礼貌池
    S2_API_KEY         Semantic Scholar API Key（设置后自动加入默认源）

只读。不写文件、不读凭据文件、不起子进程。
退出码：
    0  至少一个源返回有效结果
    1  所选源正常返回但均为 0 条结果
    2  参数错误（如关键词为空）
    3  所有请求的源全部失败（网络/限流），不可当作“无文献”
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
MAX_AUTHORS = 20
# 仅匹配合法 HTML/JATS 标签，避免误删数学式中的 < 或 >（如 a < b > c）
TAG_RE = re.compile(r"</?(?:[a-zA-Z][a-zA-Z0-9:-]*)(?:\s[^>]*)?>")


def build_user_agent():
    """构造请求头 User-Agent，如设置了 CROSSREF_MAILTO 则附加礼貌池信息。"""
    mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
    if mailto:
        return f"source-first-lookup/1.3 (academic citation lookup; mailto:{mailto})"
    return "source-first-lookup/1.3 (academic citation lookup; read-only)"


def fetch(url, extra_headers=None):
    """发送只读 GET 请求并返回响应字节。"""
    headers = {"User-Agent": build_user_agent()}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def clean_text(text):
    """反转义 HTML 实体、剥离合法 HTML/JATS 标签并折叠多余空白。"""
    if not text:
        return ""
    unescaped = html.unescape(text)
    stripped = TAG_RE.sub("", unescaped)
    return " ".join(stripped.split())


def strip_doi_prefix(url):
    """去掉 https://doi.org/ 或 http://dx.doi.org/ 前缀，保留纯 DOI。"""
    s = (url or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if s.lower().startswith(prefix):
            return s[len(prefix):]
    return s


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


def parse_crossref_item(it, include_abstract=False):
    """将单条 Crossref JSON 记录规范化为统一字典。"""
    all_authors = it.get("author") or []
    pairs = [
        (a.get("family", ""), a.get("given", ""))
        for a in all_authors[:MAX_AUTHORS]
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
        "author_count": len(all_authors),
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
    return row


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
    return [
        parse_crossref_item(it, include_abstract)
        for it in data.get("message", {}).get("items", [])
    ]


def parse_openalex_item(it, include_abstract=False):
    """将单条 OpenAlex JSON 记录规范化为统一字典。"""
    all_authorships = it.get("authorships") or []
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in all_authorships[:MAX_AUTHORS]
    ]
    oa = (it.get("open_access") or {}).get("oa_url") or ""
    source_obj = (it.get("primary_location") or {}).get("source") or {}
    biblio = it.get("biblio") or {}
    first_p = biblio.get("first_page") or ""
    last_p = biblio.get("last_page") or ""
    page = f"{first_p}-{last_p}" if first_p and last_p else (first_p or last_p)
    doi_url = it.get("doi") or ""
    row = {
        "source": "openalex",
        "year": it.get("publication_year"),
        "title": clean_text(it.get("title")),
        "authors": ", ".join(a for a in authors if a),
        "author_count": len(all_authorships),
        "journal": clean_text(source_obj.get("display_name")),
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "page": page,
        "id": strip_doi_prefix(doi_url),
        "url": doi_url,
        "oa_url": oa,
        "cited_by_count": it.get("cited_by_count"),
        "type": "open access" if oa else "",
    }
    if include_abstract:
        row["abstract"] = decode_openalex_abstract(it.get("abstract_inverted_index"))
    return row


def query_openalex(kw, limit, include_abstract=False):
    """查询 OpenAlex 文献接口。"""
    fields = "title,publication_year,doi,authorships,open_access,primary_location,cited_by_count,biblio"
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
    return [parse_openalex_item(it, include_abstract) for it in data.get("results", [])]


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
        all_authors = e.findall("a:author", ns)
        authors = [
            a.findtext("a:name", "", ns) for a in all_authors[:MAX_AUTHORS]
        ]
        row = {
            "source": "arxiv",
            "year": published[:4],
            "title": title,
            "authors": ", ".join(a for a in authors if a),
            "author_count": len(all_authors),
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


def query_dblp(kw, limit, include_abstract=False):
    """查询 DBLP 计算机科学文献接口（免费、无 Key、顶会期刊元数据干净）。"""
    url = (
        "https://dblp.org/search/publ/api?"
        + urllib.parse.urlencode({"q": kw, "h": str(limit), "format": "json"})
    )
    data = json.loads(fetch(url))
    hits = (data.get("result") or {}).get("hits") or {}
    hit_list = hits.get("hit") or []
    if isinstance(hit_list, dict):
        hit_list = [hit_list]
    out = []
    for h in hit_list:
        info = h.get("info") or {}
        raw_authors = (info.get("authors") or {}).get("author") or []
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]
        elif isinstance(raw_authors, str):
            raw_authors = [{"text": raw_authors}]
        authors = [
            (a.get("text") if isinstance(a, dict) else str(a)).strip()
            for a in raw_authors[:MAX_AUTHORS]
        ]
        doi = strip_doi_prefix(info.get("doi") or "")
        ee = info.get("ee")
        if isinstance(ee, list):
            ee = ee[0] if ee else ""
        url_val = f"https://doi.org/{doi}" if doi else (ee or info.get("url") or "")
        title = clean_text(info.get("title") or "").rstrip(".")
        row = {
            "source": "dblp",
            "year": info.get("year") or "",
            "title": title,
            "authors": ", ".join(a for a in authors if a),
            "author_count": len(raw_authors),
            "journal": clean_text(info.get("venue") or ""),
            "volume": str(info.get("volume") or ""),
            "issue": str(info.get("number") or ""),
            "page": str(info.get("pages") or ""),
            "id": doi or info.get("key") or "",
            "url": url_val,
            "type": info.get("type") or "",
        }
        if include_abstract:
            row["abstract"] = ""
        out.append(row)
    return out


def query_doaj(kw, limit, include_abstract=False):
    """查询 DOAJ 开放获取期刊接口（编码斜杠，避免路径破坏）。"""
    url = (
        "https://doaj.org/api/search/articles/"
        + urllib.parse.quote(kw, safe="")
        + f"?pageSize={limit}"
    )
    data = json.loads(fetch(url))
    out = []
    for it in data.get("results", []):
        b = it.get("bibjson", {})
        all_authors = b.get("author") or []
        authors = [a.get("name", "") for a in all_authors[:MAX_AUTHORS]]
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
            "author_count": len(all_authors),
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


def query_semanticscholar(kw, limit, include_abstract=False):
    """查询 Semantic Scholar 接口（建议设置 S2_API_KEY，否则极易 429）。"""
    fields = "title,year,authors,externalIds,venue,citationCount,openAccessPdf"
    if include_abstract:
        fields += ",abstract"
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/search?"
        + urllib.parse.urlencode({"query": kw, "limit": str(limit), "fields": fields})
    )
    headers = {}
    s2_key = os.environ.get("S2_API_KEY", "").strip()
    if s2_key:
        headers["x-api-key"] = s2_key
    data = json.loads(fetch(url, extra_headers=headers))
    out = []
    for it in data.get("data", []):
        all_authors = it.get("authors") or []
        authors = [a.get("name", "") for a in all_authors[:MAX_AUTHORS]]
        ext = it.get("externalIds") or {}
        doi = ext.get("DOI") or ""
        arxiv_id = ext.get("ArXiv") or ""
        ident = doi or arxiv_id or (it.get("paperId") or "")
        url_val = (
            f"https://doi.org/{doi}"
            if doi
            else (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "")
        )
        oa_pdf = (it.get("openAccessPdf") or {}).get("url") or ""
        row = {
            "source": "semanticscholar",
            "year": it.get("year"),
            "title": clean_text(it.get("title")),
            "authors": ", ".join(a for a in authors if a),
            "author_count": len(all_authors),
            "journal": clean_text(it.get("venue")),
            "volume": "",
            "issue": "",
            "page": "",
            "id": ident,
            "url": url_val,
            "oa_url": oa_pdf,
            "cited_by_count": it.get("citationCount"),
            "type": "open access" if oa_pdf else "",
        }
        if include_abstract:
            row["abstract"] = clean_text(it.get("abstract"))
        out.append(row)
    return out


def lookup_by_doi(doi_raw, include_abstract=False):
    """按 DOI 精准反查 Crossref 与 OpenAlex，合并互补字段。"""
    doi = strip_doi_prefix(doi_raw)
    encoded = urllib.parse.quote(doi, safe="")
    cr_url = f"https://api.crossref.org/works/{encoded}"
    oa_url = f"https://api.openalex.org/works/https://doi.org/{encoded}"
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        oa_url += "?" + urllib.parse.urlencode({"api_key": api_key})

    results = []
    errors = []

    def _get_cr():
        data = json.loads(fetch(cr_url))
        msg = data.get("message")
        return [parse_crossref_item(msg, include_abstract)] if msg else []

    def _get_oa():
        data = json.loads(fetch(oa_url))
        return [parse_openalex_item(data, include_abstract)] if data.get("title") else []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_cr = pool.submit(_get_cr)
        f_oa = pool.submit(_get_oa)
        for name, fut in (("crossref", f_cr), ("openalex", f_oa)):
            try:
                results.extend(fut.result())
            except Exception as exc:
                errors.append((name, f"{type(exc).__name__}: {exc}"))
    return results, errors


QUERIES = {
    "crossref": query_crossref,
    "openalex": query_openalex,
    "arxiv": query_arxiv,
    "dblp": query_dblp,
    "doaj": query_doaj,
    "semanticscholar": query_semanticscholar,
}


def merge_and_dedupe(results):
    """按归一化 DOI 或（小写标题 + 年份）去重并互补缺失字段，返回 (列表, 合并数)。"""
    seen = {}
    deduped = []
    merged_count = 0

    for row in results:
        doi_key = strip_doi_prefix(row.get("id") or "").lower()
        title_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (row.get("title") or "").lower())
        year_key = str(row.get("year") or "")
        key = f"doi:{doi_key}" if doi_key and doi_key.startswith("10.") else f"t:{title_key}|{year_key}"

        if key in seen and (doi_key or title_key):
            existing = seen[key]
            merged_count += 1
            also = existing.setdefault("also_in", [])
            if row["source"] not in also and row["source"] != existing["source"]:
                also.append(row["source"])
            for field in ("authors", "journal", "volume", "issue", "page", "oa_url", "abstract"):
                if not existing.get(field) and row.get(field):
                    existing[field] = row[field]
            if not existing.get("author_count") and row.get("author_count"):
                existing["author_count"] = row["author_count"]
            if existing.get("cited_by_count") is None and row.get("cited_by_count") is not None:
                existing["cited_by_count"] = row["cited_by_count"]
        else:
            item = dict(row)
            seen[key] = item
            deduped.append(item)
    return deduped, merged_count


def format_bibtex(rows):
    """将结果列表格式化为标准 BibTeX 字符串。"""
    entries = []
    for idx, r in enumerate(rows, 1):
        first_author = (r.get("authors") or "unknown").split(",")[0].strip().split()[-1]
        first_author = re.sub(r"[^a-zA-Z0-9]", "", first_author).lower() or "ref"
        year = str(r.get("year") or "nd")
        cite_key = f"{first_author}{year}_{idx}"

        rtype = (r.get("type") or "").lower()
        if "proceedings" in rtype or "conference" in rtype:
            btype = "inproceedings"
            venue_field = "booktitle"
        elif r.get("source") == "arxiv" or "preprint" in rtype:
            btype = "misc"
            venue_field = "howpublished"
        elif r.get("journal"):
            btype = "article"
            venue_field = "journal"
        else:
            btype = "misc"
            venue_field = "howpublished"

        lines = [f"@{btype}{{{cite_key},"]
        if r.get("title"):
            lines.append(f"  title = {{{r['title']}}},")
        if r.get("authors"):
            lines.append(f"  author = {{{r['authors'].replace(', ', ' and ')}}},")
        if r.get("journal"):
            lines.append(f"  {venue_field} = {{{r['journal']}}},")
        if r.get("year"):
            lines.append(f"  year = {{{r['year']}}},")
        if r.get("volume"):
            lines.append(f"  volume = {{{r['volume']}}},")
        if r.get("issue"):
            lines.append(f"  number = {{{r['issue']}}},")
        if r.get("page"):
            lines.append(f"  pages = {{{r['page']}}},")
        doi = strip_doi_prefix(r.get("id") or "")
        if doi.startswith("10."):
            lines.append(f"  doi = {{{doi}}},")
        if r.get("url"):
            lines.append(f"  url = {{{r['url']}}},")
        lines[-1] = lines[-1].rstrip(",")
        lines.append("}")
        entries.append("\n".join(lines))
    return "\n\n".join(entries)


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

    default_sources = "crossref,openalex,arxiv,dblp,doaj"
    if os.environ.get("S2_API_KEY", "").strip():
        default_sources += ",semanticscholar"

    p = argparse.ArgumentParser(description="一次并发查多个文献源，合并去重输出")
    p.add_argument("keywords", nargs="?", default="", help="检索词，中英文都可（--doi 模式下可省略）")
    p.add_argument("--doi", default="", help="按 DOI 精准反查（如 10.1145/3777378）")
    p.add_argument(
        "--sources",
        default=default_sources,
        help="逗号分隔，可选 crossref/openalex/arxiv/dblp/doaj/semanticscholar",
    )
    p.add_argument("--limit", type=int, default=3, help="每个源取几条（1-10），默认 3")
    p.add_argument("--abstract", action="store_true", help="同时提取摘要（默认关闭）")
    p.add_argument("--bibtex", action="store_true", help="以 BibTeX 格式输出")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    limit = max(1, min(args.limit, 10))
    doi_mode = bool(args.doi.strip())

    if not doi_mode and not args.keywords.strip():
        print("检索词为空。请提供具体主题或使用 --doi <DOI>。")
        return 2

    if doi_mode:
        wanted = ["crossref", "openalex"]
        raw_results, errors = lookup_by_doi(args.doi.strip(), args.abstract)
    else:
        wanted = [s.strip() for s in args.sources.split(",") if s.strip() in QUERIES]
        if not wanted:
            print("没有可用的源，检查 --sources 参数")
            return 2
        raw_results, errors = run_concurrent_queries(
            wanted, args.keywords.strip(), limit, args.abstract
        )

    results, merged_count = merge_and_dedupe(raw_results)
    ok_sources = [s for s in wanted if not any(e[0] == s for e in errors)]

    if args.json:
        payload = {
            "results": results,
            "errors": errors,
            "summary": {
                "wanted_sources": wanted,
                "ok_sources": ok_sources,
                "raw_count": len(raw_results),
                "deduped_count": len(results),
                "merged_duplicates": merged_count,
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.bibtex:
        if results:
            print(format_bibtex(results))
    else:
        for r in results:
            tag = r.get("type") or ""
            also = f" +{'/'.join(r['also_in'])}" if r.get("also_in") else ""
            src_label = f"{r['source']}{also}"
            print(f"[{src_label}] {r.get('year') or '----'} | {r.get('title', '')[:75]} | {r.get('id') or r.get('url')}")
            if r.get("authors"):
                extra_a = f" (共{r['author_count']}位)" if r.get("author_count", 0) > MAX_AUTHORS else ""
                print(f"    作者: {r['authors'][:90]}{extra_a}")
            meta = [x for x in (r.get("journal"), r.get("volume"), r.get("issue"), r.get("page")) if x]
            if meta:
                print(f"    出处: {' | '.join(meta)}" + (f"  [{tag}]" if tag else ""))
            if r.get("oa_url"):
                print(f"    全文: {r['oa_url']}")
            if args.abstract and r.get("abstract"):
                print(f"    摘要: {r['abstract'][:160]}...")
        if results:
            print()
        print(f"汇总：成功 {len(ok_sources)}/{len(wanted)} 个源，原始命中 {len(raw_results)} 条，去重合并 {merged_count} 条，输出 {len(results)} 条。")
        if "doaj" in ok_sources and any(r["source"] == "doaj" for r in results):
            print("提示：DOAJ 相关性普遍偏低，使用前请人工核对标题与摘要。")
        if errors:
            err_str = "；".join(f"{s}({msg})" for s, msg in errors)
            print(f"失败源：{err_str}")

    if not results:
        if not ok_sources:
            print("所有源都请求失败（网络或限流），本轮检索未完成，不能当作“没有文献”。")
            return 3
        print("所选源正常返回但均为 0 条结果，可按第三档处理或更换检索词。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
