**项目简介**
- 从 `http://www.etmoc.com/` 抓取烟草产品详情与目录链接。
- 使用 Playwright 进行页面访问与绕过校验，BeautifulSoup 解析详情，Requests 下载图片。
- 已优化：目录页产品链接不显示进度条；图片下载统一在解析结束后批量进行以提升整体速度。

**环境要求**
- Python `>=3.12`
- 建议使用 `uv` 管理依赖与运行。

**安装依赖**
- 使用 `uv`：
  - `uv run playwright install`
  - `uv run python playwright_scrape_etmoc.py --help`
- 使用 `pip`：
  - `pip install -r requirements.txt`
  - `playwright install`

**运行方式**
- 解析目录来源（推荐）：
  - 收集链接：
    - `uv run python playwright_scrape_etmoc.py --source catalog --action list --pages 1 --out etmoc_output`
  - 解析详情：
    - `uv run python playwright_scrape_etmoc.py --source catalog --action detail --limit 50 --pages all --out etmoc_output`
- 解析品牌来源（BrandAll + 品牌页）：
  - `uv run python playwright_scrape_etmoc.py --source brands --limit 20 --out etmoc_output`

**命令行参数**
- `--limit`：最多抓取的产品条数；`0` 或不设表示不限。
- `--delay`：请求间隔秒数，默认 `0.5`（适当增大可更稳）。
- `--out`：输出目录，默认 `etmoc_output`。
- `--pages`：目录分页上限；
  - 传 `all` 表示不限；
  - 未提供时默认 `1` 页。
- `--source`：`catalog`（目录页）或 `brands`（品牌页）。
- `--action`：`list`（仅收集链接）或 `detail`（解析详情，仅 catalog 有效）。

**输出结果**
- 目录抓取：
  - `products_catalog.json`、`products_catalog.csv`
- 品牌抓取：
  - `products_playwright.json`、`products_playwright.csv`
- 图片：
  - 统一保存至 `etmoc_output/images/`；解析结束后批量下载首图，并写入 `image_local` 字段。

**GitHub Actions**
- 工作流：`.github/workflows/scrape.yml`
- 触发：`push` 到 `main` 或手动 `workflow_dispatch`。
- 步骤：安装依赖与浏览器 → 运行少量解析 → 上传 `etmoc_output` 作为 Artifact。
- 可根据需要添加 `schedule`（定时）或扩大运行规模（谨慎设置 `--limit` 与 `--pages`）。

**注意事项**
- 首次运行请务必安装 Playwright 浏览器：`uv run playwright install`。
- 长时间运行建议设置 `--limit` 与 `--pages all`，并适当增大 `--delay`。
- 若站点防护导致解析失败，可重试并增大等待时间（已内置容错等待）。

**故障排查**
- `ModuleNotFoundError: No module named 'tqdm'`：确保已安装依赖（`uv run python ...` 或 `pip install -r requirements.txt`）。
- `PlaywrightTimeoutError`：增大超时与 `--delay`，或重试。
- 浏览器意外关闭：确认本机 Playwright 安装完整并无权限问题。