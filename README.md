# ETMOC Scraper（Playwright）

一句话：基于 Python 3.10+ 与 Playwright 的 etmoc.com 抓取器，支持目录与品牌两类工作流，生成 JSON/CSV，并可下载图片。

---

## 1) 项目简介
- 目标站点：`http://www.etmoc.com/`
- 技术栈：Playwright（页面访问）、BeautifulSoup（详情解析）、Requests（图片下载）、tqdm（进度条）
- 特性：
  - 目录与品牌两类工作流
  - CLI 支持分页上限、起始页、增量检查点与续跑
  - 自动检测目录“总页数”（站点结构变更时有兜底策略）
  - 统一的 JSON/CSV 输出与图片下载

重要策略：建议做增量抓取时反复关注前几页（新→旧排序）；如需从中断处恢复，使用 `--start-page latest`。

---

## 2) 环境与安装

先决条件
- Python 3.10+（本地或虚拟环境）
- pip 与 venv（推荐）或 pipx
- 不需要 Node.js。首次运行 Playwright 需安装浏览器二进制。

安装步骤（macOS/Linux）
- git clone 本仓库
- 创建并激活虚拟环境
  - `python -m venv .venv && source .venv/bin/activate`
- 安装依赖
  - `pip install -r requirements.txt`
- 安装 Playwright 浏览器
  - `playwright install`
  - 若在 Linux 缺系统依赖，可使用：`playwright install --with-deps`

安装步骤（Windows PowerShell）
- git clone 本仓库
- 创建并激活虚拟环境
  - `python -m venv .venv`
  - `.\.venv\Scripts\activate`
- 安装依赖与浏览器
  - `pip install -r requirements.txt`
  - `playwright install`

可选：你也可以使用 uv 运行（已在 CI 中使用）。
- 查看帮助：`uv run python playwright_scrape_etmoc.py --help`
- 安装浏览器：`uv run python -m playwright install`

---

## 3) 快速开始与 CLI 用法

查看帮助
- 运行：`python playwright_scrape_etmoc.py --help`
- 实际输出如下（与代码一致）：

```
usage: playwright_scrape_etmoc.py [-h] [--limit LIMIT] [--delay DELAY] [--out OU
T] [--pages PAGES] [--start-page START_PAGE] [--incremental] [--source {catalog,
brands}] [--action {list,detail}]
使用 Playwright 爬取 ETMOC 烟草产品信息
options:
-h, --help            show this help message and exit
--limit LIMIT         最多抓取的产品条数，0 表示不限
--delay DELAY         请求间隔秒数
--out OUT             输出目录
--pages PAGES         分页上限；传 all 表示不限；未提供默认 1 页
--start-page START_PAGE
分页起始页；传整数或 latest（从检查点继续）
--incremental         启用增量模式（默认关注前几页；若需从检查点继续请配合 --s
tart-page latest）
--source {catalog,brands}
--action {list,detail}
catalog 源的动作：list 仅收集链接，detail 解析详情
```

最小可运行示例（目录抓取）
- 仅收集链接（关注前 3 页，增量模式）：
  - `python playwright_scrape_etmoc.py --source catalog --action list --incremental --pages 3 --out etmoc_output`
- 解析详情（关注前 3 页，增量模式）：
  - `python playwright_scrape_etmoc.py --source catalog --action detail --incremental --pages 3 --out etmoc_output`
- 指定起始页（从第 2 页开始抓 3 页）：
  - `python playwright_scrape_etmoc.py --source catalog --action detail --start-page 2 --pages 3 --out etmoc_output`
- 从检查点继续深页补扫（latest 会从 `catalog_checkpoint.json` 的最后完成页+1 开始）：
  - `python playwright_scrape_etmoc.py --source catalog --action list --start-page latest --pages 5 --incremental --out etmoc_output`

最小可运行示例（品牌抓取）
- `python playwright_scrape_etmoc.py --source brands --limit 20 --out etmoc_output`
- 差异点：品牌抓取不使用目录总页数检测；可用 `--limit` 控制最多解析的产品条数。

检查点/续跑机制
- 目录抓取在增量模式下会写入 `etmoc_output/catalog_checkpoint.json`，例如：`{"last_page": 12}`。
- 继续运行时使用 `--start-page latest` 从检查点后续页恢复；不指定则默认从第 1 页（聚焦新增）。

---

## 4) 输出与数据结构

输出目录结构（默认 `etmoc_output/`）
- 目录抓取（action=detail）：
  - `products_catalog.json`、`products_catalog.csv`
- 目录链接（action=list）：
  - `product_links.json`（包含 `count` 与 `links`）
- 品牌抓取：
  - `products_playwright.json`、`products_playwright.csv`
- 图片：
  - `images/` 下保存下载的首图；记录到每个条目的 `image_local` 字段
- 检查点：
  - `catalog_checkpoint.json`（用于 `--start-page latest` 续跑）

数据样例与字段含义
- 完整 JSON/CSV 示例可直接查看仓库内样例：
  - JSON: [etmoc_output/products_catalog.json](./etmoc_output/products_catalog.json)
  - CSV:  [etmoc_output/products_catalog.csv](./etmoc_output/products_catalog.csv)
- 单条 JSON 样例：

```
{
  "title": "双喜（春天幻影） Shuangxi Spring Phantom",
  "url": "http://www.etmoc.com/Firms/Product?Id=3597",
  "info": {
    "中文品名": "双喜（春天幻影）",
    "英文品名": "Shuangxi Spring Phantom",
    "产品类型": "烤烟型",
    "焦油量": "6mg",
    "烟碱量": "0.4mg",
    "一氧化碳量": "6mg",
    "包装形式": "条盒硬盒 （每盒 20 支，每条 10 盒）",
    "烟支规格": "97mm 细支",
    "小盒条码": "6901028008426",
    "小盒零售价": "35 元/盒",
    "条盒零售价": "350 元/条",
    "上市时间": "2025 年"
  },
  "images": [
    "http://www.etmoc.com/firm/2025/202510132047511970.jpg"
  ],
  "image_local": "etmoc_output/images/202510132047511970.jpg"
}
```

图片存放与命名
- 下载的图片保存在 `out/images/` 下，文件名来自图片 URL 的最后一段并做字符清洗（只保留字母、数字、._-）。
- 每条记录会在 `image_local` 字段写入对应的本地路径（如有图片）。

---

## 5) 常见问题与故障排查

Playwright 浏览器未安装/缺依赖
- 错误现象：第一次运行报错或提示未找到浏览器
- 解决：
  - `playwright install`（安装浏览器）
  - Linux 如仍报依赖，尝试：`playwright install --with-deps`

Timeout/加载超时
- 目录与详情页已将默认超时提高到 45s；仍遇超时可：
  - 增大请求间隔：`--delay 1.0` 或更高
  - 降低抓取范围：减少 `--pages` 或添加 `--limit`
  - 重试/断点续跑：配合 `--incremental --start-page latest`

权限/沙箱问题（Linux 容器常见）
- 如见到与 sandbox 相关错误，可在本地调试时临时修改 `playwright_scrape_etmoc.py` 中的启动参数：
  - `p.chromium.launch(headless=False, args=["--no-sandbox"])`

反爬/限速
- 建议初始速率：`--delay 0.5~1.5` 秒；必要时进一步升高。
- 无显式重试参数，可通过增量模式与检查点多次运行实现稳妥补采。

Headless/非 Headless 与慢速模式（调试）
- 默认以 Headless 运行。若需观察页面：
  - 将代码中的 `headless=True` 改为 `False`
  - 可增加 `slow_mo=300`（单位 ms）观察页面行为：`p.chromium.launch(headless=False, slow_mo=300)`

---

## 6) 性能与稳定性建议（简要）
- 合理控制范围：优先用 `--pages` 限制页数、`--limit` 限制条数
- 速率与稳态：`--delay` 0.5~1.0 通常较稳；网络抖动场景适当增大
- 图片下载：解析阶段不下载，任务末尾批量下载首图；如追求极致速度，可临时注释图片下载调用再运行（开发者场景）
- 无并发参数：当前实现串行抓取，避免对目标站点造成压力

---

## 7) 开发与贡献

目录结构（简要）
- `playwright_scrape_etmoc.py`：主脚本（CLI、目录/品牌工作流、解析与输出）
- `dump_html.py`：调试工具（可保存原始 HTML 页面）
- `etmoc_output/`：示例输出（JSON/CSV/图片/检查点）
- `pyproject.toml`：项目元数据与依赖（Python>=3.10）

代码风格与类型
- 建议：black 格式化、ruff 静态检查、mypy 可选（仓库未强制）

贡献
- 欢迎提交 PR：保持与现有风格一致，附上说明与最小复现步骤

---

## 8) 许可证与致谢
- 许可证：当前仓库未包含 LICENSE 文件，若需明确授权条款，请追加相应许可证文件（如 MIT/Apache-2.0 等）
- 数据来源：etmoc.com 公开页面，仅用于技术研究与学习

---

English quick note
- A Playwright-based scraper for etmoc.com (Python 3.10+). Two workflows (catalog/brands), JSON/CSV output, optional image download. See the Quick Start section above for commands.
