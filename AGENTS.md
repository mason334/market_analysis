# market_analysis — 行情指标分析项目

Python 后端项目，直连 `market_data` PostgreSQL 数据库读取行情与 universe 数据，计算支撑/阻力、趋势、板块热度等分析指标，结果写入 `market_analysis` 数据库。展示和进一步分析统一由下游 `investment_dashboard` 项目负责。

本项目目前**不提供交易策略、买卖建议、持仓管理、自动下单或策略回测**。所有“信号”“策略”相关旧表述都应视为历史遗留概念；新增功能应优先命名为 `indicator`、`analysis`、`snapshot`、`heat`、`trend` 等中性分析概念。

---

## 项目定位

| 维度 | 说明 |
|------|------|
| 职责 | 生成行情分析指标快照：支撑/阻力、趋势斜率、板块资金热度 |
| 不做 | 交易策略、买卖点建议、策略回测、持仓管理、自动下单 |
| 数据来源 | 直连 `market_data` PostgreSQL，**不 import market_data 的 Python 代码** |
| 写入目标 | `market_analysis` PostgreSQL 中的指标快照表 |
| 展示 | 不在本项目维护；统一交由 `investment_dashboard` 读取本项目输出 |

---

## 技术栈

| 用途 | 选择 | 禁止替代 |
|------|------|---------|
| 数据处理 | pandas / numpy | —— |
| 指标计算 | scipy / statsmodels | 手写复杂统计逻辑前先检查现有库 |
| 数据库连接 | psycopg 3 + psycopg-pool（直连 PostgreSQL） | SQLAlchemy ORM |
| 配置 | pydantic-settings + YAML + .env | 硬编码 |
| CLI | Typer | argparse / click |
| 日志 | structlog | logging / print |
| 测试 | pytest | unittest |
| Lint | ruff | flake8 / black |
| 包管理 | pip + venv | —— |
| 数据库迁移 | 手动幂等 SQL：`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | Alembic |
| 镜像源 | 清华或 USTC | 默认 PyPI |

---

## 当前项目结构

```
market_analysis/
├── AGENTS.md                         # Codex 协作规则
├── README.md                         # 项目说明（可能滞后，以代码和 AGENTS.md 为准）
├── dashboard.py                      # 历史遗留展示入口；当前不再维护
├── pyproject.toml                    # 包配置、依赖、ruff/pytest 配置
├── simulation.ipynb                  # 实验笔记本；不参与生产流程
├── config/
│   ├── settings.yaml                 # 指标与 pipeline 默认参数
│   ├── user_prefs.yaml               # 可选本地参数覆盖；不作为展示层契约
│   └── universe.yaml                 # 历史配置，当前主要 universe 来源为 DB
├── scripts/                          # 历史遗留脚本；不作为当前展示入口
├── src/market_analysis/
│   ├── __init__.py
│   ├── config.py                     # 唯一配置入口
│   ├── models.py                     # 共享数据模型
│   ├── db/
│   │   ├── __init__.py               # market_analysis + market_data 双连接池
│   │   ├── schema.py                 # init_schema()，幂等建表/补字段/建索引
│   │   └── queries.py                # 数据读取、写入、下游查询辅助函数
│   ├── indicators/
│   │   ├── support_resistance.py     # 支撑/阻力指标计算
│   │   ├── trend.py                  # 趋势指标计算
│   │   ├── adaptive_trend.py         # 自适应分段实验（纯计算）
│   │   ├── trend_pattern.py          # 长窗口趋势形态分类与质量统计
│   │   └── sector_heat.py            # 板块热度指标计算
│   ├── pipeline/
│   │   ├── run_indicators.py         # 每日 symbol 级指标批量执行
│   │   ├── run_adaptive_trend_experiment.py # 独立自适应分段实验
│   │   ├── run_trend_patterns.py     # 基于已持久化分段的形态分类
│   │   └── run_sector_heat.py        # 每日板块热度批量执行
│   │   └── archive/                  # 历史 pipeline，不参与当前 CLI 主流程
│   ├── strategies/
│   │   ├── __init__.py               # 仅说明归档用途，不暴露 STRATEGIES 注册表
│   │   └── archive/                  # 已归档旧策略代码；不参与当前生产流程
│   └── cli.py                        # Typer CLI
└── tests/
    ├── conftest.py
    ├── test_support_resistance.py    # 支撑/阻力指标测试
    └── test_sector_heat.py
```

---

## 数据库 Schema

### market_analysis 写入表

`init_schema()` 当前维护以下表：

- `support_resistance_daily`：支撑/阻力快照，每 `symbol/date` 一行。
- `trend_daily`：趋势快照，每 `symbol/date/window_label` 一行。
- `trend_segmentation_daily`：自适应分段模型选择摘要，每 `symbol/date/lookback_bars` 一行。
- `trend_segment_daily`：自适应分段明细，每 `symbol/date/lookback_bars/segment_index` 一行。
- `trend_pattern_daily`：长窗口形态分类，每 `symbol/date/lookback_bars` 一行。
- `sector_heat_daily`：板块热度快照，每 `universe_ticker/date` 一行。

`queries.py` 中的快照查询函数从 `support_resistance_daily` + `trend_daily` 拼出宽表结果，不再回退到历史兼容表。


### support_resistance_daily

```sql
CREATE TABLE IF NOT EXISTS support_resistance_daily (
    symbol                TEXT    NOT NULL,
    date                  DATE    NOT NULL,
    nearest_support       FLOAT,
    nearest_resistance    FLOAT,
    dist_support_pct      FLOAT,
    dist_support_atr      FLOAT,
    dist_resistance_pct   FLOAT,
    dist_resistance_atr   FLOAT,
    atr_14                FLOAT,
    sr_status             TEXT,
    breakout_5d           TEXT,
    breakout_level        FLOAT,
    PRIMARY KEY (symbol, date)
);
```

索引：

```sql
CREATE INDEX IF NOT EXISTS support_resistance_daily_date_idx
    ON support_resistance_daily (date DESC);
CREATE INDEX IF NOT EXISTS support_resistance_daily_symbol_idx
    ON support_resistance_daily (symbol);
CREATE INDEX IF NOT EXISTS support_resistance_daily_status_idx
    ON support_resistance_daily (sr_status);
```

### trend_daily

```sql
CREATE TABLE IF NOT EXISTS trend_daily (
    symbol                       TEXT NOT NULL,
    date                         DATE NOT NULL,
    window_label                 TEXT NOT NULL,
    far_bars                     INT  NOT NULL,
    near_bars                    INT  NOT NULL DEFAULT 0,
    slope                        FLOAT,
    r2                           FLOAT,
    method                       TEXT NOT NULL DEFAULT 'linear_regression',
    observation_count            INT,
    log_slope_per_bar            FLOAT,
    linearity_r2                 FLOAT,
    fitted_log_return            FLOAT,
    actual_log_return            FLOAT,
    realized_volatility_daily    FLOAT,
    vol_adjusted_trend           FLOAT,
    efficiency_ratio             FLOAT,
    jackknife_slope_stability    FLOAT,
    adjacent_slope_stability     FLOAT,
    calculation_version          TEXT,
    PRIMARY KEY (symbol, date, window_label)
);
```

索引：

```sql
CREATE INDEX IF NOT EXISTS trend_daily_date_idx
    ON trend_daily (date DESC);
CREATE INDEX IF NOT EXISTS trend_daily_symbol_idx
    ON trend_daily (symbol);
CREATE INDEX IF NOT EXISTS trend_daily_date_window_idx
    ON trend_daily (date DESC, window_label);
CREATE INDEX IF NOT EXISTS trend_daily_symbol_date_idx
    ON trend_daily (symbol, date DESC);
```

生产固定窗口为 `5d/10d/20d/40d/60d`。`slope/r2` 暂时保留旧口径供兼容；新增
`log_slope_per_bar`、`linearity_r2`、拟合/实际 log return、日度实现波动率、
波动率调整趋势、路径效率和斜率稳定性字段使用 `fixed_trend_v2` 口径。

### 自适应趋势分段实验表

`trend_segmentation_daily` 保存 40/60 bar 实验窗口的分段数、RSS、BIC 与模型参数；
`trend_segment_daily` 保存每一段的日期边界、bar 索引、log slope、R²、return、波动率、
波动率调整趋势、路径效率，以及最大单日 log return、发生日期/bar 索引和绝对路径占比。
算法对合法断点组合执行全局连续分段最小二乘，拟合路径在断点处连续但不强制经过实际
端点；口径为 `adaptive_trend_v2`，方法为
`continuous_piecewise_log_linear_exhaustive_bic`。分段继续使用 `[start, end)`，后一段
包含 `start - 1 -> start` 的进入收益，因此各段实际 log return 之和等于整个窗口实际
log return。BIC 复杂度惩罚乘数由 `bic_penalty_multiplier` 配置，默认 3.0，并随摘要
持久化。实验由独立 CLI 触发，不属于 `run-indicators` 固定窗口流程。

### trend_pattern_daily

阶段 C 基于已持久化的自适应分段，以 `trend_pattern_v3` 口径对 40/60 bar 路径做分层描述：

- `regime`：`trending`、`ranging`、`transitioning` 或 `irregular`。
- `directional_bias`：按窗口净拟合收益与净/总运动比例得到 `up`、`down` 或 `neutral`。
- `path_structure`：`flat`、`single_leg`、`pullback`、`resumption`、`reversal`、
  `double_test`、`contracting`、`expanding`、`stable_range`、`multiple_pullbacks`、
  `complex_reversal` 或 `mixed`。
- `terminal_state`：压缩后最后一段的 `up`、`down` 或 `flat`。
- `pattern`：由上述层次派生的便捷标签，包括趋势、回撤/恢复、反转后横盘、近似双顶/双底、
  收敛/扩张区间、复杂反转和 `irregular_path` 等。

`pattern_confidence` 是描述性规则分数而非概率：趋势类使用 `net_to_gross_ratio`，区间类使用
`1 - net_to_gross_ratio`，反转类使用上下运动平衡度，近似双重测试再结合极值相似度。
形态分布、regime 分布、主导形态比例和 `irregular_path` 比例仅输出质检日志，不另建质量
统计表，也不创建 `market_indicator_snapshot_v2`。

### sector_heat_daily

```sql
CREATE TABLE IF NOT EXISTS sector_heat_daily (
    universe_ticker   TEXT  NOT NULL,
    date              DATE  NOT NULL,
    sector_turnover   FLOAT,
    constituent_count INT,
    turnover_ma20     FLOAT,
    turnover_ratio    FLOAT,
    turnover_zscore   FLOAT,
    PRIMARY KEY (universe_ticker, date)
);
```

索引：

```sql
CREATE INDEX IF NOT EXISTS sector_heat_daily_date_idx
    ON sector_heat_daily (date DESC);
CREATE INDEX IF NOT EXISTS sector_heat_daily_ticker_idx
    ON sector_heat_daily (universe_ticker);
```

---

## 数据读取约定

直连 `market_data` PostgreSQL 读取源表，不依赖 `market_data` 项目的 Python 代码。

主要源表：

- `daily_bars_split_adjusted`：OHLCV 分复权日线。
- `universe_constituents`：`universe_ticker -> stock_ticker` 成分映射。
- `universe`：ETF ticker 与分类信息。

当前 symbol 级指标分析范围：

- `universe_constituents` 中 `universe_ticker = 'OPTIONS_ACTIVE'` 的成分股。
- `universe` 表中 `LENGTH(ticker) <= 4` 的 ETF。

当前板块热度分析范围：

- `universe_constituents` 中所有 `universe_ticker`。

每次分析前实时读取数据库，不缓存行情数据到磁盘。

---

## CLI 命令

```bash
# 初始化/更新数据库表
market-analysis init-db

# 计算支撑/阻力与趋势指标，写入 support_resistance_daily + trend_daily
market-analysis run-indicators

# 历史兼容命令；不要在新文档或新脚本中优先使用
market-analysis run-strategies

# 计算板块热度，写入 sector_heat_daily
market-analysis run-sector-heat

# 运行自适应趋势分段实验，写入两张独立分段表
market-analysis run-trend-segmentation-experiment

# 基于最新分段快照计算长窗口形态
market-analysis run-trend-pattern-analysis

# 查看指定日期指标快照
market-analysis show --date 2026-05-29

# 本项目不再提供展示入口；展示由 investment_dashboard 负责
```

---

## 指标模块约定

指标计算函数应保持纯计算特性：

- 输入：标准化 `pd.DataFrame` 或宽表数据，必要参数来自 `settings.yaml` / `user_prefs.yaml`。
- 输出：可 upsert 的 dict/list[dict] 或 DataFrame。
- 不在 `indicators/` 内部连接数据库。
- 不在指标函数中写文件、读 `.env`、发网络请求。

新增分析能力优先放在 `src/market_analysis/indicators/`，再由 `pipeline/` 负责读取数据、调用计算、写入数据库。

不要新增“交易策略”注册表或交易信号事件表；如确实需要保留兼容旧代码，应明确标注为 compatibility / legacy。

---

## 依赖层次（严格遵守，禁止循环）

```
cli → pipeline → indicators + db
indicators → 无项目内部依赖（纯计算）
db → 只负责连接与 SQL，不依赖 pipeline
config → 可被 cli / pipeline / db 使用
```

`strategies/` 是历史兼容目录，不作为新功能扩展入口。

---

## 与 market_data 项目的关系

| 维度 | market_data | market_analysis |
|------|-------------|-----------------|
| 职责 | 数据采集、清洗、存储 | 指标计算、快照持久化、下游查询 |
| 写入表 | `daily_bars_split_adjusted`、`universe_constituents`、`universe` 等 | `support_resistance_daily`、`trend_daily`、`sector_heat_daily` 等 |
| 读取表 | —— | 读取 `market_data` 的行情与 universe 表 |
| 代码依赖 | 无 | 无（只共享数据库） |
| 运行频率 | 每日一次（市场收盘后） | 每日一次（market_data 更新后） |

cron 建议顺序：

```text
05:00  market-data update
05:30  market-analysis run-indicators
05:35  market-analysis run-sector-heat
```

---

## 与 investment_dashboard 的关系

`investment_dashboard` 是统一展示与分析项目，负责读取本项目写入的表或查询结果。本项目修改以下内容时，最终回复必须说明 `investment_dashboard` 影响：

- 数据库表、字段、索引、主键、唯一约束。
- `support_resistance_daily`、`trend_daily`、`trend_segmentation_daily`、
  `trend_segment_daily`、`trend_pattern_daily`、`sector_heat_daily` 的字段含义。
- `queries.py` 中供下游使用的返回列、列名、排序、空值语义。
- CLI 命令名称或输出格式。
- 下游可能依赖的配置键、参数默认值或数据语义。

如果当前 Codex workspace 看不到 `investment_dashboard`，不要猜测其代码；应明确说明本项目的接口变化，并列出下游需要检查的表/字段/命令。

---

## 自主决策规则

以下操作可直接执行，无需额外询问：

**Python 代码**
- 修复 ruff / mypy 报告的错误；类型无法推断时可用 `Any`，但应留简短说明。
- 补全缺失的 `__init__.py`，修复 import 顺序。
- 单元测试失败时自动分析并重试修复，最多 **3 轮**，之后停下报告。

**依赖管理**
- 安装新依赖、更新 `pyproject.toml`。
- 不允许降级现有依赖版本；遇到依赖冲突先报告。

**Docker**
- `docker build` / `docker-compose up -d` / `docker-compose down` / `docker logs`。
- 修改 `Dockerfile` 或 `docker-compose.yml` 后需告知改了什么。

**数据库**
- 所有 `SELECT` 查询、查看表结构/索引/执行计划。
- 开发环境执行 schema migration 前，先展示将执行的 SQL diff 或说明。

**Git**
- `git status` / `git diff` / `git log` / `git stash`。
- `git add` + `git commit`（commit message 用英文，遵循 Conventional Commits）。

**测试 & Lint**
- 运行 `pytest`、`ruff check`、覆盖率报告。
- 新增功能时自动补写或更新单元测试。

---

## 遇到不确定情况的处理方式

1. 先分析问题，列出可能方案。
2. 多方案时，选择落地可行性最优的方案。
3. 修改后自动测试；测试通过则进入下一任务。
4. 测试失败则自动分析，最多迭代 **3 轮**。
5. 3 轮后仍未解决：停下来分析原因，向用户询问建议。
6. 单个任务超过 **5 分钟**无进展：停下汇报当前状态。

---

## 回答要求

- 分析要客观有据；资料不足时主动说明并提出所需信息。
- 搜索互联网时优先使用高质量来源，给出引用链接。
- 回答聚焦，不做无关扩展。
- 展示数学公式时，必须清楚明示公式中每个字母、下标和代数符号的定义；必要时同时说明
  数据口径、单位、取值范围和计算窗口，不得依赖读者自行猜测。
- 引用前文已经定义过的字母或代数符号时，应再次说明当前使用的定义。若符号含义、统计
  口径或分母发生变化，必须明确指出并优先改用新的符号，禁止无说明地复用原符号。
- 涉及数据库输出、下游查询接口或 CLI 变化时，必须单独说明 `investment_dashboard` 是否可能需要同步修改。
