# 先有出处 (`source-first`)

**动手之前先找依据。** 写内容前查真实文献，写代码前查现成实现；每条引用都可点击核验，每个推荐都贴合你的约束，拒绝凭记忆编造。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.6.0-blue.svg)](https://github.com/XXiaer/source-first/releases)

---

## 核心理念

Agent 在学术综述与课设/毕设场景下最容易犯两个错误：**编造不存在的参考文献**，以及**只按 Star 数推荐跑不起来的工程项目**。

`source-first` 通过两条独立工作线约束输出质量（若同一提问同时涉及综述与选题查重，则分阶段串行执行）：

| 工作线 | 适用场景 | 核心机制 |
|---|---|---|
| **文献线** | 文献综述、研究现状、开题报告、课程笔记整理、上传 PDF/课件提炼、参考文献核伪 | **引用闸门**：所有参考文献必须来自真实接口检索、期刊官网核实或上传材料；按 GB/T 7714-2015 / APA 第 7 版或 BibTeX 确定性生成引文；中文库不可达时给出定制知网检索式 |
| **项目线** | 课设、毕设、竞赛选题查重、评估现成开源项目能否复用 | **可运行性优先**：默认按 `capstone` 适配评分（体量黄金区间 + 宽松协议 + 活跃度）综合排序；支持 `--repo` 单仓体检与限流自动降级；明确区分“能直接用 / 要改的 / 仅参考思路” |

> **不进入场景**：纯观点文/心得体会、已有完整出处的文字润色、直接代写论文正文或整套业务代码。

---

## 目录结构

```text
source-first/
├── SKILL.md                        # 主技能定义与路由规则
├── README.md                       # 项目说明文档
├── LICENSE                         # MIT 开源许可证
├── skill-dependencies.json         # 网络端点与可选环境依赖声明
├── evals/
│   └── evals.json                  # 触发正反例与纪律压力评测集
├── references/
│   ├── study.md                    # 文献线：引用闸门、三档流程与硬规则
│   ├── project.md                  # 项目线：适配排序、创意查重与硬规则
│   ├── sources.md                  # 文献接口地址、参数与不可用源说明
│   ├── repos.md                    # GitHub/Gitee/通用搜索仓库检索策略
│   ├── citation-formats.md         # GB/T 7714 与 APA 第 7 版字段映射
│   ├── output-templates.md         # 两条线标准输出模板与实测示例
│   └── setup-guide.md              # 可选环境变量与 Token 配置指南
├── scripts/
│   ├── lookup.py                   # 文献线只读并发检索脚本（DOI 反查、双索引去重、BibTeX）
│   ├── github_lookup.py            # 项目线只读 GitHub 检索、目录与 README 摘要脚本
│   └── check_environment.py        # 只读环境与接口连通性自检脚本
└── tests/
    └── test_scripts.py             # 零网络依赖的单元测试（Mock）
```

---

## 内置检索脚本（零第三方依赖）

所有脚本均为**只读**，不写文件、不读本地凭据文件、不起子进程，仅使用 Python 标准库。

### 1. 文献并发检索：`scripts/lookup.py`

一次并发查询 **Crossref、OpenAlex、arXiv、DBLP**（有 `S2_API_KEY` 时自动包含 **Semantic Scholar**；低相关性的 **DOAJ** 按需通过 `--sources ...,doaj` 启用），支持 DOI + `(标题, 年份)` 双索引去重互补、`--year-from` 年份过滤、DOI 精准反查（HTTP 404 返回退出码 1）与 BibTeX 导出：

```bash
# 并发检索（默认 crossref,openalex,arxiv,dblp）
python scripts/lookup.py "retrieval augmented generation" --abstract --json

# 按年份筛选近几年的文献并导出 BibTeX / GB/T 7714
python scripts/lookup.py "RAG" --year-from 2023 --limit 3 --bibtex
python scripts/lookup.py "RAG" --year-from 2023 --limit 3 --format gbt7714

# 按单条或多条 DOI 批量精准反查（用于核验已有参考文献）
python scripts/lookup.py --doi "10.1145/3777378,10.9999/fake" --json
```

### 2. GitHub 仓库检索：`scripts/github_lookup.py`

按课设/毕设适配维度查询 GitHub 仓库（默认过滤 10KB 以下空壳仓与 Fork 仓），支持 Trees API 探测 `src/` 与前后端子目录关键文件、`--readme` 提取运行指南与中间件依赖线索、`--repo` 单仓直查体检，并标记 `NOASSERTION`、`archived` 与 Copyleft（GPL/AGPL）许可证风险：

```bash
python scripts/github_lookup.py "图书管理 springboot" --language Java --check-files --readme
python scripts/github_lookup.py --repo https://github.com/owner/repo --json
python scripts/github_lookup.py "library management" --pushed-after 2025-01-01 --min-stars 5 --json
python scripts/github_lookup.py "图书管理" --include-forks --json
```

### 3. 环境自检：`scripts/check_environment.py`

自动使用已配置的环境变量探活公开端点并输出 `ready` / `degraded` / `needs_setup` / `unavailable` 状态（绝不回显密钥）：

```bash
python scripts/check_environment.py
```

可选环境变量配置细节（`GITHUB_TOKEN`、`OPENALEX_API_KEY`、`CROSSREF_MAILTO`、`S2_API_KEY`）见 [references/setup-guide.md](references/setup-guide.md)。

---

## 安装与使用

1. 从 [Releases](https://github.com/XXiaer/source-first/releases) 下载最新发行包（或直接克隆本仓库）。
2. 将 `source-first/` 文件夹放入你的 AI Agent 技能目录：
   - `~/.gemini/antigravity/skills/`
   - `~/.workbuddy/skills/`
   - 或项目级 `.agents/skills/`
3. 直接向 Agent 提出文献整理、参考文献核验、综述框架或课设/毕设查重需求即可触发。

---

## 开源许可与更新记录

- 本项目基于 [MIT License](LICENSE) 开源。
- 版本更新记录请查看 [GitHub Releases](https://github.com/XXiaer/source-first/releases)。
