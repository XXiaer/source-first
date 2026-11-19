# 仓库检索

项目线专用的检索方法。下面每个地址都实测过，状态标注在 2026-11-19。

如果环境支持 Python，优先运行：

```bash
python <skill_dir>/scripts/github_lookup.py "<关键词>" --language <语言> --check-files --readme
```

可一次拿到已过滤 Fork 仓的 GitHub 仓库列表、体积（MB）、许可证及风险分类、`src/` 与关键配置文件探测，以及 README 运行指南和中间件依赖线索（如需包含 Fork 可加 `--include-forks`）。

## GitHub：有接口

```
https://api.github.com/search/repositories?q=<关键词>+archived:false+fork:false&sort=updated&per_page=5
```

- 免费，不需要 token。实测 200，中文关键词「图书管理系统」返回 6405 条。
- `q` 支持限定符，组合起来很管用：
  - `图书管理 language:Java` — 限定语言（含空格语言名加引号，如 `language:"Jupyter Notebook"`）
  - `图书管理 stars:>50` — 限定星数
  - `图书管理 pushed:>2025-01-01` — 只要近一年有提交的
  - `图书管理 archived:false fork:false` — 排除已归档与直接 Fork 的重复仓
- `sort` 可选 `stars`、`updated`、`forks`。给学生查时用 `updated`，不要用 `stars`。

### 返回字段怎么用

| 字段 | 含义 | 怎么用 |
|---|---|---|
| `full_name` | `作者/仓库名` | 拼成 `https://github.com/<full_name>` |
| `html_url` | 完整地址 | 直接贴给用户 |
| `description` | 一句话简介 | 判断功能对不对得上 |
| `language` | 主语言 | 技术栈匹配 |
| `stargazers_count` | 星数 | 只作参考，排序时排最后 |
| `pushed_at` | 最后一次代码提交 | 判断维护状态，比 `updated_at` 准 |
| `size` | 仓库体积，**单位是 KB** | 51200 就是 50MB。超过这个数基本是工程项目 |
| `archived` | 是否已归档 | `true` 的不能推，作者已经不维护了 |
| `license` | 许可证 | `null` 表示无许可证（仅参考思路）；`NOASSERTION` 为自定义协议需点开核对；`GPL/AGPL` 须提示强传染风险 |

`updated_at` 和 `pushed_at` 的区别：前者被 issue 评论、点星也会刷新，后者才是真的推了代码。判断“还在维护”要看 `pushed_at`。

### 限流

未认证时：

- 搜索接口 10 次/分钟
- 其他接口 60 次/小时

超了返回 403 或 429，`message` 里写 `API rate limit exceeded`（`github_lookup.py` 会在串行探测时自动熔断并停止后续请求）。看到这个就**停下**，不要再试。改用以下替代路径：
- 网页搜索 `site:github.com <关键词>`
- 打开 Gitee 网页搜索或用 `site:gitee.com <关键词> 毕业设计`
- 配置只读 `GITHUB_TOKEN`（见 `references/setup-guide.md`）可将目录检查限额提升至 5000 次/小时。

## Gitee：只能开浏览器

接口试过三次，全部返回空数组：

```
https://gitee.com/api/v5/search/repositories?q=图书管理    → []
https://gitee.com/api/v5/search/repositories?q=springboot  → []
```

需要 access_token 才能用，公开调用拿不到数据。所以：

- 有浏览器能力就用网页搜索页
- 没有浏览器但有网页搜索（Web Search）工具时，用 `site:gitee.com <关键词> 毕业设计` 间接检索公开仓库页
- 两者都没有就跳过 Gitee，用 GitHub 加通用搜索顶上
- **不要在输出里写“已检索 Gitee”**，没查就是没查

国内课设项目在 Gitee 上比 GitHub 多，所以能开浏览器的话值得花这一步。

## 通用搜索

浏览器搜这几个组合，命中率不低：

- `<题目> 毕业设计 源码`
- `<题目> 课程设计 github`
- `<题目> 系统 开源 java`
- `<题目> 大作业 完整`

看搜索结果时只认落到仓库页的链接。论坛帖、资源站、需要积分的下载页都不要推荐，学生下不到。

## 关键词怎么拆

一个题目拆成三层，每层搜一次：

| 层 | 例子（图书管理系统） | 作用 |
|---|---|---|
| 核心对象 | 图书、book、library | 保证方向对 |
| 业务动作 | 借阅、归还、borrow | 保证功能对得上 |
| 技术栈 | springboot、vue、mysql | 保证能跑在你的环境里 |

三层都用上，一次能筛掉大半不相关的结果。

## 判断一个仓库值不值得推

**先用搜索接口筛出前 3-5 个候选，再只对前 2-5 个候选开启 `--check-files --readme`**，避免无谓消耗 60 次/小时的未认证限额。

按这个顺序看，任一条不过就降级或排除：

1. 看 README，有没有“如何运行/安装/启动”这一节。没有就标“仅参考思路”。
2. 看有没有依赖清单文件（`pom.xml`、`package.json`、`requirements.txt` 等）和 `src/` 目录。没有说明作者没打算让别人跑或还未推全代码。
3. 看 `size`。超过 51200（50MB）的，说明带了大量资源或依赖包，通常不适合当课设骨架；若技术栈和标题高度匹配但体积超标，先看一眼根目录是不是误提交了 `node_modules`、`target/` 或演示视频，再决定是否排除。
4. 看中间件依赖（`docker-compose.yml`、README 中的 Redis/MQ/ES 提示）。对课设而言中间件越多越容易卡在环境配置。
5. 看 `pushed_at`。超过一年没动，功能可能依赖旧版本环境，要提前说明。
6. 看 `license`。`null` 只能看思路；`GPL/AGPL` 提醒传染风险；`NOASSERTION` 提醒点开 `LICENSE` 核对。
7. 最后才看星数。星数高说明有人知道它，不说明你能改得动它。

## 报告检索过程

输出里要写清楚：查了哪些源、每个源命中多少条、筛完剩几个。

```
检索情况：GitHub（接口，关键词「图书管理 springboot」，命中 128 条，筛出 4 个）；
通用搜索（3 组关键词，筛出 2 个）；Gitee 本轮未查（接口不可用）。
```

这段话学生看不懂没关系，评委看得懂。它证明你真的查了。
