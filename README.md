# aiden估值助手 / aiden Valuation Assistant

面向飞机与发动机租赁业务的估值工具集：残值曲线、租赁收益、整机/发动机分拆估值与航司信用评估。
A valuation toolkit for aircraft and engine leasing: residual curves, lease returns, airframe/engine split valuation, and airline credit assessment.

**当前版本 / Current version：V2**（见 [`v2/`](v2/)）｜ **历史版本 / Legacy：V1**（见 [`v1/`](v1/)）

---

## 中文介绍

**aiden估值助手**是一个本地优先的航空租赁估值模型套件。核心设计理念：**整机 = 机体 + 发动机**，两者价值行为根本不同（机体随龄单调贬值，发动机随 LLP 寿命件循环数阶梯跳变），必须分开建模。

- **V2（当前版本）**：完整技能包形态，可直接安装到 WorkBuddy 等支持 `SKILL.md` 的智能体。四大模块全部实现——残值估值、租赁收益、整机/发动机分拆 DCF 估值、五家航司信用评估，测试基线 37 passed。
- **V1（初版）**：GitHub 代码仓库形态，仅实现残值估值引擎，租赁收益与资产估值模块在设计稿阶段。

### 目录结构

```
├── v1/    # aiden估值助手 V1 版本（2026-11-18 初版，仅残值引擎，保留归档）
└── v2/    # aiden估值助手 V2 版本（当前版本，完整四模块技能包）
```

### 快速开始（V2）

```bash
cd v2
python scripts/setup.py    # 创建 .venv、安装依赖、运行全部测试（基线 37 passed）
python src/engine_residual.py   # 残值曲线
python src/lease_returns.py     # 租赁收益 IRR / DSCR / MOIC
python src/asset_valuation.py   # 整机与部件 DCF 估值
python src/airline_credit.py    # 航司信用评分与 LTV/LRF 调整
```

WorkBuddy 用户直接上传 `v2/` 目录（或以 `v2/` 打包的 ZIP）安装技能，详见 [`v2/START_HERE.md`](v2/START_HERE.md) 与随包教程。

### 版本对比（V1 → V2）

| 维度 | V1（初版） | V2（当前版） |
|---|---|---|
| 形态 | GitHub 代码仓库 | 可分发技能包（SKILL.md + 完整本地项目） |
| 实现模块 | 仅模块③残值估值引擎 | 四大模块全部实现：残值、租赁收益、分拆估值、航司信用 |
| 残值曲线方法 | 从当前 CMV 出发，按锚点加权平均衰减率外推 | 按出厂机龄锚定（当前机龄点锚 CMV，15/20/25 年出厂点锚原值比例），对数线性插值，锚点扣除发动机基准价值避免重复计算 |
| LLP 计提 | 阈值前线性计提 | 硬阶梯：阈值以上不计提，阈值处全额计提，跳变量精确等于 组 share × 计提强度 |
| 发动机价值地板 | 归零（max(v, 0)） | part-out 地板：半寿价值的 12%，符合老龄残值主要由发动机构成的市场规律 |
| LRF 校准 | 0.70%/月（与市场租金 $230–250k 存在联动矛盾，仅告警） | 0.95%/月（月租约 $228k，与市场租金一致，消除矛盾） |
| 信用评估 | 单一打分配置占位 | 五家航司（Ryanair / AirAsiaX / Delta / easyJet / Lufthansa）分项复算、锚点校验、LTV/LRF 信用调整 |
| 智能体集成 | 无 | SKILL.md 技能定义 + 安装口令 + 一键 setup 脚本 |
| 报告交付 | CSV 输出 | 三份 Excel 报告 + 独立估值 Word 报告规范（DOCX 交付） |
| 测试 | 残值引擎测试 | 5 个测试文件、37 项测试基线 |

### 数据来源与免责

默认参数来自公开行业资料（IBA、Ishka、mba Aviation、Aircraft Commerce、AerCap/Aircastle 披露等），仅区间级校准，不构成投资建议。仅用于学习与技术演示。

---

## English

**aiden Valuation Assistant** is a local-first valuation suite for aircraft and engine leasing. Core design principle: **aircraft = airframe + engines**, whose value dynamics differ fundamentally (airframes depreciate monotonically with age; engines step down at LLP replacement thresholds), so they must be modeled separately.

- **V2 (current)**: a complete, installable skill package for WorkBuddy and other `SKILL.md`-compatible agents. All four modules implemented — residual value, lease returns, split airframe/engine DCF valuation, and airline credit assessment. Test baseline: 37 passed.
- **V1 (legacy)**: the original GitHub codebase with only the residual value engine; lease returns and asset valuation were still design drafts.

### Quick start (V2)

```bash
cd v2
python scripts/setup.py         # create .venv, install deps, run the full test suite (37 passed)
python src/engine_residual.py   # residual curve
python src/lease_returns.py     # lease returns: IRR / DSCR / MOIC
python src/asset_valuation.py   # airframe & engine DCF valuation
python src/airline_credit.py    # airline credit scoring, LTV/LRF adjustments
```

WorkBuddy users can install the skill directly by uploading the `v2/` folder (or a ZIP of it). See [`v2/START_HERE.md`](v2/START_HERE.md) and the bundled tutorial.

### Version comparison (V1 → V2)

| Aspect | V1 (initial) | V2 (current) |
|---|---|---|
| Form | GitHub codebase | Distributable skill package (SKILL.md + full local project) |
| Modules | Residual engine only | All four: residual, lease returns, split valuation, airline credit |
| Residual curve | Extrapolates from current CMV with anchor-weighted average decay | Anchored to manufacture age (current age → CMV; years 15/20/25 → % of original price), log-linear interpolation, engine baseline stripped from anchors to avoid double counting |
| LLP accrual | Linear before threshold | Hard step: zero above threshold, full accrual at threshold, step size = group share × accrual intensity |
| Engine value floor | Zero | Part-out floor at 12% of half-life value, matching the market pattern that old-age residual value is engine-driven |
| LRF calibration | 0.70%/month (inconsistent with market rent $230–250k, warning only) | 0.95%/month (≈$228k/month rent, consistent with market) |
| Credit assessment | Single score placeholder | Five airlines (Ryanair / AirAsiaX / Delta / easyJet / Lufthansa) with line-item re-computation, anchor validation, LTV/LRF credit adjustments |
| Agent integration | None | SKILL.md definition + install commands + one-shot setup script |
| Reporting | CSV output | Three Excel reports + independent valuation Word report spec (DOCX delivery) |
| Tests | Residual engine tests | 5 test files, 37-test baseline |

### Disclaimer

Default parameters are calibrated against public industry sources (IBA, Ishka, mba Aviation, Aircraft Commerce, AerCap/Aircastle filings) to interval-level accuracy only. Not investment advice. For educational and demonstration purposes.
