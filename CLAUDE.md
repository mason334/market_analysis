# market_analysis — 行情信号分析项目

Python 后端项目，读取 `market_data` 数据库的行情数据，运行策略识别交易信号，结果持久化存储并通过 Streamlit 展示。

---

## 项目定位

| 维度 | 说明 |
|------|------|
| 职责 | 信号生成（每日跑策略，发现交易机会） |
| 不做 | 策略回测（另立项目） |
| 数据来源 | 直连 `market_data` 同一 PostgreSQL，**不 import market_data 的 Python 代码** |
| 指标 | 不存储，从 OHLCV 实时计算 |
| 信号 | 存储触发事件（`signals` 表），保留历史记录 |

---

## 技术栈

| 用途 | 选择 | 禁止替代 |
|------|------|---------|
| 数据处理 | pandas | —— |
| 数据库连接 | psycopg（直连 PostgreSQL） | SQLAlchemy ORM |
| 配置 | pydantic-settings + YAML + .env | 硬编码 |
| CLI | Typer | argparse / click |
| 日志 | structlog | logging / print |
| Dashboard | Streamlit | —— |
| 测试 | pytest | unittest |
| Lint | ruff | flake8 / black |
| 包管理 | pip + venv | —— |
| 数据库迁移 | 手动 `CREATE TABLE IF NOT EXISTS` | Alembic |
| 镜像源 | 清华或 USTC | 默认 PyPI |

---

## 项目目录结构

```
market_analysis/
├── dashboard.py                  # Streamlit 展示入口
├── config/
│   ├── settings.yaml
│   └── universe.yaml             # 与 market_data 保持一致
├── scripts/                      # shell / cron 脚本
├── src/market_analysis/
│   ├── __init__.py
│   ├── config.py                 # 唯一配置入口（pydantic-settings）
│   ├── db/
│   │   ├── __init__.py           # 连接池
│   │   ├── schema.py             # init_schema()，建 signals 等表
│   │   └── queries.py            # 信号读写函数
│   ├── strategies/
│   │   ├── __init__.py           # STRATEGIES 注册表
│   │   ├── sudden_surge.py       # 策略：近期暴涨
│   │   └── ma_support.py         # 策略：均线支撑压力
│   ├── pipeline/
│   │   └── run_analysis.py       # 每日批量执行所有策略
│   └── cli.py                    # Typer CLI
└── tests/
    ├── conftest.py
    └── test_strategies.py
```

---

## 数据库 Schema

### signals（信号事件表）

```sql
CREATE TABLE IF NOT EXISTS signals (
    signal_id    TEXT        PRIMARY KEY,
    symbol       TEXT        NOT NULL,
    date         DATE        NOT NULL,        -- 信号触发的交易日
    strategy     TEXT        NOT NULL,        -- 'sudden_surge' | 'ma_support' | ...
    signal_type  TEXT        NOT NULL,        -- 'bullish' | 'bearish'
    detail_json  JSONB,                       -- 策略专属细节
    created_at   TIMESTAMPTZ NOT NULL,
    UNIQUE (symbol, date, strategy)           -- 每 symbol 每日每策略最多一条
);
```

`detail_json` 各策略示例：
- `sudden_surge`：`{"return_5d": 0.12, "volume_ratio": 2.3, "trigger_day_return": 0.08}`
- `ma_support`：`{"ma_period": 50, "ma_value": 182.5, "close": 184.2, "proximity_pct": 0.009, "direction": "support"}`

---

## 策略框架设计

每个策略是一个**纯函数**，输入标准化 DataFrame，输出信号列表：

```python
# strategies/__init__.py
from market_analysis.strategies.sudden_surge import sudden_surge
from market_analysis.strategies.ma_support import ma_support

STRATEGIES: dict[str, callable] = {
    "sudden_surge": sudden_surge,
    "ma_support":   ma_support,
}
```

```python
# 每个策略函数签名
def sudden_surge(
    symbol: str,
    df: pd.DataFrame,   # 完整历史 OHLCV，index = date，已排序
    params: dict,       # 来自 settings.yaml
) -> list[SignalRecord]:
    ...
```

**新增策略只需：**
1. 在 `strategies/` 新建一个文件
2. 在 `STRATEGIES` 注册表加一行
3. 在 `settings.yaml` 加参数配置

---

## 策略参数（config/settings.yaml）

```yaml
strategies:
  sudden_surge:
    lookback_days: 5          # 观察窗口（交易日）
    min_return: 0.08          # 窗口内涨幅阈值（8%）
    volume_ratio_min: 1.5     # 成交量放大倍数（相对近30日均量）

  ma_support:
    periods: [20, 50, 200]    # 关注的均线周期
    proximity_pct: 0.02       # 价格距均线 ≤ 2% 触发
    directions: [support, resistance]  # 两个方向都检测
```

**待确认**：以上参数为建议初始值，需用户根据实际回测效果调整。

---

## 数据读取约定

直连 PostgreSQL，读取 `market_data` 的表：

```python
# 读取 OHLCV（直接 SQL，不依赖 market_data 代码）
SELECT symbol, date, open, high, low, close, volume
FROM daily_bars_split_adjusted
WHERE symbol = %s AND source = 'tiingo'
ORDER BY date
```

- 读取范围：至少 200 个交易日（200 日均线需要）
- 每次分析前实时读取，不缓存到磁盘

---

## CLI 命令

```bash
# 初始化数据库表
market-analysis init-db

# 指标分析：从 universe_constituents(OPTIONS_ACTIVE) 取 ticker，写入 support_resistance_daily + trend_daily
market-analysis run-sr

# 板块热度分析：从 universe_constituents 取所有板块，写入 sector_heat_daily
market-analysis run-sector-heat

# 查看指定日期快照
market-analysis show --date 2026-05-29

# 启动 Streamlit Dashboard
streamlit run dashboard.py --server.port 8504
```

---

## 依赖层次（严格遵守，禁止循环）

```
cli → pipeline → strategies + db
dashboard → db（直接查询，不经过 pipeline）
strategies → 无内部依赖（纯函数）
db → 无内部依赖
```

---

## 自主决策规则

以下操作**直接执行，无需询问**：

**Python 代码**
- 修复 ruff / mypy 报告的错误；类型无法推断时用 `Any` 并留注释
- 补全缺失的 `__init__.py`，修复 import 顺序
- 单元测试失败时自动重试修复，最多 **3 次**，之后停下报告

**依赖管理**
- 安装新依赖、更新 `pyproject.toml`、同步锁文件
- 不允许降级现有依赖版本，遇到冲突先报告

**Docker**
- `docker build` / `docker-compose up -d` / `docker-compose down` / `docker logs`
- 修改 `Dockerfile` 或 `docker-compose.yml` 后需告知改了什么

**数据库**
- 所有 `SELECT` 查询、查看表结构/索引/执行计划
- 开发环境执行 migration，执行前展示 diff

**Git**
- `git status` / `git diff` / `git log` / `git stash`
- `git add` + `git commit`（commit message 用英文，遵循 Conventional Commits）

**测试 & Lint**
- 运行 `pytest`、生成覆盖率报告
- 新增功能时自动补写单元测试


## 与 market_data 项目的关系

| 维度 | market_data | market_analysis |
|------|-------------|-----------------|
| 职责 | 数据采集、存储 | 信号生成、展示 |
| 写入表 | daily_bars_split_adjusted 等 | signals |
| 读取表 | —— | daily_bars_split_adjusted |
| 代码依赖 | 无 | 无（只共享数据库） |
| 运行频率 | 每日一次（市场收盘后） | 每日一次（market_data 更新后） |

cron 建议顺序：
```
05:00  market-data update      # 拉取行情
05:30  market-analysis run-sr  # 跑 SR 分析（OPTIONS_ACTIVE 成分股）
```
## 遇到不确定情况的处理方式

1. 先分析问题，列出可能方案
2. 多方案时，选择落地可行性最优（时间、人工干预、资源综合最优）的方案
3. 修改后自动测试；测试通过则进入下一任务
4. 测试失败则自动分析，最多迭代 **3 轮**
5. 3 轮后仍未解决：停下来分析原因，向用户询问建议
6. 单个任务超过 **5 分钟**无进展：停下汇报当前状态

## 回答要求

- 分析要客观有据，资料不足时主动向用户说明并提出需求
- 搜索互联网时优先使用高质量来源，给出引用链接
- 回答聚焦，不做无关扩展
