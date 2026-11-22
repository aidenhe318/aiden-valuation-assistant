# MODEL_SPEC — 整机与发动机租赁估值套件 / Aircraft & Engine Lease Valuation Suite

**版本**：v1.0（设计稿，部分已实现）
**参考素材**：`references/asset-based-finance-models`（下称 A）、`references/aviation-lease-financial-model`（下称 B）
**技术栈**：Python 3.11+（pandas / numpy / numpy-financial / matplotlib / openpyxl / PyYAML），YAML 配置，Excel 仅作输出
**原则**：参考 A/B 的思路与数据，**不继承其代码结构与硬编码路径**；三个模块可独立运行，共享统一数据层。

---

## 0. 目标 Goal

```
模块③ 残值估值 ──→ 模块① 租赁收益测算
      │                    ↑
模块② 资产估值 ───────────┘（估值→定价/LTV 校验）
```

- **模块① Lease Returns**：整机/发动机租赁交易的权益回报与偿债能力（Equity IRR、MOIC、DSCR、CFADS）
- **模块② Asset Valuation**：机体与发动机**分开**的 DCF 估值
- **模块③ Residual Value**：三情景残值曲线，回灌模块①的期末现金流

---

## 1. 核心设计决策：整机 = 机体 + 发动机 / Aircraft = Airframe + Engines

| | 机体 Airframe | 发动机 Engine |
|---|---|---|
| 折旧驱动 | 机龄、利用率、市场成熟度 | **循环数 cycles**、LLP 剩余寿命 |
| 价值曲线 | 近似单调递减 | 阶梯式（LLP 到期跳变） |
| 关键输入 | 出厂年、月飞行小时 FH | 总循环数、LLP 分组（限额/成本占比） |
| 估值方法 | 经济折旧 + 市场对比 | maintenance-adjusted DCF |

> **整机公允价值 = 机体 DCF + Σ 各台发动机（DCF − LLP 负债准备）**。这是本套件相对 A/B 的主要增值点。

---

## 2. 配置层（`config/aircraft.yaml`，默认值已校准）

```yaml
asset:
  type: B737-800
  year_of_manufacture: 2016
  purchase_price_usd: 51_000_000       # 2016 新机交付价（约值）
  current_market_value_usd: 24_000_000 # 10 年机龄 CMV $22–26M（IBA/Ishka）
  fh_per_fc: 2.0

engines:
  model: CFM56-7B26
  count: 2
  half_life_value_each_usd: 5_000_000  # IBA：半寿在役单台 $4M–6M
  llps:
    - {group: Fan_LPC, limit_cycles: 30000, shipset_share: 0.30}
    - {group: HPC_HPT,  limit_cycles: 20000, shipset_share: 0.45}
    - {group: LPT,      limit_cycles: 25000, shipset_share: 0.25}
  llp_shipset_cost_usd: 3_900_000      # CFM56 套件目录价（-5B 外推，可信度中）
  shop_visit_cost_usd: 1_300_000       # 全大修；性能恢复 $0.8M
  first_shop_visit_cycles: 14000

lease:
  term_months: 96
  lease_rate_factor: 0.0070            # 市场 0.65–0.75%/月
  escalation_pct: 0.0
  # ⚠️ 联动校验：0.70% × $24M ≈ $168k/月 < 市场租 $230–250k/月，模型须自动提示

debt:
  ltv: 0.70                            # 行业 65–80%
  interest_rate_annual: 0.060          # 头部 ~4%，中小 5.5–6.5%
  amortization: "monthly_12yr"

residual_value_curve:                  # 占原值 %，公开数据仅区间级
  year_15: 0.40                        # mba Aviation 直接披露
  year_20: 0.32
  year_25: 0.15                        # ≈ part-out 价值，主要由发动机决定

wacc: {rf: 0.04, erp: 0.055, beta: 1.2, kd: 0.06, tax: 0.21, target_d_e: 2.0}

scenarios:
  base:     {fuel_pct: 0.00, demand_pct: 0.00,  margin_comp: 0.000}
  downside: {fuel_pct: 0.15, demand_pct: -0.08, margin_comp: 0.025}
  severe:   {fuel_pct: 0.30, demand_pct: -0.20, margin_comp: 0.050}

lessee_credit:
  score: 66.5                          # 复用 A 的打分框架（0–100）
  # 规则：score < 60 → LTV 上限降至 60%、LRF +10bp
```

### 校准来源与可信度

| 参数 | 来源 | 可信度 |
|---|---|---|
| 737-800 10 年机龄 CMV $22–26M | IBA、Ishka | 高 |
| LRF 0.65–0.75%/月；737-800 月租 $230–250k | IBA 2024-09、Ishka | 高 |
| LTV 65–80%；利率 4–6.5% | AerCap 20-F、Avation、FLYING Finance | 高 |
| CFM56-7B LLP：Fan/LPC 30k、HPC/HPT 20k、LPT 25k FC | Aircraft Commerce、EASA TCDS ALS | 高 |
| LLP shipset ~$3.9M；大修 $1.3M | Aircraft Commerce（-5B 外推） | 中 |
| 发动机半寿 $4–6M；full-life ~$13M（2026 行情） | IBA、SimpleFlying、avi-go | 高/中 |
| 15 年残值 40%；20–25 年 10–35% | mba Aviation 737-800 Market Profile | 中 |
| 目标 Equity IRR 10–15% | Aircastle/AerCap 公开表述 | 中 |

**已知局限**：逐岁残值曲线、逐件 LLP 限额均为付费库内容（IBA Insight / Ascend / Ishka / CFM ESM Ch.5），公开数据只支持区间级校准。

---

## 3. 模块规格

### 模块③ `src/engine_residual.py` ✅ 已实现

- **输入**：`asset` + `engines` 配置、月利用率（循环/月）、情景参数
- **逻辑**：逐月推进循环数；某组 LLP 剩余寿命 < `llp_replacement_threshold_cycles`（默认 500）时计提更换支出（= shipset_cost × share）；情景调整需求侧残值因子
- **特殊因子**：`engine_premium_switch`（默认 False）——发动机超额溢价（CFM56-7B 因 LEAP 供应链问题从 ~$6M 涨至 ~$13M）作为**可开关的市场因子**，默认关闭
- **输出**：`outputs/residual_curve.csv` — 逐月整机残值（机体锚点插值 + 发动机调整值 × count），三情景各一列

### 模块② `src/asset_valuation.py`（task-04）

- 机体：租约现金流 DCF（结构参照 B），经济折旧（参照 A）
- 发动机：maintenance-adjusted DCF（现金流 − 预期 LLP 更换支出）
- WACC 按 config 透明构建；敏感性：WACC ±200bp、LRF ±25bp
- 输出：`outputs/valuation_report.xlsx`；恒等式：整机 = 机体 + Σ发动机，误差 < 1%

### 模块① `src/lease_returns.py`（task-03）

1. 月租金 = LRF × 资产定价基数（含 escalation）
2. 债务：LTV 提款 → 月摊还表 → CFADS → DSCR（含 Min DSCR）
3. 权益现金流 → 月度 XIRR、MOIC
4. 信用调整：score < 60 → LTV 上限 60%、LRF +10bp
5. 租金合理性校验：737-800 偏离 $150k–280k/月 自动警告
- 输出：`outputs/returns_report.xlsx`

---

## 4. 校验基准（Validation Targets）

| # | 校验项 | 目标 |
|---|---|---|
| 1 | 基准案例 Equity IRR（96 月、LRF 0.70%、LTV 70%、6%） | 10%–15% |
| 2 | Min DSCR | ≥ 1.15 |
| 3 | 月租金合理性 | $150k–280k/月（737-800） |
| 4 | 第 15 年残值 | ≈ 原值 40% ± 5pp |
| 5 | LLP 阶梯测试 | 更换时点发动机估值向下跳变 ≈ shipset_cost × 组 share |
| 6 | 25 年残值构成 | ≥ 60% 来自发动机 |
| 7 | 拆分恒等式 | 整机 = 机体 + Σ发动机，误差 < 1% |
| 8 | 单元测试 | 现金流/XIRR 与手算样例误差 < 1e-6 |

任何一项不通过 → 对应任务回滚，不影响其他模块。

---

## 5. 给 K3.1 的任务拆分

1. **task-01** 仓库骨架 + config_loader ✅ 已完成
2. **task-02** engine_residual.py + 校验 #5 ✅ 已完成
3. **task-03** lease_returns.py + 校验 #1、#2、#3、#8
4. **task-04** asset_valuation.py + 敏感性 + 校验 #7
5. **task-05** 信用调整因子 + 租金合理性警告 + 仪表盘
6. **task-06** 用 A 仓库五家航司财报做信用打分接入示例（锚点：Ryanair 66.5 / AirAsiaX 51.55 / Delta 50.75 / easyJet 46.8 / Lufthansa 43.6）

---

## 6. 三项特别声明

1. **残值曲线是公开数据最薄弱的地方**：实现为"锚点插值 + 发动机调整值"，不得假装有精确曲线；输出必须标注来源与可信度。
2. **LRF 与资产定价联动校验**：模型必须自动检测租金低于市场水平的矛盾并提示。
3. **发动机溢价开关**：`engine_premium_switch` 默认关闭，不得把 2025-26 特殊行情内化为默认残值。
