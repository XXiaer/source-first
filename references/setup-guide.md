# 环境与凭据配置指南

`source-first` 的所有检索脚本均仅使用 Python 标准库、只读运行，**不写入本地配置文件，也不读取任何凭据文件**。以下环境变量均为可选配置，但配置后可显著减少限流（特别是 `GITHUB_TOKEN` 和 `OPENALEX_API_KEY`）。

## 1. GitHub Token（强烈建议配置，用于项目线）

未认证时 GitHub 搜索接口限流 10 次/分钟、仓库目录接口仅 60 次/小时，共享出口 IP 下极易触发 403/429。

- **申请入口**：GitHub → `Settings` → `Developer settings` → `Personal access tokens` → `Tokens (classic)` 或 `Fine-grained tokens`
- **最小权限**：仅勾选公开仓库只读权限（`public_repo` 只读，无需写权限或管理员权限）
- **配置方式**（仅设为环境变量，切勿写入代码或仓库）：
  - PowerShell：`$env:GITHUB_TOKEN = "ghp_你的Token"`
  - Bash / Zsh：`export GITHUB_TOKEN="ghp_你的Token"`
- **轮换与撤销**：在 GitHub Token 设置页随时可点击 `Regenerate` 或 `Revoke`。

## 2. OpenAlex API Key（可选，用于文献线）

OpenAlex 无 Key 时按 IP 共享每日 0.10 美元额度，云端或密集检索易返回 429；申请免费 Key 后每日额度提升至 1 美元。

- **申请入口**：`https://openalex.org/upgrade`
- **配置方式**：设置环境变量 `OPENALEX_API_KEY`。

## 3. Crossref 礼貌池邮箱（可选，用于文献线）

Crossref 不需要 Key，但在 `User-Agent` 中附带联系邮箱可进入礼貌池（Polite Pool），连接更稳定。

- **配置方式**：设置环境变量 `CROSSREF_MAILTO="your_email@example.com"`（仅填邮箱即可）。

## 4. Semantic Scholar API Key（可选，用于文献线）

Semantic Scholar 无 Key 时经常返回 429，因此脚本默认不主动查询它。

- **申请入口**：`https://www.semanticscholar.org/product/api`
- **配置方式**：设置环境变量 `S2_API_KEY`（配置后自动激活，或通过 `--sources semanticscholar` 显式调用）。

## 5. 环境验证

运行只读自检脚本验证 Python 版本、环境变量配置状态（仅检测是否存在，**不会回显任何密钥值**）及各公开接口连通性；自检脚本会自动使用已设置的 `GITHUB_TOKEN`、`OPENALEX_API_KEY`、`CROSSREF_MAILTO` 与 `S2_API_KEY` 进行探测：

```bash
python <skill_dir>/scripts/check_environment.py
python <skill_dir>/scripts/check_environment.py --offline   # 仅检查本地环境，不发网络请求
```
