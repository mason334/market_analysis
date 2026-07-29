# trend_pattern_v4 指标设计规格

> 本文档是 `trend_pattern_v4` 指标定义、讨论状态和设计决策的唯一事实来源。
> 聊天记录用于讨论，本文档用于保存已经确认的结论、候选定义和未决问题。

## 1. 当前工作状态

- 最后更新：2026-07-26
- 当前阶段：第二阶段指标已实现，进入字段冗余审核和规格冻结
- 当前审核指标：审核字段冗余和最终持久化集合
- 下一审核指标：冻结 v4 第二阶段指标规格 v0.1
- 第一阶段状态：1～4 effective legs 的 40 个方向无关 `structure_code` 已实现并验证
- 生产状态：`trend_pattern_v4` 不替换 `trend_pattern_v3`；已写入独立表
  `trend_pattern_v4_daily`，`investment_dashboard` 尚未接入
- 当前主要未决问题：审核最终持久化集合并冻结第二阶段规格

新 session 开始时，应依次阅读：

1. `AGENTS.md`
2. 本文档的“当前工作状态”“已锁定设计决策”“指标总表”和“待审核队列”
3. 当前审核指标的详细规格
4. 与当前指标直接相关的实现代码

## 2. 已锁定设计决策

以下结论在后续讨论中视为当前基线。如需修改，必须更新本文档的决策日志。

1. 第一版只分析 split-adjusted daily close price。
2. Open、high、low、volume 属于后续辅助信息层，不进入第一版 close-path 核心分类。
3. 顶层描述分为：
   - 基础路径几何：价格去了哪里、路径走了多远、总波动多大、终点及 terminal leg 起点
     处于什么价格区域。
   - 时间组织：主要运动以什么顺序发生、平方运动主要发生在何时、是否集中于少数日期、
     movement 的时间位置如何。
4. 市场、行业、新闻和基本面归因不属于本模块。
5. 优先保存未经阈值离散化的原始数值型指标；分类标签和人类 pattern 名称属于派生层。
   这里的“数值型”只表示字段直接保留计算结果（例如 `path_efficiency = 0.731`），
   不表示该指标随时间平滑或在数学意义上处处连续。
6. 控制指标数量。能够由其他持久化字段稳定推导的指标，优先作为 derived metric，
   不重复持久化。
7. 自适应分段用于构建 effective legs 和方向无关结构指纹，不作为全部路径指标的计算基础。
8. 40 日和 60 日是两个独立观察尺度。跨尺度稳定性暂时用于验证，不进入第一版核心字段。
9. 数学公式必须在对应章节重新定义所有字母、下标、数据口径、单位和计算窗口。
10. 指标进入生产代码、数据库 Schema 或下游接口前，必须先达到 `accepted` 状态。
11. `trend_pattern_v4` 将输出独立的新版指标集合并形成新的数据表结构；评估 v4 字段冗余和
    持久化必要性时，只比较 v4 自身 accepted 字段，不以 `trend.py`、`trend_pattern.py`
    或旧表中的历史同义字段作为否决依据。
12. `lookback_bars = m` 表示窗口包含的 close-price observations 数量；daily return 数量
    为 `n = m - 1`。因此 40/60 bars 分别包含 39/59 个 daily return intervals。
13. v4 实现拆分为 `trend_pattern_v4_legs.py`、`trend_pattern_v4_structure.py` 和
    `trend_pattern_v4_metrics.py` 三个并列纯计算模块，由 pipeline 统一编排；v4 使用独立表
    `trend_pattern_v4_daily` 和独立 CLI `run-trend-pattern-v4-analysis`。

## 3. 状态定义

| 状态 | 含义 |
|---|---|
| `proposed` | 已提出候选定义，但尚未逐项审核 |
| `reviewing` | 当前正在审核 |
| `accepted` | 定义、边界情况和用途已确认 |
| `derived` | 有解释价值，但可由其他核心字段推导，不独立持久化 |
| `deferred` | 当前阶段不实现，保留未来研究可能 |
| `rejected` | 已明确不采用；原因必须记录在决策日志 |

## 4. 英文缩写、通用数据口径和符号

### 4.1 英文缩写和编号前缀

| 缩写或前缀 | 英文全称 | 本文档中的含义 |
|---|---|---|
| `OHLCV` | Open, High, Low, Close, Volume | 开盘价、最高价、最低价、收盘价和成交量 |
| `CLI` | Command-Line Interface | 命令行接口 |
| `ID` | Identifier | 指标或条目的唯一编号 |
| `G` | Geometry | 基础路径几何指标的编号前缀，例如 `G01` |
| `T` | Temporal Organization | 时间组织指标的编号前缀，例如 `T01` |
| `D`（编号前缀） | Derived | 派生指标的编号前缀，例如 `D01`；不要与公式中的净位移 `D` 混淆 |
| `V` | Validation Candidate | 验证性候选指标的编号前缀，例如 `V01`；当前均为延期研究项 |
| `RV` | Realized Volatility | 已实现波动率；本文档不再用它表示二次变差，避免与 `QV` 混淆 |

### 4.2 通用数学符号

以下符号只提供全局索引。每个具体公式仍必须在其指标章节重新说明相关定义和英文名称。

| 符号 | 英文名称 | 定义 | 单位或范围 |
|---|---|---|---|
| `C_t` | Close Price at Time `t` | lookback window 内第 `t` 个 split-adjusted daily close | 价格，`C_t > 0` |
| `x_t` | Log Price at Time `t` | `log(C_t)`，第 `t` 个 log price | log price |
| `m` | Number of Close Observations | lookback window 内 close-price observations 数量，即 `lookback_bars` | 40 或 60 |
| `n` | Number of Return Intervals | 窗口内 daily return 的数量；价格点数量为 `n + 1` | 正整数 |
| `t` | Time Index | 价格点或收益间隔的时间索引 | `0..n` 或 `1..n` |
| `r_t` | Log Return at Interval `t` | `x_t - x_(t-1)`，第 `t` 个 daily log return | log return |
| `r_bar` | Mean Log Return | `n` 个 daily log returns 的算术平均值 | daily log return |
| `L_i` | Effective-Leg Fitted Log Return | 第 `i` 条 effective leg 的 fitted log return | log return |
| `k` | Effective-Leg Count | effective-leg 数量 | `1..4` |
| `i` | Effective-Leg Index | effective-leg 索引 | `1..k` |
| `tau` | Terminal-Leg Start Index | terminal effective leg 起点对应的价格点索引 | `0..n` |
| `I(condition)` | Indicator Function | 指示函数；条件成立为 1，否则为 0 | `0` 或 `1` |
| `D`（公式符号） | Net Log-Price Displacement | 窗口终点与起点之间的 log-price 净位移 | log return |
| `TV` | Total Variation | 每日 log-return 路径的总绝对运动 | 非负 log-return 量 |
| `QV` | Quadratic Variation | 每日 log returns 平方之和 | 非负 squared-log-return 量 |
| `O_h(y)` | Price Occupation Function | 路径落在中心 `y`、半宽 `h` 的 log-price 邻域内的价格点比例 | `0..1` |

本文档区分以下四个基础数学对象：

### 4.3 净位移 `D`（Net Log-Price Displacement）

定义：

```text
D = x_n - x_0 = sum(r_t), t = 1..n
```

其中：

- `D` 来自 Displacement，表示整个窗口的 net log return；
- `x_0` 是窗口起点 log price；
- `x_n` 是窗口终点 log price；
- `r_t` 是第 `t` 个 daily log return。

### 4.4 总变差 `TV`（Total Variation）

定义：

```text
TV = sum(abs(r_t)), t = 1..n
```

其中：

- `TV` 是 Total Variation，即每日 log-return 路径的总绝对运动；
- `abs(r_t)` 是第 `t` 个 daily log return 的绝对值。

### 4.5 二次变差 `QV`（Quadratic Variation）

定义：

```text
QV = sum(r_t ** 2), t = 1..n
```

其中：

- `QV` 是 Quadratic Variation，即 squared daily log returns 的总量；
- `QV` 不减去平均收益，因此不等同于通常定义的样本波动率；
- 本文档使用 `QV`，不再使用容易与 Realized Volatility（已实现波动率）混淆的 `RV` 符号。

### 4.6 价格占用函数 `O_h(y)`（Price Occupation Function）

候选定义：

```text
O_h(y) = mean(I(abs(x_t - y) <= h)), t = 0..n
```

其中：

- `O` 来自 Occupation，表示价格路径对某一区域的占用；
- `y` 是需要检查的某个 log-price 中心水平；
- 下标 `h` 是正数，表示 log-price 邻域的 half-width（半宽）；
- `O_h(y)` 是路径落在 `y - h` 到 `y + h` 区域内的价格点比例；
- 第一版不直接持久化完整占用函数，而是研究其低维摘要。

`O_h(y)` 只描述 close-price occupation，不能解释为成交量密度、订单簿深度或真实市场接受度。

## 5. 指标总表

### 5.1 基础路径几何

| ID | 指标 | 状态 | 依赖分段 | 当前持久化建议 |
|---|---|---|---:|---|
| G01 | `net_log_return` | `accepted` | 否 | 是 |
| G02 | `path_efficiency` | `accepted` | 否 | 是 |
| G03 | `historical_volatility` | `accepted` | 否 | 是 |
| G04 | `terminal_price_rank` | `accepted` | 否 | 是 |
| G05 | `terminal_price_position` | `accepted` | 否 | 是 |
| G06 | `terminal_leg_start_position` | `accepted` | 是 | 是 |
| G07 | `terminal_breakout_distance_vol` | `accepted` | 否 | 是 |
| G08 | `terminal_crossing_density` | `deferred` | 否 | 否 |

### 5.2 时间组织

| ID | 指标 | 状态 | 依赖分段 | 当前持久化建议 |
|---|---|---|---:|---|
| T01 | `start_direction` | `accepted` | 是 | 是 |
| T02 | `structure_code` | `accepted` | 是 | 是 |
| T03 | `squared_movement_time_position` | `accepted` | 否 | 是 |
| T04 | `squared_movement_concentration` | `accepted` | 否 | 是 |
| T05 | `terminal_leg_contribution` | `rejected` | 是 | 否 |

### 5.3 派生或延期指标

| ID | 指标 | 状态 | 原因 |
|---|---|---|---|
| D01 | `standardized_displacement` | `derived` | 可由 `net_log_return`、`historical_volatility`、固定 `basis = 252` 和 `n` 推导；与历史 Sharpe/t-statistic-like 量高度接近 |
| D02 | `direction_sequence` | `derived` | effective legs 必然交替，可由 `start_direction` 和 `structure_code` 重建 |
| D03 | `terminal_leg_range_share` | `derived` | 若与 G05/G06 使用同一实际 log-price range，可由两者的绝对差稳定推导 |
| V01 | `scale_stability` | `deferred` | 第一版只分别输出 40 日和 60 日结果，跨尺度一致性先用于质量验证 |
| V02 | 单窗口 return autocorrelation | `deferred` | 40/60 个数据点的估计误差较大，不作为当前路径描述核心轴 |
| V03 | variance ratio / Hurst / entropy / fractal dimension | `deferred` | 参数和样本长度敏感；只有证明存在增量信息后再考虑 |

## 6. 基础路径几何详细规格

### G01 — `net_log_return`

- 状态：`accepted`
- 目的：描述窗口终点相对起点的 signed log-price 净位移。
- 自适应分段依赖：否。

定义：

```text
net_log_return = x_n - x_0
```

其中：

- `x_0 = log(C_0)`，`C_0` 是窗口起点的复权收盘价；
- `x_n = log(C_n)`，`C_n` 是窗口终点的复权收盘价；
- `n` 是窗口内 daily return 的数量；
- 输出单位为 log return，取值可以为任意有限实数。

解释：

- 大于 0：终点高于起点；
- 小于 0：终点低于起点；
- 等于 0：终点等于起点。

边界与持久化结论：

- 持久化原始 `net_log_return`，不重复持久化可通过
  `exp(net_log_return) - 1` 推导的 simple return；
- `neutral` 阈值属于后续派生分类层，不改变 G01 原始数值，也不在 G01 中应用阈值；
- 若窗口价格点不足，或任一所需 close 缺失、非有限或小于等于 0，则
  `net_log_return = NULL`；
- 手算示例：当 `C_0 = 100`、`C_n = 110` 时，
  `net_log_return = log(110) - log(100) = log(1.1) ≈ 0.09531`；
- flat 边界示例：当 `C_0 = C_n > 0` 时，`net_log_return = 0`；
- 达到持久化标准。它是 v4 基础路径几何的原始 signed displacement，不能由其他 v4
  accepted 字段稳定推导，并且是 D01 等派生量及后续方向解释的基础输入；
- v4 新表直接使用字段名 `net_log_return`；不因旧 `trend_daily.actual_log_return` 存在
  数值等价字段而取消持久化，因为 v4 数据契约不依赖旧趋势识别表。

### G02 — `path_efficiency`

- 状态：`accepted`
- 目的：描述每日 close 路径的总绝对运动中，有多少转化为起点到终点的净位移。
- 自适应分段依赖：否。

定义：

```text
D = x_n - x_0
TV = sum(abs(r_t)), t = 1..n
path_efficiency = abs(D) / TV
```

其中：

- `D` 是 Net Log-Price Displacement（净 log-price 位移），即窗口 net log return；
- `r_t = x_t - x_(t-1)` 是第 `t` 个 daily log return；
- `TV` 是 Total Variation（总变差），即所有 daily log returns 绝对值之和；
- 当 `TV = 0` 时，路径内所有 daily log returns 均为 0，定义 `path_efficiency = 0`；
- 若窗口价格点不足，或任一所需 close 缺失、非有限或小于等于 0，则
  `path_efficiency = NULL`；
- 输出理论范围为 `0..1`。

解释：

- 接近 1：路径大部分运动方向一致；
- 接近 0：路径包含大量相互抵消的往返运动。

手算示例与持久化结论：

- 当 daily log returns 为 `[0.02, -0.01, 0.03]` 时，`D = 0.04`、`TV = 0.06`，
  `path_efficiency = 2 / 3 ≈ 0.6667`；
- 当全部 daily log returns 为 0 时，`D = 0`、`TV = 0`，按边界定义输出 `0`；
- 达到持久化标准。它回答“每日 close 路径的总绝对运动中有多少转化为净位移”这一
  独立问题，不能由 G01、G03、G04、G05、G07、T03 或 T04 稳定推导；
- 不依赖自适应分段或经验阈值，输出无量纲，40/60 日窗口具有相同解释；
- 持久化 `path_efficiency`，不单独持久化公式中间量 `TV`；
- effective-leg 版本保留为未来待审核变体，但它依赖分段并描述去噪后的结构效率，
  不属于当前 G02 daily-close 核心字段，也不阻塞当前字段持久化。

### G03 — `historical_volatility`

- 状态：`accepted`
- 目的：采用与 thinkorswim `HistoricalVolatility` 相同的口径，描述窗口内 daily log
  return 的年化样本标准差。
- 自适应分段依赖：否。

定义：

```text
r_bar = sum(r_t) / n, t = 1..n
s_daily = sqrt(
    sum((r_t - r_bar) ** 2) / (n - 1)
), t = 1..n
historical_volatility = 100 * sqrt(b) * s_daily
```

其中：

- `r_t` 是 Log Return at Interval `t`，即第 `t` 个 daily log return；
- `r_bar` 是 Mean Log Return，即窗口内 `n` 个 daily log returns 的算术平均值；
- `n` 是 daily return 数量，公式要求 `n >= 2`；
- `s_daily` 是减去均值后的 daily log-return 样本标准差，方差分母为 `n - 1`；
- `b` 是 Annualization Basis（年化基数），由于输入为交易日级 close，v4 固定为 `252`；
- `100` 将年化小数波动率转换为百分比数值；
- `historical_volatility` 输出单位为年化百分比点，例如 `20.0` 表示年化波动率 `20%`；
- 理论范围为大于或等于 `0` 的有限实数。
- 当窗口内所有 daily log returns 相同时，样本标准差为 0，定义
  `historical_volatility = 0`；依赖波动率作分母的指标应在各自规格中定义零分母语义。
- 当 `n < 2`，或任一所需 close 缺失、非有限或小于等于 0 时，定义
  `historical_volatility = NULL`。

手算示例与持久化结论：

- 当 daily log returns 为 `[0.01, -0.01]` 时，`r_bar = 0`，
  `s_daily = sqrt(0.0002) ≈ 0.0141421`，因此
  `historical_volatility = 100 * sqrt(252) * s_daily ≈ 22.45`；
- 当 daily log returns 为 `[0, 0]` 时，`historical_volatility = 0`；
- 达到持久化标准。它保留路径运动的绝对波动尺度，不能由 G01 净位移、G02 路径效率、
  T03 运动时间位置或 T04 运动集中度稳定推导；尤其 T03、T04 使用归一化 movement weights，
  已消除了绝对运动幅度；
- 不依赖自适应分段或经验阈值；`basis = 252` 固定后，40/60 日窗口均输出同口径的年化
  百分比点，窗口长度差异只影响估计所用样本；
- 持久化 `historical_volatility`，不另行持久化可由它还原的中间量
  `s_daily = historical_volatility / (100 * sqrt(252))`。

平台口径对照：

- thinkorswim `HistoricalVolatility` 同样先计算 logarithmic return，并减去窗口内平均
  logarithmic return；因此它不是未中心化的 `sqrt(sum(r_t ** 2))`。
- thinkorswim `HistoricalVolatility` 的官方公式使用样本标准差口径，方差分母为
  `n - 1`，并使用由 `basis` 决定的尺度系数；当前 G03 采用相同算法，并将 daily
  `basis` 固定为 `252`。
- thinkScript 通用 `StDev` 函数使用分母 `n`，但不能用该函数的实现反推
  `HistoricalVolatility` 的独立公式。
- 官方参考：[HistoricalVolatility](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/G-L/HistoricalVolatility.html)。

### G04 — `terminal_price_rank`

- 状态：`accepted`
- 目的：描述终点价格在窗口此前价格分布中的相对位置。
- 自适应分段依赖：否。

定义：

```text
terminal_price_rank = (
    sum(I(x_t < x_n) + 0.5 * I(x_t == x_n)), t = 0..n-1
) / n
```

其中：

- `x_n` 是窗口终点 log price；
- `x_t` 是终点之前第 `t` 个 log price；
- `n` 是终点之前的价格点数量，同时也是窗口 daily return 数量；
- `I(condition)` 是 Indicator Function（指示函数）；
- 输出范围为 `0..1`；
- 计算时排除终点自身，避免终点必然贡献一次匹配。

- 相同价格采用半权重的 mid-rank；
- split-adjusted close 的浮点相等判断不增加额外容差，只有数值精确相等时才计入
  `I(x_t == x_n)`。

### G05 — `terminal_price_position`

- 状态：`accepted`
- 目的：补充 G04 只保留排序而不保留距离的限制，描述终点 log price 在完整窗口
  log-price range 中的归一化距离位置。
- 自适应分段依赖：否。

定义：

```text
x_min = min(x_t), t = 0..n
x_max = max(x_t), t = 0..n
R_x = x_max - x_min

terminal_price_position = (x_n - x_min) / R_x
```

其中：

- `x_t = log(C_t)` 是窗口内第 `t` 个 split-adjusted daily close 的 log price；
- `C_t` 是窗口内第 `t` 个 split-adjusted daily close，且 `C_t > 0`；
- `t` 是价格点索引，取值为 `0..n`；
- `n` 是 daily return 数量，因此完整窗口包含 `n + 1` 个价格点；
- `x_min` 和 `x_max` 分别是包含终点 `x_n` 在内的完整窗口最低和最高 log price；
- `R_x` 是完整窗口 log-price range，单位为 log return；
- 当 `R_x > 0` 时，输出范围为 `0..1`：`0` 表示终点位于窗口最低点，`1` 表示终点
  位于窗口最高点，中间值表示终点在最低点到最高点之间的相对距离位置；
- 使用 log price 而不是原始 close，使位置按比例价格距离定义，并避免绝对价格尺度影响。

与 G04 的区别：

- `terminal_price_rank` 只取决于有多少历史价格低于、等于或高于终点，不考虑价格间距；
- `terminal_price_position` 使用终点到窗口最低点的 log-price 距离，占完整 log-price
  range 的比例，因而保留归一化 distance information；
- 本字段不保留 `R_x` 本身的绝对大小。不同窗口即使 range 大小差异显著，只要终点的
  相对位置相同，`terminal_price_position` 也可以相同。
- 当 `R_x = 0`，即窗口内所有 close 完全相同时，定义
  `terminal_price_position = 0.5`，表示没有可区分的高低位置；
- 只持久化 `terminal_price_position`，不单独持久化计算中间量 `R_x`。

### G06 — `terminal_leg_start_position`

- 状态：`accepted`
- 目的：描述 terminal effective leg 从整个窗口价格区间的什么相对位置开始。
- 自适应分段依赖：是；分段负责识别 terminal effective leg 的起点索引。

定义：

```text
x_min = min(x_t), t = 0..n
x_max = max(x_t), t = 0..n
R_x = x_max - x_min

terminal_leg_start_position = (x_tau - x_min) / R_x
```

其中：

- `x_t = log(C_t)` 是窗口内第 `t` 个 split-adjusted daily close 的实际 log price；
- `C_t` 是窗口内第 `t` 个 split-adjusted daily close，且 `C_t > 0`；
- `t` 是价格点索引，取值为 `0..n`；
- `tau` 是自适应分段和 effective-leg 合并规则识别出的 terminal effective leg 起点索引：
  它取最后一条 effective directional leg 所合并的第一条源 segment 的实际价格起点；按照
  当前 `[start, end)` 分段约定，源 segment 的 `start = 0` 时 `tau = 0`，否则
  `tau = start - 1`；
- `x_tau` 是该起点日期的实际 log price，而不是 fitted log price；
- `x_min`、`x_max` 和 `R_x` 与 G05 使用相同的完整窗口实际 log-price 口径；
- 当 `R_x > 0` 时，理论范围为 `0..1`：`0` 表示从窗口最低价格处开始，`1` 表示从
  窗口最高价格处开始；
- 末端存在被 effective-leg 规则过滤的 flat segments 时，不改变 `tau`；动作终点仍固定为
  窗口当前终点 `x_n`，因此该字段描述“从最后一条有效方向运动开始至今”的 terminal phase；
- 当 `R_x = 0` 时不存在可识别的 effective directional leg，定义
  `terminal_leg_start_position = NULL`；
- 若不能识别 terminal effective leg，或任一所需 close 缺失、非有限或小于等于 0，定义
  `terminal_leg_start_position = NULL`。

与 G05 和派生动作量的关系：

```text
signed_terminal_leg_range_share = (
    terminal_price_position - terminal_leg_start_position
)

terminal_leg_range_share = abs(signed_terminal_leg_range_share)
```

其中：

- `terminal_price_position` 是 G05 定义的终点在完整窗口 log-price range 中的位置；
- `signed_terminal_leg_range_share` 的正负号表示从 terminal leg 起点到窗口终点的实际
  价格方向，绝对值表示该运动覆盖完整窗口 range 的比例；
- `terminal_leg_range_share` 是 D03 派生量，不重复持久化；
- 上述代数关系要求 G05、G06 和 D03 使用同一实际 log-price range，并以窗口当前终点
  `x_n` 作为动作终点。

手算示例与持久化结论：

- 当实际 log prices 为 `[0, 0.04, 0.02, 0.08]`，且 terminal effective leg 从
  `tau = 2` 开始时，`x_min = 0`、`x_max = 0.08`、`R_x = 0.08`，因此
  `terminal_leg_start_position = 0.02 / 0.08 = 0.25`；G05 为 `1`，D03
  `terminal_leg_range_share = abs(1 - 0.25) = 0.75`；
- 达到持久化标准。G05 只保存终点位置，T01/T02 只保存方向和结构类别，均无法恢复
  terminal effective leg 的实际起点价格位置；
- 本字段依赖自适应分段和 effective-leg 阈值，但这些规则同时是 v4 结构定义的组成部分，
  依赖关系明确且可随 calculation version 固定；
- 40/60 日结果分别表示各自 lookback price range 内的相对起点位置，不宣称完全消除
  lookback 对 range 的影响；
- 持久化 `terminal_leg_start_position`；D03 `terminal_leg_range_share` 由 G05 和 G06
  稳定推导，不重复持久化。

### G07 — `terminal_breakout_distance_vol`

- 状态：`accepted`
- 目的：描述终点是否超出此前窗口价格区间，以及超出距离相当于多少日收益标准差。
- 自适应分段依赖：否。

定义：

```text
prior_high = max(x_t), t = 0..n-1
prior_low = min(x_t), t = 0..n-1

up_extension = max(x_n - prior_high, 0)
down_extension = max(prior_low - x_n, 0)

terminal_breakout_distance_vol = (
    up_extension - down_extension
) / (historical_volatility / (100 * sqrt(b)))
```

其中：

- `x_n` 是窗口终点 log price；
- `prior_high` 是终点之前 log prices 的最大值；
- `prior_low` 是终点之前 log prices 的最小值；
- `historical_volatility` 是 G03 定义的年化百分比 Historical Volatility；
- `b` 是 G03 固定为 `252` 的 Annualization Basis；
- `historical_volatility / (100 * sqrt(b))` 将 G03 还原为 daily log-return 样本标准差；
- 输出为无量纲 signed distance；
- 正数表示向上超出此前区间，负数表示向下超出，0 表示仍在此前区间内。
- 理论取值范围为任意有限实数；绝对值越大，表示终点超出此前区间的距离相对于日波动率
  越大；
- 当 `up_extension = 0` 且 `down_extension = 0` 时，无论波动率是否为 0，均定义
  `terminal_breakout_distance_vol = 0`，表示终点未超出此前区间；
- 当存在向上或向下 extension，但 `historical_volatility = 0` 时，标准化分母为 0，
  定义输出为 `NULL`，不输出无穷值；
- 当 G03 为 `NULL`，或任一所需 close 缺失、非有限或小于等于 0 时，定义
  `terminal_breakout_distance_vol = NULL`；
- 分母固定使用一个交易日尺度的样本标准差，不再引入额外的 `m` 日尺度参数。
- G03 使用包含终点收益在内的同一完整窗口，因此本字段是事后路径描述量，不解释为只用
  突破前信息计算的 ex-ante breakout surprise。

手算示例与持久化结论：

- 当 log prices 为 `[0, 0.01, 0, 0.03]` 时，daily log returns 为
  `[0.01, -0.01, 0.03]`，其样本标准差为 `0.02`；`prior_high = 0.01`，
  `up_extension = 0.02`，因此 `terminal_breakout_distance_vol = 1`；
- 当终点位于 `prior_low..prior_high` 内时，上下 extension 均为 0，输出 `0`；
- 达到持久化标准。它回答“终点突破此前区间多远”这一独立问题；G04/G05 只能说明终点的
  排序或归一化位置，突破时通常饱和在 `0` 或 `1`，无法恢复超出区间的距离；G03 只提供
  波动尺度，也无法恢复 breakout extension；
- 不依赖自适应分段或经验阈值，输出无量纲，40/60 日窗口具有相同的标准差倍数解释；
- 持久化最终字段，不单独持久化 `prior_high`、`prior_low`、`up_extension` 或
  `down_extension` 等计算中间量。

### G08 — `terminal_crossing_density`

- 状态：`deferred`
- 目的：描述终点价格水平在此前 close-price 路径中被反复穿越的程度。
- 自适应分段依赖：否。

候选公式：

```text
crossing_t = I((x_(t-1) - x_n) * (x_t - x_n) <= 0)
terminal_crossing_density = sum(crossing_t, t = 1..n-1) / (n - 1)
```

其中：

- `x_n` 是窗口终点 log price；
- `x_(t-1)` 和 `x_t` 是终点之前相邻的两个 log prices；
- `crossing_t = 1` 表示连接两个相邻 close 的线段穿过或接触终点价格水平；
- 最后一段 `x_(n-1) -> x_n` 不参与计算，否则会产生一次必然穿越；
- 候选公式要求 `n >= 2`；
- 输出范围为 `0..1`。

延期原因：

1. 仅在终点位于此前 close-price range 内、即 G07 为 `0` 时提供信息，适用范围较窄。
2. 与 G02 `path_efficiency` 的全局往返信息相关，控制 G02、G04 和 G05 后的增量价值
   尚未验证。
3. 对终点精确价格水平较敏感，crossing 数量可能随终点的小幅变化发生跳变。
4. close-to-close 线段穿越不能直接解释为支撑、成交密度或市场接受度；连续相等价格还
   可能造成重复计数。
5. 第一版不持久化；未来只有在证明存在稳定增量信息后，才重新审核 exact crossing、
   visit 或 volatility-scaled price band 等定义。

## 7. 时间组织详细规格

### T01 — `start_direction`

- 状态：`accepted`
- 目的：保存方向归一化之前第一条 effective leg 的实际方向。
- 自适应分段依赖：是。

定义：

```text
start_direction = "up"   if L_1 > 0
start_direction = "down" if L_1 < 0
```

其中：

- `L_1` 是第一条 Effective-Leg Fitted Log Return，即过滤 flat segments 并合并连续同方向
  segments 后的第一条 effective leg fitted log return；
- effective leg 不允许为 0；
- `start_direction` 与方向无关的 `structure_code` 组合后，可以重建实际方向序列。

### T02 — `structure_code`

- 状态：`accepted`
- 目的：保存 1～4 effective legs 在方向镜像归一化后的结构指纹。
- 自适应分段依赖：是。

令：

- `k` 是 Effective-Leg Count，即 effective-leg 数量，取值为 `1..4`；
- `L_i` 是第 `i` 条 Effective-Leg Fitted Log Return；字母 `L` 表示 Leg；
- `A_i = abs(L_i)` 是第 `i` 条 leg 的 Amplitude（绝对振幅）；字母 `A` 表示 Amplitude；
- `tau`（希腊字母 tau，`τ`）是 `pivot_retest_tolerance` 阈值，当前默认值为 `0.25`；
- `delta_i`（希腊字母 delta，`δ`）是第 `i` 条 leg 与前一条 leg 的对称相对振幅差异。

关系公式：

```text
delta_i = abs(A_i - A_(i-1)) / max(A_i, A_(i-1)), i = 2..k
```

关系分类：

```text
retest   if delta_i <= tau
short_of if delta_i > tau and A_i < A_(i-1)
break    if delta_i > tau and A_i > A_(i-1)
```

编码使用 `S / R / B` 分别表示 `Short of / Retest / Break`。例如：

```text
L3-RB
```

表示 3 条 effective legs，第二条相对第一条为 retest，第三条相对第二条为 break。

1～4 条 legs 分别有 `1 / 3 / 9 / 27` 个方向无关结构，总计 40 个。

### T03 — `squared_movement_time_position`

- 状态：`accepted`
- 目的：描述 squared daily movements 主要发生在窗口前段还是后段。
- 自适应分段依赖：否。

定义：

```text
QV = sum(r_t ** 2), t = 1..n
w_t = (r_t ** 2) / QV
u_t = (t - 0.5) / n

squared_movement_time_position = sum(u_t * w_t, t = 1..n)
```

其中：

- `r_t` 是 Log Return at Interval `t`，即第 `t` 个 daily log return；
- `QV` 是 Quadratic Variation（二次变差），即窗口 squared daily log returns 的总量；
- `w_t` 是 Squared-Movement Weight，即第 `t` 个 squared return 占 `QV` 的比例；
- `u_t` 是 Normalized Time Position，即第 `t` 个 return interval 在窗口中的标准化时间中点；
- 第 `t` 个 return interval 在标准化窗口中覆盖 `[(t - 1) / n, t / n]`，因此其中点为
  `u_t = (t - 0.5) / n`；这里 `t` 减去 `0.5` 来自取区间中点，不是经验调参；
- 当 squared movements 均匀分布时，`w_t = 1 / n`，此时
  `sum(u_t * w_t, t = 1..n) = 0.5`；直接保留该加权时间重心，不再额外减去 `0.5`
  或乘以系数 `2`；
- 精确理论范围为 `1 / (2n) .. 1 - 1 / (2n)`；40 个 closes 对应 `n = 39`，范围约为
  `0.01282..0.98718`；60 个 closes 对应 `n = 59`，范围约为 `0.00847..0.99153`；
- 接近 `0` 表示 squared movements 集中在窗口开头附近，`0.5` 表示运动时间重心位于
  窗口中央，接近 `1` 表示 squared movements 集中在窗口结尾附近；
- 新公式与旧公式 `2 * (new_value - 0.5)` 之间是一一对应的线性变换，不损失信息，
  但直接输出标准化时间重心更容易解释。
- 当 `QV = 0` 时，窗口不存在 squared movement，定义
  `squared_movement_time_position = 0.5`，避免把无运动误解为运动集中在窗口开头；
- 单日 shock 可能使时间重心接近理论边界；该值仍按发生时间解释，并结合 T04
  `squared_movement_concentration` 判断运动是否由少数日期主导。

### T04 — `squared_movement_concentration`

- 状态：`accepted`
- 目的：描述 squared daily movements 是均匀分布，还是集中在少数交易日。
- 自适应分段依赖：否。

定义：

```text
QV = sum(r_t ** 2), t = 1..n
w_t = (r_t ** 2) / QV
H_w = sum(w_t ** 2), t = 1..n
effective_movement_day_ratio = 1 / (n * H_w)

squared_movement_concentration = 1 - effective_movement_day_ratio
```

其中：

- `r_t` 是 Log Return at Interval `t`，即第 `t` 个 daily log return；
- `QV` 是 Quadratic Variation（二次变差），即窗口 squared daily log returns 的总量；
- `w_t` 是 Squared-Movement Weight，即第 `t` 个 squared return 占 `QV` 的比例；
- `H_w` 是 Movement-Weight Herfindahl Concentration Index，即 squared-movement 权重的
  平方和；
- `n` 是 daily return 数量，公式要求 `n >= 2`；
- `1 / H_w` 是 Effective Movement Day Count，即相当于多少个等权日期共同贡献
  squared movements；
- `effective_movement_day_ratio = 1 / (n * H_w)` 是有效贡献日期数占整个窗口日期数的比例；
- `squared_movement_concentration` 等于 1 减去有效贡献日期比例，数值越大表示 movement
  越集中在少数日期；
- 精确理论范围为 `0 .. 1 - 1 / n`：完全均匀时为 `0`，单日独占时为 `1 - 1 / n`；
  40 个 closes 对应 `n = 39`，上限约为 `0.97436`；60 个 closes 对应 `n = 59`，
  上限约为 `0.98305`；
- 例如权重为 `[0.5, 0.5, 0, 0]` 时，`H_w = 0.5`，有效贡献日期比例为 `0.5`，
  `squared_movement_concentration = 0.5`；权重为 `[0.4, 0.6, 0, 0]` 时，集中度约为
  `0.5192`；
- 当 `QV = 0` 时，窗口不存在 squared movement，定义
  `squared_movement_concentration = 0`；
- 本指标使用全部日期的权重分布；相比 `max(r_t ** 2) / QV` 只观察最大单日贡献，
  它能区分其余 movement 是分散在许多日期还是仍集中在少数日期。

持久化结论：

- 达到持久化标准。它回答“movement 是否由少数日期主导”这一独立问题，与 T03 的
  时间位置维度互补；
- 不能由 G03 Historical Volatility、T03 时间位置或其他 accepted 字段稳定推导；
- 不依赖自适应分段或经验阈值，40/60 日窗口具有相同的有效贡献日期比例语义；
- 持久化 `squared_movement_concentration`，不额外持久化可由它和 `n` 推导的
  `effective_movement_day_ratio` 或 Effective Movement Day Count。

### T05 — `terminal_leg_contribution`

- 状态：`rejected`
- 原目的：描述最后一条 effective leg 的方向，以及其绝对运动占全部 effective-leg 运动的比例。
- 自适应分段依赖：是。

已淘汰公式：

```text
terminal_leg_contribution = L_k / sum(abs(L_i)), i = 1..k
```

其中：

- `k` 是 Effective-Leg Count，即 effective-leg 数量；
- `L_k` 是最后一条 Effective-Leg Fitted Log Return；
- `L_i` 是第 `i` 条 Effective-Leg Fitted Log Return；
- 分母是所有 effective-leg 绝对振幅之和；
- 输出范围为 `-1..1`；
- 正负号表示最后一条 leg 方向，绝对值表示其路径贡献比例。

淘汰原因：

1. 分母随 effective-leg 数量和 lookback window 内累计往返运动增加，导致相同 terminal leg
   在更长或更复杂的窗口中被机械稀释；
2. 当 `k = 1` 时恒为 `-1` 或 `1`，没有幅度区分能力；
3. 正负方向可由 `start_direction + structure_code` 推导，存在重复；
4. 由 G06 `terminal_leg_start_position` 取代；terminal leg 相对完整窗口 price range 的
   动作量由 G05 与 G06 派生，不再以全部 effective-leg 绝对运动之和作为分母。

## 8. 派生指标定义

### D01 — `standardized_displacement`

- 状态：`derived`
- 不建议独立持久化。

候选公式：

```text
standardized_displacement = net_log_return / (
    (historical_volatility / (100 * sqrt(b))) * sqrt(n)
)
```

其中：

- `net_log_return` 是 G01 定义的窗口 net log return；
- `historical_volatility` 是 G03 定义的年化百分比 Historical Volatility；
- `b` 是 G03 固定为 `252` 的 Annualization Basis；
- `historical_volatility / (100 * sqrt(b))` 是由 G03 还原的 daily log-return 样本标准差；
- `n` 是窗口 daily return 数量；
- 输出是无量纲 signed ratio；
- 未扣除无风险收益，不应直接命名为 Sharpe ratio；
- 在忽略无风险收益时，它与 daily historical Sharpe 乘以 `sqrt(n)` 代数等价，
  也接近传统均值 t-statistic 的形式。

已淘汰的旧候选定义：

```text
net_log_return / sqrt(QV)
```

其中 `QV` 是 Quadratic Variation（二次变差），即未中心化的 squared daily log returns 总量。
该旧定义不能解释为常规 volatility-adjusted displacement，
也不应与当前 `standardized_displacement` 共用同一符号或字段名。

## 9. 指标审核检查表

每个指标从 `reviewing` 变为 `accepted` 前，必须回答：

1. 它具体回答哪个不可替代的问题？
2. 所有输入数据、字母、下标、统计口径和单位是否定义清楚？
3. 理论范围和典型数值范围是什么？
4. flat path、零波动、缺失值和窗口过短时如何处理？
5. 是否依赖自适应分段或阈值参数？
6. 是否能由其他 accepted 字段稳定推导？
7. 是否与其他指标高度重复？
8. 40 日和 60 日之间是否具有可比较含义？
9. 是否需要持久化，还是只在展示时派生？
10. 是否有至少一个手算示例和一个边界测试？

## 10. 待审核队列

严格按一次一个指标的顺序讨论：

1. 审核字段冗余和最终持久化集合
2. 冻结 v4 第二阶段指标规格 v0.1
3. 再设计人类可读的派生状态和 pattern 名称

## 11. 决策日志

### 2026-07-24

- 完成 1～4 effective legs 的 40 个方向无关结构表。
- `structure_code` 使用 `L{leg_count}-{S/R/B sequence}`。
- 当前 `pivot_retest_tolerance` 默认值为 `0.25`。

### 2026-07-25 至 2026-07-26

- 第二阶段不直接从 40 个结构映射人类 pattern 名称，先建立可检验的路径描述轴。
- close-price 核心描述确定为“基础路径几何 + 时间组织”两层。
- 市场、行业、新闻和基本面不进入当前 K 线路径分类。
- OHLCV 辅助信息推迟到 close-price 分析完成之后。
- 明确基础数学对象为净位移、总变差、二次变差和价格占用；二次变差统一使用 `QV` 符号，
  避免与常规波动率混淆。
- 常规 daily return volatility 使用中心化的 daily log-return 样本标准差候选定义。
- `net_log_return / ((historical_volatility / (100 * sqrt(252))) * sqrt(n))` 与历史 Sharpe/t-statistic-like 量高度接近，
  当前降级为 derived metric。
- `net_log_return / sqrt(QV)` 是不同的未中心化量，旧讨论中曾与前一公式混用；当前不作为核心指标。
- 为控制数量，暂不采用 Hurst、entropy、fractal dimension、单窗口 autocorrelation 等指标。
- 新增英文缩写和数学符号索引；`D`、`TV`、`QV`、`O_h(y)` 等符号在标题、符号表和
  具体公式章节中同时给出英文名称，避免缩写与编号前缀混淆。
- `G01 net_log_return` 和 `G02 path_efficiency` 已逐项审核并标记为 `accepted`。
- `G02 path_efficiency` 在 `TV = 0` 时定义为 `0`；effective-leg 版本保留为待审核候选，未来再确定，当前不进入核心字段。
- `G03 historical_volatility` 达到持久化标准；只持久化年化百分比点口径的最终字段，
  不重复持久化可由它还原的 daily 样本标准差 `s_daily`。
- 已核对 thinkorswim `HistoricalVolatility` 官方公式：log return 减去均值，方差分母为
  `n - 1`；此前根据通用 `StDev` 函数推断分母为 `n` 的说法已更正。
- G03 字段名由 `return_volatility_daily` 改为 `historical_volatility`，采用 thinkorswim
  `HistoricalVolatility` 算法；daily 输入固定 `basis = 252`，输出为年化百分比点。
- `G03 historical_volatility` 已完成审核并标记为 `accepted`；零波动窗口输出 `0`，
  下游以波动率作分母时的零分母语义由对应指标单独定义。
- 新增 `G05 terminal_price_position`，使用终点在完整窗口 log-price range 中的
  归一化距离位置补充 G04 的 ordinal information。
- `G07 terminal_breakout_distance_vol` 已标记为 `accepted`：区间内输出 `0`；存在
  extension 但 Historical Volatility 为 0 时输出 `NULL`；标准化固定使用一日波动尺度。
- `G07 terminal_breakout_distance_vol` 达到持久化标准；G04/G05 的端点位置在突破时会
  饱和，不能替代 G07 保留的 signed volatility-scaled extension。
- `G05 terminal_price_position` 已标记为 `accepted`：flat range 输出 `0.5`；只持久化
  终点归一化位置，不单独持久化 `R_x`。
- `G04 terminal_price_rank` 已标记为 `accepted`：相同价格采用 mid-rank 半权重，
  split-adjusted close 的相等判断不增加浮点容差。
- `G08 terminal_crossing_density` 标记为 `deferred`：它提供终点价位的局部重复穿越信息，
  但适用范围窄、对精确价位敏感，并且相对 G02、G04、G05 的增量价值尚未验证。
- `T03 squared_movement_time_position` 已标记为 `accepted`：`u_t` 使用 return interval
  中点，并直接输出 `sum(u_t * w_t)` 作为标准化运动时间重心，不再做外层中心化和
  两倍缩放；均匀分布及 `QV = 0` 时输出 `0.5`，接近 `0/1` 分别表示偏窗口开头/结尾。
- T03 字段名由 `squared_movement_time_shift` 改为
  `squared_movement_time_position`，使名称与 `0..1` 附近的标准化时间位置语义一致。
- `T04 squared_movement_concentration` 已标记为 `accepted` 并达到持久化标准；使用
  `1 - 1 / (n * sum(w_t ** 2))` 表示 1 减去有效贡献日期比例，`QV = 0` 时输出 `0`。
- 确认 `trend_pattern_v4` 将形成独立的新版指标表；v4 字段持久化只按 v4 内部的信息
  独立性审核，不复用 `trend.py`、`trend_pattern.py` 或旧表字段。基于该原则，
  `G01 net_log_return` 达到持久化标准并确定写入 v4 新表。
- `G02 path_efficiency` 达到持久化标准并确定写入 v4 新表；effective-leg 版本仅作为
  未来待审核变体保留，不阻塞 daily-close 版本。
- 放弃 T05 signed `terminal_leg_contribution`：其分母随 leg 数量和 lookback 内往返运动
  增长，且方向信息重复；引入 G06 `terminal_leg_start_position`，与 G05 一起描述
  terminal leg 的窗口区间起点和动作量。原 breakout/crossing 指标顺延为 G07/G08。
- `G06 terminal_leg_start_position` 已标记为 `accepted` 并达到持久化标准；使用 terminal
  effective leg 第一条源 segment 的实际起点，trailing flat segments 不改变该起点，
  flat range 或无法识别 effective leg 时输出 `NULL`。D03 range share 由 G05/G06 派生。
- 锁定窗口计数口径：`lookback_bars` 是 close observations 数量，daily return 数量为
  `lookback_bars - 1`；40/60 bars 分别对应 39/59 个 daily returns。
- v4 已按独立模块和独立表落地；2026-07-07 受控快照写入 364 行，其中40/60 bars 各
  182 行、50 行无 effective leg，0 行跳过，所有已持久化指标均无范围违规。

## 12. Session 交接模板

结束一次讨论或准备切换 session 前，更新本节和“当前工作状态”：

```text
本次确认：
- <accepted / rejected / deferred 的指标和结论>

本次未决：
- <仍需验证的问题>

下一项：
- <下一个指标 ID 和名称>

相关代码：
- <需要阅读的文件和函数>

接口影响：
- <数据库 / CLI / investment_dashboard 是否受影响>
```

当前交接内容：

```text
本次确认：
- `G01 net_log_return` 已标记为 accepted。
- `G02 path_efficiency` 已标记为 accepted。
- `G03 historical_volatility` 已标记为 accepted，并确定持久化。
- `G04 terminal_price_rank` 已标记为 accepted。
- `G05 terminal_price_position` 已标记为 accepted。
- `G06 terminal_leg_start_position` 已标记为 accepted，并确定持久化。
- `G07 terminal_breakout_distance_vol` 已标记为 accepted，并确定持久化。
- `G08 terminal_crossing_density` 已标记为 deferred，不进入第一版核心持久化字段。
- `T03 squared_movement_time_position` 已标记为 accepted。
- `T04 squared_movement_concentration` 已标记为 accepted，并确定持久化。
- `T05 terminal_leg_contribution` 已标记为 rejected，由 G06 取代。

本次未决：
- G02 effective-leg 版本作为未来变体保留待审核，但不影响当前 daily-close 字段。

下一项：
- 审核字段冗余和最终持久化集合。

相关代码：
- src/market_analysis/indicators/trend_pattern_v4_legs.py
- src/market_analysis/indicators/trend_pattern_v4_structure.py
- src/market_analysis/indicators/trend_pattern_v4_metrics.py
- src/market_analysis/pipeline/run_trend_pattern_v4.py
- src/market_analysis/db/schema.py
- src/market_analysis/db/queries.py

接口影响：
- 新增 `trend_pattern_v4_daily` 表和 `run-trend-pattern-v4-analysis` CLI；不修改旧
  `trend_pattern_daily` 或 v3 CLI。`investment_dashboard` 尚未接入新表，当前页面无变化。
```
