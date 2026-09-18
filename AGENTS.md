# market_analysis — 自适应与 Pivot 分段项目

Python 后端项目，直连 `market_data` PostgreSQL 读取分复权日线和 universe 数据，依次执行
自适应初分段与 close Pivot 精炼分段，结果写入 `market_analysis` PostgreSQL。展示和交互操作由
下游 `investment_dashboard` 的“市场指标快照”页面负责。

本项目不提供交易策略、买卖建议、持仓管理、自动下单或策略回测。`trend-pattern v3/v4` 路线因
复杂度过高已暂停，其规格文档仅作为历史研究记录保留。

## 项目边界

| 维度 | 说明 |
|---|---|
| 核心职责 | 自适应连续分段、close Pivot 精炼、分段快照持久化 |
| 数据来源 | 直连 `market_data` PostgreSQL，不 import `market_data` Python 代码 |
| 写入目标 | `market_analysis` PostgreSQL 的四张分段表 |
| 展示 | `investment_dashboard` 直接读取数据库，并通过显式按钮调用本项目 CLI |
| 不做 | 固定窗口指标、支撑/阻力 pipeline、板块热度、形态分类、交易信号 |

## 技术栈

| 用途 | 选择 | 禁止替代 |
|---|---|---|
| 数据处理 | pandas / numpy | —— |
| 数据库连接 | psycopg 3 + psycopg-pool | SQLAlchemy ORM |
| 配置 | pydantic-settings + YAML + `.env` | 硬编码凭据 |
| CLI | Typer | argparse / click |
| 日志 | structlog | logging / print |
| 测试 | pytest | unittest |
| Lint | ruff | flake8 / black |
| 数据库迁移 | 幂等 SQL | Alembic |

## 当前结构

```text
market_analysis/
├── config/settings.yaml
├── docs/
│   ├── adaptive_segmentation_development.md
│   └── trend_pattern_v4_metric_spec.md
├── scripts/cron_run_segmentation_pipeline.sh
├── src/market_analysis/
│   ├── cli.py
│   ├── config.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── queries.py
│   │   └── schema.py
│   ├── segmentation/
│   │   ├── adaptive_segmentation.py
│   │   └── pivot_segmentation.py
│   └── pipeline/
│       ├── _progress.py
│       ├── preview_adaptive_segmentation.py
│       ├── run_adaptive_segmentation.py
│       ├── run_pivot_segmentation.py
│       └── evaluate_adaptive_segmentation.py
└── tests/
```

## 数据库契约

`init_schema()` 只维护以下四张表：

- `trend_segmentation_daily`：每 `symbol/date/lookback_bars` 一行的自适应模型摘要。
- `trend_segment_daily`：每 `symbol/date/lookback_bars/segment_index` 一行的自适应分段明细。
- `pivot_segmentation_daily`：每 `symbol/date/lookback_bars` 一行的 Pivot 精炼摘要。
- `pivot_segment_daily`：每 `symbol/date/lookback_bars/segment_index` 一行的 Pivot 分段明细。

历史数据库可能仍有固定指标、板块热度或 trend-pattern 表。本项目不再创建、更新或查询这些表，
但清理代码时不得自动删除历史表或数据。

自适应分段 summary 使用 `requested_lookback_bars` 保存请求窗口，使用 `lookback_bars` 和
`observation_count` 保存实际窗口。历史不足请求窗口、但至少达到配置的 fallback bars 时，仍写入
合法 fallback 快照。

自适应算法口径为 `adaptive_segmentation_v3`，拟合路径在断点处连续。候选数不超过预算时精确
穷举；超预算时使用确定性 beam 与局部优化，并持久化搜索模式、收敛状态和最优性审计字段。

Pivot 口径为 `pivot_refined_segmentation_v3`。内部 Pivot 只在 seed 左右固定半径的 close 中搜索，
不使用 OHLC high/low。每个 Pivot-to-Pivot 区间独立 OLS，相邻拟合端点允许不连续。

## 数据读取范围

主要源表：

- `daily_bars_split_adjusted`
- `universe_constituents`
- `universe`

生产 symbol 范围是已启用 constituent 行情更新的最新普通股成分，加上 universe 中全部有效 ETF；
代码必须是 1～5 位大写英文字母。每次运行实时读取数据库，不缓存行情到磁盘。

## CLI

```bash
market-analysis init-db
market-analysis run-adaptive-segmentation
market-analysis run-adaptive-segmentation --date 2026-09-18 --lookback 250 --force
market-analysis run-pivot-segmentation
market-analysis run-pivot-segmentation --date 2026-09-18
market-analysis compute-adaptive-segmentation --symbol AAPL --lookback 250
market-analysis validate-adaptive-segmentation --date 2026-09-18
```

不要重新引入旧 `run-indicators`、`run-strategies`、`run-sector-heat` 或 trend-pattern 命令。

## 代码分层

```text
cli → pipeline → segmentation + db
segmentation → 无项目内部依赖（纯计算）
db → 只负责连接和 SQL，不依赖 pipeline
config → 可被 cli / pipeline / db 使用
```

分段计算函数不得连接数据库、读 `.env`、写文件或发网络请求。pipeline 负责读取、计算编排和
写入。不得新增策略注册表或交易信号事件表。

## 与 investment_dashboard 的关系

`investment_dashboard` 不 import 本项目模块。它通过自己的 SQL 读取四张分段表，仅在用户明确点击
操作按钮时调用：

- `run-adaptive-segmentation`
- `run-pivot-segmentation`

修改表、字段、索引、主键、字段语义、CLI 名称、CLI 输出或下游查询所需配置时，最终回复必须单独
说明 `investment_dashboard` 影响。若 workspace 可见该项目，应同步检查或修改；不可见时不得猜测。

## 自主执行规则

- 可直接修复 ruff/mypy、import 顺序和缺失的 `__init__.py`。
- 测试失败时自动分析并重试，最多 3 轮；仍失败则停止并报告。
- 可运行 pytest、ruff 和覆盖率报告。
- 可安装依赖和更新 `pyproject.toml`，但不得降级现有依赖。
- 所有数据库 `SELECT` 可直接执行；执行 schema migration 前必须说明 SQL 变化。
- 可直接运行 `git status`、`git diff`、`git log`。
- 不得自动执行 `git add`、`git commit`、`git stash`、`git push` 或其他改变 Git 状态的命令。
- 只有用户明确要求对应 Git 操作时才可执行；“修改”“实现”“执行方案”不构成 Git 操作授权。

## 回答要求

- 分析要客观有据；资料不足时明确说明。
- 数学公式必须定义每个字母、下标、单位、数据口径和窗口。
- 数据库输出、下游查询接口或 CLI 变化必须单独说明 `investment_dashboard` 是否需要同步修改。

## 方案命名与版本管理

当回复中给出可供后续修改或执行的正式方案、设计方案、技术方案或实施计划时，必须明确列出：

- **方案名称**：便于人阅读和讨论的名称。
- **方案编号**：稳定、简短、可引用的标识；同一方案后续修订继续使用同一编号。
- **版本号**：使用 `vMAJOR.MINOR.PATCH` 语义化格式；首次正式方案默认从 `v1.0.0` 开始。
- **状态**：例如“提议”“已确认”“执行中”“已实施”或“已废弃”。

版本递增规则：不改变目标和结构的文字或小参数修订增加 `PATCH`；向后兼容的功能、范围或重要
设计扩展增加 `MINOR`；不兼容变更、核心口径变化或整体替代原方案增加 `MAJOR`。不得在发生实质
变化后继续沿用原版本号。每次重述、比较或修改方案时都应再次给出上述四项信息，不依赖读者回看
前文。若用户随后要求按方案实施，最终交付必须注明实际执行的方案编号和版本号。普通问答、故障
诊断、状态汇报或不构成正式方案的简短建议不强制使用该格式。
