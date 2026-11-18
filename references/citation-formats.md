# 引用格式

两份格式，按用户要求选。没要求时，中文论文用 GB/T 7714，英文论文用 APA 第 7 版。

所有字段都从接口返回里取，不手打。下面是取哪个字段、怎么拼。

## 接口字段映射

### Crossref（`message.items[]` 里的每一项）

| 引用字段 | 取自 | 说明 |
|---|---|---|
| 作者 | `author[]` 的 `family` + `given` | 中文名 `family` 也可能是全名 |
| 标题 | `title[0]` | 可能带 HTML 标签，要清掉 |
| 期刊名 | `container-title[0]` | 没有这个字段说明是书或会议论文，看 `type` |
| 年份 | `issued.date-parts[0][0]` | 有时是字符串，转成数字 |
| 卷 | `volume` | 可能缺失 |
| 期 | `issue` | 可能缺失 |
| 页码 | `page` | 可能是 `123-145` 或 `e0123456` |
| DOI | `DOI` | 拼成 `https://doi.org/<DOI>` |
| 类型 | `type` | `journal-article` 期刊、`proceedings-article` 会议、`book-chapter` 章节 |

### OpenAlex

| 引用字段 | 取自 |
|---|---|
| 标题 | `title` |
| 年份 | `publication_year` |
| 作者 | `authorships[].author.display_name` |
| 期刊/来源 | `primary_location.source.display_name` |
| DOI | `doi`（已经是完整 URL） |
| 全文 | `open_access.oa_url` |

### arXiv（`<entry>` 里的 XML）

| 引用字段 | 取自 |
|---|---|
| 作者 | `<author><name>`，多个用逗号连 |
| 标题 | `<title>`，去掉换行和多余空格 |
| 年份 | `<published>` 的前四位 |
| 编号 | `<id>` 末尾那段，形如 `2401.12345` |
| 链接 | `https://arxiv.org/abs/<编号>` |
| 类型 | 一律标"预印本" |

### DOAJ

| 引用字段 | 取自 |
|---|---|
| 标题 | `bibjson.title` |
| 作者 | `bibjson.author[].name` |
| 期刊 | `bibjson.journal.title` |
| 年 | `bibjson.year` |
| 卷期页 | `bibjson.journal.volume` / `number` / `start_page`-`end_page` |
| DOI | `bibjson.identifier[]` 里 `type` 为 `doi` 的那条 |

## GB/T 7714-2015

期刊论文：

```
[1] 作者1, 作者2. 题名[J]. 刊名, 年, 卷(期): 起止页码. DOI
```

专著：

```
[2] 作者. 题名[M]. 出版地: 出版者, 出版年: 起止页码.
```

学位论文：

```
[3] 作者. 题名[D]. 保存地: 保存者, 年份.
```

会议论文：

```
[4] 作者. 题名[C]//论文集名. 出版地: 出版者, 年份: 页码.
```

电子资源、预印本：

```
[5] 作者. 题名[EB/OL]. (发布日期)[引用日期]. 网址.
```

规则：

- 作者三人以内全列，超过三人列前三个加"等"（英文加 `et al.`）。
- 西文作者姓在前名缩写在后，不加缩写点：`Smith J, Brown A`.
- 文献类型标识必须带：`[J]` 期刊、`[M]` 专著、`[D]` 学位论文、`[C]` 会议、`[EB/OL]` 电子资源。
- 缺卷或缺期就跳过那一项，不要写"无"或留空括号。
- 中文文献末尾不加句点以外的标点。

## APA 第 7 版

期刊论文：

```
Author, A. A., & Author, B. B. (Year). Title of article. Title of Journal, Volume(Issue), pages. https://doi.org/xxxx
```

两位作者用 `&`，三位以上在正文里用 `et al.`，参考文献表里最多列 20 位。

网页或预印本：

```
Author, A. A. (Year, Month Day). Title. Site Name. https://arxiv.org/abs/2401.12345
```

规则：

- 年份放括号里，后面跟句点。
- 期刊名和卷号斜体（Markdown 里用 `*斜体*`）。
- 标题只有首词和专有名词大写。
- DOI 写成完整链接，结尾不加句点。

## 预印本和会议论文的标注

论文类型影响证据强度，必须在引用里体现：

- arXiv 预印本：GB/T 7714 用 `[EB/OL]`，并在后面注明"预印本，未经同行评审"。APA 在标题后加 `[Preprint]`。
- 会议论文：`[C]`，注明会议名和年份。
- 期刊论文：`[J]`，正常格式。

不要为了好看把预印本写成期刊论文。这是造假。

## 自检

生成参考文献表之后，逐条核对：

- [ ] 每条都能在接口返回里找到原始记录
- [ ] DOI 或 arXiv 编号和返回的一致，一位不差
- [ ] 年份和返回的一致
- [ ] 作者顺序和返回的一致
- [ ] 没有把预印本标成期刊论文
- [ ] 中文和英文的标点没混用
