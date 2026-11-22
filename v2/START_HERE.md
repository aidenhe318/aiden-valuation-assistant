# aiden估值助手：从这里开始

这是一个包含完整本地模型的 WorkBuddy Skill，不只是提示词。压缩包根目录已经直接放置 `SKILL.md`，并保留 `src/`、`config/`、`data/`、`tests/`、`references/` 和 `scripts/`。

## 最简安装

### 方式一：技能页面上传 ZIP

1. 打开 WorkBuddy 的“专家·技能·连接器”或“技能”管理页。
2. 选择“添加技能”→“上传技能”，上传整个 ZIP。
3. 安装后新建对话，输入：`用 aiden估值助手运行完整测算`。
4. 首次使用时，助手应运行 `python scripts/setup.py`，创建技能专用环境、安装依赖并执行 37 项测试。

### 方式二：把 ZIP 发给 WorkBuddy

将 ZIP 放入对话，并完整发送下面这段话：

> 请安装我发送的 aiden估值助手技能包。解压后确认 SKILL.md 位于技能根目录，将完整目录安装到 WorkBuddy 技能目录；不要只导入 SKILL.md，必须保留 src、config、data、tests、references 和 scripts。安装后在技能根目录运行 python scripts/setup.py，安装依赖并运行全量测试。只有测试通过才报告安装成功；如果失败，请给出完整错误。

## 使用前提

- 电脑已安装 Python 3.10 或更高版本，并能在终端运行 `python --version`。
- 首次安装依赖需要访问 Python 软件源；模型本身不需要 GitHub，也不需要执行 `git push/pull`。
- 如果 WorkBuddy 版本没有“上传技能”入口，使用方式二，或把完整解压目录放到用户级 `.workbuddy/skills/aiden-valuation-assistant/` 后重启 WorkBuddy。

## 安装后直接这样说

- `用 aiden估值助手运行完整测算。`
- `用 aiden估值助手分析这台发动机现在值多少钱，带租约买多少钱合理。`
- `把估值结论写成正式 Word 报告。`
- `修改 config/aircraft.yaml 中的假设，重新测算并比较前后结果。`

详细步骤、验证方法和故障排查见随包 Word 教程。
