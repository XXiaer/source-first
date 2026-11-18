"""零网络依赖的单元测试（Mock HTTP 响应），覆盖核心解析、去重、边界条件与退出码契约。"""

import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import github_lookup  # noqa: E402
import lookup  # noqa: E402


class TestLookupHelpers(unittest.TestCase):
    """测试 lookup.py 的纯函数与边界解析。"""

    def test_clean_text_preserves_math_inequalities(self):
        raw = "<i>Retrieval</i> &amp; a < b > c with <jats:italic>JATS</jats:italic>   spaces"
        self.assertEqual(lookup.clean_text(raw), "Retrieval & a < b > c with JATS spaces")

    def test_decode_openalex_abstract(self):
        inv = {"Hello": [0], "world": [1], "RAG": [2]}
        self.assertEqual(lookup.decode_openalex_abstract(inv), "Hello world RAG")
        self.assertEqual(lookup.decode_openalex_abstract({}), "")
        self.assertEqual(lookup.decode_openalex_abstract(None), "")

    def test_join_authors_dedupes_chinese_names(self):
        pairs = [("张", "张"), ("三", "三"), ("Smith", "John"), ("", "")]
        self.assertEqual(lookup.join_authors(pairs), "张, 三, Smith John")

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
            "type": "open access",
        }
        deduped, merged = lookup.merge_and_dedupe([cr, oa])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(merged, 1)
        self.assertEqual(deduped[0]["volume"], "43")
        self.assertEqual(deduped[0]["oa_url"], "https://arxiv.org/pdf/2408.08921")
        self.assertEqual(deduped[0]["cited_by_count"], 924)
        self.assertEqual(deduped[0]["also_in"], ["openalex"])

    def test_format_bibtex(self):
        rows = [
            {
                "source": "crossref",
                "year": 2025,
                "title": "Graph RAG Survey",
                "authors": "Boci Peng, Yun Zhu",
                "journal": "ACM TOIS",
                "volume": "43",
                "issue": "2",
                "page": "1-30",
                "id": "10.1145/3777378",
                "url": "https://doi.org/10.1145/3777378",
                "type": "journal-article",
            }
        ]
        bib = lookup.format_bibtex(rows)
        self.assertIn("@article{peng2025_1,", bib)
        self.assertIn("author = {Boci Peng and Yun Zhu}", bib)
        self.assertIn("doi = {10.1145/3777378}", bib)


class TestLookupQueriesAndCLI(unittest.TestCase):
    """测试各源解析与 CLI 退出码契约。"""

    def test_query_arxiv_xml_parsing(self):
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
        with mock.patch.object(lookup, "fetch", return_value=sample_xml):
            rows = lookup.query_arxiv("Sample", 2, include_abstract=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "2401.12345v1")
        self.assertEqual(rows[0]["year"], "2024")
        self.assertEqual(rows[0]["abstract"], "Shows x < y > z in math.")

    def test_query_dblp_parsing(self):
        sample_json = json.dumps(
            {
                "result": {
                    "hits": {
                        "hit": {
                            "info": {
                                "title": "Attention Is All You Need.",
                                "authors": {"author": {"text": "Ashish Vaswani"}},
                                "venue": "NIPS",
                                "year": "2017",
                                "pages": "5998-6008",
                                "doi": "10.5555/3295222.3295349",
                                "type": "Conference and Workshop Papers",
                            }
                        }
                    }
                }
            }
        ).encode("utf-8")
        with mock.patch.object(lookup, "fetch", return_value=sample_json):
            rows = lookup.query_dblp("attention", 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Attention Is All You Need")
        self.assertEqual(rows[0]["authors"], "Ashish Vaswani")
        self.assertEqual(rows[0]["journal"], "NIPS")

    def test_query_doaj_encodes_slash(self):
        with mock.patch.object(lookup, "fetch", return_value=b'{"results": []}') as m:
            lookup.query_doaj("AI/ML", 2)
        called_url = m.call_args[0][0]
        self.assertIn("AI%2FML", called_url)

    def test_cli_exit_codes(self):
        # 退出码 2：空关键词
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["lookup.py", "   "]), redirect_stdout(buf):
            rc = lookup.main()
        self.assertEqual(rc, 2)

        # 退出码 3：所有源请求失败
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["lookup.py", "RAG", "--sources", "crossref"]),
            mock.patch.object(lookup, "fetch", side_effect=OSError("network down")),
            redirect_stdout(buf),
        ):
            rc = lookup.main()
        self.assertEqual(rc, 3)

        # 退出码 1：正常返回但 0 条结果
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["lookup.py", "nonexistent_xyz", "--sources", "crossref"]),
            mock.patch.object(lookup, "fetch", return_value=b'{"message": {"items": []}}'),
            redirect_stdout(buf),
        ):
            rc = lookup.main()
        self.assertEqual(rc, 1)


class TestGitHubLookup(unittest.TestCase):
    """测试 github_lookup.py 的校验、Trees API 探测与限流熔断。"""

    def test_empty_keywords_returns_2(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["github_lookup.py", ""]), redirect_stdout(buf):
            rc = github_lookup.main()
        self.assertEqual(rc, 2)

    def test_trees_api_finds_separated_backend_frontend(self):
        tree_payload = {
            "tree": [
                {"type": "blob", "path": "README.md"},
                {"type": "blob", "path": "backend/pom.xml"},
                {"type": "blob", "path": "frontend/package.json"},
                {"type": "blob", "path": "sql/init.sql"},
                {"type": "blob", "path": "a/b/c/d/deep.sql"},
            ]
        }
        with mock.patch.object(github_lookup, "fetch_json", return_value=tree_payload):
            files = github_lookup.inspect_repo_files("user/repo", "main")
        self.assertIn("README.md", files)
        self.assertIn("backend/pom.xml", files)
        self.assertIn("frontend/package.json", files)
        self.assertIn("sql/init.sql", files)
        self.assertNotIn("a/b/c/d/deep.sql", files)

    def test_search_repos_flags_too_small_and_oversize(self):
        search_payload = {
            "total_count": 2,
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
                    "full_name": "u/huge",
                    "default_branch": "main",
                    "html_url": "https://github.com/u/huge",
                    "description": "huge",
                    "language": "Java",
                    "stargazers_count": 10,
                    "pushed_at": "2026-11-01T00:00:00Z",
                    "size": 60000,
                    "license": {"spdx_id": "MIT"},
                },
            ],
        }
        with mock.patch.object(github_lookup, "fetch_json", return_value=search_payload):
            total, items = github_lookup.search_repos(
                "图书管理", "Java", "", 10, 0, "updated", 5, False, 2
            )
        self.assertEqual(total, 2)
        self.assertTrue(items[0]["too_small"])
        self.assertEqual(items[0]["license"], "无")
        self.assertTrue(items[1]["oversize"])
        self.assertEqual(items[1]["license"], "MIT")

    def test_rate_limit_returns_3(self):
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


if __name__ == "__main__":
    unittest.main()
