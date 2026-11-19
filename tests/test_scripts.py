"""零网络依赖的单元测试（Mock HTTP 响应），覆盖核心解析、去重、边界条件、环境自检与退出码契约。"""

import base64
import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = str(ROOT_DIR / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import check_environment  # noqa: E402
import github_lookup  # noqa: E402
import lookup  # noqa: E402


class TestLookupHelpers(unittest.TestCase):
    """测试 lookup.py 的纯函数、跨源去重与 BibTeX 导出。"""

    def test_clean_text_preserves_math_inequalities(self):
        raw = "<i>Retrieval</i> &amp; a < b > c with <jats:italic>JATS</jats:italic>   spaces"
        self.assertEqual(lookup.clean_text(raw), "Retrieval & a < b > c with JATS spaces")

    def test_decode_openalex_abstract(self):
        inv = {"Hello": [0], "world": [1], "RAG": [2]}
        self.assertEqual(lookup.decode_openalex_abstract(inv), "Hello world RAG")
        self.assertEqual(lookup.decode_openalex_abstract({}), "")
        self.assertEqual(lookup.decode_openalex_abstract(None), "")

    def test_join_authors_unifies_western_and_chinese_names(self):
        pairs = [("张", "张"), ("李", "明"), ("Smith", "John"), ("", "")]
        self.assertEqual(lookup.join_authors(pairs), "张, 李明, John Smith")

    def test_author_limit_20_and_count(self):
        many_authors = [{"family": f"F{i}", "given": f"G{i}"} for i in range(25)]
        item = {
            "title": ["Test Paper"],
            "author": many_authors,
            "issued": {"date-parts": [[2025]]},
            "DOI": "10.1000/test",
            "container-title": ["Journal A"],
            "type": "journal-article",
        }
        parsed = lookup.parse_crossref_item(item)
        self.assertEqual(parsed["author_count"], 25)
        self.assertEqual(len(parsed["authors"].split(", ")), 20)
        self.assertTrue(parsed["authors"].startswith("G0 F0"))

    def test_merge_and_dedupe_complements_fields(self):
        cr = {
            "source": "crossref",
            "year": 2025,
            "title": "Graph RAG Survey",
            "authors": "Boci Peng",
            "author_count": 1,
            "journal": "ACM TOIS",
            "volume": "43",
            "issue": "2",
            "page": "1-30",
            "id": "10.1145/3777378",
            "url": "https://doi.org/10.1145/3777378",
            "type": "journal-article",
        }
        oa = {
            "source": "openalex",
            "year": 2025,
            "title": "Graph RAG Survey",
            "authors": "Boci Peng",
            "author_count": 1,
            "journal": "",
            "volume": "",
            "issue": "",
            "page": "",
            "id": "10.1145/3777378",
            "url": "https://doi.org/10.1145/3777378",
            "oa_url": "https://arxiv.org/pdf/2408.08921",
            "cited_by_count": 924,
            "type": "article",
        }
        deduped, merged = lookup.merge_and_dedupe([cr, oa])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(merged, 1)
        self.assertEqual(deduped[0]["volume"], "43")
        self.assertEqual(deduped[0]["oa_url"], "https://arxiv.org/pdf/2408.08921")
        self.assertEqual(deduped[0]["cited_by_count"], 924)
        self.assertEqual(deduped[0]["also_in"], ["openalex"])

    def test_merge_dual_index_arxiv_and_crossref(self):
        arxiv_row = {
            "source": "arxiv",
            "year": "2024",
            "title": "Attention Is All You Need!",
            "authors": "Ashish Vaswani",
            "author_count": 1,
            "journal": "arXiv",
            "volume": "",
            "issue": "",
            "page": "",
            "id": "1706.03762",
            "url": "https://arxiv.org/abs/1706.03762",
            "type": "preprint",
        }
        cr_row = {
            "source": "crossref",
            "year": 2024,
            "title": "Attention Is All You Need",
            "authors": "Ashish Vaswani",
            "author_count": 1,
            "journal": "NeurIPS",
            "volume": "30",
            "issue": "",
            "page": "1-11",
            "id": "10.5555/3295222.3295349",
            "url": "https://doi.org/10.5555/3295222.3295349",
            "type": "proceedings-article",
        }
        deduped, merged = lookup.merge_and_dedupe([arxiv_row, cr_row])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(merged, 1)
        self.assertEqual(deduped[0]["id"], "10.5555/3295222.3295349")
        self.assertEqual(deduped[0]["url"], "https://doi.org/10.5555/3295222.3295349")
        self.assertEqual(deduped[0]["oa_url"], "https://arxiv.org/abs/1706.03762")
        self.assertEqual(deduped[0]["volume"], "30")
        self.assertEqual(deduped[0]["also_in"], ["crossref"])

    def test_format_bibtex_escaping_empty_authors_and_corr_preprint(self):
        rows = [
            {
                "source": "crossref",
                "year": 2025,
                "title": "Graph_RAG & 100% #1 \\Test",
                "authors": "Boci Peng, Yun Zhu",
                "journal": "ACM & IEEE",
                "volume": "43",
                "issue": "2",
                "page": "1-30",
                "id": "10.1145/3777378",
                "url": "https://doi.org/10.1145/3777378",
                "type": "journal-article",
            },
            {
                "source": "dblp",
                "year": "2025",
                "title": "Preprint Note",
                "authors": " , ",
                "journal": "CoRR",
                "volume": "",
                "issue": "",
                "page": "",
                "id": "journals/corr/abs-2501-00001",
                "url": "https://arxiv.org/abs/2501.00001",
                "type": "Informal and Other Publications",
            },
        ]
        bib = lookup.format_bibtex(rows)
        self.assertIn("@article{peng2025_1,", bib)
        self.assertIn(r"Graph\_RAG \& 100\% \#1 \textbackslash{}Test", bib)
        self.assertIn(r"journal = {ACM \& IEEE}", bib)
        self.assertIn("url = {https://doi.org/10.1145/3777378}", bib)
        self.assertIn("@misc{ref2025_2,", bib)
        self.assertIn("howpublished = {CoRR}", bib)


class TestLookupQueriesAndCLI(unittest.TestCase):
    """测试各源解析、类型规范化、年份过滤、DOI 404 与 CLI 输出契约。"""

    def test_query_arxiv_xml_parsing_and_syntax_preservation(self):
        sample_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>http://arxiv.org/abs/2401.12345v1</id>
            <published>2024-01-22T00:00:00Z</published>
            <title>Sample   arXiv Title</title>
            <summary>Shows x &lt; y &gt; z in math.</summary>
            <author><name>Alice</name></author>
          </entry>
        </feed>"""
        with mock.patch.object(lookup, "fetch", return_value=sample_xml) as m:
            rows = lookup.query_arxiv('"graph rag" retrieval', 2, include_abstract=True)
        called_url = urllib.parse.unquote_plus(m.call_args[0][0])
        self.assertIn('all:"graph rag" AND all:retrieval', called_url)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "2401.12345v1")
        self.assertEqual(rows[0]["year"], "2024")
        self.assertEqual(rows[0]["abstract"], "Shows x < y > z in math.")

        with mock.patch.object(lookup, "fetch", return_value=sample_xml) as m2:
            lookup.query_arxiv('cat:cs.DC AND all:"consistency"', 2)
        called_url2 = urllib.parse.unquote_plus(m2.call_args[0][0])
        self.assertIn('search_query=cat:cs.DC AND all:"consistency"', called_url2)

    def test_query_arxiv_rejects_error_entry(self):
        error_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/api/errors#incorrect_id_format</id>
            <title>Error</title>
            <summary>Malformed query syntax</summary>
          </entry>
        </feed>"""
        with mock.patch.object(lookup, "fetch", return_value=error_xml):
            with self.assertRaises(ValueError):
                lookup.query_arxiv("bad_query", 2)

    def test_query_dblp_parsing_and_corr_preprint(self):
        sample_json = json.dumps(
            {
                "result": {
                    "hits": {
                        "hit": [
                            {
                                "info": {
                                    "title": "Attention Is All You Need.",
                                    "authors": {"author": {"text": "Ashish Vaswani"}},
                                    "venue": "NIPS",
                                    "year": "2017",
                                    "pages": "5998-6008",
                                    "doi": "10.5555/3295222.3295349",
                                    "type": "Conference and Workshop Papers",
                                }
                            },
                            {
                                "info": {
                                    "title": "Some Preprint.",
                                    "authors": {"author": [{"text": "Bob"}]},
                                    "venue": "CoRR",
                                    "year": "2025",
                                    "ee": "https://arxiv.org/abs/2501.00001",
                                    "type": "Informal and Other Publications",
                                }
                            },
                        ]
                    }
                }
            }
        ).encode("utf-8")
        with mock.patch.object(lookup, "fetch", return_value=sample_json):
            rows = lookup.query_dblp("attention", 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "Attention Is All You Need")
        self.assertEqual(rows[0]["journal"], "NIPS")
        self.assertEqual(rows[1]["type"], "preprint")

    def test_query_doaj_type_and_slash_encoding(self):
        payload = json.dumps(
            {
                "results": [
                    {
                        "bibjson": {
                            "title": "Open Journal Article",
                            "author": [{"name": "Jane Doe"}],
                            "journal": {"title": "Sensors", "volume": "10", "number": "2"},
                            "year": "2025",
                            "identifier": [{"type": "doi", "id": "10.3390/test"}],
                        }
                    }
                ]
            }
        ).encode("utf-8")
        with mock.patch.object(lookup, "fetch", return_value=payload) as m:
            rows = lookup.query_doaj("AI/ML", 2)
        self.assertIn("AI%2FML", m.call_args[0][0])
        self.assertEqual(rows[0]["type"], "journal-article")

    def test_openalex_and_s2_type_mapping(self):
        oa_item = {
            "title": "OpenAlex Preprint",
            "publication_year": 2025,
            "doi": "",
            "authorships": [{"author": {"display_name": "Alice"}}],
            "open_access": {"oa_url": "https://arxiv.org/abs/2501.00002"},
            "primary_location": {"source": {"display_name": "arXiv (Cornell University)"}},
            "biblio": {},
            "type": "",
        }
        parsed_oa = lookup.parse_openalex_item(oa_item)
        self.assertEqual(parsed_oa["type"], "preprint")
        oa_item_article = dict(oa_item, type="article")
        self.assertEqual(lookup.parse_openalex_item(oa_item_article)["type"], "article")

        s2_payload = json.dumps(
            {
                "data": [
                    {
                        "title": "S2 Conf Paper",
                        "year": 2024,
                        "authors": [{"name": "Tom"}],
                        "externalIds": {"DOI": "10.1000/conf"},
                        "venue": "NeurIPS",
                        "publicationTypes": ["Conference"],
                    },
                    {
                        "title": "S2 ArXiv Only",
                        "year": 2025,
                        "authors": [{"name": "Jerry"}],
                        "externalIds": {"ArXiv": "2502.00001"},
                        "venue": "arXiv.org",
                        "publicationTypes": [],
                    },
                ]
            }
        ).encode("utf-8")
        with mock.patch.object(lookup, "fetch", return_value=s2_payload):
            s2_rows = lookup.query_semanticscholar("rag", 2)
        self.assertEqual(s2_rows[0]["type"], "proceedings-article")
        self.assertEqual(s2_rows[1]["type"], "preprint")

    def test_doi_404_returns_exit_code_1_and_valid_json(self):
        err_404 = urllib.error.HTTPError("https://api.crossref.org/works/x", 404, "Not Found", {}, io.BytesIO(b""))
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["lookup.py", "--doi", "10.9999/fake", "--json"]),
            mock.patch.object(lookup, "fetch", side_effect=err_404),
            redirect_stdout(buf),
        ):
            rc = lookup.main()
        self.assertEqual(rc, 1)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["results"], [])
        self.assertEqual(data["errors"], [])
        self.assertIn("message", data)

    def test_year_from_filtering_and_validation(self):
        cr_payload = json.dumps(
            {
                "message": {
                    "items": [
                        {"title": ["Old Paper"], "issued": {"date-parts": [[2018]]}, "DOI": "10.1/old"},
                        {"title": ["New Paper"], "issued": {"date-parts": [[2024]]}, "DOI": "10.1/new"},
                    ]
                }
            }
        ).encode("utf-8")
        with mock.patch.object(lookup, "fetch", return_value=cr_payload) as m:
            rows = lookup.query_crossref("rag", 5, year_from=2023)
        self.assertIn("filter=from-pub-date%3A2023", m.call_args[0][0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "New Paper")

        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["lookup.py", "rag", "--year-from", "1800", "--json"]),
            redirect_stdout(buf),
        ):
            rc = lookup.main()
        self.assertEqual(rc, 2)
        self.assertIn("error", json.loads(buf.getvalue()))

    def test_default_sources_excludes_doaj_and_unknown_source_warns(self):
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["lookup.py", "RAG", "--sources", "Crossref,arxvi", "--json"]),
            mock.patch.object(lookup, "fetch", return_value=b'{"message": {"items": []}}'),
            mock.patch.dict(os.environ, {}, clear=False),
            redirect_stdout(buf),
        ):
            rc = lookup.main()
        self.assertEqual(rc, 1)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["summary"]["wanted_sources"], ["crossref"])
        self.assertTrue(any("arxvi" in w for w in data["warnings"]))

        # 默认源不包含 doaj
        buf2 = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["lookup.py", "RAG", "--json"]),
            mock.patch.dict(os.environ, {"S2_API_KEY": ""}, clear=False),
            mock.patch.object(lookup, "run_concurrent_queries", return_value=([], [])) as m_run,
            redirect_stdout(buf2),
        ):
            lookup.main()
        wanted = m_run.call_args[0][0]
        self.assertEqual(wanted, ["crossref", "openalex", "arxiv", "dblp"])

    def test_cli_json_and_bibtex_exit_codes_clean_stdout(self):
        # 退出码 2：空关键词 (--json)
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["lookup.py", "   ", "--json"]), redirect_stdout(buf):
            rc = lookup.main()
        self.assertEqual(rc, 2)
        self.assertIn("error", json.loads(buf.getvalue()))

        # 退出码 3：全失败 (--json)
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["lookup.py", "RAG", "--sources", "crossref", "--json"]),
            mock.patch.object(lookup, "fetch", side_effect=OSError("network down")),
            redirect_stdout(buf),
        ):
            rc = lookup.main()
        self.assertEqual(rc, 3)
        parsed = json.loads(buf.getvalue())
        self.assertIn("所有源都请求失败", parsed["message"])

        # 退出码 1：0 结果 (--bibtex) 不污染 stdout
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["lookup.py", "nonexistent_xyz", "--sources", "crossref", "--bibtex"]),
            mock.patch.object(lookup, "fetch", return_value=b'{"message": {"items": []}}'),
            redirect_stdout(out_buf),
            redirect_stderr(err_buf),
        ):
            rc = lookup.main()
        self.assertEqual(rc, 1)
        self.assertEqual(out_buf.getvalue().strip(), "")
        self.assertIn("0 条结果", err_buf.getvalue())


class TestGitHubLookup(unittest.TestCase):
    """测试 github_lookup.py 的校验、Trees/README 探测、许可证风险与串行熔断。"""

    def test_empty_keywords_returns_2_and_json_contract(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["github_lookup.py", "", "--json"]), redirect_stdout(buf):
            rc = github_lookup.main()
        self.assertEqual(rc, 2)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["items"], [])
        self.assertIn("error", data)

    def test_trees_api_finds_separated_backend_frontend_and_src_dirs(self):
        tree_payload = {
            "tree": [
                {"type": "tree", "path": "src"},
                {"type": "tree", "path": "backend/src"},
                {"type": "tree", "path": "a/b/c/src"},
                {"type": "blob", "path": "README.md"},
                {"type": "blob", "path": "backend/pom.xml"},
                {"type": "blob", "path": "frontend/package.json"},
                {"type": "blob", "path": "sql/init.sql"},
                {"type": "blob", "path": "a/b/c/d/deep.sql"},
            ]
        }
        with mock.patch.object(github_lookup, "fetch_json", return_value=tree_payload):
            files = github_lookup.inspect_repo_files("user/repo", "main")
        self.assertIn("src/", files)
        self.assertIn("backend/src/", files)
        self.assertNotIn("a/b/c/src/", files)
        self.assertIn("README.md", files)
        self.assertIn("backend/pom.xml", files)
        self.assertIn("frontend/package.json", files)
        self.assertIn("sql/init.sql", files)
        self.assertNotIn("a/b/c/d/deep.sql", files)

    def test_search_repos_flags_license_risk_forks_and_spaced_language(self):
        search_payload = {
            "total_count": 3,
            "items": [
                {
                    "full_name": "u/tiny",
                    "default_branch": "main",
                    "html_url": "https://github.com/u/tiny",
                    "description": "tiny",
                    "language": "Java",
                    "stargazers_count": 0,
                    "pushed_at": "2026-11-01T00:00:00Z",
                    "size": 20,
                    "license": None,
                },
                {
                    "full_name": "u/gpl",
                    "default_branch": "main",
                    "html_url": "https://github.com/u/gpl",
                    "description": "gpl",
                    "language": "Java",
                    "stargazers_count": 10,
                    "pushed_at": "2026-11-01T00:00:00Z",
                    "size": 60000,
                    "license": {"spdx_id": "GPL-3.0"},
                },
                {
                    "full_name": "u/custom",
                    "default_branch": "main",
                    "html_url": "https://github.com/u/custom",
                    "description": "custom",
                    "language": "Jupyter Notebook",
                    "stargazers_count": 5,
                    "pushed_at": "2026-11-01T00:00:00Z",
                    "size": 500,
                    "license": {"spdx_id": "NOASSERTION"},
                },
            ],
        }
        with mock.patch.object(github_lookup, "fetch_json", return_value=search_payload) as m:
            total, items = github_lookup.search_repos(
                "图书管理", "Jupyter Notebook", "", 10, 0, "updated", 5, False, 2
            )
        called_q = urllib.parse.unquote_plus(m.call_args[0][0])
        self.assertIn("fork:false", called_q)
        self.assertIn('language:"Jupyter Notebook"', called_q)
        self.assertEqual(total, 3)
        self.assertTrue(items[0]["too_small"])
        self.assertEqual(items[0]["license"], "无")
        self.assertEqual(items[0]["license_risk"], "none_prohibited")
        self.assertTrue(items[1]["oversize"])
        self.assertEqual(items[1]["license"], "GPL-3.0")
        self.assertEqual(items[1]["license_risk"], "copyleft")
        self.assertEqual(items[2]["license"], "自定义/需人工核对")
        self.assertEqual(items[2]["license_risk"], "manual_check")

    def test_serial_circuit_breaker_stops_after_rate_limit(self):
        search_payload = {
            "total_count": 3,
            "items": [
                {"full_name": "u/r1", "default_branch": "main", "size": 500},
                {"full_name": "u/r2", "default_branch": "main", "size": 500},
                {"full_name": "u/r3", "default_branch": "main", "size": 500},
            ],
        }
        err_429 = urllib.error.HTTPError(
            "https://api.github.com/repos/u/r2/git/trees/main",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b"rate limit"),
        )
        calls = []

        def fake_fetch(url):
            calls.append(url)
            if "search/repositories" in url:
                return search_payload
            if "u/r1" in url:
                return {"tree": [{"type": "blob", "path": "README.md"}]}
            if "u/r2" in url:
                raise err_429
            raise AssertionError("熔断后不应继续请求第三个仓库")

        with mock.patch.object(github_lookup, "fetch_json", side_effect=fake_fetch):
            _, items = github_lookup.search_repos(
                "library", "Java", "", 10, 0, "updated", 5, True, 3
            )
        self.assertEqual(items[0]["key_files"], ["README.md"])
        self.assertIn("429 限流", items[1]["key_files_error"])
        self.assertNotIn("key_files", items[2])
        self.assertNotIn("key_files_error", items[2])
        self.assertEqual(len(calls), 3)

    def test_readme_detection(self):
        readme_md = "# Book System\n## 快速开始\n请先启动 Redis 和 Docker。"
        encoded = base64.b64encode(readme_md.encode("utf-8")).decode("ascii")
        with mock.patch.object(
            github_lookup, "fetch_json", return_value={"encoding": "base64", "content": encoded}
        ):
            info = github_lookup.fetch_repo_readme("u/repo")
        self.assertTrue(info["has_run_guide"])
        self.assertEqual(info["middleware_hints"], ["redis", "docker"])
        self.assertIn("Book System", info["readme_excerpt"])

    def test_rate_limit_returns_3_json_and_text(self):
        err = urllib.error.HTTPError(
            "https://api.github.com/search/repositories",
            403,
            "rate limit exceeded",
            {},
            io.BytesIO(b'{"message":"API rate limit exceeded"}'),
        )
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["github_lookup.py", "library"]),
            mock.patch.object(github_lookup, "fetch_json", side_effect=err),
            redirect_stdout(buf),
        ):
            rc = github_lookup.main()
        self.assertEqual(rc, 3)
        self.assertIn("site:github.com library", buf.getvalue())

        err2 = urllib.error.HTTPError(
            "https://api.github.com/search/repositories",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b"rate limit"),
        )
        buf_json = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["github_lookup.py", "library", "--json"]),
            mock.patch.object(github_lookup, "fetch_json", side_effect=err2),
            redirect_stdout(buf_json),
        ):
            rc2 = github_lookup.main()
        self.assertEqual(rc2, 3)
        data = json.loads(buf_json.getvalue())
        self.assertIn("429", data["error"])
        self.assertIn("site:github.com library", data["fallback"])


class TestCheckEnvironment(unittest.TestCase):
    """测试 check_environment.py 的密钥透传、脱敏、状态判定与 --strict 退出码。"""

    def test_probe_uses_env_vars_and_redacts_secrets_on_error(self):
        captured = []

        class DummyResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=15):
            captured.append((req.full_url, dict(req.header_items())))
            if "api.openalex.org" in req.full_url:
                raise RuntimeError(f"failed url={req.full_url}")
            return DummyResp()

        env_patch = {
            "GITHUB_TOKEN": "ghp_secret_123",
            "OPENALEX_API_KEY": "oa_secret_456",
            "CROSSREF_MAILTO": "user@example.com",
            "S2_API_KEY": "s2_secret_789",
        }
        with (
            mock.patch.dict(os.environ, env_patch, clear=False),
            mock.patch.object(check_environment.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            endpoints = check_environment.build_probe_endpoints()
            self.assertTrue(any(name == "Semantic Scholar" for name, _, _ in endpoints))

            _, _, ok_cr, _ = check_environment.probe_endpoint(
                "Crossref", "https://api.crossref.org/works?rows=1", True
            )
            _, _, ok_oa, detail_oa = check_environment.probe_endpoint(
                "OpenAlex", "https://api.openalex.org/works?per-page=1", False
            )
            _, _, ok_gh, _ = check_environment.probe_endpoint(
                "GitHub", "https://api.github.com/search/repositories?q=RAG", True
            )
            _, _, ok_s2, _ = check_environment.probe_endpoint(
                "Semantic Scholar", "https://api.semanticscholar.org/graph/v1/paper/search", False
            )

        self.assertTrue(ok_cr)
        self.assertFalse(ok_oa)
        self.assertNotIn("oa_secret_456", detail_oa)
        self.assertIn("***", detail_oa)
        self.assertTrue(ok_gh)
        self.assertTrue(ok_s2)

        hdrs_by_host = {url: {k.lower(): v for k, v in h.items()} for url, h in captured}
        cr_url = next(u for u in hdrs_by_host if "crossref.org" in u)
        self.assertIn("user@example.com", hdrs_by_host[cr_url]["user-agent"])
        gh_url = next(u for u in hdrs_by_host if "github.com" in u)
        self.assertEqual(hdrs_by_host[gh_url]["authorization"], "Bearer ghp_secret_123")
        s2_url = next(u for u in hdrs_by_host if "semanticscholar.org" in u)
        self.assertEqual(hdrs_by_host[s2_url]["x-api-key"], "s2_secret_789")

    def test_status_evaluation_and_strict_exit_codes(self):
        self.assertEqual(check_environment.evaluate_overall_status(False, False, []), "unavailable")
        self.assertEqual(check_environment.evaluate_overall_status(True, True, []), "partial")
        self.assertEqual(
            check_environment.evaluate_overall_status(
                True, False, [{"required": True, "ok": True}, {"required": False, "ok": True}]
            ),
            "ready",
        )
        self.assertEqual(
            check_environment.evaluate_overall_status(
                True, False, [{"required": True, "ok": True}, {"required": False, "ok": False}]
            ),
            "degraded",
        )
        self.assertEqual(
            check_environment.evaluate_overall_status(
                True, False, [{"required": True, "ok": False}, {"required": False, "ok": True}]
            ),
            "needs_setup",
        )

        # OpenAlex(可选) 失败但必需源均成功 -> --strict 返回 0 且总体状态 degraded
        def fake_probe_degraded(name, url, required):
            if name == "OpenAlex":
                return name, required, False, "HTTP 429"
            return name, required, True, "HTTP 200"

        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["check_environment.py", "--strict", "--json"]),
            mock.patch.object(check_environment, "probe_endpoint", side_effect=fake_probe_degraded),
            redirect_stdout(buf),
        ):
            rc = check_environment.main()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["status"], "degraded")

        # 必需源 Crossref 失败 -> --strict 返回 1
        def fake_probe_req_fail(name, url, required):
            if name == "Crossref":
                return name, required, False, "HTTP 503"
            return name, required, True, "HTTP 200"

        buf2 = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["check_environment.py", "--strict", "--json"]),
            mock.patch.object(check_environment, "probe_endpoint", side_effect=fake_probe_req_fail),
            redirect_stdout(buf2),
        ):
            rc2 = check_environment.main()
        self.assertEqual(rc2, 1)
        self.assertEqual(json.loads(buf2.getvalue())["status"], "needs_setup")


class TestSkillMetadataAndEvals(unittest.TestCase):
    """校验 skill-dependencies.json 与 evals/evals.json 结构完整性。"""

    def test_dependencies_and_evals_files(self):
        deps = json.loads((ROOT_DIR / "skill-dependencies.json").read_text(encoding="utf-8"))
        self.assertEqual(deps["version"], "1.4.0")
        oa = next(e for e in deps["network_endpoints"] if e["name"] == "OpenAlex")
        self.assertFalse(oa["required"])

        evals = json.loads((ROOT_DIR / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual(evals["skill_name"], "source-first")
        self.assertEqual(evals["version"], "1.4.0")
        trigs = evals["trigger_evals"]
        self.assertEqual(len(trigs), 20)
        self.assertEqual(sum(1 for t in trigs if t["should_trigger"]), 10)
        self.assertEqual(sum(1 for t in trigs if not t["should_trigger"]), 10)
        behs = evals["behavioral_evals"]
        self.assertEqual(len(behs), 5)
        for b in behs:
            self.assertTrue(b["id"] and b["prompt"] and b["assertions"])


if __name__ == "__main__":
    unittest.main()
