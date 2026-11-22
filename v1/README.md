# aiden估值助手 V1 版本（初版归档）
# aiden Valuation Assistant — V1 (legacy archive)

> 这是 aiden估值助手 的初版（2026-11-18 发布），仅实现残值估值引擎，保留归档供对照。
> This is the initial release (2026-11-18) with only the residual value engine, kept for reference.
> **当前版本请见仓库根目录的 V2：[`../`](..) ｜ 版本对比见根 README。**

[中文](#中文介绍) | [English](#english)

---

## 中文介绍

面向飞机租赁业务的一体化估值模型套件，覆盖**租赁收益测算、资产估值、残值估值**三大模块。核心设计理念：**整机 = 机体 + 发动机**，两者价值行为根本不同（机体随龄单调贬，发动机随 LLP 寿命件循环数阶梯跳变），必须分开建模。

设计文档见 [MODEL_SPEC.md](MODEL_SPEC.md)。默认参数已按 IBA、Ishka、mba Aviation、Aircraft Commerce 等公开来源校准，所有输出报告附假设来源与可信度标注。

### 三大模块

| 模块 | 功能 | 状态 |
|---|---|---|
| `engine_residual.py` | 逐月残值曲线：机体经济折旧锚点插值 + 发动机 LLP 更换调整，三情景（Base / Downside / Severe） | ✅ 已实现 |
| `asset_valuation.py` | 机体与发动机分开的 DCF 估值 + WACC 敏感性 | 待开发（task-04） |
| `lease_returns.py` | 租赁交易收益：Equity IRR、MOIC、DSCR、CFADS、债务摊还 | 待开发（task-03） |

### 快速开始

```bash
pip install -r requirements.txt
pytest                          # 运行测试
python src/engine_residual.py   # 生成残值曲线 outputs/residual_curve.csv
```

### 目录结构

```
├── MODEL_SPEC.md          # 设计规格总文档（含数据字典、校验基准、任务拆分）
├── config/
│   └── aircraft.yaml      # 全部假设参数（禁止硬编码路径）
├── src/
│   ├── config_loader.py   # YAML → dataclass 配置加载器
│   └── engine_residual.py # 模块③：残值估值引擎
├── tests/
│   └── test_engine_residual.py
└── references/            # 参考项目 A/B（思路参考，非继承）
```

### 数据来源与免责

默认参数来自公开行业资料（IBA、Ishka、mba Aviation、Aircraft Commerce、AerCap/Aircastle 披露等），残值曲线等仅区间级校准，不构成投资建议。仅用于学习与技术演示。

---

## English

An integrated valuation suite for aircraft leasing, covering **lease returns, asset valuation, and residual value modeling**. Core design principle: **aircraft = airframe + engines**, whose value dynamics differ fundamentally (airframes depreciate monotonically with age; engines step down at LLP life-limited-part replacement thresholds), so they must be modeled separately.

See [MODEL_SPEC.md](MODEL_SPEC.md) for the full design spec. Default parameters are calibrated against public sources (IBA, Ishka, mba Aviation, Aircraft Commerce, AerCap/Aircastle filings); all output reports carry source and confidence annotations.

### Modules

| Module | Function | Status |
|---|---|---|
| `engine_residual.py` | Monthly residual curve: airframe economic-depreciation anchor interpolation + engine LLP-replacement adjustment, three scenarios (Base / Downside / Severe) | Implemented |
| `asset_valuation.py` | Separate DCF valuation for airframe and engines, WACC sensitivity | Pending (task-04) |
| `lease_returns.py` | Lease transaction returns: Equity IRR, MOIC, DSCR, CFADS, debt amortization | Pending (task-03) |

### Quick Start

```bash
pip install -r requirements.txt
pytest                          # run tests
python src/engine_residual.py   # generate outputs/residual_curve.csv
```

### Disclaimer

Default parameters are based on public industry sources and are calibrated to interval-level accuracy only. Not investment advice. For educational and demonstration purposes.
