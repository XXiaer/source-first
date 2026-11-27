"""一次并发查多个文献源，去重合并并支持多 DOI 精准反查与多格式引文导出。

用法：
    python lookup.py "retrieval augmented generation"
    python lookup.py "图书管理" --sources crossref,openalex,dblp
    python lookup.py --doi 10.1145/3777378 --json
    python lookup.py --doi "10.1145/3777378,10.9999/fake" --json
    python lookup.py "RAG" --limit 3 --year-from 2023 --abstract --json
    python lookup.py "RAG" --limit 3 --format gbt7714
    python lookup.py "RAG" --limit 3 --bibtex

可选环境变量：
    OPENALEX_API_KEY   OpenAlex 免费 API Key，提升每日额度
    CROSSREF_MAILTO    联系邮箱，放入 User-Agent 进入 Crossref 礼貌池
    S2_API_KEY         Semantic Scholar API Key（设置后自动加入默认源）

只读。不写文件、不读凭据文件、不起子进程。
退出码：
    0  至少一个源返回有效结果
    1  所选源正常返回但均为 0 条结果
    2  参数错误（如关键词为空、年份范围非法、输出格式冲突或无可用源）
    3  所有请求的源全部失败（网络/限流），不可当作“无文献”
"""

import argparse
import concurrent.futures
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TIMEOUT = 20
RETRY_DELAY = 1.0
MAX_AUTHORS = 20
# 仅匹配合法 HTML/JATS 标签，避免误删数学式中的 < 或 >（如 a < b > c）
TAG_RE = re.compile(r"</?(?:[a-zA-Z][a-zA-Z0-9:-]*)(?:\s[^>]*)?>")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ARXIV_SYNTAX_RE = re.compile(
    r"(?:^|\s)(?:cat|ti|au|abs|co|jr|rn|id|all):|\s(?:AND|OR|ANDNOT)\s"
)
BIBTEX_ESCAPE_RE = re.compile(r"([\\&%#_])")
BIBTEX_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
}


def build_user_agent():
    """构造请求头 User-Agent，如设置了 CROSSREF_MAILTO 则附加礼貌池信息。"""
    mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
    if mailto:
        return f"source-first-lookup/1.6 (academic citation lookup; mailto:{mailto})"
    return "source-first-lookup/1.6 (academic citation lookup; read-only)"


def fetch(url, extra_headers=None):
    """发送只读 GET 请求并返回响应字节；遇到瞬时 429/5xx 时短退避重试 1 次。"""
    headers = {"User-Agent": build_user_agent()}
    if extra_headers:
        headers.update(extra_headers)
    for attempt in range(2):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if attempt == 0 and exc.code in (429, 500, 502, 503, 504):
                if RETRY_DELAY > 0:
                    time.sleep(RETRY_DELAY)
                continue
            raise


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
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ):
        if s.lower().startswith(prefix):
            return s[len(prefix):]
    return s


def join_authors(pairs):
    """pairs 是 (姓, 名) 列表；中文保留姓+名，西文统一为 Given Family，与其余源一致。"""
    out = []
    for fam, giv in pairs:
        fam = (fam or "").strip()
        giv = (giv or "").strip()
        if fam and fam == giv:
            name = giv
        elif fam and giv:
            name = f"{fam}{giv}" if CJK_RE.search(fam + giv) else f"{giv} {fam}"
        else:
            name = fam or giv
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


def extract_year_int(year_val):
    """从年份字段提取 4 位整数年份，失败返回 None。"""
    m = re.search(r"\b(1\d{3}|2\d{3})\b", str(year_val or ""))
    return int(m.group(1)) if m else None


def filter_by_year(rows, year_from=None):
    """按起始年份过滤条目；仅剔除可解析年份且明确早于 year_from 的记录。"""
    if year_from is None:
        return rows
    kept = []
    for row in rows:
        y = extract_year_int(row.get("year"))
        if y is None or y >= year_from:
            kept.append(row)
    return kept


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


def query_crossref(kw, limit, include_abstract=False, year_from=None):
    """查询 Crossref 书目接口。"""
    fields = "title,author,issued,DOI,container-title,volume,issue,page,type"
    if include_abstract:
        fields += ",abstract"
    params = {
        "query.bibliographic": kw,
        "rows": str(limit),
        "select": fields,
    }
    if year_from is not None:
        params["filter"] = f"from-pub-date:{year_from}"
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    raw = fetch(url)
    if not raw or not raw.strip():
        raise ValueError("API returned empty response")
    data = json.loads(raw)
    rows = [
        parse_crossref_item(it, include_abstract)
        for it in data.get("message", {}).get("items", [])
    ]
    return filter_by_year(rows, year_from)


def parse_openalex_item(it, include_abstract=False):
    """将单条 OpenAlex JSON 记录规范化为统一字典。"""
    all_authorships = it.get("authorships") or []
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in all_authorships[:MAX_AUTHORS]
    ]
    oa = (it.get("open_access") or {}).get("oa_url") or ""
    source_obj = (it.get("primary_location") or {}).get("source") or {}
    source_name = clean_text(source_obj.get("display_name"))
    biblio = it.get("biblio") or {}
    first_p = biblio.get("first_page") or ""
    last_p = biblio.get("last_page") or ""
    page = f"{first_p}-{last_p}" if first_p and last_p else (first_p or last_p)
    doi_url = it.get("doi") or ""
    item_type = (it.get("type") or "").strip()
    if not item_type and "arxiv" in source_name.lower():
        item_type = "preprint"
    row = {
        "source": "openalex",
        "year": it.get("publication_year"),
        "title": clean_text(it.get("title")),
        "authors": ", ".join(a for a in authors if a),
        "author_count": len(all_authorships),
        "journal": source_name,
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "page": page,
        "id": strip_doi_prefix(doi_url),
        "url": doi_url,
        "oa_url": oa,
        "cited_by_count": it.get("cited_by_count"),
        "type": item_type,
    }
    if include_abstract:
        row["abstract"] = decode_openalex_abstract(it.get("abstract_inverted_index"))
    return row


def query_openalex(kw, limit, include_abstract=False, year_from=None):
    """查询 OpenAlex 文献接口。"""
    fields = "title,publication_year,doi,authorships,open_access,primary_location,cited_by_count,biblio,type"
    if include_abstract:
        fields += ",abstract_inverted_index"
    params = {
        "search": kw,
        "per-page": str(limit),
        "select": fields,
    }
    if year_from is not None:
        params["filter"] = f"from_publication_date:{year_from}-01-01"
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    raw = fetch(url)
    if not raw or not raw.strip():
        raise ValueError("API returned empty response")
    data = json.loads(raw)
    rows = [parse_openalex_item(it, include_abstract) for it in data.get("results", [])]
    return filter_by_year(rows, year_from)


def query_arxiv(kw, limit, include_abstract=False, year_from=None):
    """查询 arXiv Atom 接口（保留引号短语与显式语法，过滤错误 Feed 条目）。"""
    kw_clean = kw.strip()
    if ARXIV_SYNTAX_RE.search(kw_clean):
        sq = kw_clean
    else:
        terms = re.findall(r'"[^"]+"|\S+', kw_clean)
        sq = " AND ".join(f"all:{t}" for t in terms) if len(terms) > 1 else f"all:{kw_clean}"
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
    error_msgs = []
    for e in root.findall("a:entry", ns):
        raw_id = e.findtext("a:id", "", ns) or ""
        title = clean_text(e.findtext("a:title", "", ns))
        if "/api/errors" in raw_id.lower() or title.lower() == "error":
            err_detail = clean_text(e.findtext("a:summary", "", ns)) or "arXiv API returned Error entry"
            error_msgs.append(err_detail)
            continue
        ident = raw_id.rsplit("/abs/", 1)[-1]
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
    if error_msgs and not out:
        raise ValueError(error_msgs[0])
    return filter_by_year(out, year_from)


def query_dblp(kw, limit, include_abstract=False, year_from=None):
    """查询 DBLP 计算机科学文献接口（免费、无 Key、顶会期刊元数据干净）。"""
    url = (
        "https://dblp.org/search/publ/api?"
        + urllib.parse.urlencode({"q": kw, "h": str(limit), "format": "json"})
    )
    raw = fetch(url)
    if not raw or not raw.strip():
        raise ValueError("API returned empty response")
    data = json.loads(raw)
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
        venue = clean_text(info.get("venue") or "")
        raw_type = info.get("type") or ""
        if venue.lower() == "corr" or (
            "informal" in raw_type.lower() and "arxiv" in url_val.lower()
        ):
            norm_type = "preprint"
        else:
            norm_type = raw_type
        row = {
            "source": "dblp",
            "year": info.get("year") or "",
            "title": title,
            "authors": ", ".join(a for a in authors if a),
            "author_count": len(raw_authors),
            "journal": venue,
            "volume": str(info.get("volume") or ""),
            "issue": str(info.get("number") or ""),
            "page": str(info.get("pages") or ""),
            "id": doi or info.get("key") or "",
            "url": url_val,
            "type": norm_type,
        }
        if include_abstract:
            row["abstract"] = ""
        out.append(row)
    return filter_by_year(out, year_from)


def query_doaj(kw, limit, include_abstract=False, year_from=None):
    """查询 DOAJ 开放获取期刊接口（编码斜杠，避免路径破坏）。"""
    url = (
        "https://doaj.org/api/search/articles/"
        + urllib.parse.quote(kw, safe="")
        + f"?pageSize={limit}"
    )
    raw = fetch(url)
    if not raw or not raw.strip():
        raise ValueError("API returned empty response")
    data = json.loads(raw)
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
            "type": "journal-article",
        }
        if include_abstract:
            row["abstract"] = clean_text(b.get("abstract"))
        out.append(row)
    return filter_by_year(out, year_from)


def map_s2_type(pub_types, doi, arxiv_id):
    """将 Semantic Scholar publicationTypes 映射为标准文献类型。"""
    if not doi and arxiv_id:
        return "preprint"
    types_list = [str(t).strip() for t in (pub_types or []) if str(t).strip()]
    lower_set = {t.lower() for t in types_list}
    if "journalarticle" in lower_set:
        return "journal-article"
    if "conference" in lower_set:
        return "proceedings-article"
    return ", ".join(types_list)


def query_semanticscholar(kw, limit, include_abstract=False, year_from=None):
    """查询 Semantic Scholar 接口（建议设置 S2_API_KEY，否则极易 429）。"""
    fields = "title,year,authors,externalIds,venue,citationCount,openAccessPdf,publicationTypes"
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
            "type": map_s2_type(it.get("publicationTypes"), doi, arxiv_id),
        }
        if include_abstract:
            row["abstract"] = clean_text(it.get("abstract"))
        out.append(row)
    return filter_by_year(out, year_from)


def lookup_by_doi(doi_raw, include_abstract=False):
    """按 DOI 精准反查 Crossref 与 OpenAlex，合并互补字段。404 视为正常无结果。"""
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
        try:
            data = json.loads(fetch(cr_url))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise
        msg = data.get("message")
        return [parse_crossref_item(msg, include_abstract)] if msg else []

    def _get_oa():
        try:
            data = json.loads(fetch(oa_url))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise
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


def parse_doi_args(raw_doi_list):
    """解析 --doi 参数列表（支持逗号/空白分隔与重复传参），剥离前缀并保序去重。"""
    seen = set()
    dois = []
    for item in raw_doi_list or []:
        for part in re.split(r"[\s,]+", item.strip()):
            cleaned = strip_doi_prefix(part)
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                dois.append(cleaned)
    return dois


def lookup_multiple_dois(doi_list, include_abstract=False):
    """按单条或多条 DOI 并发反查 Crossref 与 OpenAlex，返回 (raw_results, errors, ok_sources, doi_checks)。"""
    if len(doi_list) == 1:
        per_doi = [(doi_list[0], *lookup_by_doi(doi_list[0], include_abstract))]
    else:
        has_mailto = bool(os.environ.get("CROSSREF_MAILTO", "").strip())
        workers = min(4 if has_mailto else 2, len(doi_list))
        per_doi_map = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(lookup_by_doi, d, include_abstract): d
                for d in doi_list
            }
            for fut in concurrent.futures.as_completed(futs):
                d = futs[fut]
                per_doi_map[d] = fut.result()
        per_doi = [(d, *per_doi_map[d]) for d in doi_list]

    raw_results = []
    all_errors = []
    seen_errors = set()
    source_had_ok = {"crossref": False, "openalex": False}
    doi_checks = []

    for doi, sub_raw, sub_errors in per_doi:
        raw_results.extend(sub_raw)
        err_sources = {s for s, _ in sub_errors}
        for src in ("crossref", "openalex"):
            if src not in err_sources:
                source_had_ok[src] = True
        for err_item in sub_errors:
            if err_item not in seen_errors:
                seen_errors.add(err_item)
                all_errors.append(err_item)

        sub_deduped, _ = merge_and_dedupe(sub_raw)
        if sub_deduped:
            rec = sub_deduped[0]
            doi_checks.append(
                {
                    "query_doi": doi,
                    "status": "found",
                    "title": rec.get("title", ""),
                    "year": rec.get("year"),
                    "authors": rec.get("authors", ""),
                    "url": rec.get("url", ""),
                    "errors": sub_errors,
                }
            )
        elif len(sub_errors) < 2:
            doi_checks.append(
                {
                    "query_doi": doi,
                    "status": "not_found",
                    "title": "",
                    "year": None,
                    "authors": "",
                    "url": "",
                    "errors": sub_errors,
                }
            )
        else:
            doi_checks.append(
                {
                    "query_doi": doi,
                    "status": "error",
                    "title": "",
                    "year": None,
                    "authors": "",
                    "url": "",
                    "errors": sub_errors,
                }
            )

    ok_sources = [s for s in ("crossref", "openalex") if source_had_ok[s]]
    return raw_results, all_errors, ok_sources, doi_checks


QUERIES = {
    "crossref": query_crossref,
    "openalex": query_openalex,
    "arxiv": query_arxiv,
    "dblp": query_dblp,
    "doaj": query_doaj,
    "semanticscholar": query_semanticscholar,
}


def merge_and_dedupe(results):
    """通过 DOI 与 (小写标题, 年份) 双索引去重并互补缺失字段，返回 (列表, 合并数)。"""
    seen_by_doi = {}
    seen_by_title = {}
    deduped = []
    merged_count = 0

    for row in results:
        doi_key = strip_doi_prefix(row.get("id") or "").lower()
        has_doi = bool(doi_key and doi_key.startswith("10."))
        title_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (row.get("title") or "").lower())
        year_key = str(row.get("year") or "")
        t_key = f"t:{title_key}|{year_key}" if title_key else ""

        existing = seen_by_doi.get(doi_key) if has_doi else None
        if existing is None and t_key:
            existing = seen_by_title.get(t_key)

        if existing is not None:
            merged_count += 1
            also = existing.setdefault("also_in", [])
            if row["source"] not in also and row["source"] != existing["source"]:
                also.append(row["source"])

            existing_doi = strip_doi_prefix(existing.get("id") or "").lower()
            existing_has_doi = bool(existing_doi and existing_doi.startswith("10."))
            if not existing_has_doi and has_doi:
                if existing.get("url") and not existing.get("oa_url"):
                    existing["oa_url"] = existing["url"]
                existing["id"] = strip_doi_prefix(row.get("id") or "")
                existing["url"] = row.get("url") or f"https://doi.org/{existing['id']}"
                seen_by_doi[doi_key] = existing
            elif row.get("source") == "arxiv" and row.get("url") and not existing.get("oa_url"):
                existing["oa_url"] = row["url"]

            for field in ("authors", "journal", "volume", "issue", "page", "oa_url", "abstract"):
                if not existing.get(field) and row.get(field):
                    existing[field] = row[field]

            existing_type = (existing.get("type") or "").strip()
            row_type = (row.get("type") or "").strip()
            if (not existing_type or existing_type.lower() == "open access") and (
                row_type and row_type.lower() != "open access"
            ):
                existing["type"] = row_type

            if not existing.get("author_count") and row.get("author_count"):
                existing["author_count"] = row["author_count"]
            if existing.get("cited_by_count") is None and row.get("cited_by_count") is not None:
                existing["cited_by_count"] = row["cited_by_count"]

            if t_key and t_key not in seen_by_title:
                seen_by_title[t_key] = existing
        else:
            item = dict(row)
            if has_doi:
                seen_by_doi[doi_key] = item
            if t_key:
                seen_by_title[t_key] = item
            deduped.append(item)
    return deduped, merged_count


def escape_bibtex(text):
    """转义 BibTeX/LaTeX 常见特殊字符（\\、&、%、#、_）。"""
    if not text:
        return ""
    return BIBTEX_ESCAPE_RE.sub(lambda m: BIBTEX_ESCAPE_MAP[m.group(1)], str(text))


def format_bibtex(rows):
    """将结果列表格式化为标准 BibTeX 字符串。"""
    entries = []
    for idx, r in enumerate(rows, 1):
        first_seg = (r.get("authors") or "").split(",")[0].strip()
        tokens = first_seg.split()
        raw_last = tokens[-1] if tokens else ""
        first_author = re.sub(r"[^a-zA-Z0-9]", "", raw_last).lower() or "ref"
        year = str(r.get("year") or "nd")
        cite_key = f"{first_author}{year}_{idx}"

        rtype = (r.get("type") or "").lower()
        journal_lower = (r.get("journal") or "").strip().lower()
        if "proceedings" in rtype or "conference" in rtype:
            btype = "inproceedings"
            venue_field = "booktitle"
        elif (
            r.get("source") == "arxiv"
            or "preprint" in rtype
            or "informal" in rtype
            or journal_lower == "corr"
        ):
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
            lines.append(f"  title = {{{escape_bibtex(r['title'])}}},")
        if r.get("authors"):
            authors_bib = escape_bibtex(r["authors"]).replace(", ", " and ")
            lines.append(f"  author = {{{authors_bib}}},")
        if r.get("journal"):
            lines.append(f"  {venue_field} = {{{escape_bibtex(r['journal'])}}},")
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


def split_authors(authors_str):
    """按 ', ' 拆分作者字符串并过滤空项。"""
    return [a.strip() for a in (authors_str or "").split(",") if a.strip()]


def _is_preprint_row(r):
    """判断记录是否属于预印本（arXiv、DBLP CoRR 等）。"""
    rtype = (r.get("type") or "").lower()
    j_low = (r.get("journal") or "").strip().lower()
    return (
        r.get("source") == "arxiv"
        or "preprint" in rtype
        or "informal" in rtype
        or j_low == "corr"
        or j_low.startswith("arxiv")
    )


def _format_gbt7714_author(name):
    """按 GB/T 7714-2015 转换单名作者：中文保留，西文转为 'Surname I I'（不加点）。"""
    if CJK_RE.search(name):
        return name
    parts = name.split()
    if len(parts) <= 1:
        return name
    surname = parts[-1]
    initials = []
    for p in parts[:-1]:
        for sub in re.split(r"[\s.-]+", p):
            if sub:
                initials.append(sub[0].upper())
    return f"{surname} {' '.join(initials)}" if initials else surname


def format_gbt7714_entry(r, idx):
    """将单条规范化记录格式化为 GB/T 7714-2015 引文。"""
    author_list = split_authors(r.get("authors"))
    total_n = max(len(author_list), r.get("author_count") or 0)
    shown = [_format_gbt7714_author(a) for a in author_list[:3]]
    has_cjk = bool((author_list and CJK_RE.search(author_list[0])) or CJK_RE.search(r.get("title") or ""))
    authors_str = ", ".join(shown)
    if total_n > 3 and authors_str:
        authors_str += ", 等" if has_cjk else ", et al."
    if not authors_str:
        authors_prefix = ""
    elif authors_str.endswith("."):
        authors_prefix = f"{authors_str} "
    else:
        authors_prefix = f"{authors_str}. "

    title = r.get("title") or ""
    year = str(r.get("year") or "").strip()
    journal = (r.get("journal") or "").strip()
    vol = str(r.get("volume") or "").strip()
    iss = str(r.get("issue") or "").strip()
    page = str(r.get("page") or "").strip()
    rtype = (r.get("type") or "").lower()

    is_preprint = _is_preprint_row(r)
    is_conf = (not is_preprint) and ("proceedings" in rtype or "conference" in rtype)
    doi = strip_doi_prefix(r.get("id") or "")
    link = f"https://doi.org/{doi}" if doi.startswith("10.") else (r.get("url") or "")

    if is_preprint or (not is_conf and not journal):
        out = f"[{idx}] {authors_prefix}{title}[EB/OL]."
        if year:
            out += f" ({year})."
        if link:
            out += f" {link}."
        if is_preprint:
            out += " （预印本，未经同行评审）"
        return out

    if is_conf:
        venue = journal or "Proceedings"
        out = f"[{idx}] {authors_prefix}{title}[C]//{venue}"
        if year:
            out += f", {year}"
        if page:
            out += f": {page}"
        out += "."
        if link:
            out += f" {link}"
        return out

    vol_issue = f"{vol}({iss})" if (vol and iss) else (vol if vol else (f"({iss})" if iss else ""))
    pub_parts = [x for x in (journal, year, vol_issue) if x]
    pub_seg = ", ".join(pub_parts)
    if page:
        pub_seg += f": {page}"
    out = f"[{idx}] {authors_prefix}{title}[J]. {pub_seg}."
    if link:
        out += f" {link}"
    return out


def format_gbt7714(rows):
    """将结果列表格式化为 GB/T 7714-2015 参考文献表。"""
    return "\n".join(format_gbt7714_entry(r, idx) for idx, r in enumerate(rows, 1))


def _format_apa_author(name):
    """按 APA 第 7 版转换单名作者：中文保留，西文转为 'Surname, I. I.'（带缩写点）。"""
    if CJK_RE.search(name):
        return name
    parts = name.split()
    if len(parts) <= 1:
        return name
    surname = parts[-1]
    initials = []
    for p in parts[:-1]:
        for sub in re.split(r"[\s.-]+", p):
            if sub:
                initials.append(f"{sub[0].upper()}.")
    return f"{surname}, {' '.join(initials)}" if initials else surname


def format_apa_entry(r):
    """将单条规范化记录格式化为 APA 第 7 版引文。"""
    author_list = split_authors(r.get("authors"))
    total_n = max(len(author_list), r.get("author_count") or 0)
    formatted = [_format_apa_author(a) for a in author_list[:20]]
    if not formatted:
        authors_part = ""
    elif len(formatted) == 1:
        authors_part = f"{formatted[0]} "
    elif total_n > 20 and len(formatted) >= 20:
        authors_part = f"{', '.join(formatted[:19])}, ... {formatted[19]} "
    else:
        authors_part = f"{', '.join(formatted[:-1])}, & {formatted[-1]} "

    year = str(r.get("year") or "").strip()
    year_part = f"({year})." if year else "(n.d.)."
    title = r.get("title") or ""
    journal = (r.get("journal") or "").strip()
    vol = str(r.get("volume") or "").strip()
    iss = str(r.get("issue") or "").strip()
    page = str(r.get("page") or "").strip()
    doi = strip_doi_prefix(r.get("id") or "")
    link = f"https://doi.org/{doi}" if doi.startswith("10.") else (r.get("url") or "")

    if _is_preprint_row(r):
        venue = journal or "arXiv"
        out = f"{authors_part}{year_part} {title} [Preprint]. {venue}."
        if link:
            out += f" {link}"
        return out

    out = f"{authors_part}{year_part} {title}."
    pub_segs = []
    if journal:
        pub_segs.append(f"*{journal}*")
    if vol:
        v_str = f"*{vol}*"
        if iss:
            v_str += f"({iss})"
        pub_segs.append(v_str)
    elif iss:
        pub_segs.append(f"({iss})")
    if page:
        pub_segs.append(page)
    if pub_segs:
        out += f" {', '.join(pub_segs)}."
    if link:
        out += f" {link}"
    return out


def format_apa(rows):
    """将结果列表格式化为 APA 第 7 版参考文献表。"""
    return "\n".join(format_apa_entry(r) for r in rows)


def run_concurrent_queries(wanted, keywords, limit, include_abstract, year_from=None):
    """并发查询所选文献源并保持原有顺序返回 (results, errors)。"""
    results_by_source = {}
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        future_map = {}
        for name in wanted:
            fn = QUERIES[name]
            if year_from is not None:
                fut = pool.submit(fn, keywords, limit, include_abstract, year_from=year_from)
            else:
                fut = pool.submit(fn, keywords, limit, include_abstract)
            future_map[fut] = name

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


def parse_sources_arg(raw_sources):
    """解析 --sources，忽略大小写并去重，同时返回 (有效源列表, 未知源列表)。"""
    wanted = []
    unknown = []
    for part in raw_sources.split(","):
        s = part.strip().lower()
        if not s:
            continue
        if s in QUERIES:
            if s not in wanted:
                wanted.append(s)
        else:
            if s not in unknown:
                unknown.append(s)
    return wanted, unknown


def emit_arg_error(message, out_fmt, warnings=None):
    """按输出模式安全报告参数错误（退出码 2），保证 --json/--bibtex/--format stdout 契约。"""
    warnings = warnings or []
    if out_fmt == "json":
        print(
            json.dumps(
                {
                    "error": message,
                    "results": [],
                    "errors": [],
                    "warnings": warnings,
                    "summary": {},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif out_fmt in ("bibtex", "gbt7714", "apa"):
        for w in warnings:
            print(f"警告：{w}", file=sys.stderr)
        print(message, file=sys.stderr)
    else:
        for w in warnings:
            print(f"警告：{w}")
        print(message)


def resolve_output_format(args):
    """解析并校验输出格式冲突，返回 (out_fmt, conflict_msg)。"""
    chosen = []
    if args.json:
        chosen.append("json")
    if args.bibtex:
        chosen.append("bibtex")
    if args.format and args.format not in chosen:
        chosen.append(args.format)
    if len(chosen) > 1:
        return ("json" if "json" in chosen else chosen[0]), f"输出格式参数冲突：{', '.join(chosen)} 不能同时使用。"
    return (chosen[0] if chosen else "text"), ""


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    default_sources = "crossref,openalex,arxiv,dblp"
    if os.environ.get("S2_API_KEY", "").strip():
        default_sources += ",semanticscholar"

    p = argparse.ArgumentParser(description="一次并发查多个文献源，合并去重输出")
    p.add_argument("keywords", nargs="?", default="", help="检索词，中英文都可（--doi 模式下可省略）")
    p.add_argument(
        "--doi",
        action="append",
        default=[],
        help="按 DOI 精准反查（支持单个、逗号分隔多个或重复传参）",
    )
    p.add_argument(
        "--sources",
        default=default_sources,
        help="逗号分隔，可选 crossref/openalex/arxiv/dblp/doaj/semanticscholar",
    )
    p.add_argument("--limit", type=int, default=3, help="每个源取几条（1-10），默认 3")
    p.add_argument("--year-from", type=int, default=None, help="过滤起始发表年份（1900-2100）")
    p.add_argument("--abstract", action="store_true", help="同时提取摘要（默认关闭）")
    p.add_argument("--bibtex", action="store_true", help="以 BibTeX 格式输出")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument(
        "--format",
        choices=["text", "json", "bibtex", "gbt7714", "apa"],
        default=None,
        help="指定输出格式（text / json / bibtex / gbt7714 / apa）",
    )
    args = p.parse_args()

    out_fmt, conflict_msg = resolve_output_format(args)
    if conflict_msg:
        emit_arg_error(conflict_msg, out_fmt)
        return 2

    limit = max(1, min(args.limit, 10))
    doi_list = parse_doi_args(args.doi)
    doi_mode = bool(doi_list)
    warnings = []

    if args.year_from is not None and not (1900 <= args.year_from <= 2100):
        emit_arg_error("起始年份非法，--year-from 必须在 1900 到 2100 之间。", out_fmt)
        return 2

    if not doi_mode and not args.keywords.strip():
        emit_arg_error("检索词为空。请提供具体主题或使用 --doi <DOI>。", out_fmt)
        return 2

    doi_checks = []
    if doi_mode:
        wanted = ["crossref", "openalex"]
        raw_results, errors, ok_sources, doi_checks = lookup_multiple_dois(doi_list, args.abstract)
    else:
        wanted, unknown = parse_sources_arg(args.sources)
        if unknown:
            warnings.append(f"已忽略未知检索源：{', '.join(unknown)}")
        if not wanted:
            emit_arg_error("没有可用的源，请检查 --sources 参数。", out_fmt, warnings)
            return 2
        raw_results, errors = run_concurrent_queries(
            wanted, args.keywords.strip(), limit, args.abstract, year_from=args.year_from
        )
        ok_sources = [s for s in wanted if not any(e[0] == s for e in errors)]

    results, merged_count = merge_and_dedupe(raw_results)

    if results:
        exit_code = 0
        status_msg = ""
    elif not ok_sources:
        exit_code = 3
        status_msg = "所有源都请求失败（网络或限流），本轮检索未完成，不能当作“没有文献”。"
    else:
        exit_code = 1
        status_msg = "所选源正常返回但均为 0 条结果，可按第三档处理或更换检索词。"

    if out_fmt == "json":
        enriched_results = []
        for idx, r in enumerate(results, 1):
            item = dict(r)
            item["citation_gbt7714"] = format_gbt7714_entry(r, idx)
            item["citation_apa"] = format_apa_entry(r)
            enriched_results.append(item)
        payload = {
            "results": enriched_results,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "wanted_sources": wanted,
                "ok_sources": ok_sources,
                "raw_count": len(raw_results),
                "deduped_count": len(results),
                "merged_duplicates": merged_count,
            },
        }
        if doi_mode:
            payload["doi_checks"] = doi_checks
        if status_msg:
            payload["message"] = status_msg
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif out_fmt in ("bibtex", "gbt7714", "apa"):
        for w in warnings:
            print(f"警告：{w}", file=sys.stderr)
        if results:
            if out_fmt == "bibtex":
                print(format_bibtex(results))
            elif out_fmt == "gbt7714":
                print(format_gbt7714(results))
            else:
                print(format_apa(results))
        if status_msg:
            print(status_msg, file=sys.stderr)
    else:
        for w in warnings:
            print(f"警告：{w}")
        if doi_mode and len(doi_checks) > 1:
            print("批量 DOI 核验结果：")
            for chk in doi_checks:
                if chk["status"] == "found":
                    print(f"  [找到] {chk['query_doi']} -> {chk['title']} ({chk['year'] or '----'})")
                elif chk["status"] == "not_found":
                    print(f"  [未找到] {chk['query_doi']}（源正常返回 404）")
                else:
                    print(f"  [请求失败] {chk['query_doi']}")
            print()
        for r in results:
            tag = r.get("type") or ""
            also = f" +{'/'.join(r['also_in'])}" if r.get("also_in") else ""
            src_label = f"{r['source']}{also}"
            print(
                f"[{src_label}] {r.get('year') or '----'} | {r.get('title', '')[:75]} | {r.get('id') or r.get('url')}"
            )
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
        print(
            f"汇总：成功 {len(ok_sources)}/{len(wanted)} 个源，原始命中 {len(raw_results)} 条，去重合并 {merged_count} 条，输出 {len(results)} 条。"
        )
        if "doaj" in ok_sources and any(r["source"] == "doaj" for r in results):
            print("提示：DOAJ 相关性普遍偏低，使用前请人工核对标题与摘要。")
        if errors:
            err_str = "；".join(f"{s}({msg})" for s, msg in errors)
            print(f"失败源：{err_str}")
        if status_msg:
            print(status_msg)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
