"""在技能目录内创建独立 Python 环境、安装依赖并运行全量测试。"""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT_DIR / ".venv"
REQUIREMENTS_PATH = ROOT_DIR / "requirements.txt"


def environment_python() -> Path:
    """返回技能专用环境的 Python 路径。"""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(command: list[str], description: str) -> None:
    """执行安装步骤，失败时保留完整错误并停止。"""
    print(f"\n==> {description}", flush=True)
    subprocess.run(command, cwd=ROOT_DIR, check=True)


def main() -> int:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本。请先安装 Python，再重新运行本脚本。", file=sys.stderr)
        return 2
    if not REQUIREMENTS_PATH.is_file():
        print("缺少 requirements.txt，请确认分享包已完整解压。", file=sys.stderr)
        return 2

    python_path = environment_python()
    try:
        if not python_path.is_file():
            print("==> 在技能目录内创建独立环境 .venv", flush=True)
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        run(
            [str(python_path), "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)],
            "安装项目依赖（首次安装需要访问 Python 软件源）",
        )
        run([str(python_path), "-m", "pytest", "-q"], "运行全量测试")
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"\n安装或测试未完成：{exc}", file=sys.stderr)
        print("请把上方完整错误交给 WorkBuddy 排查，不要跳过测试继续使用。", file=sys.stderr)
        return 1

    print("\n安装完成。之后可在 WorkBuddy 中说：用 aiden估值助手运行完整测算。")
    print(f"技能目录：{ROOT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
