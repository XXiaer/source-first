# 先有出处 (`source-first`)

**动手之前先找依据。** 写内容前查真实文献，写代码前查现成实现；每条引用都可点击核验，每个推荐都贴合你的约束，拒绝凭记忆编造。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.3.0-blue.svg)](https://github.com/XXiaer/source-first/releases)

---

## 核心理念

Agent 在学术综述与课设/毕设场景下最容易犯两个错误：**编造不存在的参考文献**，以及**只按 Star 数推荐跑不起来的工程项目**。

`source-first` 通过两条独立工作线约束输出质量：

| 工作线 | 适用场景 | 核心机制 |
|---|---|---|
| **文献线** | 文献综述、研究现状、开题报告、课程笔记整理、上传 PDF/课件提炼 | **引用闸门**：所有参考文献必须来自真实接口检索或上传材料；按 GB/T 7714-2015 / APA 第 7 版或 BibTeX 自动映射字段；如实披露中文数据库（知网/万方/维普）覆盖缺口 |
| **项目线** | 课设、毕设、竞赛选题查重、评估现成开源项目能否复用 | **可运行性优先**：按“能否跑通 → 代码体量（100KB~50MB 黄金区间）→ 依赖复杂度 → 技术栈匹配 → Star 数”排序；明确区分“能直接用 / 要改的 / 仅参考思路”与许可证风险 |

> **不进入场景**：纯观点文/心得体会、已有完整出处的文字润色、直接代写论文正文或整套业务代码。

---

## 目录结构

```text
source-first/
├── SKILL.md                        # 主技能定义与路由规则
├── README.md                       # 项目说明文档
├── LICENSE                         # MIT 开源许可证
├── skill-dependencies.json         # 网络端点与可选环境依赖声明
├── references/
│   ├── study.md                    # 文献线：引用闸门、三档流程与硬规则
│   ├── project.md                  # 项目线：适配排序、创意查重与硬规则
│   ├── sources.md                  # 文献接口地址、参数与不可用源说明
│   ├── repos.md                    # GitHub/Gitee/通用搜索仓库检索策略
│   ├── citation-formats.md         # GB/T 7714 与 APA 第 7 版字段映射
│   ├── output-templates.md         # 两条线标准输出模板与实测示例
│   └── setup-guide.md              # 可选环境变量与 Token 配置指南
├── scripts/
│   ├── lookup.py                   # 文献线只读并发检索脚本（支持 DOI 反查、去重、BibTeX）
│   ├── github_lookup.py            # 项目线只读 GitHub 检索与项目树检查脚本
│   └── check_environment.py        # 只读环境与接口连通性自检脚本
└── tests/
    └── test_scripts.py             # 零网络依赖的单元测试（Mock）
```

---

## 内置检索脚本（零第三方依赖）

所有脚本均为**只读**，不写文件、不读本地凭据文件、不起子进程，仅使用 Python 标准库。

### 1. 文献并发检索：`scripts/lookup.py`

一次并发查询 **Crossref、OpenAlex、arXiv、DBLP、DOAJ**（及可选的 **Semantic Scholar**），自动去重并互补缺失字段，支持按 DOI 精准反查与 BibTeX 导出：

```bash
# 并发检索（默认 crossref,openalex,arxiv,dblp,doaj）
python scripts/lookup.py "retrieval augmented generation"

# 按 DOI 精准反查（用于核验已有参考文献）
python scripts/lookup.py --doi 10.1145/3777378 --json

# 导出 BibTeX 或附带摘要的 JSON
python scripts/lookup.py "RAG" --limit 3 --bibtex
python scripts/lookup.py "RAG" --limit 3 --abstract --json
```

### 2. GitHub 仓库检索：`scripts/github_lookup.py`

按课设/毕设适配维度查询 GitHub 仓库，默认过滤 10KB 以下空壳仓，支持通过 Trees API 识别前后端分离项目（`backend/`、`frontend/` 等）的关键文件：

```bash
python scripts/github_lookup.py "图书管理 springboot" --language Java --check-files
python scripts/github_lookup.py "library management" --pushed-after 2025-01-01 --min-stars 5 --json
```

### 3. 环境自检：`scripts/check_environment.py`

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
3. 直接向 Agent 提出文献整理、综述框架或课设/毕设查重需求即可触发。

---

## 开源许可与更新记录

- 本项目基于 [MIT License](LICENSE) 开源。
- 版本更新记录请查看 [GitHub Releases](https://github.com/XXiaer/source-first/releases)。
