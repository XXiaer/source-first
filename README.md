# 先有出处 (`source-first`)

**动手之前先找依据。** 写内容前查真实文献，写代码前查现成实现；每条引用都可点击核验，每个推荐都贴合你的约束，拒绝凭记忆编造。

[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](https://github.com/XXiaer/source-first/releases/tag/v1.2.0)

---

## 核心理念

Agent 在学术综述与课设/毕设场景下最容易犯两个错误：**编造不存在的参考文献**，以及**只按 Star 数推荐跑不起来的工程项目**。

`source-first` 通过两条独立工作线约束输出质量：

| 工作线 | 适用场景 | 核心机制 |
|---|---|---|
| **文献线** | 文献综述、研究现状、开题报告、课程笔记整理、上传 PDF/课件提炼 | **引用闸门**：所有参考文献必须来自真实接口检索或上传材料；按 GB/T 7714-2015 / APA 第 7 版自动映射字段；如实披露中文数据库（知网/万方/维普）覆盖缺口 |
| **项目线** | 课设、毕设、竞赛选题查重、评估现成开源项目能否复用 | **可运行性优先**：按“能否跑通 → 代码体量（<50MB）→ 依赖复杂度 → 技术栈匹配 → Star 数”排序；明确区分“能直接用 / 要改的 / 仅参考思路”与许可证风险 |

> **不进入场景**：纯观点文/心得体会、已有完整出处的文字润色、直接代写论文正文或完整业务代码。

---

## 目录结构

```text
source-first/
├── SKILL.md                        # 主技能定义与路由规则
├── README.md                       # 项目说明文档
├── references/
│   ├── study.md                    # 文献线：引用闸门、三档流程与硬规则
│   ├── project.md                  # 项目线：适配排序、创意查重与硬规则
│   ├── sources.md                  # 文献接口地址、参数与不可用源说明
│   ├── repos.md                    # GitHub/Gitee/通用搜索仓库检索策略
│   ├── citation-formats.md         # GB/T 7714 与 APA 第 7 版字段映射
│   └── output-templates.md         # 两条线标准输出模板与实测示例
└── scripts/
    ├── lookup.py                   # 文献线只读并发检索脚本（纯 Python 标准库）
    └── github_lookup.py            # 项目线只读 GitHub 检索与结构检查脚本
```

---

## 内置检索脚本（零第三方依赖）

两个脚本均为**只读**，不写文件、不读本地凭据文件、不起子进程，仅使用 Python 标准库。

### 1. 文献并发检索：`scripts/lookup.py`

一次并发查询 **Crossref、OpenAlex、arXiv、DOAJ** 四个公开文献源，自动清理 HTML/XML 标签并提取期刊、卷期页码、DOI 与 OA 链接：

```bash
# 并发检索四个源（默认每个源返回 3 条）
python scripts/lookup.py "retrieval augmented generation"

# 输出完整书目 JSON 并附带摘要
python scripts/lookup.py "RAG" --limit 3 --abstract --json

# 指定部分源
python scripts/lookup.py "图书管理" --sources crossref,openalex
```

可选环境变量（用于提升请求额度或进入礼貌池）：
- `OPENALEX_API_KEY`：OpenAlex 免费 API Key
- `CROSSREF_MAILTO`：联系邮箱（附加到 `User-Agent`）

### 2. GitHub 仓库检索：`scripts/github_lookup.py`

按课设/毕设适配维度查询 GitHub 仓库，自动换算体积（MB）、标注 `>50MB` 与无许可证风险，并支持检查前 5 个候选的根目录关键文件（README / 依赖清单 / `.sql`）：

```bash
python scripts/github_lookup.py "图书管理 springboot" --language Java --check-files
python scripts/github_lookup.py "library management" --pushed-after 2025-01-01 --json
```

可选环境变量：
- `GITHUB_TOKEN`：提升 GitHub API 未认证限额（60 次/小时 → 5000 次/小时）

---

## 安装与使用

1. 从 [Releases](https://github.com/XXiaer/source-first/releases) 下载 `source-first-1.2.0.zip`（或直接克隆本仓库）。
2. 将 `source-first/` 文件夹放入你的 AI Agent 技能目录（如 `~/.gemini/antigravity/skills/` 或项目 `.agents/skills/`）。
3. 直接向 Agent 提出文献整理、综述框架或课设/毕设查重需求即可触发。

---

## v1.2.0 更新亮点

- **文献脚本升级**：`lookup.py` 支持 `ThreadPoolExecutor` 并发请求、补全 `journal`/`volume`/`issue`/`page`/`oa_url`/`cited_by_count` 字段、支持 `--abstract` 摘要提取、清洗 HTML/JATS 标签及兼容 Windows UTF-8 输出。
- **新增项目线脚本**：提供 `scripts/github_lookup.py`，支持仓库体积换算、许可证提示与根目录关键文件检测。
- **规则与文档完善**：`SKILL.md` 增加双语触发词与分阶段按需加载指引；`repos.md` 补充 `site:gitee.com` 备选检索与 API 限流保护策略；`study.md` 强化参考文献核验的三要素对齐要求。
