# ETMOC Scraper（Playwright）

**项目简介**
- 从 `http://www.etmoc.com/` 抓取烟草产品目录与详情。
- 页面访问使用 Playwright；详情解析用 BeautifulSoup；图片下载用 Requests。
- 已优化：统一的解析与图片下载工具函数、目录/品牌两条抓取流程去重、CLI 支持增量更新与数值分页。

**重要策略：增量关注前几页**
- 产品列表按“新 → 旧”排序；增量更新建议反复抓取前几页以捕获新增。
- 在增量模式下默认从第 1 页开始（聚焦新内容）；如需继续历史深页，使用 `--start-page latest` 从检查点继续。

---

## 环境要求
- Python `>= 3.12`
- 建议使用 `uv` 管理依赖与运行。
- 安装 Playwright 浏览器：`playwright install`（用 `uv` 或 `pip` 方式均可）。

## 安装与检查
- 使用 `uv`：
  - `uv run playwright install`
  - `uv run python playwright_scrape_etmoc.py --help`
- 使用 `pip`（可选）：
  - 安装依赖：`pip install playwright beautifulsoup4 requests tqdm`
  - 安装浏览器：`playwright install`

## 运行方式

### 目录来源（推荐）
- 仅收集链接（关注前 3 页，增量模式）：
  - `uv run python playwright_scrape_etmoc.py --source catalog --action list --incremental --pages 3 --out etmoc_output`
- 解析详情（关注前 3 页，增量模式）：
  - `uv run python playwright_scrape_etmoc.py --source catalog --action detail --incremental --pages 3 --out etmoc_output`
- 一次性补扫历史深页（从检查点继续，向后抓）：
  - `uv run python playwright_scrape_etmoc.py --source catalog --action list --start-page latest --pages 5 --incremental --out etmoc_output`

### 品牌来源（BrandAll + 品牌页）
- `uv run python playwright_scrape_etmoc.py --source brands --limit 20 --out etmoc_output`

## 命令行参数
- `--source`：`catalog`（目录页）或 `brands`（品牌页）。
- `--action`：`list`（仅收集链接）或 `detail`（解析详情；仅 catalog 源使用）。
- `--pages`：分页上限；
  - 传 `all` 表示不限；
  - 未提供时默认 `1` 页。
- `--start-page`：分页起始页；传整数或 `latest`（从检查点继续）。
- `--incremental`：启用增量模式；默认关注前几页（不清理输出目录）。如需从检查点继续请配合 `--start-page latest`。
- `--limit`：最多解析的产品条数；`0` 或不设表示不限（对 `detail` 生效，对 `list` 表示链接收集上限）。
- `--delay`：请求间隔秒数，默认 `0.5`（适当增大可更稳）。
- `--out`：输出目录，默认 `etmoc_output`。

## 输出结构
- 目录链接（`catalog` 源，`action=list`）：
  - `out/product_links.json`：`{"count": <数量>, "links": [<链接>...]}`。
- 目录详情（`catalog` 源，`action=detail`）：
  - `out/products_catalog.json`：详情 JSON。
  - `out/products_catalog.csv`：详情 CSV。
- 品牌详情（`brands` 源）：
  - `out/products_playwright.json`、`out/products_playwright.csv`。
- 图片：
  - `out/images/`：下载的图片文件；条目会写入 `image_local` 指向本地路径（如有）。
- 检查点：
  - `out/catalog_checkpoint.json`：`{"last_page": <最后完成页号>}`；在 `--start-page latest` 时用于继续深页抓取。

## 推荐使用模式
- 增量更新（日常抓取）：`--incremental --pages 3`，定期运行即可覆盖新增内容。
- 历史补扫（一次性）：先增量抓若干，再 `--start-page latest` 向后持续抓取。

## 其他说明
- 解析阶段不下载图片，统一在任务末尾批量下载，避免阻塞页面解析。
- 若网站存在访问校验，Playwright 会设置必要的 `security_verify_data` 参数与 cookie；如遇页面仍受防护，可适当增大 `--delay` 或重试。
- 运行帮助：`uv run python playwright_scrape_etmoc.py --help`。