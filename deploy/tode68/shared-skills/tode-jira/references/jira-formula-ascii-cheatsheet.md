# Jira 数学公式 ASCII 写法速查表

适用场景：在 Jira Wiki Markup 正文里表达公式、约束、上下界、目标函数、伪代码式数学描述。

结论先行：**不要在 Jira 正文里写 LaTeX / MathJax。统一改成 ASCII 可读形式，并放进 `{code}` 块。**

## 总规则

- 一条约束一行
- 统一放进 `{code}` 块
- 变量名优先保留业务可读性，不追求排版感
- 不依赖数学上标 / 下标渲染
- 乘号优先写 `*`
- 上下界优先拆成两行
- 需要中文说明时，把解释写在 `{code}` 块外

## 推荐骨架

```text
h2. 关键约束

{code}
constraint 1
constraint 2
constraint 3
{code}
```

## 常见改写规则

| 原始数学意图 | 推荐 ASCII 写法 | 不推荐写法 |
|---|---|---|
| 下标 | `C_trans`, `P_ac`, `SOC_t` | `C_{trans}`, `P_{ac}`, `SOC_t`（指望下标渲染） |
| 希腊字母 | `tau`, `alpha`, `beta`, `lambda` | `τ`, `α`, `β`, `λ` |
| 乘法 | `a * b` | `ab`, `a \cdot b`, `a × b` |
| 分式 | `(a + b) / c` | `\frac{a+b}{c}` |
| 求和 | `sum_g P(g,t)` | `\sum_g P_{g,t}` |
| 最小化 / 最大化 | `min total_cost`, `max revenue` | `\min`, `\max` |
| 绝对值 | `abs(x)` | `|x|`（尤其复杂表达式） |
| 属于 | `x in S` | `x \in S` |
| 对所有 | `for all t in T` | `\forall t \in T` |
| 存在 | `exists g` | `\exists g` |
| 蕴含 / 推出 | `A -> B` | `A \Rightarrow B` |
| 不等于 | `!=` | `\neq` |
| 大于等于 / 小于等于 | `>=`, `<=` | `\ge`, `\le` |

## 变量命名建议

### 推荐

```text
P_gen(g,t)
P_load(b,t)
C_trans(l,tau)
SOC(s,t)
eta_charge(s)
P_max(g)
P_min(g)
```

### 不推荐

```text
P_{gen}^{max}
C_{trans}(l,\tau)
\eta_{charge}
SOC_{s,t}
```

## 常见模式模板

### 1. 上下界

```text
{code}
P_min(g) <= P_gen(g,t)
P_gen(g,t) <= P_max(g)
{code}
```

### 2. 正负双边约束

```text
{code}
-ramp_down(g) <= P_gen(g,t) - P_gen(g,t-1)
P_gen(g,t) - P_gen(g,t-1) <= ramp_up(g)
{code}
```

### 3. 分式改写

```text
{code}
avg_cost = total_cost / total_energy
loss_rate = loss_power / send_power
{code}
```

### 4. 求和改写

```text
{code}
sum_g P_gen(g,t) + sum_r P_ren(r,t) = sum_b P_load(b,t)
{code}
```

若求和项较长，允许拆行并先加说明：

```text
发电侧总出力 = 常规机组 + 新能源 + 储能放电

{code}
sum_g P_gen(g,t) + sum_r P_ren(r,t) + sum_s P_discharge(s,t)
= sum_b P_load(b,t) + sum_s P_charge(s,t)
{code}
```

### 5. 目标函数

```text
{code}
min total_cost

total_cost = fuel_cost + startup_cost + curtailment_penalty + shed_penalty
{code}
```

### 6. 绝对值 / 偏差

```text
{code}
deviation = abs(P_actual(t) - P_target(t))
{code}
```

### 7. 条件逻辑

```text
{code}
if unit_on(g,t) = 0 -> P_gen(g,t) = 0
if charge_flag(s,t) = 1 -> discharge_flag(s,t) = 0
{code}
```

## TEAP 常见示例

### 输电成本上下界

```text
{code}
-c_trans(l,tau) * P_ac(l,tau) <= C_trans(l,tau)
C_trans(l,tau) <= c_trans(l,tau) * P_ac(l,tau)
{code}
```

### 储能荷电状态更新

```text
{code}
SOC(s,t) = SOC(s,t-1)
         + eta_charge(s) * P_charge(s,t)
         - P_discharge(s,t) / eta_discharge(s)
{code}
```

### 机组启停与出力绑定

```text
{code}
P_gen(g,t) <= P_max(g) * unit_on(g,t)
P_gen(g,t) >= P_min(g) * unit_on(g,t)
{code}
```

## 明确不要这样写

```text
$C_{trans}(l,\tau) \le \frac{1}{2} P_{ac}(l,\tau)$
\[
\min \sum_t \sum_g c_g P_{g,t}
\]
\forall t \in T
```

## 选择原则

如果你在两种写法之间犹豫，优先选：

1. **更容易直接读懂** 的写法
2. **更像代码 / 伪代码** 的写法
3. **更不依赖排版渲染** 的写法

宁可朴素，不赌渲染。
