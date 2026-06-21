#!/usr/bin/env python3
"""
每日数据更新 + 选股 一键脚本。

步骤:
  1. tushare 增量拉取最近 N 天日线数据
  2. dump_bin 重建 qlib 二进制索引
  3. 运行选股脚本（由外部参数指定）

用法:
  python daily_update.py --stock_file data/main_board_stocks.txt --select_script scripts/my_stock_selector.py
  python daily_update.py --stock_file data/main_board_stocks.txt   # 仅更新数据，不选股
"""

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QLIB_DATA_DIR = Path("~/.qlib/qlib_data/cn_data_tushare").expanduser()
CSV_DIR = Path("~/.qlib/csv_data/cn").expanduser()


def run(cmd: str, description: str) -> bool:
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"  CMD: {cmd}")
    print(f"{'='*60}")
    t0 = time.time()
    result = subprocess.run(cmd, shell=True, cwd=str(PROJECT_ROOT),
                            capture_output=False)
    elapsed = time.time() - t0
    if result.returncode == 0:
        print(f"✅ {description} — 耗时 {elapsed:.1f}s")
        return True
    else:
        print(f"❌ {description} 失败 (exit={result.returncode})，耗时 {elapsed:.1f}s")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="每日数据更新 + 选股")
    parser.add_argument("--stock_file", default="data/main_board_stocks.txt", help="股票列表文件")
    parser.add_argument("--update_days", type=int, default=3, help="增量更新天数")
    parser.add_argument("--select_script", default="", help="选股脚本路径（可选）")
    parser.add_argument("--include_fields", default="open,high,low,close,pre_close,volume,amount,pct_chg",
                        help="dump_bin 保留字段")
    args = parser.parse_args()

    stock_file = PROJECT_ROOT / args.stock_file
    if not stock_file.exists():
        print(f"❌ 股票列表文件不存在: {stock_file}")
        sys.exit(1)

    # Step 1: 增量更新
    print("\n📡 步骤 1/3: 增量更新 tushare 数据 ...")
    ok = run(
        f"source .venv13/bin/activate && python scripts/tushare_collector.py update "
        f"--stock_file {stock_file} --days {args.update_days} "
        f"--batch_size 50 --batch_delay 60 --delay 0.3",
        "tushare 增量更新"
    )
    if not ok:
        print("⚠️ 数据更新失败，但继续后续步骤...")

    # Step 2: 重建 qlib 二进制
    print("\n📦 步骤 2/3: 重建 qlib 二进制索引 ...")
    ok = run(
        f"source .venv13/bin/activate && python scripts/dump_bin.py dump_all "
        f"--data_path {CSV_DIR} --qlib_dir {QLIB_DATA_DIR} "
        f"--symbol_field_name symbol --date_field_name date "
        f"--include_fields {args.include_fields} "
        f"--freq day",
        "dump_bin 重建索引"
    )
    if not ok:
        print("❌ qlib 数据重建失败，终止。")
        sys.exit(1)

    # Step 3: 选股（可选）
    if args.select_script:
        script_path = PROJECT_ROOT / args.select_script
        if not script_path.exists():
            print(f"❌ 选股脚本不存在: {script_path}")
            sys.exit(1)
        print("\n📊 步骤 3/3: 运行选股脚本 ...")
        run(
            f"source .venv13/bin/activate && python {script_path}",
            f"选股: {args.select_script}"
        )

    print("\n🎉 全部完成!")


if __name__ == "__main__":
    main()