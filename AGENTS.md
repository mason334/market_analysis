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
│   │   ├── adaptive_segmentation.py  # 自适应初分段（纯计算）
│   │   ├── adaptive_trend.py         # 旧模块名兼容入口
│   │   ├── pivot_segmentation.py     # close Pivot 精炼分段（纯计算）
│   │   ├── trend_pattern.py          # 长窗口趋势形态分类与质量统计
│   │   ├── trend_pattern_v4_legs.py  # v4 effective-leg 提取与索引保留
│   │   ├── trend_pattern_v4_structure.py # v4 方向无关结构分类
│   │   ├── trend_pattern_v4_metrics.py # v4 close-path 数值指标
│   │   └── sector_heat.py            # 板块热度指标计算
│   ├── pipeline/
│   │   ├── run_indicators.py         # 每日 symbol 级指标批量执行
│   │   ├── run_adaptive_segmentation.py # 独立自适应初分段
│   │   ├── run_pivot_segmentation.py # Pivot 精炼分段快照
│   │   ├── run_trend_patterns.py     # 基于已持久化分段的形态分类
│   │   ├── run_trend_pattern_v4.py   # v4 结构与路径指标快照
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
- `pivot_segmentation_daily`：Pivot 精炼分段摘要，每 `symbol/date/lookback_bars` 一行。
- `pivot_segment_daily`：Pivot 精炼分段及终点节点，每 `symbol/date/lookback_bars/segment_index` 一行。
- `trend_pattern_daily`：长窗口形态分类，每 `symbol/date/lookback_bars` 一行。
- `trend_pattern_v4_daily`：v4 结构与 close-path 指标，每 `symbol/date/lookback_bars` 一行。
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

### 自适应初分段与 Pivot 精炼分段表

`trend_segmentation_daily` 当前保存目标 250 bar 生产窗口的分段数、RSS、BIC 与模型参数；标的历史
不足 250 但至少 40 bars 时保存实际可用窗口，少于 40 bars 时跳过；
summary 使用 `requested_lookback_bars` 保存配置目标，使用 `lookback_bars` 和
`observation_count` 保存实际窗口；例如目标 250、实际 65 时保存 `250/65/65`。
`trend_segment_daily` 保存每一段的日期边界、bar 索引、log slope、R²、return、波动率、
波动率调整趋势、路径效率，以及最大单日 log return、发生日期/bar 索引和绝对路径占比。
算法对合法断点组合执行全局连续分段最小二乘，拟合路径在断点处连续但不强制经过实际
端点；当前口径为 `adaptive_segmentation_v3`，方法为
`continuous_piecewise_log_linear_deterministic_hybrid_bic`。候选数不超过预算时执行分批
精确穷举，超预算时执行确定性 beam + 单断点全域优化 + 相邻双断点局部优化，并在 summary
中记录 exact/approximate、全局最优保证、候选数、收敛和搜索审计信息。分段继续使用
`[start, end)`，后一段
包含 `start - 1 -> start` 的进入收益，因此各段实际 log return 之和等于整个窗口实际
log return。BIC 复杂度惩罚乘数由 `bic_penalty_multiplier` 配置，默认 3.0，并随摘要
持久化。实验由独立 CLI 触发，不属于 `run-indicators` 固定窗口流程。

`pivot_segmentation_daily` 保存 `pivot_refined_segmentation_v2` 窗口摘要与源分段版本，并从初分段
summary 继承 `requested_lookback_bars`；
`pivot_segment_daily` 保存固定 close pivot 后重新连续拟合的分段指标。内部 pivot 作为左侧
segment 的终点保存，`end_point_type` 为 `high/low`；末段为 `window_end`。Pivot 只在 seed
左右各 5 bars 的 close 中搜索，不读取 OHLC 极值。数据归属继续使用 `[start, end)`，共享
pivot 的端点索引另存，避免边界语义混淆。
批处理按 symbol 聚合同一日期的全部 `lookback_bars`，复用一次 OHLCV 读取；该 symbol 的所有窗口
均成功后才一次性 upsert。任一窗口失败则该 symbol 本轮整体不写入，其他 symbol 继续处理。

### trend_pattern_daily

阶段 C 基于已持久化的自适应分段，以 `trend_pattern_v3` 口径对 60 bar 路径做分层描述：

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

### trend_pattern_v4_daily

v4 使用独立表，不覆盖 `trend_pattern_daily`。`lookback_bars` 表示 close observations
数量，当前生产窗口 60 bars 对应 59 个 daily returns。表中保存 1～4 effective legs 的
`start_direction`、`structure_index`、`structure_code`，以及已接受的 G01～G07、T03、T04
连续数值指标；无 effective leg 时仍写入 raw-close 指标，结构字段和
`terminal_leg_start_position` 为 `NULL`。D01～D03 为可推导量，不重复持久化。

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

# 运行自适应初分段，写入 trend_segmentation_daily + trend_segment_daily
market-analysis run-adaptive-segmentation

# 基于已持久化初分段执行 close Pivot 精炼
market-analysis run-pivot-segmentation

# 基于最新分段快照计算长窗口形态
market-analysis run-trend-pattern-analysis

# 基于已持久化分段计算 v4 结构与路径指标；可选 --date YYYY-MM-DD
market-analysis run-trend-pattern-v4-analysis

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
  `trend_segment_daily`、`pivot_segmentation_daily`、`pivot_segment_daily`、
  `trend_pattern_daily`、`trend_pattern_v4_daily`、`sector_heat_daily` 的字段含义。
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

### 方案命名与版本管理

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
