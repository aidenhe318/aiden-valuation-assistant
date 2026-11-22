# aiden估值助手

面向飞机与发动机租赁业务的本地估值 Skill，包含完整模型、配置、测试、航司信用输入和独立估值 Word 报告规范。适用于 WorkBuddy 及其他支持 `SKILL.md` 的智能体。

## 已实现模块

| 文件 | 功能 |
|---|---|
| `src/engine_residual.py` | 逐月三情景残值曲线、机体折旧与发动机 LLP／维修状态调整 |
| `src/lease_returns.py` | 租金、债务摊还、CFADS、DSCR、Equity IRR、MOIC 和收益报告 |
| `src/asset_valuation.py` | 整机无杠杆 DCF、机体／发动机分摊、维护调整和敏感性矩阵 |
| `src/airline_credit.py` | 五家航司信用评分、锚点校验与 LTV／LRF 信用调整 |

模型规格和校验口径见 `MODEL_SPEC.md`，技能执行规则见 `SKILL.md`。

## WorkBuddy 最简安装

1. 在技能管理页上传整个 `aiden估值助手-WorkBuddy分享包.zip`，或把 ZIP 发给 WorkBuddy 并要求安装完整 Skill。
2. 首次使用时，在技能根目录运行：

   ```bash
   python scripts/setup.py
   ```

3. 脚本会在技能目录内创建 `.venv`、安装依赖并运行全量测试。当前基线为 **37 passed**。
4. 新建对话，输入：`用 aiden估值助手运行完整测算`。

更详细的两种安装方式、安装口令和故障排查见随包《aiden估值助手_WorkBuddy安装与使用教程.docx》及 `START_HERE.md`。

## 手工运行完整测算

环境配置完成后，在技能根目录激活 `.venv`，运行：

```bash
python src/engine_residual.py
python src/lease_returns.py
python src/asset_valuation.py
python src/airline_credit.py
python -m pytest -q
```

主要报告写入 `outputs/`。模型假设只从 `config/aircraft.yaml` 进入，航司信用输入使用 `data/airline_credit_inputs.csv`。

## 使用边界

- 本地文件夹为唯一真相源，不需要 GitHub，不执行 `git push/pull`。
- 禁止在模型实现中硬编码绝对路径，禁止为命中目标结果反向调参。
- 裸机市场价值与带租约 DCF 分开列示，不得重复相加。
- 默认参数用于研究和区间级校准，不构成正式评估或投资建议。
