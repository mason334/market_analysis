# market_analysis

`market_analysis` 是一个面向股票与 ETF 日线数据的 Python 行情分析后端，核心功能是**将K线路径识别为具有明确趋势的分段行情，帮助用户识别市场趋势**

通过完成两阶段价格路径分段算法实现上述目标：

1. **自适应初分段（Adaptive Segmentation）**：在 log close 路径上选择连续的分段线性模型。
2. **Pivot 精炼分段（Pivot Refinement）**：把初始内部断点调整到确定性的局部 close Pivot，并对各段独立重新拟合。

项目直接读取 `market_data` PostgreSQL 中的分复权日线与 universe 数据，并把分段快照写入独立的
`market_analysis` PostgreSQL。结果展示、筛选和手动触发入口统一由下游 `investment_dashboard`
的“市场指标快照”页面负责。

本项目只提供描述性行情分析，不提供交易策略、买卖建议、持仓管理、自动下单或策略回测。

## 开发文档


- `docs/adaptive_segmentation_development.md`：自适应分段与 Pivot 精炼的设计、实现和历史决策记录。
- `docs/trend_pattern_v4_metric_spec.md`：已经暂停的 trend-pattern v4 历史规格，仅供研究追溯。

README、开发文档和代码注释应随当前架构与接口同步维护。历史设计记录如果包含已经删除的路径或命令，
必须明确标注为历史内容，避免与当前生产行为混淆。

## 项目边界

| 维度 | 说明 |
|---|---|
| 核心职责 | 自适应连续分段、close Pivot 精炼、分段快照持久化 |
| 数据来源 | 直连 `market_data` PostgreSQL，不 import `market_data` 的 Python 代码 |
| 写入目标 | `market_analysis` PostgreSQL 的四张分段表 |
| 展示入口 | `investment_dashboard` 直接查询数据库，并通过显式操作按钮调用本项目 CLI |
| 不包含 | 固定窗口指标、支撑/阻力、板块热度、形态分类、交易信号和回测 |

`trend-pattern v3/v4` 研究路线因复杂度过高已经暂停。相关规格文档仅作为历史研究记录保留，
对应实现不属于当前生产代码。

## 系统架构

```text
market_data PostgreSQL
        │
        ▼
Adaptive Segmentation
        │
        ├── trend_segmentation_daily
        └── trend_segment_daily
        │
        ▼
Pivot Refinement
        │
        ├── pivot_segmentation_daily
        └── pivot_segment_daily
        │
        ▼
investment_dashboard / 市场指标快照
```

项目内部依赖方向保持单向：

```text
cli → pipeline → segmentation + db
segmentation → pandas / numpy（纯计算）
db → PostgreSQL 连接与 SQL
config → cli / pipeline / db
```

- `segmentation/` 只实现分段算法，不连接数据库、不读取 `.env`、不写文件。
- `pipeline/` 负责编排数据读取、算法调用、批处理、进度、评估和持久化。
- `db/` 负责双数据库连接、查询、幂等 schema 初始化与 upsert。
- `investment_dashboard` 不 import 本项目模块，只使用数据库和稳定的 CLI 边界。

## 项目结构

```text
market_analysis/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── config/
│   └── settings.yaml
├── docs/
│   ├── adaptive_segmentation_development.md
│   └── trend_pattern_v4_metric_spec.md
├── scripts/
│   └── cron_run_segmentation_pipeline.sh
├── src/market_analysis/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── queries.py
│   │   └── schema.py
│   ├── segmentation/
│   │   ├── __init__.py
│   │   ├── adaptive_segmentation.py
│   │   └── pivot_segmentation.py
│   └── pipeline/
│       ├── __init__.py
│       ├── _progress.py
│       ├── preview_adaptive_segmentation.py
│       ├── run_adaptive_segmentation.py
│       ├── run_pivot_segmentation.py
│       └── evaluate_adaptive_segmentation.py
└── tests/
```

### `segmentation/`

- `adaptive_segmentation.py`：执行连续分段 log-linear 拟合、模型选择和搜索审计。
- `pivot_segmentation.py`：在自适应断点附近搜索 close Pivot，并对 Pivot-to-Pivot 区间独立拟合。

### `pipeline/`

- `preview_adaptive_segmentation.py`：对单只标的执行只读预览，返回 JSON，不写数据库。
- `run_adaptive_segmentation.py`：批量运行自适应初分段，支持 fallback、续跑和强制重算。
- `run_pivot_segmentation.py`：读取已持久化的初分段，批量生成并写入 Pivot 精炼快照。
- `evaluate_adaptive_segmentation.py`：执行参数组合与历史稳定性评估，生成 CSV 和 HTML 报告。
- `_progress.py`：提供批处理共用的进度、完成比例和 ETA 日志字段。

## 算法口径

### 自适应初分段

当前计算版本为 `adaptive_segmentation_v3`。

- 默认请求窗口为 250 bars。
- 历史不足 250 bars、但至少达到 40 bars 时，使用实际可用窗口生成 fallback 快照。
- 在 log close 路径上执行连续分段线性回归，拟合路径在断点处连续。
- 候选规模不超过预算时执行精确枚举；超过预算时使用确定性 beam search 和局部优化。
- 使用带可配置惩罚倍数的 BIC 在不同分段数之间选择模型。
- 将搜索模式、收敛状态、候选规模和最优性保证等审计字段写入 summary。
- 分段数据归属采用 `[start, end)`；后一段包含跨入该段的收益。

### Pivot 精炼分段

当前计算版本为 `pivot_refined_segmentation_v3`。

- 以已持久化的自适应内部断点作为 seed。
- 只在 seed 左右固定半径的 close 中搜索 Pivot，不使用 OHLC high/low。
- 每个 Pivot-to-Pivot 区间执行独立 OLS，相邻拟合端点不要求连续。
- 内部 Pivot 作为左侧 segment 的终点保存，末段终点类型为 `window_end`。
- 同一 symbol 的所有 lookback 必须全部成功后才会一次性写入。

完整计算约定见 `docs/adaptive_segmentation_development.md` 和对应模块 docstring。

## 数据范围

项目主要读取 `market_data` 中的以下源表：

- `daily_bars_split_adjusted`
- `universe_constituents`
- `universe`

生产 symbol 范围包括：

- 已启用 constituent 行情更新的最新普通股成分；
- `universe` 中全部有效 ETF；
- symbol 必须符合 1～5 位大写英文字母的格式。

每次运行都会实时读取数据库，不把行情数据缓存到磁盘。

## 数据库输出

`init_schema()` 幂等维护以下四张生产表：

| 表名 | 主键 | 用途 |
|---|---|---|
| `trend_segmentation_daily` | `symbol, date, lookback_bars` | 自适应模型选择摘要与搜索审计 |
| `trend_segment_daily` | `symbol, date, lookback_bars, segment_index` | 自适应分段边界与逐段统计 |
| `pivot_segmentation_daily` | `symbol, date, lookback_bars` | Pivot 精炼摘要与源版本契约 |
| `pivot_segment_daily` | `symbol, date, lookback_bars, segment_index` | Pivot-to-Pivot 分段及端点信息 |

自适应 summary 使用：

- `requested_lookback_bars` 保存请求窗口；
- `lookback_bars` 保存实际使用窗口；
- `observation_count` 保存实际观测数量。

历史数据库可能仍保留旧固定指标、板块热度或 trend-pattern 表。当前代码不再创建、更新或查询这些表，
但 schema 初始化和代码清理也不会自动删除历史表或数据。

## 安装

运行环境要求：

- Python 3.11 或更高版本；
- 可访问包含源行情表的 `market_data` PostgreSQL；
- 可写入目标 `market_analysis` PostgreSQL。

创建虚拟环境并安装：

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
```

复制环境变量模板：

```bash
cp .env.example .env
```

Windows PowerShell 可以使用：

```powershell
Copy-Item .env.example .env
```

填写数据库连接信息：

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=market_analysis
DB_USER=postgres
DB_PASSWORD=your_password
SOURCE_DB_NAME=market_data
```

`.env` 已加入 `.gitignore`，不得提交真实凭据。

## 配置

默认算法与 pipeline 参数位于 `config/settings.yaml`：

```yaml
indicators:
  adaptive_segmentation:
    lookbacks: [250]
    min_fallback_bars: 40
    min_segment_bars: 5
    max_segments_cap: 10
    bic_penalty_multiplier: 2.0

  pivot_refinement:
    search_radius_bars: 5
    min_segment_bars: 5

pipeline:
  source: tiingo
```

`indicators` 是当前配置文件中的兼容键名，不代表代码仍使用旧 `indicators/` 目录。

## CLI

初始化或更新数据库 schema：

```bash
market-analysis init-db
```

批量运行自适应初分段：

```bash
market-analysis run-adaptive-segmentation
market-analysis run-adaptive-segmentation --date 2026-09-18 --lookback 250
market-analysis run-adaptive-segmentation --date 2026-09-18 --lookback 250 --force
```

未指定 `--date` 时，每只 symbol 使用自身最新可用行情日期。默认会跳过参数、版本和分段完整性均匹配的
已有快照；`--force` 用于显式强制重算。

基于已持久化的初分段运行 Pivot 精炼：

```bash
market-analysis run-pivot-segmentation
market-analysis run-pivot-segmentation --date 2026-09-18
```

未指定 `--date` 时，命令使用最新可用的自适应分段快照日期。

计算单只标的的只读 JSON 预览：

```bash
market-analysis compute-adaptive-segmentation \
  --symbol AAPL \
  --lookback 250 \
  --min-segment-bars 5 \
  --max-segments 10 \
  --bic-penalty-multiplier 2.0
```

预览命令返回 summary、segments、fitted points 和耗时，不写入数据库。

生成只读参数评估报告：

```bash
market-analysis validate-adaptive-segmentation --date 2026-09-18
```

评估结果默认写入 `artifacts/adaptive_segmentation_validation/`，该目录不会提交到 Git。

## 定时运行

生产 cron 脚本按依赖顺序执行：

```text
1. market-analysis run-adaptive-segmentation
2. market-analysis run-pivot-segmentation
```

使用脚本：

```bash
scripts/cron_run_segmentation_pipeline.sh
```

Pivot 精炼依赖第一步写入的自适应分段，因此只有第一步成功后才会继续执行第二步。脚本使用 `flock`
避免任务重叠，并把每次运行日志写入 `logs/`。

## 与 investment_dashboard 的关系

`investment_dashboard` 的“市场指标快照”页面：

- 通过自己的 SQL 层读取四张分段表；
- 在详情页读取已持久化的自适应与 Pivot 分段，并重建拟合路径；
- 通过显式按钮调用 `run-adaptive-segmentation` 和 `run-pivot-segmentation`；
- 不调用单标的 `compute-adaptive-segmentation`；
- 不 import `market_analysis` Python 模块。

因此，修改数据库表、字段、计算版本、空值语义、CLI 名称或 CLI 参数时，必须同步检查
`investment_dashboard`。

## 开发与验证

运行静态检查和测试：

```bash
python -m ruff check .
python -m pytest
```

当前测试覆盖：

- 自适应分段核心算法；
- Pivot 精炼核心算法；
- pipeline 的 fallback、续跑、失败隔离和写入契约；
- 数据库查询与 upsert 契约；
- 单标的预览和参数评估；
- CLI 参数传递与进度辅助函数。

## License

当前仓库尚未授予 open-source license。仓库公开可见不等于允许复制、修改或再分发；在正式选择并添加
license 之前，相关权利仍由版权持有人保留。
