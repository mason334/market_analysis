# 自适应分段算法开发记录

> 文档状态：已实施
> 当前初分段口径：`adaptive_segmentation_v3`
> 当前初分段方法：`continuous_piecewise_log_linear_deterministic_hybrid_bic`
> 当前精炼分段口径：`pivot_refined_segmentation_v2`
> 当前精炼分段方法：`pivot_seeded_continuous_piecewise_log_linear`
> 已实施方案：`MA-PIVOT-SEG v2.0.0`
> 最近更新：2026-08-15

## 1. 文档目的

本文档集中说明、记录和跟踪自适应趋势分段算法的开发。它用于：

- 固化当前已经实现的数学口径和数据语义；
- 记录长 lookback 下的性能问题及其规模；
- 区分已实现功能、已确认设计方向和仍待验证的候选方案；
- 跟踪自适应初分段与 Pivot 精炼分段的设计、验证、实施和下游影响；
- 为后续开发 session 提供统一交接入口。

本文档不是当前生产行为的替代说明。代码、测试、`AGENTS.md` 和数据库实际结构仍是当前行为的
最终依据；尚未标记为“已实现”的内容不得当作生产口径使用。

## 2. 当前工作状态

### 2.1 已实现：`adaptive_segmentation_v3` 与 `pivot_refined_segmentation_v2`

当前 `src/market_analysis/indicators/adaptive_segmentation.py` 已实现：

- 默认只计算 250 bars 生产 lookback；
- 候选数不超过预算时执行分批精确穷举；
- 超过预算时执行确定性 beam expansion、单断点全域优化和相邻双断点局部优化；
- 对每组断点执行全局连续分段 log-linear OLS；
- 使用 BIC 在不同分段数之间选择最终模型；
- 输出模型摘要、逐段指标以及拟合路径重建结果；
- 分段采用 `[start, end)`，后一段拥有 `start - 1 -> start` 的进入收益；
- 拟合路径在断点处连续，但不强制经过实际窗口端点。

当前默认配置为：

```yaml
indicators:
  adaptive_segmentation:
    lookbacks: [250]
    min_segment_bars: 5
    max_segments_cap: 10
    bic_penalty_multiplier: 3.0
    segment_classification:
      min_abs_fitted_log_return: 0.02
      min_linearity_r2: 0.35
      min_abs_vol_adjusted_trend: 0.75
    search:
      exact_candidate_budget: 100000
      exhaustive_batch_size: 2048
      beam_width: 8
      deterministic_seed_count: 4
      max_refinement_passes: 10
      rss_improvement_tolerance: 1.0e-10
      pair_refinement_enabled: true
      pair_refinement_radius: 5

  pivot_refinement:
    search_radius_bars: 5
    min_segment_bars: 5
```

250 bars 默认窗口的最大分段数上限为 10，候选空间超过预算时进入
`hybrid_approximate`。summary 明确保存搜索模式、全局最优保证、实际候选数、refinement
收敛状态、搜索配置和逐 `K` 诊断。随后 `pivot_segmentation.py` 对初始段分类、合并，只用
close 在 seed 左右各 5 bars 搜索确定性 pivot，并将连续重拟合结果写入两张独立新表。

### 2.2 版本来源与 v1 → v2 沿革

`adaptive_trend_v2` 不是根据文件名、日期或 Git commit message 自动推断出来的版本，而是代码
在运行时主动写出的计算口径标识：

```python
_METHOD = "continuous_piecewise_log_linear_exhaustive_bic"
_CALCULATION_VERSION = "adaptive_trend_v2"
```

`compute_adaptive_segmentation()` 会把这两个常量写入 summary 和每一条 segment，随后
`upsert_trend_segmentation_daily()` 将其分别持久化到 `trend_segmentation_daily` 和
`trend_segment_daily` 的 `method`、`calculation_version` 字段。因此数据库每条结果可以声明
自己使用的算法口径。

当前版本还由以下位置共同约束：

- `README.md` 记录当前方法和版本；
- `AGENTS.md` 记录当前表语义和计算口径；
- `tests/test_adaptive_trend.py` 断言 summary 的方法和版本；
- `tests/test_adaptive_trend_db_contract.py` 固化数据库写入契约；
- `trend_pattern_v4_daily` 使用 `source_segmentation_method` 和
  `source_segmentation_calculation_version` 追踪其输入分段版本。

Git 历史提供了可核验的版本沿革：

| 日期 | Git commit | 自适应版本 | 方法 | 核心语义 |
|---|---|---|---|---|
| 2026-07-15 | `52f6466` | `adaptive_trend_v1` | `piecewise_log_linear_dp_bic` | 各段独立线性回归，动态规划最小化各段 RSS 之和 |
| 2026-07-24 | `05e4e54` | `adaptive_trend_v2` | `continuous_piecewise_log_linear_exhaustive_bic` | 全局连续 linear spline，对合法断点组合精确穷举 |
| 2026-07-29 | 当前工作树 | `adaptive_trend_v3` | `continuous_piecewise_log_linear_deterministic_hybrid_bic` | 分批精确搜索 + 超预算确定性混合搜索，显式审计近似状态 |

v1 到 v2 不是单纯的性能重构，而是以下计算语义变化：

1. v1 为每段分别估计 slope 和 intercept，拟合路径可以在边界处跳跃；
2. v2 使用一套全局 hinge-basis OLS，只允许斜率在 knot 处变化，拟合水平必须连续；
3. v1 可以利用可加的独立段 RSS 做动态规划，v2 的全局连续性使各段成本不再独立可加，因此改为
   穷举合法边界后对整条路径全局拟合；
4. v1 的 BIC 参数数目为 `3K - 1`，v2 改为 `2K`；其中 `K` 是分段数，v2 的 `2K` 由
   `K + 1` 个连续样条系数和 `K - 1` 个 knot locations 构成；
5. v2 明确后一段拥有边界进入收益，保证逐段实际 log return 可以无遗漏、无重复地加回窗口收益；
6. v2 的段内 slope、R² 和 fitted return 来自同一条全局连续拟合，不再对每段重新独立回归；
7. v2 增加最大单日变化及其日期、bar index、绝对路径占比，并支持 fitted path 重建。

2026-07-24 的 commit message 提到“v3版的 segment 和 pattern 识别”，但 commit message 不是算法
版本的事实来源：该提交内 `trend_pattern` 使用 v3 口径，而 `adaptive_trend.py` 明确写入
`adaptive_trend_v2`。判断自适应分段版本应以代码常量、持久化字段、测试契约和对应语义为准。

在本文档建立之前，项目没有独立的自适应分段版本历史文档；README 只记录当前口径，完整
v1 → v2 差异主要存在于 Git 历史。本节开始作为该算法的正式版本沿革记录。

后续版本管理采用以下规则：

- `method` 标识主要拟合和搜索方法，例如独立段动态规划、连续模型精确穷举或确定性近似搜索；
- `calculation_version` 标识完整计算契约，包括边界语义、BIC 口径、逐段指标和异常值语义；
- 版本号由开发者显式决定并写入代码，不是自动生成的代码哈希；
- 只改变 batch size、向量化方式或内存布局，且经过测试证明输出语义和数值结果等价时，不必
  升级计算版本；
- 改变连续性约束、搜索是否保证全局最优、BIC 参数计数、边界收益归属、指标公式或空值语义时，
  必须升级 `calculation_version`；
- 改变生产默认参数但不改变公式时，结果行已经持久化具体参数，通常不单独升级计算版本，但
  必须记录配置变化并评估下游分布影响；
- 引入 approximate 搜索会改变“全局最优保证”，即使 fitted model 形式不变，也应使用新的
  method 和 calculation version；
- 版本升级时必须同步更新代码常量、README、本文档、测试契约和所有下游 source-version 检查。

### 2.3 已识别问题

v2 会先收集固定分段数下的全部合法边界，再一次性构造批量设计矩阵。随着 lookback 和
`max_segments` 增加：

1. 合法断点组合数按组合数量增长；
2. 一次性设计矩阵、hinge 数组和 OLS 中间数组会占用大量内存；
3. 继续使用完整精确穷举会逐渐不适合 dashboard 的交互式计算；
4. 单纯提高 `max_segments` 可能改变 `trend_pattern_v3/v4` 的下游结构语义。

### 2.4 当前开发目标

v3 已实现可审计、确定性、分层退化搜索；当前目标转为：

- 规模可控时保持精确穷举；
- 精确穷举改为分批处理，控制峰值内存；
- 规模过大时使用确定性的候选搜索；
- 明确区分 exact 与 approximate 结果；
- 持续扩大精确对照样本，监控近似搜索质量；
- 完成 `investment_dashboard` 对单个 symbol 使用可调参数展示 fitted curve 的联调。

## 3. 当前数学模型与不变量

### 3.1 输入和索引

设：

- `n` 为当前 lookback 内 close observations 数量，单位为 bars；
- `P_i` 为第 `i` 个 close price，必须有限且大于 0；
- `y_i = ln(P_i)` 为第 `i` 个 log price；
- `i = 0, ..., n - 1` 为窗口内 bar index；
- `K` 为候选分段数量；
- `m` 为 `min_segment_bars`；
- `b_0 = 0 < b_1 < ... < b_K = n` 为半开区间边界；
- 每段必须满足 `b_j - b_(j-1) >= m`，其中 `j = 1, ..., K`。

每个分段为 `[b_(j-1), b_j)`。对 `j > 1` 的后一段，实际收益统计包含进入该段的
`b_(j-1) - 1 -> b_(j-1)` 收益，从而保证各段实际 log return 之和等于整个窗口实际
log return。

### 3.2 连续分段设计矩阵

时间位置标准化为：

```text
x_i = i / (n - 1)
```

其中 `x_i` 是第 `i` 个观测在窗口内的标准化时间位置，范围为 `0..1`。

对内部边界 `b_j`，当前口径将 slope knot 放在：

```text
k_j = (b_j - 1) / (n - 1)
```

其中 `k_j` 是第 `j` 个内部断点在标准化时间轴上的位置。对应 hinge column 为：

```text
h_(i,j) = max(x_i - k_j, 0)
```

`K` 段模型的设计矩阵包含：

- 1 列截距；
- 1 列基础时间 `x_i`；
- `K - 1` 列 hinge columns。

因此设计矩阵形状为 `n × (K + 1)`。hinge 在 knot 之前和 knot 位置为 0，之后线性增加，
因此只能改变后续斜率，不能引入价格水平跳跃，拟合路径在断点处连续。

### 3.3 固定边界下的 OLS 与 RSS

对第 `c` 组候选边界，设：

- `X_c` 为该候选的设计矩阵；
- `y` 为实际 log price 向量；
- `beta_c` 为 OLS 系数；
- `fitted_c = X_c beta_c` 为拟合 log price；
- `RSS_c` 为该候选的残差平方和。

计算关系为：

```text
beta_c = argmin_beta ||y - X_c beta||²
RSS_c = sum((y_i - fitted_(c,i))²), i = 0..n-1
```

固定分段数 `K` 时，RSS 最小的边界就是该 `K` 下的最佳边界。

### 3.4 BIC 的职责

当前 BIC 定义为：

```text
BIC(K) = n * ln(max(RSS(K) / n, epsilon))
         + lambda * (2K) * ln(n)
```

其中：

- `RSS(K)` 是固定 `K` 后最佳边界的残差平方和；
- `epsilon` 是防止对 0 取对数的极小正数；
- `lambda` 是 `bic_penalty_multiplier`；
- `2K` 是当前复杂度参数口径：`K + 1` 个连续样条系数加 `K - 1` 个断点位置；
- `ln` 是自然对数。

固定 `K` 时，`n`、`K` 和 `lambda` 不变，因此候选边界按 BIC 排序与按 RSS 排序完全相同。
开发实现应遵循：

1. 先为每个固定 `K` 找到最小 RSS 边界；
2. 再计算该 `K` 的最佳 BIC；
3. 最后在不同 `K` 之间选择 BIC 最小的模型。

不需要为同一个 `K` 的每组候选边界重复计算 BIC。

## 4. 历史基线：`adaptive_trend_v2` 计算流程

本章保留 v2 的历史流程，作为 v3 精确分支的 golden baseline；当前代码行为以第 2、6～8 节为准。

### 4.1 公开入口与职责

`adaptive_trend.py` 提供三个主要入口：

- `compute_adaptive_segmentation()`：计算一个 symbol、一个 lookback 的 summary 和 segments；
- `compute_adaptive_trend_experiment()`：按配置循环多个 lookback，汇总所有结果；
- `reconstruct_adaptive_fit()`：根据已保存的 segment boundaries 重新构造逐 bar fitted path。

指标模块保持纯计算，不读取配置文件、不连接数据库、不写文件。pipeline 负责读取 OHLCV、传入
参数并持久化结果。

### 4.2 输入验证和窗口截取

`compute_adaptive_segmentation()` 在以下任一条件成立时返回 `(None, [])`：

- 输入 DataFrame 为空或没有 `close` 列；
- `lookback_bars < 2`；
- `min_segment_bars < 2`；
- `max_segments < 1`；
- `bic_penalty_multiplier <= 0`；
- 输入行数少于 `lookback_bars`；
- 截取窗口内任一 close 非有限或小于等于 0。

通过验证后，v2 使用：

```text
window = df.iloc[-lookback_bars:]
```

因此：

- `n = lookback_bars` 是实际参与拟合的 close observations 数量；
- summary 的 `date` 是 `window.index[-1].date()`；
- 调用方必须保证 DataFrame 已按日期升序排列且 index 可转换为日期；
- 算法只使用 close，其他 OHLCV 列不参与分段拟合。

随后计算 `y_i = ln(P_i)`，其中 `P_i` 是第 `i` 个 close，`y_i` 是第 `i` 个 log price，
`i = 0, ..., n - 1`。

### 4.3 可行分段数

实际尝试的最大分段数为：

```text
feasible_max = min(max_segments, floor(n / m))
```

其中：

- `n` 是窗口 close observations 数量；
- `m` 是 `min_segment_bars`；
- `floor(n / m)` 是满足每段至少 `m` 个 observations 时理论上最多能拥有的分段数。

算法依次计算 `K = 1, ..., feasible_max`，其中 `K` 是当前候选分段数量。

### 4.4 合法边界枚举

`_boundary_candidates()` 使用 `itertools.combinations` 枚举 `K - 1` 个内部边界，并在首尾
补上 `0` 和 `n`。候选边界必须满足：

```text
b_0 = 0 < b_1 < ... < b_K = n
b_j - b_(j-1) >= m, j = 1..K
```

其中：

- `b_j` 是第 `j` 个半开区间边界；
- 第 `j` 段为 `[b_(j-1), b_j)`；
- `m` 是每段最少拥有的 observations 数量。

当前 `_best_boundaries()` 会先执行 `list(_boundary_candidates(...))`，把固定 `K` 的全部合法
边界放入内存。这正是 v3 分批精确搜索准备解决的第一个扩展性问题。

### 4.5 固定边界的全局连续拟合

`_continuous_design()` 根据边界生成 `n × (K + 1)` 设计矩阵。`_fit_continuous_piecewise()`
使用 `np.linalg.lstsq()` 对完整窗口执行一次全局 OLS，得到：

- OLS coefficients；
- 每个 observation 的 fitted log price；
- 每段 `log_slope_per_bar`；
- 整个窗口的 RSS。

若标准化时间系数为 `beta_1`，第 `j` 个 hinge 系数为 `delta_j`，则第 `q` 段的每 bar
log slope 为：

```text
slope_q = (beta_1 + sum(delta_j, j = 1..q-1)) / (n - 1)
```

其中：

- `q = 1, ..., K` 是分段序号；
- `beta_1` 是基础标准化时间斜率系数；
- `delta_j` 是经过第 `j` 个内部 knot 后新增的斜率变化；
- `n - 1` 将标准化时间斜率换算为每 bar log slope。

### 4.6 固定 `K` 的批量精确搜索

对于 `K > 1`，当前 v2 不是逐个候选调用 Python OLS，而是一次性构造所有候选的设计矩阵：

```text
designs.shape = (candidate_count, n, K + 1)
```

设：

- `c` 是候选边界编号；
- `X_c` 是候选 `c` 的设计矩阵；
- `y` 是实际 log price 向量；
- `G_c = X_c' X_c` 是 Gram matrix；
- `r_c = X_c' y` 是 OLS 右端项；
- `beta_c` 是候选 `c` 的 OLS 系数。

NumPy 批量计算：

```text
G_c = X_c' X_c
r_c = X_c' y
beta_c = solve(G_c, r_c)
RSS_c = y' y - beta_c' r_c
```

其中 `'` 表示矩阵转置。`RSS_c` 是候选 `c` 对整个窗口的残差平方和。代码用
`np.einsum()` 计算所有 `G_c` 和 `r_c`，再用批量 `np.linalg.solve()` 求解。

`np.argmin(rss_values)` 选出固定 `K` 的最低 RSS 候选。选中边界后，代码再调用
`_fit_continuous_piecewise()`，使用 `np.linalg.lstsq()` 重新计算最终 fitted path、slopes 和 RSS，
而不是直接把批量 normal-equation 中间结果作为最终输出。

这种实现对固定 `K` 检查了全部合法边界，因此在当前连续模型和离散边界集合内是精确搜索；
“批量”只描述计算方式，不代表近似。但它会一次性分配所有候选矩阵，峰值内存随候选数量快速
增长。

### 4.7 跨分段数的 BIC 选择

每个 `K` 完成精确搜索后，v2 保存：

```text
(BIC(K), K, boundaries(K), fitted(K), slopes(K), RSS(K))
```

最终按以下 key 取最小值：

```text
(BIC(K), K, boundaries(K))
```

因此平局规则为：

1. 选择 BIC 更小的模型；
2. BIC 相同时选择分段数更少的模型；
3. BIC 和分段数都相同时选择字典序更小的边界。

`single_segment_rss` 和 `single_segment_bic` 来自 `K = 1` 候选，摘要中的：

```text
bic_improvement = single_segment_bic - selected_bic
```

其中：

- `single_segment_bic` 是单直线模型的 BIC；
- `selected_bic` 是最终选中分段模型的 BIC；
- `bic_improvement > 0` 表示选中模型相对单直线具有更低 BIC。

### 4.8 逐段收益归属

对边界 `[start, end)`：

```text
anchor = start                  if start == 0
anchor = start - 1              if start > 0
actual_segment = y[anchor:end]
fitted_segment = fitted[anchor:end]
```

其中：

- `start` 是该段第一个 owned observation 的 bar index；
- `end` 是不属于该段的右侧边界；
- `anchor` 是计算该段进入收益时使用的前一个价格点；
- `y` 是实际 log price；
- `fitted` 是全局连续 fitted log price。

这种口径使后一段拥有 `start - 1 -> start` 的边界收益。数据库中的 `start_bar_index` 和
`observation_count` 仍描述 `[start, end)` 的 owned observations，不把 anchor 重复计入段长度。

### 4.9 已实现的逐段指标

对每一段，v2 输出：

- `log_slope_per_bar`：全局连续拟合在该段的每 bar log slope；
- `linearity_r2`：该段 actual log price 与同一条全局拟合路径之间的 R²；
- `fitted_log_return`：段内 fitted log price 终点减起点；
- `actual_log_return`：段内实际 log price 终点减起点；
- `realized_volatility_daily`：段内 daily log returns 的样本标准差，使用 `ddof = 1`；
- `vol_adjusted_trend`：每 bar log slope 乘收益间隔数平方根，再除以日度实现波动率；
- `efficiency_ratio`：实际净 log return 绝对值除以绝对 daily log return 之和；
- `largest_move_log_return`：绝对值最大的单日 log return，保留正负号；
- `largest_move_date`：最大单日变化落点日期；
- `largest_move_bar_index`：最大单日变化落点在窗口内的 bar index；
- `largest_move_path_share`：最大单日变化绝对值占该段绝对路径总和的比例。

`linearity_r2` 定义为：

```text
linearity_r2 = 1 - RSS_segment / TSS_segment
```

其中：

- `RSS_segment` 是该段 actual log price 与全局 fitted log price 的残差平方和；
- `TSS_segment` 是该段 actual log price 相对段内均值的总平方和；
- 当 `TSS_segment <= epsilon` 时，若 `RSS_segment <= epsilon` 则返回 1，否则返回 0；
- 最终结果裁剪到 `0..1`。

`vol_adjusted_trend` 定义为：

```text
vol_adjusted_trend = slope * sqrt(intervals) / volatility
```

其中：

- `slope` 是该段 `log_slope_per_bar`；
- `intervals` 是该段参与收益统计的 daily return 数量；
- `volatility` 是 `realized_volatility_daily`；
- 收益少于两个或波动率不大于 `epsilon` 时返回 `NULL`。

`efficiency_ratio` 定义为：

```text
efficiency_ratio = abs(actual_log_return) / sum(abs(daily_log_return))
```

其中分母为 0 时返回 0，最终结果裁剪到 `0..1`。

### 4.10 summary、segments 与拟合重建

summary 记录 symbol、窗口截止日期、lookback、候选参数、最终段数、RSS/BIC 对比、方法和计算
版本。segments 记录每段日期边界、bar index、owned observation 数量和上述逐段指标。

数据库没有逐 bar 保存 fitted price。`reconstruct_adaptive_fit()` 从 segments 取回内部
`start_bar_index`，重建边界并对传入窗口重新执行相同的全局连续 OLS，返回：

- `close`；
- `log_close`；
- `fitted_close`；
- `fitted_log_close`；
- `residual_log`。

调用重建函数时，传入 DataFrame 的窗口截止日期和排序必须与原分段快照一致；否则相同 bar index
会指向不同的实际日期和价格，重建曲线将不再对应原快照。

### 4.11 v2 已知限制

- 固定 `K` 的全部边界和全部设计矩阵一次性进入内存；
- 候选数量随 lookback、`max_segments` 增长而组合爆炸；
- 批量 normal equations 使用 `X'X`，数值条件数通常劣于直接 QR/SVD，虽然最终选中边界会用
  `lstsq()` 重新拟合；
- 任何一个批量 `G_c` 奇异时，当前批量 `np.linalg.solve()` 没有逐候选降级策略；
- 所有 lookback 共用一个 `max_segments`，尚不支持按 lookback 推荐或覆盖；
- `reconstruct_adaptive_fit()` 不验证 segment 的 method、version、symbol、date 和参数一致性；
- 当前 summary 没有记录候选数量、峰值批次、搜索精确性或收敛状态。

## 5. 组合规模与内存问题

### 5.1 合法边界组合数量

固定 `n`、`K`、`m` 时，如果 `n >= K * m`，合法半开区间边界数量为：

```text
candidate_count(n, K, m) = C(n - K*m + K - 1, K - 1)
```

其中：

- `C(a, b)` 表示从 `a` 个元素中选择 `b` 个元素的组合数；
- `n` 是 close observations 数量；
- `K` 是分段数量；
- `m` 是每段最少拥有的 observations 数量。

当 `m = 5` 且最大分段数按 40 bars 对应 4 段、以后每增加 20 bars 增加一段时：

| lookback `n` | 最大分段数 | 最复杂单层候选数 | 1..最大分段累计候选数 |
|---:|---:|---:|---:|
| 40 | 4 | 1,771 | 2,154 |
| 60 | 5 | 82,251 | 95,725 |
| 80 | 6 | 3,478,761 | 3,975,881 |
| 250 | 14 | 54,768,392,943,431,685,168 | 60,277,449,290,656,271,519 |

80 bars、6 段已经不适合继续使用当前一次性完整矩阵方式。

### 5.2 设计矩阵内存

若一批包含 `B` 组候选、窗口有 `n` 个 observations、候选分成 `K` 段，则批量设计矩阵
形状为：

```text
(B, n, K + 1)
```

使用 64 位浮点数时，仅设计矩阵的近似字节数为：

```text
B * n * (K + 1) * 8
```

例如 60 bars、5 段：

- 一次放入 82,251 组：设计矩阵本身约 226 MiB；
- 每批放入 2,048 组：设计矩阵本身约 5.6 MiB。

实际峰值还包括 hinge、Gram matrix、右端项、系数和 NumPy 临时数组。因此分批大小只是一项
工程内存参数，不改变数学结果。

### 5.3 250 bars 精确穷举容量估算

本节假设：

- `n = 250`，即单个窗口包含 250 个 close observations；
- `m = 5`，即每段至少拥有 5 个 observations；
- 暂不施加更低的 `max_segments_cap`，按推荐关系
  `4 + floor((n - 40) / 20)` 得到最大分段数 `K_max = 14`；
- 对每个 `K = 1, ..., K_max` 都精确穷举合法边界，再用 BIC 在不同 `K` 之间选模型。

累计候选数为：

```text
sum(candidate_count(250, K, 5), K=1..14)
    = 60,277,449,290,656,271,519
    ~= 6.03 * 10^19
```

其中 `K = 14` 单层约有 `5.48 * 10^19` 个候选，占累计数量约 90.9%。因此总量主要由最大
分段数决定。即使假设每秒能够完整拟合一百万个候选，单个 250 bars 窗口仍需约 191 万年；
即使达到每秒十亿个候选，也仍需约 1,910 年。这两个速度都显著高估了当前单机 NumPy 实现
在 250 observations、14 段连续 OLS 下的实际吞吐率。

2026-07-28 在当前开发环境对现有 v2 实现做了一次小规模合成序列校准，观测吞吐率约为：

| `n` | `K` | 候选数 | 约候选/秒 |
|---:|---:|---:|---:|
| 40 | 4 | 1,771 | 105,000 |
| 60 | 4 | 12,341 | 102,000 |
| 50 | 5 | 23,751 | 84,000 |
| 45 | 6 | 15,504 | 30,000 |

该校准不是稳定性能基准，只用于确认每秒一百万候选已经是乐观吞吐率假设，对应运行时间的
乐观下界。单个候选的主要矩阵
工作量还会随 `n` 和 `K` 增长，粗略量级为 `O(n * (K + 1)^2 + (K + 1)^3)`；其中 `O`
表示渐近工作量上界，`K + 1` 是连续线性样条的回归系数数量。

若按当前实现计算完整 Gram matrix、右端项和线性方程求解，全部 `K = 1..14` 候选的算术
工作量粗估为 `7.3 * 10^24` 次浮点运算量级。这里“一次浮点运算”是一次浮点加、乘等基本
操作的估算单位，不包含内存分配、数据搬运、候选生成和 Python 开销。即使能够持续达到
`10^12` 次浮点运算/秒，纯算术下界仍约为 23 万年；实际实现还会受内存带宽和批次开销限制。

当前 v2 还会一次构造固定 `K` 的全部设计矩阵。250 bars、4 段时已有 2,081,156 个候选，
仅 `(candidate_count, n, K + 1)` 的 64 位设计矩阵就约 19.4 GiB，实际峰值还要加 hinge 和
临时数组。250 bars、14 段时，设计矩阵理论大小约 `1.53 * 10^15 GiB`，没有实际分配的
可能。改成每批 2,048 个候选可把单批设计矩阵压到约 58.6 MiB，但 `K = 14` 仍需约
`2.67 * 10^16` 批，因此分批只解决峰值内存，不解决总时间。

结论：在上述无低上限条件下，250 bars 不能进入精确穷举分支，必须由候选预算触发确定性
近似搜索，或显著限制 `max_segments_cap`。该阶段当时 `settings.yaml` 的固定 `max_segments = 4`
不等同于上述动态推荐关系；即使保持 4 段，现有一次性矩阵实现仍有内存风险，需先完成分批化。

### 5.4 250 bars、`max_segments_cap = 10` 容量估算

保持 `n = 250`、`m = 5`，把最大分段数限制为 `K_max = 10` 时，精确扫描
`K = 1, ..., 10` 的累计候选数为：

```text
1,857,029,997,859,945 ~= 1.857 * 10^15
```

其中 `K = 10` 单层有 1,760,806,558,963,166 个候选，占累计数量约 94.8%。不同上限的
累计规模为：

| `K_max` | 累计候选数 |
|---:|---:|
| 4 | 2,109,364 |
| 5 | 113,716,865 |
| 6 | 4,708,863,785 |
| 8 | 4,232,748,588,977 |
| 9 | 96,223,438,896,779 |
| 10 | 1,857,029,997,859,945 |

按候选吞吐率直接计算的乐观时间下界为：

| 假设吞吐率 | 运行时间 |
|---:|---:|
| 100,000 候选/秒 | 约 588 年 |
| 1,000,000 候选/秒 | 约 58.8 年 |
| 1,000,000,000 候选/秒 | 约 21.5 天 |

最后一种速度要求每秒完成约十亿次 11 系数连续 OLS 拟合，并不适用于当前单机实现。根据
5.3 节的小规模实测，再按 `n` 和系数数量的矩阵工作量粗略缩放，250 bars、10 段可能只有
每秒数千个候选，对应约一万至数万年；该数字只是复杂度外推，不是实际 250 bars 基准。

全部候选的算术工作量粗估约 `1.23 * 10^20` 次浮点运算。即使持续达到
`10^12` 次浮点运算/秒，且完全忽略数据搬运、内存分配和候选生成，纯算术下界仍约 3.9 年。

当前 v2 一次性矩阵实现中，`K = 10` 的设计矩阵理论大小约 `3.61 * 10^10 GiB`，即约
38.7 EB；实际上算法在 `K = 5` 时设计矩阵就已约 1.22 TiB。若改为每批 2,048 个候选，
`K = 10` 的单批设计矩阵约 43.0 MiB，但完整扫描 `K = 1..10` 仍需约
906,752,928,648 批。由此可见，`max_segments_cap = 10` 仍不能作为 250 bars 精确穷举的
可行上限，只能作为近似搜索允许考虑的模型复杂度上限。

## 6. `adaptive_trend_v3` 已实现架构

本章架构已于 2026-07-29 实现。后续参数调整仍必须经过第 9 节验证。

### 6.1 总体模型选择流程

```text
for K in 1..feasible_max_segments:
    count = candidate_count(n, K, m)
    if count <= exact_candidate_budget:
        best_K = exact_chunked_search(K)
    else:
        best_K = deterministic_candidate_search(K)
    bic_K = BIC(best_K.rss, K)

selected = min((bic_K, K, boundaries_K))
```

其中：

- `feasible_max_segments = min(max_segments, floor(n / m))`；
- `exact_candidate_budget` 是是否允许该层完整穷举的候选数量预算；
- 最终排序先使用 BIC，再使用较少分段数，最后使用边界字典序，保证结果确定。

### 6.2 精确分支：分批穷举

候选步骤：

1. 使用 generator 按确定顺序产生合法边界；
2. 每累计 `exhaustive_batch_size` 组边界构造一批设计矩阵；
3. 批量计算 OLS 和 RSS；
4. 更新当前 `K` 的全局最小 RSS 及其边界；
5. 释放当前批次数组并继续下一批；
6. 所有批次完成后返回与完整穷举数学等价的结果。

如果下一层 `K + 1` 也会精确穷举，当前层只需保留最佳边界。如果下一层将进入近似搜索，
当前层可额外保留 RSS 排名前 `beam_width` 的边界，作为下一层候选起点。前 `beam_width` 名
不用于当前 `K` 的最终 BIC 选择。

### 6.3 大规模分支：确定性候选搜索

当前候选方向为 beam expansion：

1. 从上一层保留的多组边界分别插入一个新断点；
2. 遍历每组边界下所有合法插入位置；
3. 对插入后的完整路径重新执行全局连续 OLS；
4. 按 `(RSS, boundaries)` 排序并去重；
5. 保留前 `beam_width` 名；
6. 补充等间距及确定性偏移的初始边界；
7. 对候选边界执行后续 refinement；
8. 选择该 `K` 下最低 RSS 结果。

该搜索不使用随机数。相同输入、参数和计算版本必须产生相同结果，但“确定性”只表示结果可复现，
不表示结果是全局最优。

### 6.4 大规模分支的最优性结论

对固定分段数 `K`，定义：

```text
F_K(b) = min_beta ||y - X(b) beta||²
```

其中：

- `b` 是一组满足最短分段约束的内部整数边界；
- `X(b)` 是由边界 `b` 生成的全局连续 spline 设计矩阵；
- `beta` 是固定边界后的 OLS 系数；
- `y` 是窗口实际 log price 向量；
- `F_K(b)` 是边界 `b` 下能达到的最小 RSS。

beam search 每层只保留 `beam_width` 个部分边界状态。如果通向全局最佳完整边界的某个中间状态
在某层排名低于 `beam_width`，该状态会被永久删除。后续单断点或双断点 refinement 只有在保留
候选的下降邻域内移动，不保证能够跨越到已删除状态所在的吸引域。

因此当前候选框架：

```text
beam initializations
→ single-boundary refinement
→ optional adjacent-pair refinement
→ single-boundary refinement
```

不能保证找到固定 `K` 下的全局最佳边界。它只能返回“已评估候选中的最低 RSS 边界”或
`best_found_boundaries`，不能在接口、日志或文档中无条件称为 `optimal_boundaries`。

如果 approximate `RSS(K)` 高于真实全局最小 RSS，不仅边界可能错误，跨 `K` 的 BIC 比较也可能
选择错误的分段数。因此 approximate 状态必须进入方法标识、计算版本和验证报告。

### 6.5 获得全局最优保证需要的附加条件

至少满足以下一种条件，才能声称固定 `K` 下得到全局最佳离散边界：

1. 对该 `K` 的全部合法边界执行完整穷举；
2. `beam_width` 大到每层不丢弃任何合法状态，此时 beam search 实质上退化为完整搜索；
3. 对被裁剪状态进行系统回溯，并为搜索树建立足以证明最优性的 admissible lower bound；
4. 使用 branch-and-bound、cutting-angle 或其他能给出全局上下界收敛证书的方法；
5. 使用针对离散一阶 free-knot least-squares spline 的专门全局算法，并先证明其模型、knot 位置
   和连续性约束与本项目问题等价。

普通 beam search 没有第 3 项的系统回溯。Beam-stack search 的理论价值正在于把回溯加入 beam
search，使被暂时裁剪的路径不会永久丢失，并在相应假设下恢复完备性和最优性保证。将它用于本项目
仍需要重新定义状态、扩展规则、上界和 admissible lower bound，不能直接套用论文结论。

## 7. 候选 refinement 设计

本章记录当前讨论结论，不代表已经决定进入生产实现。

### 7.1 单断点全域优化

固定其他断点，对当前内部断点检查全部合法整数位置。设正在优化 `b_j`，合法范围为：

```text
b_(j-1) + m <= b_j <= b_(j+1) - m
```

每测试一个位置，都对整条连续曲线重新执行 OLS，并只接受 RSS 严格下降的移动。建议接受条件为：

```text
RSS_new < RSS_old - tau * max(1, RSS_old)
```

其中：

- `RSS_old` 是移动前的 RSS；
- `RSS_new` 是移动后的 RSS；
- `tau` 是防止浮点噪声导致等值振荡的相对改善容差。

由于合法整数边界集合有限，且每次接受移动都严格降低 RSS，在不提前触发迭代上限时，算法
一定会有限停止。停止结果只保证单坐标局部最优，不保证全局最优。

实现时需要区分：

- `converged = true`：完整一轮没有可接受移动；
- `converged = false`：达到最大 refinement 轮数时仍可能继续改善。

### 7.2 相邻双断点局部优化

该步骤用于处理“两个相邻断点必须同时移动才能降低 RSS”的局部障碍。设当前相邻内部断点为
`b_j`、`b_(j+1)`，新位置为 `u`、`v`，局部半径为 `r`，则候选必须同时满足：

```text
abs(u - b_j) <= r
abs(v - b_(j+1)) <= r
u - b_(j-1) >= m
v - u >= m
b_(j+2) - v >= m
```

其中：

- `r` 是 `pair_refinement_radius`，单位为 bars；
- `m` 是 `min_segment_bars`；
- 相邻外部边界和最短分段约束会进一步裁剪半径定义的邻域。

未裁剪的候选对数量量级约为 `(2r + 1)^2`。因此 `r` 越大，越有机会越过局部障碍，
但全局 OLS 求解次数也按平方量级增加。

当前结论：相邻双断点优化不是第一版混合搜索的必选步骤。应先验证 beam search 加单断点全域
优化相对精确穷举的误差；只有观察到主要失败来自双断点协同移动时，才引入该步骤。

候选初始规则为：

```text
pair_refinement_radius = min(min_segment_bars, 10)
```

该规则尚未确认，必须通过半径网格和精确基准验证。

### 7.3 组合 refinement 的收敛保证

若算法只接受超过数值容差的严格 RSS 下降，边界只能取有限整数位置，并且单断点与双断点步骤
交替运行到都没有可接受移动，则整个 refinement 会有限停止。停止点满足：

- 任何单个断点移动到任意合法位置都不能降低 RSS；
- 在 `pair_refinement_radius` 定义的邻域内，任何一对相邻断点同时移动都不能降低 RSS。

这是相对于指定单点和局部双点邻域的局部最优，不是全局最优。连续优化中的 block coordinate
descent 文献为“逐块最小化收敛到 stationary point”提供了理论背景，但本项目的目标函数定义在
有限离散边界集合上，最直接的停止证明来自“有限状态 + 严格下降”，不能把连续坐标下降论文的
stationary-point 定理直接当作本算法的全局最优证明。

## 8. 参数分类与当前状态

### 8.1 模型与生产参数

| 参数 | 当前值 | 状态 | 说明 |
|---|---:|---|---|
| `lookbacks` | `[60]` | 已实现 | 当前固定生产输出窗口 |
| `min_segment_bars` | `5` | 已实现 | 每段最少拥有的 observations 数量 |
| `max_segments` | `5` | 已实现 | 当前 60-bars 生产窗口的最大候选分段数 |
| `bic_penalty_multiplier` | `3.0` | 已实现 | BIC 复杂度惩罚乘数 |

### 8.2 搜索工程参数

| 参数 | 候选初值 | 状态 | 说明 |
|---|---:|---|---|
| `exact_candidate_budget` | `100000` | 已实现 | 按单个固定 `K` 判断是否允许精确穷举 |
| `exhaustive_batch_size` | `2048` | 已实现 | 每批设计矩阵候选数；不改变精确结果 |
| `beam_width` | `8` | 已实现 | 近似搜索每层保留的候选边界数量 |
| `deterministic_seed_count` | `4` | 已实现 | 每层补充的确定性初始边界上限 |
| `max_refinement_passes` | `10` | 已实现 | 单断点往返扫描安全上限 |
| `rss_improvement_tolerance` | `1e-10` | 已实现 | 接受 RSS 改善的相对容差 |
| `pair_refinement_enabled` | `true` | 已实现 | 精确对照验证后默认启用一轮相邻双断点优化 |
| `pair_refinement_radius` | `5` | 已实现 | 双断点局部搜索半径，单位为 bars |

这些参数会影响性能，部分参数也会影响近似结果。进入生产后必须由配置或明确的计算版本管理，
不能成为不可追踪的隐式常量。dashboard 主参数菜单不一定需要暴露这些搜索工程参数。

### 8.3 推荐 `max_segments` 关系

当前 dashboard 候选推荐关系为：

```text
recommended_max_segments(n)
    = min(max_segments_cap, 4 + floor((n - 40) / 20))
```

其中：

- `n` 是交互计算的 lookback bars，要求 `n >= 40`；
- `max_segments_cap = 10` 是当前确认的安全上限；
- `floor` 表示向下取整。

该关系继续用于单标的自定义交互计算；固定 60-bars 生产输出显式使用 5 段上限。
`trend_pattern_v4` 对超过4条
effective legs 的输入保留raw-close指标和leg数量，但结构字段为空，不再让整个symbol失败。

### 8.4 250 bars、10 段混合搜索资源预算

以下预算不是已实现性能承诺，而是按 6～8 节候选架构建立的首版工程基线。假设：

- `n = 250`，`m = 5`，`K_max = 10`；
- `exact_candidate_budget = 100000`，因此 `K = 1..3` 精确搜索，共评估 28,208 个候选，
  `K = 4..10` 进入近似分支；
- `beam_width = 8`；
- 每个近似 `K` 最多保留 8 个 beam 候选，再补 4 个等间距或确定性偏移候选，记总初始数
  `S = 12`；这里 `S` 只是资源估算口径，尚未成为正式配置；
- 单断点优化通常 3 轮收敛，安全上限为 10 轮；
- 可选双断点优化使用 `pair_refinement_radius = 5`，执行一轮；若发生移动，再执行 2 轮
  单断点回扫；
- 所有可并组的 OLS 候选按不超过 2,048 组批量求解。

在这些假设下，候选拟合次数估算如下：

| 工作项 | 典型候选拟合数 | 安全上限口径 |
|---|---:|---:|
| `K = 1..3` 分批精确搜索 | 28,208 | 28,208 |
| `K = 4..10` beam expansion | 不超过 13,496 | 不超过 13,496 |
| 首轮单断点全域优化 | 约 109,872（3 轮） | 约 366,240（10 轮） |
| 可选相邻双断点一轮 | 约 50,820 | 约 50,820 |
| 双断点移动后的单断点回扫 | 约 73,248（2 轮） | 约 366,240（10 轮） |

因此：

- 首版不启用双断点优化：典型约 15.2 万次，跑满单断点上限约 40.8 万次；
- 启用一轮双断点优化和 2 轮回扫：典型约 27.6 万次；
- 单断点初扫和回扫都跑满 10 轮的保守上限约 82.5 万次。

这里“一次候选拟合”表示：为一组完整离散边界构造连续样条设计，并对全部 250 个 log-price
observations 重新执行全局 OLS、计算 RSS。beam 排序、BIC 和最终曲线生成的开销相对较小。

2026-07-28 在当前开发环境做的 250 bars 微基准结果为：

| 分段数 `K` | 逐候选 OLS（候选/秒） | 2,048 组批量矩阵（候选/秒） |
|---:|---:|---:|
| 4 | 约 16,100 | 约 14,300 |
| 7 | 约 3,370 | 约 15,400 |
| 10 | 约 1,550 | 约 7,310 |

该微基准不包含完整 beam 去重、候选生成和收敛控制，也不能替代实现后的端到端基准。按其给出
首版单 symbol 粗估：

| 搜索配置 | 候选批量化实现 | 逐候选朴素实现 |
|---|---:|---:|
| 不启用双断点优化 | 约 20～90 秒 | 约 1～3 分钟 |
| 启用双断点优化 | 约 45 秒～2.5 分钟 | 约 2～9 分钟 |

批量大小为 2,048、`K = 10` 时，设计矩阵约 43 MiB，hinge 数组约 35 MiB；加上 Gram
matrix、临时数组、Python、NumPy 和行情数据，建议按每个计算 worker 0.5～1 GiB 内存预算。
单 symbol 不需要 GPU，也不需要 TB 级内存。多个 symbol 并行时，CPU 时间和内存近似随 worker
数量增长，应限制并发 worker，而不能按股票数量无限并发。

当前目标机器有 32 GiB 物理内存，其中至少 10 GiB 可作为自适应分段计算预算。该预算足以运行
混合搜索，但仍不足以运行 250 bars、4 段约 19.4 GiB 设计矩阵的一次性精确实现。建议不要让
单个请求占满 10 GiB，而采用以下资源边界：

- 默认候选计算批量暂时保持 2,048；将 8,192 和 16,384 作为实现后的对照基准，而不是因为
  内存充足就直接提高默认值；
- `K = 10`、批量 16,384 时，设计矩阵和 hinge 主数组合计约 625 MiB，计入 Gram matrix、
  NumPy 临时数组和进程基础内存后，建议为单 worker 设置约 1.5～2 GiB 预算；
- 在线单 symbol 计算默认使用 1 个 worker；后台批量计算先从 4 个 worker 开始，按每个 worker
  约 2 GiB 预留后仍保留约 2 GiB 计算预算余量；
- 多 worker 时应限制每个 NumPy/BLAS worker 的内部线程数，避免“进程并发 × BLAS 线程”造成
  CPU 过度订阅；
- 最终 batch size 不应只按可用内存取最大值，应在 2,048、8,192、16,384 三档做端到端基准，
  选择吞吐率最佳且峰值内存稳定的一档。

同日在 250 bars、`K = 10` 的单次微基准中，三档结果为：

| batch size | 设计矩阵加 hinge 主数组 | 本轮候选吞吐率 |
|---:|---:|---:|
| 2,048 | 约 78.1 MiB | 约 21,200/秒 |
| 8,192 | 约 312.5 MiB | 约 17,300/秒 |
| 16,384 | 约 625.0 MiB | 约 18,700/秒 |

微基准会受缓存、NumPy 和 BLAS 运行状态影响，绝对速度不能视为稳定承诺；但同一进程内的比较
说明更大批次没有自动带来更高吞吐率。因此首版保留 2,048 更稳妥，之后以端到端结果决定。

增大 batch 主要改善精确层和可以集中评估的 beam 候选。单断点 refinement 的下一步依赖当前
断点刚刚选出的最佳位置，存在串行决策，因此额外内存不会按比例缩短单 symbol 延迟。10 GiB
预算更适合增加 symbol 级后台并发，而不是构造更大的单次矩阵。

这一延迟适合后台计算、异步详情计算或每日预计算缓存，不属于稳定的即时交互延迟。若 dashboard
要求 5 秒内返回，首版需要进一步降低 `S`、`beam_width` 或 refinement 轮数，或者实现针对 hinge
基函数交叉乘积的预计算优化，并用精确可计算窗口重新验证搜索质量。

2026-07-29 实现后的端到端合成序列基准：

- 250 bars、10段、双断点关闭：11.4秒，评估139,532个候选，正常收敛；
- 250 bars、10段、双断点开启：14.2秒，评估200,584个候选，正常收敛；
- 12组60-bars精确对照中，双断点关闭时BIC分段数一致率100%，RSS差距中位数0、P95为
  1.15%、最大4.55%；
- 同组样本启用半径5的双断点优化后，BIC分段数一致率100%，RSS差距P95为0、最大0.081%。

因此 v3 默认启用双断点优化。上述结果来自有限合成样本，不代表所有市场路径上的统计保证；
后续验证报告仍需扩大真实symbol和历史截面覆盖。

## 9. 验证计划

### 9.1 分批精确搜索等价性

- 对现有测试数据和随机可计算窗口比较旧实现与分批实现；
- 边界、分段数、RSS、BIC 和 fitted curve 应在数值容差内一致；
- 验证不同 `exhaustive_batch_size` 不改变结果；
- 验证批次边界处的候选不会漏失。

### 9.2 确定性和约束

- 相同输入重复运行必须得到完全相同的边界；
- 所有边界满足严格递增和 `min_segment_bars`；
- 拟合曲线在 knot 处连续；
- 各段实际 log return 之和等于窗口实际 log return；
- 等值结果按固定边界字典序打破平局。

### 9.3 近似搜索质量

在仍可执行精确穷举的样本上，将混合搜索与精确全局最优比较。RSS 相对差距定义为：

```text
rss_relative_gap
    = (RSS_hybrid - RSS_exact) / max(RSS_exact, epsilon)
```

其中：

- `RSS_hybrid` 是候选混合搜索得到的 RSS；
- `RSS_exact` 是精确穷举得到的最小 RSS；
- `epsilon` 是防止分母为 0 的极小正数；
- `rss_relative_gap >= 0`，越接近 0 越好。

同时比较：

- 最终 BIC 差距；
- BIC 选择的分段数是否一致；
- 边界完全匹配率和容差内匹配率；
- 单断点 refinement 后是否仍存在可改善的单断点位置；
- `beam_width` 增加带来的质量收益和运行成本；
- 是否存在必须依靠双断点协同移动才能修复的样本。

### 9.4 性能验证

至少覆盖 40、60、80、120、250、700 bars，并记录：

- 合法候选估算数量；
- 实际 OLS 评估次数；
- 运行时间；
- 峰值内存；
- 搜索方式；
- 是否收敛；
- dashboard 单 symbol 交互延迟。

### 9.5 真实行情稳定性验证

复用 `validation.adaptive_trend` 的 symbols、anchor offsets 和当前稳定性报告，比较 v2/v3：

- 分段数分布；
- 断点位置变化；
- 少量新增 bars 后的断点稳定性；
- BIC improvement 分布；
- `trend_pattern_v3/v4` 下游结构变化。

验证结果只生成报告，不应自动修改生产配置。

## 10. dashboard 集成边界

`investment_dashboard` 是独立项目，不得 import `market_analysis` Python 模块。候选集成方式为：

1. 详情页只显示一套自适应分段参数，不设置“固定/自定义”模式开关；
2. 参数与持久化快照完全一致时，可读取数据库边界并重建 fitted curve；
3. 参数不一致时，由显式按钮调用只读 `market-analysis compute-adaptive-trend` JSON CLI；
4. 自定义结果不写入现有分段表，因为当前主键不包含全部算法参数；
5. dashboard 不修改 `market_analysis/config/settings.yaml`；
6. UI 可使用提交按钮避免 Streamlit 参数每次变化都触发昂贵计算，但该按钮不代表模式选择；
7. 自定义结果只进入 Streamlit 会话缓存；外部命令显示工作目录、完整命令和日志；
8. dashboard 不复制或 import 分段算法，固定曲线使用持久化锚点和斜率重建。

## 11. 数据库与下游影响

v3 已增加以下摘要字段：`search_mode`、`is_global_optimum`、`candidates_evaluated`、
`refinement_converged`、`search_config` 和 `search_diagnostics`。segment 明细增加
`fitted_anchor_log_price`，供dashboard无需OLS重建固定拟合曲线。

如果新增字段、改变 `method/calculation_version`、将 60 bars 的 `max_segments` 从 4 改为 5，
或改变分段边界语义，必须同步检查：

- `investment_dashboard` 对 `trend_segmentation_daily`、`trend_segment_daily` 的读取；
- `trend_pattern_v3` 分类结果；
- `trend_pattern_v4` 的 1～4 effective legs 限制；
- 现有 CLI 输出和验证报告中的版本判断。

## 12. 实施阶段

### 阶段 A：冻结基准

- [x] 为当前 v2 增加边界与拟合 golden tests；
- [x] 固化代表性40/60 bars结果作为回归基准；
- [x] 增加候选组合数量计算函数及测试。

### 阶段 B：分批精确穷举

- [x] 实现 generator + batch 设计矩阵；
- [x] 保持精确分支与 v2 数学结果一致；
- [x] 测试分批精确结果；
- [x] 测量60 bars精确对照和250 bars混合性能。

### 阶段 C：确定性候选搜索

- [x] 实现 beam expansion；
- [x] 实现确定性初始边界；
- [x] 实现单断点全域优化和收敛标记；
- [x] 使用 `beam_width = 8`、单层预算100,000完成首轮精确对照；
- [x] 验证双断点优化显著降低P95 RSS差距并默认启用。

### 阶段 D：口径升级决策

- [x] 冻结 `adaptive_trend_v3` 方法名称和计算版本；
- [x] 持久化搜索审计信息；
- [x] 固定60 bars允许5段；
- [x] 处理 `trend_pattern_v4` 超过4条effective legs的兼容性；
- [x] 更新 README、配置说明和数据库契约测试。

### 阶段 E：dashboard 展示

- [x] 增加针对 symbol/参数的固定分段读取；
- [x] 增加固定 fitted curve 重建和只读CLI交互计算；
- [x] 增加统一参数菜单和会话缓存键；
- [x] 在 K 线上显示 fitted curve 和断点；
- [x] 增加两个项目的重建和CLI契约测试。

## 13. 待决策问题

1. 在更大真实symbol/历史截面样本上，`beam_width = 8` 和半径5是否仍满足质量门槛？
2. 是否需要为自定义结果增加跨会话持久缓存，还是继续只使用Dashboard会话缓存？
3. 交互lookback未来是否需要从250扩展到700 bars；扩展前必须单独做性能与质量验证。
4. 大规模分支未来是否需要回溯/上下界以提供全局最优证书？当前明确只报告best found。

## 14. 决策日志

### 2026-08-09

- 固定生产输出由 40/60 bars 两个 lookback 简化为仅 60 bars；
- 生产配置显式使用 `max_segments = 5`，不再配置 `max_segments_cap`；
- 数据库继续保留 `lookback_bars` 维度及既有主键、索引，历史 40-bars 数据不删除；
- 单标的只读计算仍支持自定义 lookback，历史 40/60 验证记录继续保留。

### 2026-07-29

- 发布计算口径升级为 `adaptive_trend_v3`，方法为
  `continuous_piecewise_log_linear_deterministic_hybrid_bic`；
- 精确预算按单个固定 `K` 判断，默认100,000；分批大小2,048，beam width 8，确定性种子4；
- 60-bars精确对照证明双断点半径5显著降低尾部RSS差距，因此默认启用；
- 固定40/60 bars分别允许4/5段，交互窗口封顶250 bars、10段；
- summary持久化搜索状态与逐K诊断，segment持久化拟合锚点；
- `trend_pattern_v4` 对超过4条effective legs保留连续指标但不生成结构码；
- dashboard使用统一参数菜单；固定参数读数据库，自定义参数通过显式只读CLI计算并进入会话缓存；
- 250-bars、10段、双断点启用的合成序列基准为14.2秒、200,584个候选、正常收敛。

### 2026-07-28

- 根据 Git 历史补录 `adaptive_trend_v1` 到 `adaptive_trend_v2` 的语义变化和版本事实来源；
- 确认算法版本由代码常量显式声明并写入数据库，不由文件名或 commit message 自动推断；
- 确认长 lookback 下不能无限延续当前一次性完整穷举实现；
- 确认精确穷举应优先改为分批设计矩阵，控制峰值内存且不改变数学结果；
- 确认固定分段数 `K` 时 BIC 与 RSS 对候选边界的排序一致；
- 确认 BIC 用于不同分段数之间的模型选择，不需要在同一 `K` 的每个候选上重复作为排序指标；
- 明确 `beam_width` 候选只在下一层进入近似搜索时具有扩展价值；
- 明确单断点全域优化在有限整数边界和严格 RSS 改善规则下会有限停止，但只保证单坐标局部最优；
- 明确 `pair_refinement_radius` 定义双断点搜索的局部邻域，并受 `min_segment_bars` 和相邻边界裁剪；
- 决定暂不把双断点局部优化视为必选步骤，先用精确基准验证其增量价值；
- 确认 beam + 单断点/双断点 refinement 只能保证确定性和邻域局部最优，不能保证全局最佳边界；
- 确认 approximate RSS 还可能改变跨分段数的 BIC 选择，结果必须显式标注搜索精确性；
- dashboard 参数菜单不区分固定快照与自定义计算模式；参数是否匹配持久化快照由后台判断；
- dashboard 参数保存不得隐式修改 `market_analysis/config/settings.yaml`。

## 15. 理论基础与参考文献

以下文献用于说明 free-knot spline、局部优化、beam search 和全局搜索的理论背景。它们的问题
设定与本项目并非全部相同，不能在未证明模型等价前直接移植其全局最优结论。

1. David L. B. Jupp, “Approximation to Data by Splines with Free Knots,” *SIAM Journal on
   Numerical Analysis*, 15(2), 328–343, 1978.  
   DOI: <https://doi.org/10.1137/0715022>  
   相关性：说明固定 knots 时线性的 spline 拟合，在 knots 自由后会变成关于 knots 的非线性
   least-squares 问题，并讨论 free-knot spline 的 lethargy 困难。

2. P. D. Loach and A. J. Wathen, “On the Best Least Squares Approximation of Continuous Functions
   using Linear Splines with Free Knots,” *IMA Journal of Numerical Analysis*, 11(3), 393–409,
   1991.  
   DOI: <https://doi.org/10.1093/imanum/11.3.393>  
   相关性：研究 linear free-knot spline 的局部算法，并使用 dynamic-programming-based 起点提高
   全局可靠性；支持“全局初始化 + 局部 refinement”的混合思路，但不等于本文 beam 框架拥有
   全局最优保证。

3. Gleb Beliakov, “Least Squares Splines with Free Knots: Global Optimization Approach,”
   *Applied Mathematics and Computation*, 149(3), 783–798, 2004.  
   DOI: <https://doi.org/10.1016/S0096-3003(03)00179-6>  
   相关性：明确指出 free-knot least-squares objective 非线性、非凸并存在多个局部极小值；使用
   cutting-angle 全局优化及局部 discrete-gradient 改进，并讨论全局证书的高计算成本。

4. Paul Tseng, “Convergence of a Block Coordinate Descent Method for Nondifferentiable
   Minimization,” *Journal of Optimization Theory and Applications*, 109(3), 475–494, 2001.  
   DOI: <https://doi.org/10.1023/A:1017501703105>  
   相关性：给出特定正则性、可分性和拟凸条件下 block coordinate descent 的 stationary-point
   收敛结论。本文离散断点算法不直接满足或需要照搬这些条件，其直接有限停止证明来自有限状态
   和严格 RSS 下降。

5. Rong Zhou and Eric A. Hansen, “Beam-Stack Search: Integrating Backtracking with Beam Search,”
   *Proceedings of the Fifteenth International Conference on Automated Planning and Scheduling*,
   2005.  
   论文：<https://cdn.aaai.org/ICAPS/2005/ICAPS05-010.pdf>  
   相关性：通过把系统回溯加入 beam search，使搜索最终能够达到最优解；反向说明普通固定宽度
   beam 永久裁剪状态时不具备相同的完备性和最优性保证。

6. Ludwig J. Cromme, Jens Kunath and Andreas Krebs, “Computing Best Discrete Least-Squares
   Approximations by First-Degree Splines with Free Knots,” 2017.  
   预印本：<https://arxiv.org/abs/1704.05670>  
   相关性：针对离散数据和一阶 free-knot splines 给出有限步获得 global best approximation 的
   专门算法。采用前必须核对其 knot 可取位置、连续性、端点和最短分段约束是否与本项目完全等价。

7. Richard Bellman and Robert Roth, “Curve Fitting by Segmented Straight Lines,” *Journal of the
   American Statistical Association*, 64(327), 1079–1084, 1969.  
   DOI: <https://doi.org/10.1080/01621459.1969.10501038>  
   相关性：展示 dynamic programming 在 segmented straight-line fitting 中获得 optimal fit 的
   经典思路。能否应用取决于目标是否可分解为独立 segment costs；当前全局连续 hinge OLS 不具备
   v1 那样直接的段成本可加性。

8. Jushan Bai and Pierre Perron, “Computation and Analysis of Multiple Structural Change Models,”
   *Journal of Applied Econometrics*, 18(1), 1–22, 2003.  
   DOI: <https://doi.org/10.1002/jae.659>  
   相关性：说明多断点模型中 dynamic programming 获得 global SSR minimizer 的条件，并指出在
   某些 partial structural change 结构下不能直接使用同一动态规划。用于提醒“存在断点”并不自动
   意味着标准 DP 可用于本项目连续全局 spline。

## 16. Session 交接模板

每次完成算法讨论、验证或实现后，更新“当前工作状态”“实施阶段”“待决策问题”和本节：

```text
本次确认：
- <已经确认的数学口径、实现决策或验证结论>

本次完成：
- <代码、测试、文档或性能报告>

本次未决：
- <仍需验证或选择的问题>

下一步：
- <下一项具体工作>

相关代码：
- <文件和函数>

接口影响：
- <数据库 / CLI / investment_dashboard 是否受影响>
```

当前交接内容：

```text
本次确认：
- v3 采用“分批精确搜索 + 超预算确定性混合搜索”，大规模分支不声明全局最优。
- 默认启用半径5的相邻双断点优化；250 bars与10段上限已通过首轮性能验收。
- dashboard固定结果读数据库，自定义结果调用只读JSON CLI，不修改market_analysis配置或生产表。

本次完成：
- 实现 adaptive_trend_v3、搜索审计schema、固定40/60输出、单标的CLI和下游v4兼容。
- 实现dashboard统一参数菜单、会话缓存、拟合曲线、断点和命令日志。
- 两项目测试及250-bars合成序列性能/质量基准通过。

本次未决：
- 扩大真实市场样本的精确对照范围。
- 是否需要跨会话自定义结果缓存和700-bars交互窗口。

下一步：
- 执行开发数据库幂等schema更新，并由用户重启Streamlit进行视觉验收。

相关代码：
- src/market_analysis/indicators/adaptive_trend.py
- src/market_analysis/pipeline/compute_adaptive_trend.py
- src/market_analysis/db/schema.py
- tests/test_adaptive_trend.py
- ../investment_dashboard/src/investment_dashboard/pages/market_snapshot.py

接口影响：
- 两张adaptive表新增审计/拟合锚点字段；新增compute-adaptive-trend CLI；dashboard需要同步部署并重启。
```

## 17. 已实施：自适应初分段与 Pivot 精炼分段

### 17.1 方案元数据

- **方案名称**：自适应初分段与 Pivot 精炼分段
- **方案编号**：`MA-PIVOT-SEG`
- **版本号**：`v2.0.0`
- **状态**：已实施
- **确认日期**：2026-08-15
- **实施日期**：2026-08-15

本节是当前正式开发口径。代码、配置、幂等数据库 schema、CLI、测试和下游查询函数均已按本节
实现；第 2～16 节保留为历史沿革与旧口径说明。实际数据库仍需在部署时运行
`market-analysis init-db` 才会创建新表。

### 17.2 目标与边界

本方案把行情路径识别拆成两个职责明确的阶段：

1. `adaptive_segmentation_v3`：通过 BIC 选择连续分段模型，生成用于发现路径结构的初始分段；
2. `pivot_refined_segmentation_v2`：根据初始分段方向生成 pivot seed，在局部 close-price 极值附近
   定位 pivot，并以固定 pivot 横坐标重新执行全局连续最小二乘拟合。

目标是得到由实际价格转折点主导、边界语义明确且可供下游读取的分段快照。该结果属于描述性
行情指标，不构成交易策略、买卖建议、持仓规则或自动交易信号。

本方案不删除历史 `adaptive_trend_v1/v2/v3` 数据，也不以新的 pivot 分段覆盖现有
`trend_pattern_daily` 或 `trend_pattern_v4_daily`。

### 17.3 命名重构

`adaptive_trend_v3` 的算法职责是“选择分段”，不是对市场趋势作最终判断，因此实施时统一改为：

| 历史名称 | 当前规范名称 | 说明 |
|---|---|---|
| `adaptive_trend_v3` | `adaptive_segmentation_v3` | 计算版本的语义更名 |
| `adaptive_trend.py` | `adaptive_segmentation.py` | 纯计算模块 |
| `indicators.adaptive_trend` | `indicators.adaptive_segmentation` | 配置键 |
| `validation.adaptive_trend` | `validation.adaptive_segmentation` | 验证配置键 |
| `compute-adaptive-trend` | `compute-adaptive-segmentation` | 单标的只读计算 CLI |
| `validate-trend-segmentation` | `validate-adaptive-segmentation` | 验证 CLI |
| `run-trend-segmentation-experiment` | `run-adaptive-segmentation` | 批量持久化 CLI |

旧 CLI 在一个迁移周期内作为 legacy alias 保留；新文档和 Dashboard 只使用规范名称。数据库历史行
继续保留原 `calculation_version`，新运行写入 `adaptive_segmentation_v3`，禁止原地伪装为同一版本。

现有 `trend_segmentation_daily` 和 `trend_segment_daily` 暂不改表名。表名虽包含 `trend`，但直接
重命名会同时影响 v3/v4 形态分析和 Dashboard；本阶段用计算版本和新派生表区分语义。

### 17.4 默认生产参数

当前默认配置为：

```yaml
indicators:
  adaptive_segmentation:
    lookbacks: [250]
    min_segment_bars: 5
    max_segments_cap: 10
    bic_penalty_multiplier: 3.0
    segment_classification:
      min_abs_fitted_log_return: 0.02
      min_linearity_r2: 0.35
      min_abs_vol_adjusted_trend: 0.75

  pivot_refinement:
    search_radius_bars: 5
    min_segment_bars: 5
```

`lookback_bars = 250` 表示窗口包含 250 个 close observations，对应 249 个相邻日度收益间隔。
在 250 bars 和较高候选分段数下，现有候选预算会触发确定性混合近似搜索；summary 必须继续准确
记录 `search_mode`、`is_global_optimum`、候选数、收敛状态和逐分段数诊断，不得把 best found
标记为已经证明的全局最优。

### 17.5 初始分段方向分类

对 `adaptive_segmentation_v3` 的每个初始分段计算 `up`、`down` 或 `flat`。第 `j` 段满足以下
有效方向条件时才可归类为 `up/down`：

\[
|\widehat r_j| \ge \theta_r
\quad\text{且}\quad
\left(R_j^2 \ge \theta_{R^2}\ \text{或}\ |z_j| \ge \theta_z\right)
\]

公式中：

- `j` 是从 0 开始的分段序号；
- `\widehat r_j` 是第 `j` 段拟合 log price 的终点值减起点值，单位为 log return；
- `|x|` 表示 `x` 的绝对值；
- `R_j^2` 是第 `j` 段实际 log close 相对连续拟合线的决定系数，理论解释范围为 0～1；
- `z_j` 是第 `j` 段的波动率调整趋势；
- `\theta_r = 0.02` 是最低拟合 log return 绝对值，约等于 2.02% 简单收益；
- `\theta_{R^2} = 0.35` 是最低线性度；
- `\theta_z = 0.75` 是最低波动率调整趋势绝对值。

波动率调整趋势定义为：

\[
z_j = \frac{s_j\sqrt{m_j}}{\sigma_j}
\]

其中：

- `s_j` 是第 `j` 段拟合 log price 的每 bar 斜率；
- `m_j` 是第 `j` 段用于计算收益的相邻 bar 间隔数；
- `\sqrt{m_j}` 是 `m_j` 的平方根；
- `\sigma_j` 是第 `j` 段日度 log return 的样本标准差；
- 若 `\sigma_j` 不存在或接近 0，则 `z_j` 为空，不能单独使该段通过方向条件。

通过有效方向条件后，`\widehat r_j > 0` 分类为 `up`，`\widehat r_j < 0` 分类为 `down`；
未通过条件的分段分类为 `flat`。分类阈值放在 `adaptive_segmentation` 自身配置下，不再隐式依赖
`trend_pattern` 配置，避免两个 pipeline 之间形成隐藏耦合。

### 17.6 同类分段合并

按 `segment_index` 排序后，对相邻且类型相同的初始分段执行稳定合并：

- `up + up -> up`；
- `down + down -> down`；
- `flat + flat -> flat`。

合并段保存其覆盖的源 `segment_index` 列表、最早起点、最晚终点和左右方向。合并阶段只用于
生成 pivot seed，不将各段独立 OLS 指标简单相加后冒充新的回归结果；最终统计全部由 pivot
固定后重新执行的全局连续 OLS 产生。

### 17.7 Pivot seed 类型

每个合并段内部终点（最后一个终点除外）按相邻类型生成一个 pivot seed：

| 左侧类型 → 右侧类型 | Pivot seed 类型 | 解释 |
|---|---|---|
| `up → down` | `high` | 上涨转为下跌 |
| `up → flat` | `high` | 上涨进入平台 |
| `flat → down` | `high` | 平台向下离开 |
| `down → up` | `low` | 下跌转为上涨 |
| `down → flat` | `low` | 下跌进入平台 |
| `flat → up` | `low` | 平台向上离开 |

同类合并后不会出现 `up → up`、`down → down` 或 `flat → flat`。`up → flat → down` 可能在平台
两端产生两个 `high` seed，`down → flat → up` 可能产生两个 `low` seed；后续归一化只保留合法且
更极端的同类 pivot。

### 17.8 局部 Pivot 搜索与确定性规则

设 seed 所在 bar 为 `q`，窗口 observation 数为 `n`，默认搜索半径为 `h = 5` bars。搜索范围为：

\[
[\max(0,q-h),\ \min(n-1,q+h)]
\]

其中 `max` 取两个值中的较大值，`min` 取两个值中的较小值，方括号在本公式中表示左右端点均
包含的离散搜索集合。

- `high` seed 在该范围内搜索最高 close；
- `low` seed 在该范围内搜索最低 close；
- pivot 的横坐标来自找到的 bar，pivot price 等于该 bar 的 close；
- pivot 搜索和后续 `log(close)` 回归使用同一价格序列，不读取 `open/high/low/volume`；
- 极值相同时，先选择距 seed 较近者；距离仍相同时选择较早日期，保证结果可重复。

Pivot 选择必须同时满足：

1. pivot bar 索引严格递增；
2. 首段、相邻 pivot 段和终段均满足 `pivot_refinement.min_segment_bars`；
3. 同一 bar 不生成多个最终 pivot；
4. 连续同类 pivot 只保留 close 更高的 `high` 或 close 更低的 `low`；
5. 首选极值违反约束时，在原 ±5 bars 范围内选择次优合法极值；
6. 整个局部范围内不存在合法位置时舍弃该 seed，并持久化舍弃原因供审计。

实现应采用确定性冲突消解，不依赖随机数或数据库返回顺序。

### 17.9 Pivot bar 与半开边界映射

设窗口包含按时间升序排列的 close observations：

\[
P_0,P_1,\ldots,P_{n-1}
\]

其中 `P_i` 是 bar `i` 的 close，`n` 是 observation 数。设最终内部 pivot bar 为：

\[
p_1 < p_2 < \cdots < p_K
\]

其中 `K` 是最终内部 pivot 数。半开分段边界定义为：

\[
b_0=0,\quad b_k=p_k+1\ (1\le k\le K),\quad b_{K+1}=n
\]

第 `j` 段拥有的 observations 继续定义为：

\[
[b_j,b_{j+1})
\]

其中左方括号表示包含 `b_j`，右圆括号表示不包含 `b_{j+1}`。但是第 `j > 0` 段的几何拟合
起点和收益锚点是共享 pivot bar `b_j - 1 = p_j`；该 bar 由左段拥有，同时作为右段进入收益的
起点。由此满足：

- 数据归属无重叠、无遗漏；
- 相邻拟合线共享同一个 pivot 横坐标；
- 各段实际 log return 之和等于整个 250-bars 窗口的实际 log return；
- `pivot_bar_index` 与 `boundary_index` 不会发生一根 bar 的语义错位。

新表不得仅使用含义不明确的 `start_bar_index/end_bar_index`。应分别保存
`start_boundary_index`、`end_boundary_index_exclusive`、`start_endpoint_bar_index` 和
`end_endpoint_bar_index`。

### 17.10 固定 Pivot 的全局连续拟合

令：

- `i` 是窗口内 bar 索引，取值为 0～`n-1`；
- `y_i = log(P_i)`，其中 `P_i` 是第 `i` 根 bar 的 close；
- `p_k` 是第 `k` 个内部 pivot 的 bar 索引；
- `K` 是内部 pivot 数；
- `β_0` 是回归截距；
- `β_1` 是第一个线性区间的基础斜率；
- `δ_k` 是通过 pivot `p_k` 后的斜率变化；
- `ε_i` 是 observation `i` 的拟合残差。

最终模型为：

\[
y_i = \beta_0 + \beta_1 i
      + \sum_{k=1}^{K}\delta_k\max(0,i-p_k)
      + \varepsilon_i
\]

`Σ` 表示对 `k = 1` 到 `K` 求和，`max(0,i-p_k)` 是 linear-spline hinge 项。所有系数通过一次
全局普通最小二乘求解，因此拟合曲线在每个 `p_k` 处连续。

Pivot 只固定 knot 的横坐标，不强制拟合曲线经过该 bar 的实际 close。该选择保留普通最小二乘的
统计含义；如果未来要求强制穿过 pivot close，应另立不兼容方案和新计算版本。

### 17.11 最终分段指标

每个 pivot-refined segment 至少保存：

- `segment_type`：按第 17.5 节相同口径重新分类的 `up/down/flat`；
- `log_slope_per_bar`；
- `linearity_r2`；
- `fitted_start_log_price`、`fitted_end_log_price` 和 `fitted_log_return`；
- `actual_start_log_price`、`actual_end_log_price` 和 `actual_log_return`；
- `realized_volatility_daily`；
- `vol_adjusted_trend`；
- `efficiency_ratio`；
- 半开边界、共享端点、日期和 observation/return interval 数。

最终 `segment_type` 必须根据重新拟合后的指标计算，不能直接继承用于生成 seed 的初始段类型。

### 17.12 数据库契约

新增两张独立表，不覆盖现有自适应分段和长期形态结果。

原 `v1.0.0` 草案曾把 pivot 和 segment 分别设计为 `pivot_point_daily` 与
`pivot_segment_daily`。两者的主键尾列名称不同，行数也分别为 `S-1` 和 `S`，并非天然相同；
但是内部 pivot 同时是左侧 segment 的终点和右侧 segment 的共享起点。`v2.0.0` 明确规定 pivot
元数据归属于左侧 segment 的终点，因此可以合并到一张 segment 明细表而不丢失语义。

#### `pivot_segmentation_daily`

主键：`(symbol, date, lookback_bars)`。

保存窗口 observation 数、pivot 数、最终段数、全局 RSS、搜索半径、最短段长度、分类阈值、源分段
method/version、当前 method/version 和冲突消解摘要。

#### `pivot_segment_daily`

主键：`(symbol, date, lookback_bars, segment_index)`。

保存第 17.9 节的边界和端点字段、第 17.11 节的数值指标、method 和 calculation version，并在
每一行保存该 segment 终点的节点信息：

- `end_point_type`：`high`、`low` 或 `window_end`；
- `end_point_bar_index`、`end_point_date`；
- `actual_end_log_price` 和可选的 `actual_end_close`；两者与 close pivot price 使用同一口径；
- `pivot_seed_bar_index`、`pivot_search_start_bar_index`、`pivot_search_end_bar_index`；
- `pivot_displacement_bars`；
- `pivot_source_left_type`、`pivot_source_right_type`；
- `pivot_source_segment_indices`；
- `pivot_resolution_status` 和可选审计信息。

对第 `j < S-1` 行，`end_point_type` 为 `high/low`，其终点同时是第 `j+1` 段的共享起点；对最后
一行，`end_point_type = 'window_end'`，所有 seed/search/source 等 pivot 专用字段为空。窗口起点通过
第一行的 `start_endpoint_*` 字段保存，不额外制造 pivot 行。

内部 pivot 数可由 `end_point_type IN ('high', 'low')` 的行数得到，并必须等于 summary 的
`pivot_count`。单段路径只有一条 `window_end` segment 行，`pivot_count = 0`。

推荐索引：

- 两张表均建立 `(date DESC, lookback_bars)`；
- summary 和 segment 均建立 `(symbol, date DESC)`；
- segment 建立 `(date DESC, lookback_bars, end_point_type)`，支持按日期读取 pivot 节点。

Upsert 同一 `symbol/date/lookback_bars` 前必须删除该快照不再存在的旧 segment 明细，避免分段数量
减少后残留脏行。Schema migration 继续使用幂等 SQL；实际执行开发库 migration 前必须
单独展示 SQL diff。

### 17.13 Pipeline 与 CLI

规范批处理顺序为：

```text
market-analysis run-indicators
market-analysis run-adaptive-segmentation
market-analysis run-pivot-segmentation
```

`run-adaptive-segmentation` 批量运行时按 symbol 显示当前处理标的、已处理数/总数、完成百分比和
预计剩余时间；成功写库数量仍由命令结束信息单独报告。跳过或计算失败但继续执行的 symbol 也计入
“已处理数”，避免进度条与实际循环位置不一致。

`run-pivot-segmentation` 使用相同的进度展示，并额外显示当前 lookback bars。其处理单位是一个
`symbol/lookback_bars` 快照；默认只配置 250 bars，因此默认情况下快照数等于 symbol 数。

`run-pivot-segmentation` 读取已持久化的 `adaptive_segmentation_v3` summary/segments，同时从
`market_data.daily_bars_split_adjusted` 读取同一 symbol、date 和 250-bars close，不在
`indicators/` 内连接数据库。

单标的诊断命令为：

```text
market-analysis compute-adaptive-segmentation --symbol AAPL --lookback 250
```

是否额外提供只读 `compute-pivot-segmentation` JSON CLI 在实施阶段按详情页需要决定；若提供，输出
必须包含 summary、segments（包括终点 pivot 元数据）和 fitted points，且不写数据库。

### 17.14 现有形态算法与历史数据

- `trend_pattern_daily` 和 `trend_pattern_v4_daily` 不删除、不覆盖；
- 新版 Dashboard 主快照不再依赖 40/60-bars `trend_pattern` 宽表；
- 旧形态 CLI 标记为 legacy，但在本方案首版不强制删除代码；
- 历史 40/60-bars adaptive 数据继续按原 `calculation_version` 查询；
- 新的 250-bars pivot 分段是独立数据产品，不冒充旧 v3/v4 形态结果。

### 17.15 `investment_dashboard` 影响

本方案会新增数据库表、计算版本、配置键和 CLI 名称，因此 `investment_dashboard` 必须同步修改：

- 新增读取 `pivot_segmentation_daily` 和 `pivot_segment_daily`；
- 不再硬编码 `adaptive_trend_v3` 作为新版快照来源；
- 外部操作按钮改为规范 segmentation CLI；
- 标的详情曲线改用 pivot-refined segments，并可显示 high/low pivot；
- 主快照按 `ID-PIVOT-SNAPSHOT v1.1.0` 计算六个终段指标；
- Dashboard 仍只写本项目会话状态，不向 `market_analysis` 数据库写入派生结果。

下游同步设计记录见 `investment_dashboard/docs/market_snapshot_development.md`。

### 17.16 实施阶段与验收

#### 阶段 A：命名与基线

- [x] 冻结并迁移现有自适应分段测试；
- [x] 完成模块、配置、日志、CLI 和版本名称迁移；
- [x] 保留历史数据和 legacy CLI 兼容；
- [x] 将默认生产 lookback 改为 250 bars。

#### 阶段 B：分类、合并与 Pivot 搜索

- [x] 实现独立纯计算分类函数；
- [x] 实现同类相邻段稳定合并；
- [x] 实现六种 pivot seed 映射；
- [x] 实现 ±5 bars close 极值搜索；
- [x] 实现最短段、同类 pivot 和冲突消解规则。

#### 阶段 C：连续重拟合与持久化

- [x] 实现固定 pivot 横坐标的全局连续 log-linear OLS；
- [x] 实现无歧义的半开边界和共享端点字段；
- [x] 创建两张幂等新表和 upsert/query 函数；
- [x] 增加批量 pipeline、日志、CLI 和失败隔离。

#### 阶段 D：测试与下游交付

- [x] 测试 `up/down/flat` 阈值边界；
- [x] 测试六种转向、平台双 seed、窗口边缘、同价和冲突；
- [x] 断言 pivot 严格递增、分段无重叠/遗漏、拟合在 knot 连续；
- [x] 断言非末段 `end_point_type` 均为 `high/low`、末段为 `window_end`，且
  `pivot_count = segment_count - 1`；
- [x] 断言各段实际 log return 之和等于窗口实际 log return；
- [x] 测试 schema、upsert、旧明细清理和快照查询契约；
- [x] 对本方案变更文件运行 `ruff check`，并运行完整 `pytest`；
- [x] 同步实施 `ID-PIVOT-SNAPSHOT v1.1.0`。

验收时至少使用单边上涨、单边下跌、V 形、倒 V 形、上涨平台后下跌、下跌平台后上涨、窄幅横盘、
相邻 pivot 冲突和窗口边缘 pivot 等合成路径，并抽取代表性 ETF/Option Active 个股进行人工图形复核。

### 17.17 2026-08-15 决策记录

- 确认将 `adaptive_trend_v3` 语义更名为 `adaptive_segmentation_v3`；
- 确认新的生产 lookback 默认值为 250 close observations；
- 确认初始分段先分类为 `up/down/flat`，再合并相邻同类段；
- 确认内部合并段终点生成 `high/low` seed，并在左右各 5 bars 搜索 close 极值；
- 确认 pivot bar 映射为右段半开边界 `boundary = pivot_bar_index + 1`；
- 确认最终拟合使用固定 pivot 横坐标的全局连续 log-close OLS，不强制经过 pivot price；
- 确认 pivot 元数据归属于左侧 segment 的终点，结果进入 summary + segment 两张独立表；
- 方案随后于同日进入实施；实际数据库迁移留给部署时执行 `market-analysis init-db`。

版本记录：

- `v1.0.0`：初始草案；pivot 在 OHLC high/low 中搜索，并计划使用 summary、point、segment 三表；
- `v2.0.0`：已实施方案；pivot 统一在 close 中搜索，删除独立 point 表，将节点及审计字段合并到
  `pivot_segment_daily` 的 segment 终点。

### 17.18 实施结果

- 规范初分段模块、批处理、单标的计算和验证入口已迁移到 `adaptive_segmentation` 命名；旧模块与
  CLI 作为隐藏兼容入口保留一个迁移周期；
- 新增纯计算 `pivot_segmentation.py`、批处理 `run_pivot_segmentation.py`、两张幂等表及完整
  upsert/query 契约；
- 合成测试覆盖六种方向转换、平台双 seed、同价极值、窗口边缘、相邻冲突、连续拟合、收益可加性
  和数据库契约；
- 2026-08-15 验证结果：本方案变更文件 Ruff 通过，完整 pytest 为 `139 passed`；
- 本次未连接或修改实际 PostgreSQL；部署时需先运行 `market-analysis init-db`，再按第 17.13 节
  顺序生成快照。
