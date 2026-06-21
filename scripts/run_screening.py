#!/usr/bin/env python3
"""选股入口 — 自动检查数据新鲜度 → 调用自定义选股逻辑 → 输出结果。

用法:
    python scripts/run_screening.py              # 默认：检查+筛选，输出 CSV
    python scripts/run_screening.py --skip-check # 跳过数据检查直接跑
    python scripts/run_screening.py --output table  # 终端打印表格

你的自定义选股逻辑写在 screening_logic.py 的 screen() 函数里。
"""

import os
import sys
import json
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_FILE = os.path.join(PROJECT_DIR, "data", "data_status.json")


def print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def check_data_freshness():
    """Step 1: 检查并更新数据"""
    print_header("STEP 1: 数据新鲜度检查")

    checker = os.path.join(SCRIPT_DIR, "check_and_update.py")
    result = subprocess.run(
        [sys.executable, checker],
        cwd=PROJECT_DIR,
        capture_output=False,
        text=True,
        timeout=900,  # 15 分钟足够更新
    )

    if result.returncode != 0:
        print("\n[ERROR] 数据更新失败，中止选股。")
        sys.exit(1)

    # 显示状态
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE) as f:
            status = json.load(f)
        print(f"\n  本地数据: 截至 {status.get('local_latest', 'N/A')}")
        print(f"  最新交易日: {status.get('latest_trading_day', 'N/A')}")
        print(f"  状态: {'✅ 最新' if status.get('fresh') else '⚠️ 已更新'}")


def run_screening(output_format: str = "csv"):
    """Step 2: 运行自定义选股逻辑"""
    print_header("STEP 2: 运行选股逻辑")

    # 加载用户自定义的选股逻辑
    screening_path = os.path.join(SCRIPT_DIR, "screening_logic.py")
    if not os.path.exists(screening_path):
        print("\n[ERROR] 未找到 screening_logic.py，请先定义选股条件。")
        print(f"  期望路径: {screening_path}")
        sys.exit(1)

    # 动态导入
    import importlib.util
    spec = importlib.util.spec_from_file_location("screening_logic", screening_path)
    screening = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screening)

    if not hasattr(screening, "screen"):
        print("\n[ERROR] screening_logic.py 中未定义 screen() 函数")
        sys.exit(1)

    print(f"\n  执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 调用用户的选股逻辑
    results = screening.screen()

    if results is None or len(results) == 0:
        print("\n  无符合条件的股票。")
        return None

    print(f"\n  选股结果: {len(results)} 只")
    return results


def save_results(results, output_format: str = "csv"):
    """Step 3: 保存结果"""
    if results is None:
        return

    print_header("STEP 3: 保存结果")

    out_dir = os.path.join(PROJECT_DIR, "output")
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_path = os.path.join(out_dir, f"screening_{timestamp}.csv")
    results.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n  CSV 已保存: {csv_path}")
    print(f"  行数: {len(results)}, 列数: {len(results.columns)}")

    if output_format == "table":
        print("\n" + "=" * 80)
        print(results.to_string(index=False))


def main():
    skip_check = "--skip-check" in sys.argv
    output_format = "csv"
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_format = sys.argv[idx + 1]

    print_header("QLIB 选股系统")
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not skip_check:
        check_data_freshness()
    else:
        print("\n  ⏭️  跳过数据检查 (--skip-check)")

    results = run_screening(output_format)
    save_results(results, output_format)

    print_header("完成")
    print(f"  结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()