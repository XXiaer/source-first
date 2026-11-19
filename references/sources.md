# 检索源

公开文献检索接口清单，它们是所有在线引用的合法来源。下面每个地址都实测过，状态标注在 2026-11-19。

**优先使用内置脚本**：如果环境支持 Python，直接运行 `python <skill_dir>/scripts/lookup.py "<关键词>" --abstract --json`（支持 `--doi` 反查、`--year-from`、`--sources`、`--limit`、`--bibtex`）。脚本默认并发查询 Crossref、OpenAlex、arXiv、DBLP（有 `S2_API_KEY` 时自动包含 Semantic Scholar；相关性较低的 DOAJ 默认关闭，按需通过 `--sources ...,doaj` 显式启用），并自动跨源去重互补字段。环境变量配置见 `references/setup-guide.md`。跑不了 Python 时，再按下面地址直接发请求。

## Crossref：书目信息最准

```
https://api.crossref.org/works?query.bibliographic=<关键词>&rows=5&select=title,author,issued,DOI,container-title,volume,issue,page,type
```

- 免费，不需要 key，不限流（建议设置 `CROSSREF_MAILTO` 进入礼貌池）。实测 200。
- 收录两亿多条 DOI，是绝大多数期刊论文的权威登记处；支持 `filter=from-pub-date:<YYYY>`（对应 `--year-from`）。
- 中文期刊收录有限，但正规的中文核心期刊多数有 DOI。
- 字段在 `message.items[]`，映射方法见 `references/citation-formats.md`。
- 关键词用 `+` 连接，中文要百分号编码。

## OpenAlex：覆盖广，带开放获取链接

```
https://api.openalex.org/works?search=<关键词>&per-page=5&select=title,publication_year,cited_by_count,doi,open_access,authorships,primary_location,type
```

- 免费，无 key 时按 IP 共享每日 0.10 美元额度，云端/共享出口易返回 429；因此标记为可选补充源，降级走 Crossref / DBLP / arXiv。
- 要长期用建议申请免费 key，每天 1 美元额度：`https://openalex.org/upgrade`
- `open_access.oa_url` 有值，说明有合法免费的全文，可以直接读。
- `cited_by_count` 是真实的被引数。可以拿来判断一篇文献的地位，但不要拿它当质量唯一标准。
- 支持 `filter=publication_year:>=<YYYY>`（对应 `--year-from`）。

## arXiv：理工科预印本，能拿全文

```
https://export.arxiv.org/api/query?search_query=<百分号编码的查询>&start=0&max_results=5&sortBy=relevance&sortOrder=descending
```

查询写法：`cat:cs.DC AND (all:"cache invalidation" OR all:"consistency")`

- 返回 Atom XML，不是 JSON。每条在 `<entry>` 里：`<id>`、`<title>`、`<summary>`、`<published>`、`<author><name>`。
- 论文编号形如 `2401.12345`，全文地址是 `https://arxiv.org/abs/<编号>`。
- **坑：空格、引号、括号必须百分号编码。** 直接拼 `+AND+` 会返回 400（`lookup.py` 已自动保留引号短语与显式 `cat:`/`AND` 语法）。
- 一次查一个分类，请求之间隔几秒。并发会被限。
- 只覆盖预印本，未经同行评审的居多。引用时要注明这是预印本。

## DBLP：计算机/AI 顶会与期刊，元数据极干净

```
https://dblp.org/search/publ/api?q=<关键词>&h=5&format=json
```

- 完全免费，不需要 key；但偶发 HTTP 202 排队或特定环境 SSL 证书验证失败，属于可选源——失败时直接降级依赖 Crossref / arXiv / OpenAlex。
- 计算机科学与人工智能领域的会议（`[C]`）和期刊（`[J]`）覆盖极好；注意 `venue` 为 `CoRR` 的记录实质上是 arXiv 预印本，不可当作期刊论文。

## DOAJ：开放获取期刊，默认关闭按需启用

```
https://doaj.org/api/search/articles/<百分号编码的关键词>?pageSize=5
```

- 免费，不需要 key。实测 200，返回 JSON。
- **相关性普遍偏低，默认已移出 `lookup.py` 默认源。** 仅当需要补充人文社科或冷门开放获取期刊时用 `--sources crossref,doaj` 显式调用，使用前必须人工核对标题与摘要。

## Semantic Scholar：备用，经常限流

```
https://api.semanticscholar.org/graph/v1/paper/search?query=<关键词>&limit=5&fields=title,year,authors,externalIds,abstract,publicationTypes
```

- 无 key 时经常返回 429，因此未配置 `S2_API_KEY` 时默认跳过。
- 手动调用时先试一次，通了就用；不通立刻跳过，不要重试超过两次。
- 有 key 会稳定很多，申请页：`https://www.semanticscholar.org/product/api`

## 不可用的源

这些别浪费时间，也不要在输出里承诺能查。

| 源 | 状态 | 原因 |
|---|---|---|
| 知网 | 进不去 | 机构账号 + IP 白名单 |
| 万方、维普 | 进不去 | 订阅制 |
| 国家哲学社会科学文献中心 | 403 | 直接拒绝脚本访问 |
| 超星、尔雅 | 需登录 | 课程内容在登录态后面 |
| 国家高等教育智慧教育平台 | 有反爬 | 返回 JS 挑战页，只有真浏览器能过 |
| 各校图书馆、教务系统 | 进不去 | 校内网 |

## 中文文献怎么办

这是已知短板，要主动讲，不要遮。

按可靠性排，四条路：

1. **让用户自己下载 PDF。** 从学校图书馆把 PDF 下下来上传，技能负责整理、核验、生成引用。这条路命中率接近百分之百，优先用它。**注意：知网默认下载的 `.caj` 专有格式无法直接读取，需提前提醒用户在下载页选择“PDF 下载”或用 CAJViewer 转为 PDF 再上传。**
2. **Crossref 查中文期刊的 DOI。** 正规中文期刊多数有 DOI，能核到就是真的，而且元数据最规范。
3. **中国科技论文在线** `https://www.paper.edu.cn/` 可访问，收录部分中文论文，能直接检索。
4. **ChinaXiv** `https://chinaxiv.org/`，中科院科技论文预发布平台，可访问。站点是前端渲染，静态抓取进不去，必须用浏览器打开页面检索。

另外两个可访问，但覆盖有限：

- **NSTL 国家科技图书文献中心** `https://www.nstl.gov.cn/`：文摘库，能查到题录，看不到全文。适合确认“这篇中文文献确实存在”。
- **国家科技报告服务系统** `https://www.nstrs.cn/`：科技报告，工科方向有些用。

做不到的必须明说：**查不了知网、万方、维普，覆盖不了大部分中文核心期刊。** 写进输出的覆盖说明里，不要拿英文文献假装覆盖了国内研究。

## 检索纪律

- 一条论断配 2-3 个检索词，中英文各试一次。
- 每个接口最多查两次。第一次用关键词，第二次放宽（去掉限定词）。
- 连续两次空手就停，写“这个方向没有找到公开文献”，不要换着法儿硬凑。
- 拿到记录先看年份。五年前的文献可以用，但要标出年份，让用户自己判断时效（需要近几年文献时优先带 `--year-from`）。
- 预印本、会议论文、期刊论文要分开标。三者的证据强度不一样。

## 关键词检索的一个坑

Crossref 和 OpenAlex 是关键词匹配，不是语义匹配。搜 "attention is all you need"，头两条命中的是**引用它的其他文章**，不是原论文。它们的标题很像，年份、作者、期刊全不同。

所以拿到结果必须核对三件事再引用：

1. 标题是不是你要的那篇（不是“包含这些词”的某篇）
2. 作者对不对
3. 年份对不对

三条里有一条对不上，就不是目标文献。这一步不做，引用表里会混进一堆“看着像”的文献，比空着更糟。

arXiv 的噪音比 Crossref 更大。它做的是全文检索，按 relevance 排序，头一条通常对得上，往下几条就开始飘。查「retrieval augmented generation」会搜出讲文献综述自动化的论文。所以 arXiv 回来的结果，第一条也要核对标题再引用。
