# 价格相关性与收益率相关性讨论记录

- **记录日期**：2026-11-01
- **文档性质**：研究结论与指标口径讨论
- **状态**：已整理；不代表已经进入 `market_analysis` 生产计算
- **讨论对象**：以 SMH 与 XLP 为主要例子，研究价格水平、收益率、趋势和相对强弱之间的关系
- **交互图解**：[Pearson Correlation：曲线、收益率与中心化](correlation-price-vs-return-visualization.html)

## 1. 核心结论

本次讨论最重要的区分是：

> **收益率 correlation 衡量“配对收益率相对各自均值的偏离是否呈线性共同波动”；价格 correlation 衡量“配对价格水平相对各自均值的高低是否呈线性关系”。**

不能把前者简单表述为“涨跌方向是否一致”，也不宜称为“return 的时间趋势是否一致”。Pearson
correlation 不读取观测的先后顺序；“return 的变化”若严格解释为 \(\Delta g_t=g_t-g_{t-1}\)，已经是
对数价格的二阶差分，并不是通常所说的收益率 correlation。

二者并不互相替代。对同一对 symbol，它们可能给出方向相反、强弱差异很大的结果，甚至一个有定义、
另一个没有定义。

针对 SMH 与 XLP 的风险偏好或板块轮动观察，主要结论如下：

1. 若目标是观察两者同一根 bar 的收益率相对各自常态是否共同偏强或共同偏弱，应计算收益率
   correlation；若问题是“同涨、同跌或方向相反的 bar 占多少”，应另算同向或反向 bar 比例。
2. 若目标是观察一个 symbol 持续上涨、另一个持续下跌，即使两者每日节奏不同，应分别估计对数价格
   趋势斜率，并观察 `log(SMH/XLP)` 的趋势。
3. 原始价格 correlation 容易被共同时间趋势、窗口长度和复利路径主导，不宜作为主要联动指标。
4. 收益率 correlation 只衡量线性共同波动，不表示哪一方持续占优。方向性应由相对收益或相对价格
   趋势补充。
5. 完整观察至少需要两个维度：
   - **短期共同波动维度**：滚动收益率 correlation 与收益率符号同向/反向比例；
   - **中期方向维度**：各自趋势斜率和 `log(SMH/XLP)` 的斜率。

## 2. Pearson correlation 到底衡量什么

统计学中不加限定的 correlation 通常指 Pearson correlation。它衡量两个变量相对于各自均值的偏离，
是否呈现稳定的线性共同变化。

对 \(N\) 对观测：

\[
(x_1,y_1),\ldots,(x_N,y_N)
\]

样本 Pearson correlation 为：

\[
r=
\frac{
\sum_{i=1}^{N}(x_i-\bar{x})(y_i-\bar{y})
}{
\sqrt{\sum_{i=1}^{N}(x_i-\bar{x})^2}
\sqrt{\sum_{i=1}^{N}(y_i-\bar{y})^2}
}
\]

其中：

- \(i\) 是配对观测序号；
- \(N\) 是配对观测数量，无单位；
- \((x_i,y_i)\) 是第 \(i\) 对观测；
- \(\bar{x},\bar{y}\) 是两个样本各自的均值；
- \(r\) 是样本相关系数，无单位，取值范围为 \([-1,1]\)。

解释如下：

- \(r>0\)：一个变量高于自身均值时，另一个也倾向于高于自身均值；
- \(r<0\)：一个变量高于自身均值时，另一个倾向于低于自身均值；
- \(r=0\)：不存在可检测的线性共同变化，不等价于不存在任何关系；
- \(r=1\)：两个变量存在严格正线性关系；
- \(r=-1\)：两个变量存在严格负线性关系。

若任一变量在窗口内为常数，其标准差为零，correlation 的分母为零，结果没有定义。

Pearson correlation 本身不认识“日期”或“趋势”。只要保持每一对 \((x_i,y_i)\) 不变，同时打乱所有
配对观测的顺序，correlation 不会改变。时间信息来自分析者放入 correlation 的变量，而不是来自
correlation 公式本身。

因此，以下三种说法必须区分：

- **收益率 correlation**：比较 \((g^X_i,g^Y_i)\) 相对各自均值的偏离；
- **收益率符号一致率**：比较 \(\operatorname{sign}(g^X_i)\) 与
  \(\operatorname{sign}(g^Y_i)\)，直接回答同涨、同跌或反向 bar 的比例；
- **收益率变化 correlation**：比较 \((\Delta g^X_i,\Delta g^Y_i)\)，研究的是收益率自身的上升或
  下降，属于另一个指标。

## 3. 价格、收益率与一阶差分

设：

- \(P_t>0\) 是第 \(t\) 根 bar 的价格，单位为报价货币；
- \(t\) 是时间序号，单位为 bar；
- \(p_t=\ln P_t\) 是对数价格，无单位。

简单收益率定义为：

\[
r_t=\frac{P_t}{P_{t-1}}-1
\]

对数收益率定义为：

\[
g_t=\ln\left(\frac{P_t}{P_{t-1}}\right)
=p_t-p_{t-1}
=\Delta p_t
\]

因此，更精确的类比是：

> 收益率接近对数价格的一阶差分，而不是价格本身的一阶导数。

当单期价格变化较小时：

\[
g_t\approx r_t\approx\frac{P_t-P_{t-1}}{P_{t-1}}
\]

连续时间下：

\[
\frac{d\ln P}{dt}=\frac{1}{P}\frac{dP}{dt}
\]

价格包含从起点到当前时点的全部累积变化；收益率只描述相邻两根 bar 之间的变化。对价格做
correlation，会把历史累积、长期漂移和短期联动混在一起。

## 4. 两种 correlation 的正式口径

### 4.1 价格 correlation

对两个 symbol \(X\) 和 \(Y\)，在第 \(t\) 根 bar 使用最近 \(N\) 根价格：

\[
\rho_t^{(P)}=
\operatorname{Corr}
\left(
P^X_{t-N+1:t},
P^Y_{t-N+1:t}
\right)
\]

其中：

- \(P^X_i,P^Y_i\) 是第 \(i\) 根 bar 的价格，单位为各自报价货币；
- \(N\) 是滚动窗口包含的 bar 数；
- \(\rho_t^{(P)}\) 是价格水平 correlation，无单位。

它回答：

> 最近 \(N\) 根 bar 内，一个 symbol 的价格水平高于自身窗口均值时，另一个 symbol 的价格水平是否
> 也倾向于高于（或低于）自身窗口均值，并近似形成线性关系？

它比较的是配对价格水平，不是价格的一阶变化；只是价格路径中的时间趋势经常会主导结果。它不直接
回答两者每期涨跌是否同步。

### 4.2 收益率 correlation

先计算对数收益率：

\[
g^X_i=\ln\left(\frac{P^X_i}{P^X_{i-1}}\right),\qquad
g^Y_i=\ln\left(\frac{P^Y_i}{P^Y_{i-1}}\right)
\]

然后在最近 \(N\) 个收益率观测上计算：

\[
\rho_t^{(g)}=
\operatorname{Corr}
\left(
g^X_{t-N+1:t},
g^Y_{t-N+1:t}
\right)
\]

其中 \(\rho_t^{(g)}\) 是收益率 correlation，无单位。

它回答：

> 最近 \(N\) 个配对收益率中，一个 symbol 的收益率高于自身窗口均值时，另一个 symbol 的收益率是否
> 也倾向于高于（或低于）自身窗口均值？

它不要求两个收益率的绝对符号相同，也不使用观测的时间先后顺序。它不直接回答哪一方在整个窗口内
持续占优、收益率本身的时间趋势、或两条累积价格曲线是否同向。

## 5. 与直觉相反的典型例子

以下例子均使用 Pearson correlation。除非另有说明，时间单位为交易日，价格初值为 100。价格初值
只会对整个价格序列做正数倍缩放，因此不会改变 correlation。

### 5.1 收益率方向永远相反，但收益率 correlation 没有定义

假设连续 10 日：

\[
r^{SMH}_i=+1\%,\qquad r^{XLP}_i=-1\%
\]

两个收益率序列各自都是常数，因此：

\[
\sigma_{r^{SMH}}=0,\qquad \sigma_{r^{XLP}}=0
\]

收益率 correlation 为：

\[
\frac{0}{0}
\]

即没有定义，而不是 \(-1\)。原因是 correlation 衡量围绕各自均值的共同波动；两个序列都没有
波动。

相同场景下，价格为：

\[
P^{SMH}_t=P^{SMH}_0(1.01)^t
\]

\[
P^{XLP}_t=P^{XLP}_0(0.99)^t
\]

使用 10 根价格计算，价格 correlation 约为：

\[
\rho^{(P)}\approx-0.999680
\]

价格 correlation 接近 \(-1\)，但数值会随窗口长度改变。原因是两个指数价格路径在短窗口内近似
两条方向相反的直线，而不是因为收益率存在可估计的共同波动。

### 5.2 收益率严格负相关，但没有持续方向偏好

假设：

\[
r^{SMH}=(+1\%,-1\%,+1\%,-1\%,\ldots)
\]

\[
r^{XLP}=(-1\%,+1\%,-1\%,+1\%,\ldots)
\]

此时：

\[
\rho^{(r)}=-1
\]

10 根价格的 correlation 约为：

\[
\rho^{(P)}\approx-0.998401
\]

两种 correlation 在这个特殊例子中都接近完美负相关，但含义不同：

- 收益率 correlation 表示每天严格反向变化；
- 价格 correlation 表示两条累积价格曲线在该窗口内近似负线性；
- 平均相对收益为零，因为每日收益率差依次为 \((+2\%,-2\%,+2\%,-2\%,\ldots)\)。

因此，这个例子体现强烈且稳定的反向联动，但没有持续偏向 SMH 或 XLP。

### 5.3 收益率仍然严格负相关，两条价格曲线却长期都向上

假设：

\[
r^{SMH}=(+1.5\%,-0.5\%,+1.5\%,-0.5\%,\ldots)
\]

\[
r^{XLP}=(-0.5\%,+1.5\%,-0.5\%,+1.5\%,\ldots)
\]

两个序列满足严格负线性关系：

\[
r^{XLP}_i=1\%-r^{SMH}_i
\]

因此，不论窗口长度：

\[
\rho^{(r)}=-1
\]

但两者的平均简单日收益率都是 \(+0.5\%\)，所以两条价格曲线长期都向上。价格 correlation 会逐渐
被共同上涨趋势支配：

| 窗口 | 收益率 correlation | 价格 correlation |
|---:|---:|---:|
| 10日 | -1.000 | +0.785 |
| 20日 | -1.000 | +0.941 |
| 60日 | -1.000 | +0.993 |
| 120日 | -1.000 | +0.998 |

这是最重要的反直觉例子之一：

> **每日价格变化可以严格负相关，而累积价格水平却可以显示高度正相关。**

若目标是观察每日反向轮动，价格 correlation 会给出错误直觉。

### 5.4 价格 correlation 明显为负，收益率 correlation 接近零

设 60 个交易日内：

\[
\ln P^{SMH}_t
=\ln(100)+0.003t
+0.02\sin\left(\frac{2\pi t}{5}\right)
\]

\[
\ln P^{XLP}_t
=\ln(100)-0.002t
+0.02\sin\left(\frac{2\pi t}{7}+1.2\right)
\]

其中：

- \(t=0,\ldots,59\)，单位为交易日；
- SMH 的长期对数趋势约为每天 \(+0.3\%\)；
- XLP 的长期对数趋势约为每天 \(-0.2\%\)；
- SMH 围绕趋势以 5 日周期波动；
- XLP 围绕趋势以 7 日周期波动，并具有不同相位。

计算结果约为：

\[
\rho^{(P)}\approx-0.882
\]

\[
\rho^{(g)}\approx+0.082
\]

即：

> **价格 correlation 明显为负，收益率 correlation 接近零。**

两个 symbol 的长期趋势方向相反，但它们的短期波动频率和相位不同，因此逐日收益率没有明显线性
联动。这种状态可解释为“异步的持续分化”，而不是“同日同步反向”。

价格 correlation 在这个构造中发现了反向趋势，但它不能判断这种趋势来自真实经济联系，还是两个
无关序列恰好在同一窗口内一个上涨、一个下跌。因此，正式指标应直接估计趋势，而不应把原始价格
correlation 当作趋势指标。

### 5.5 高价格 correlation 不代表稳定长期关系

两只资产只要在同一窗口内共同上涨，就可能得到很高的价格 correlation，即使它们每日收益率之间
几乎没有联系。反之，一个上涨、一个下跌，也可能得到显著负的价格 correlation。

这种由非平稳趋势产生的相关性通常称为伪相关或虚假相关。若要判断价格之间是否存在可持续的长期
均衡关系，应使用协整分析，而不是仅观察价格 correlation。

## 6. 平均相对收益与相对价格趋势

定义简单日收益率差：

\[
d_i=r^{SMH}_i-r^{XLP}_i
\]

最近 \(N\) 日的平均相对收益为：

\[
M_t^{(N)}
=\frac{1}{N}\sum_{i=t-N+1}^{t}d_i
=\bar{r}^{SMH}-\bar{r}^{XLP}
\]

它就是两个 symbol 平均日收益率之差。但使用简单收益率时，它一般不等于“两端累计收益率之差再
除以天数”，因为累计简单收益包含复利。

使用对数收益率时，关系可以严格相加。定义：

\[
g^S_i=\ln\left(\frac{P^S_i}{P^S_{i-1}}\right),
\qquad S\in\{SMH,XLP\}
\]

平均相对对数收益为：

\[
M_{g,t}^{(N)}
=\frac{1}{N}\sum_{i=t-N+1}^{t}
\left(g^{SMH}_i-g^{XLP}_i\right)
\]

展开后：

\[
M_{g,t}^{(N)}
=\frac{1}{N}
\left[
\ln\left(\frac{P^{SMH}_t}{P^{XLP}_t}\right)
-
\ln\left(\frac{P^{SMH}_{t-N}}{P^{XLP}_{t-N}}\right)
\right]
\]

因此它严格等于 `log(SMH/XLP)` 在窗口内的平均每日变化速度：

- \(M_{g,t}^{(N)}>0\)：SMH 相对 XLP 持续走强；
- \(M_{g,t}^{(N)}<0\)：XLP 相对 SMH 持续走强；
- \(M_{g,t}^{(N)}\approx0\)：没有持续偏向，可能只是双方轮流占优。

## 7. 趋势关系不应由原始价格 correlation 单独承担

### 7.1 简单首尾趋势

对 symbol \(S\)，定义过去 \(H\) 根 bar 的平均对数趋势：

\[
m^S_t=
\frac{\ln P^S_t-\ln P^S_{t-H}}{H}
\]

其中：

- \(H\) 是趋势窗口，单位为 bar；
- \(m^S_t\) 是平均每根 bar 的对数增长率。

若：

\[
m^{SMH}_t m^{XLP}_t<0
\]

则两者趋势方向相反。

### 7.2 局部线性回归趋势

为降低首尾异常值的影响，可在最近 \(H\) 根 bar 内拟合：

\[
\ln P^S_{t-H+1+j}
=a^S_t+\beta^S_t j+\varepsilon^S_{t,j},
\qquad j=0,\ldots,H-1
\]

其中：

- \(a^S_t\) 是窗口内截距；
- \(\beta^S_t\) 是每根 bar 的对数价格趋势斜率；
- \(\varepsilon^S_{t,j}\) 是趋势残差；
- 回归 \(R^2\) 可用于描述价格路径被线性趋势解释的程度。

趋势状态可分为：

| SMH 斜率 | XLP 斜率 | 状态 |
|---:|---:|---|
| 正 | 正 | 同向上涨 |
| 负 | 负 | 同向下跌 |
| 正 | 负 | SMH 占优的反向趋势 |
| 负 | 正 | XLP 占优的反向趋势 |

相对趋势斜率为：

\[
\beta^{rel}_t=\beta^{SMH}_t-\beta^{XLP}_t
\]

它等价于对 `log(SMH/XLP)` 拟合趋势斜率。

## 8. 推荐的双维度观察框架

收益率联动和趋势方向应并列展示：

| 趋势方向 | 收益率 correlation | 解释 |
|---|---:|---|
| 相反 | 接近 -1 | 中期方向相反；同日收益率相对各自均值的偏离也反向 |
| 相反 | 接近 0 | 中期方向相反；同日收益率没有明显线性共同波动 |
| 相同 | 接近 -1 | 中期方向相同；同日收益率相对各自均值的偏离反向 |
| 相同 | 接近 +1 | 中期方向相同；同日收益率相对各自均值的偏离同向 |

建议至少观察以下指标：

1. **滚动收益率 correlation**：识别同日收益率相对各自均值的线性共同波动；
2. **反向日比例**：识别多少交易日方向相反；
3. **各自对数价格趋势斜率**：识别两个 symbol 的中期方向；
4. **`log(SMH/XLP)` 趋势斜率**：识别风险偏好持续偏向哪一方；
5. **趋势 \(R^2\)**：区分稳定趋势和噪声主导的首尾变化；
6. **可选的滞后 correlation**：若需要研究谁先变化，计算不同滞后 \(k\) 下的
   \(\operatorname{Corr}(g^{SMH}_t,g^{XLP}_{t-k})\)。

反向日比例可定义为：

\[
O_t=
\frac{1}{N}
\sum_{i=t-N+1}^{t}
\mathbf{1}
\left[
\operatorname{sign}(g^{SMH}_i)
\ne
\operatorname{sign}(g^{XLP}_i)
\right]
\]

其中 \(\mathbf{1}[\cdot]\) 是指示函数，条件成立取 1，否则取 0。

## 9. thinkorswim 内置 Correlation Study 的口径

thinkorswim 官方文档给出的等价实现是：

```thinkscript
plot Correlation = Correlation(close, close(secondSymbol), length);
```

因此，内置 Study 默认比较当前证券与指定证券在相同 aggregation period 下的收盘价水平，而不是
收益率。其计算公式为：

```thinkscript
Covariance(data1, data2, length)
/
(StDev(data1, length) * StDev(data2, length))
```

若要在 thinkorswim 中观察收益率 correlation，可显式构造对数收益率：

```thinkscript
declare lower;

input length = 20;
input correlationWithSecurity = "XLP";

def currentReturn = Log(close / close[1]);
def comparisonReturn =
    Log(close(correlationWithSecurity) / close(correlationWithSecurity)[1]);

plot ReturnCorrelation =
    Correlation(currentReturn, comparisonReturn, length);
```

需要注意：`length` 表示当前图表 aggregation 下的 bar 数，而不是固定的自然日数。例如日线图上的
20 表示 20 根日线，15 分钟图上的 20 表示 20 根 15 分钟 bar。

## 10. 研究边界与后续实现注意事项

本文档只记录指标口径和数学结论，不构成交易策略、买卖信号或生产实现方案。

若未来把相关指标加入 `market_analysis` 或 `investment_dashboard`，需进一步明确：

- 输入使用 split-adjusted 还是 split-and-dividend-adjusted 价格；
- 使用简单收益率还是对数收益率；
- 日线与 intraday 的交易时段和 bar 对齐规则；
- 缺失 bar、停牌和零方差窗口的处理；
- 滚动窗口长度及是否同时展示多个周期；
- correlation、趋势斜率、相对趋势和方向一致率的命名，避免用户把不同含义混为一谈；
- 是否需要在数据库中持久化，或仅由展示层按需计算。

本次仅新增研究文档，不修改数据库、CLI、生产计算或下游查询接口；`investment_dashboard` 无需同步
修改。

## 11. 参考资料

- [thinkorswim Correlation Study](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/C-D/Correlation)
- [thinkScript Correlation 函数](https://toslc.thinkorswim.com/center/reference/thinkScript/Functions/Statistical/Correlation)
- [thinkScript Covariance 函数](https://toslc.thinkorswim.com/center/reference/thinkScript/Functions/Statistical/Covariance.html)
- [thinkScript StDev 函数](https://toslc.thinkorswim.com/center/reference/thinkScript/Functions/Statistical/StDev)
- [thinkScript close 函数与 aggregation 继承规则](https://toslc.thinkorswim.com/center/reference/thinkScript/Functions/Fundamentals/close)
