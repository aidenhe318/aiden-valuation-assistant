"""配置加载器 / Config loader.

将 config/aircraft.yaml 加载为类型安全的 dataclass 对象。
Load config/aircraft.yaml into typed dataclass objects.

设计约束（MODEL_SPEC task-01）：
- 禁止硬编码绝对路径（修正参考项目 A 的 D:\\New Project 问题）
- 所有假设的唯一来源是本配置文件
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 仓库根目录（本文件位于 src/ 下）
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "aircraft.yaml"


class ConfigError(ValueError):
    """配置校验失败 / Configuration validation error."""


# ── dataclass 定义 ──────────────────────────────────────────────


@dataclass
class AssetConfig:
    type: str
    year_of_manufacture: int
    purchase_price_usd: float
    current_market_value_usd: float
    fh_per_fc: float
    monthly_utilization_fc: float


@dataclass
class LlpGroup:
    group: str
    limit_cycles: float
    shipset_share: float


@dataclass
class EnginesConfig:
    model: str
    count: int
    half_life_value_each_usd: float
    current_cycles: float
    llps: list[LlpGroup]
    llp_shipset_cost_usd: float
    shop_visit_cost_usd: float
    first_shop_visit_cycles: float
    llp_replacement_threshold_cycles: float
    engine_premium_switch: bool


@dataclass
class LeaseConfig:
    term_months: int
    lease_rate_factor: float
    escalation_pct: float
    pricing_base_usd: float


@dataclass
class DebtConfig:
    ltv: float
    interest_rate_annual: float
    amortization: str


@dataclass
class ResidualCurveConfig:
    """残值曲线锚点（占原值比例）。/ Residual curve anchors as fractions of original value."""

    year_15: float
    year_20: float
    year_25: float

    def anchors(self) -> list[tuple[float, float]]:
        """返回 [(year, fraction), ...]，含第 0 年 (1.0, 原值) 与 30 年外推点。"""
        return [(0.0, 1.0), (15.0, self.year_15), (20.0, self.year_20),
                (25.0, self.year_25), (30.0, self.year_25 * 0.5)]


@dataclass
class WaccConfig:
    rf: float
    erp: float
    beta: float
    kd: float
    tax: float
    target_d_e: float

    @property
    def cost_of_equity(self) -> float:
        return self.rf + self.beta * self.erp

    @property
    def wacc(self) -> float:
        d = self.target_d_e
        return (self.cost_of_equity * 1.0 + self.kd * (1 - self.tax) * d) / (1.0 + d)


@dataclass
class Scenario:
    fuel_pct: float
    demand_pct: float
    margin_comp: float


@dataclass
class LesseeCreditConfig:
    score: float
    ltv_cap_low_score: float
    lrf_spread_low_score_bp: float


@dataclass
class OutputConfig:
    dir: str = "outputs"


@dataclass
class SuiteConfig:
    asset: AssetConfig
    engines: EnginesConfig
    lease: LeaseConfig
    debt: DebtConfig
    residual_value_curve: ResidualCurveConfig
    wacc: WaccConfig
    scenarios: dict[str, Scenario]
    lessee_credit: LesseeCreditConfig
    output: OutputConfig = field(default_factory=OutputConfig)


# ── 校验 ────────────────────────────────────────────────────────


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def _validate(cfg: SuiteConfig) -> None:
    a = cfg.asset
    _check(a.purchase_price_usd > 0, "asset.purchase_price_usd 必须为正")
    _check(0 < a.current_market_value_usd <= a.purchase_price_usd,
           "asset.current_market_value_usd 应在 (0, 购机价] 区间内")
    _check(1.0 <= a.fh_per_fc <= 4.0, "asset.fh_per_fc 应在 [1.0, 4.0] 区间")
    _check(a.monthly_utilization_fc > 0, "asset.monthly_utilization_fc 必须为正")

    e = cfg.engines
    _check(e.count >= 1, "engines.count 必须 >= 1")
    _check(abs(sum(g.shipset_share for g in e.llps) - 1.0) < 1e-6,
           "engines.llps 的 shipset_share 之和必须为 1.0")
    _check(all(g.limit_cycles > 0 for g in e.llps), "LLP 循环限额必须为正")
    _check(e.llp_replacement_threshold_cycles >= 0, "更换阈值不能为负")

    l = cfg.lease
    _check(0 < l.lease_rate_factor < 0.05, "lease_rate_factor 异常（应在 0–5%/月 内）")
    _check(l.term_months > 0, "租期必须为正")

    d = cfg.debt
    _check(0 < d.ltv < 1.0, "LTV 应在 (0, 1) 区间")

    rv = cfg.residual_value_curve
    _check(1.0 > rv.year_15 > rv.year_20 > rv.year_25 > 0,
           "残值锚点必须单调递减且在 (0, 1) 区间")

    _check(len(cfg.scenarios) >= 1 and "base" in cfg.scenarios,
           "scenarios 至少须包含 base")


# ── 加载 ────────────────────────────────────────────────────────


def load_config(path: str | Path | None = None) -> SuiteConfig:
    """加载并校验配置文件。/ Load and validate the configuration file."""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    p = p.resolve()
    # 禁止绝对路径硬编码：仅允许仓库内的相对路径或显式传入的绝对路径
    _check(p.exists(), f"配置文件不存在: {p}")
    try:
        _check(os.path.commonpath([str(p), str(REPO_ROOT)]) == str(REPO_ROOT),
               "配置文件必须位于仓库目录内（禁止外部绝对路径）")
    except ValueError:
        raise ConfigError("配置文件路径非法") from None

    with open(p, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    asset = AssetConfig(**raw["asset"])
    engines = EnginesConfig(
        **{k: v for k, v in raw["engines"].items() if k != "llps"},
        llps=[LlpGroup(**g) for g in raw["engines"]["llps"]],
    )
    lease = LeaseConfig(**raw["lease"])
    debt = DebtConfig(**raw["debt"])
    rv = ResidualCurveConfig(**raw["residual_value_curve"])
    wacc = WaccConfig(**raw["wacc"])
    scenarios = {name: Scenario(**s) for name, s in raw["scenarios"].items()}
    credit = LesseeCreditConfig(**raw["lessee_credit"])
    output = OutputConfig(**raw.get("output", {}))

    cfg = SuiteConfig(asset=asset, engines=engines, lease=lease, debt=debt,
                      residual_value_curve=rv, wacc=wacc, scenarios=scenarios,
                      lessee_credit=credit, output=output)
    _validate(cfg)
    return cfg


if __name__ == "__main__":
    c = load_config()
    print(f"配置加载成功 / Config loaded OK: {c.asset.type}, "
          f"WACC = {c.wacc.wacc:.4f}, {len(c.scenarios)} scenarios")
