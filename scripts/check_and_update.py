#!/usr/bin/env python3
"""数据新鲜度检查 + 自动增量更新。

在每次跑选股模型前调用，确保数据是最新的。
用法:
    python scripts/check_and_update.py           # 检查，不新鲜就更新
    python scripts/check_and_update.py --force   # 强制更新
    python scripts/check_and_update.py --check-only  # 只检查不更新
"""

import json
import os
import sys
import time
import subprocess
from datetime import date, datetime, timedelta

import tushare as ts

from tushare_config import get_tushare_token

CSV_DIR = os.path.expanduser("~/.qlib/csv_data/cn")
QLIB_DIR = os.path.expanduser("~/.qlib/qlib_data/cn_data_tushare")
STOCK_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "main_board_stocks.txt")
STATUS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "data_status.json")
SCRIPT_DIR = os.path.dirname(__file__)
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)


def get_latest_trading_day() -> str:
    """获取最近一个交易日（tushare 日历）"""
    pro = ts.pro_api(get_tushare_token())
    today = date.today()
    # 查最近 10 天，取最后一个交易日
    df = pro.trade_cal(
        exchange="SSE",
        start_date=(today - timedelta(days=10)).strftime("%Y%m%d"),
        end_date=today.strftime("%Y%m%d"),
        is_open="1",
    )
    if df is None or df.empty:
        print("[check] 无法获取交易日历，跳过")
        return today.strftime("%Y%m%d")
    return str(df["cal_date"].max())


def get_local_latest_date() -> str:
    """扫描 CSV 数据中最新日期"""
    if not os.path.isdir(CSV_DIR):
        return "N/A"

    latest_dates = []
    files = [f for f in os.listdir(CSV_DIR) if f.endswith(".csv")]
    for f in files:
        fpath = os.path.join(CSV_DIR, f)
        try:
            # 读最后一行非空数据行（跳过 header）
            with open(fpath) as fh:
                lines = fh.readlines()
                for line in reversed(lines):
                    line = line.strip()
                    if line and not line.startswith("symbol"):
                        latest_dates.append(line.split(",")[1])  # date 是第二列
                        break
        except Exception:
            continue

    if not latest_dates:
        return "N/A"
    # 统一为 YYYYMMDD 格式（CSV 中是 YYYY-MM-DD）
    return max(latest_dates).replace("-", "")


def is_data_fresh() -> tuple[bool, str, str]:
    """返回 (是否新鲜, 本地最新日期, 最新交易日)"""
    local = get_local_latest_date()
    latest_td = get_latest_trading_day()

    if local == "N/A":
        return False, local, latest_td

    # 统一比较 YYYYMMDD 字符串
    fresh = local >= latest_td
    return fresh, local, latest_td


def run_update():
    """执行增量更新：拉数据 → dump_bin"""
    start = time.time()
    print(f"[update] 开始增量更新... ({datetime.now().strftime('%H:%M:%S')})")

    # Step 1: 增量拉取 — 使用 update 命令（增量模式，非 batch）
    # ⚠️ 严禁用 batch --start 做增量更新，batch 遍历全量股票耗时过长
    collector = os.path.join(SCRIPT_DIR, "tushare_collector.py")
    cmd = [
        sys.executable, collector, "update",
        "--stock_file", STOCK_FILE,
        "--days", "7",
        "--batch_size", "100",
        "--batch_delay", "5",
        "--delay", "0.1",
    ]
    print(f"[update] 拉取数据: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        print(f"[update] 拉取失败: {result.stderr[-500:]}")
        return False

    # Step 2: dump_bin 转换
    dump_cmd = [
        sys.executable, os.path.join(SCRIPT_DIR, "dump_bin.py"),
        "dump_all",
        "--data_path", CSV_DIR,
        "--qlib_dir", QLIB_DIR,
        "--symbol_field_name", "symbol",
        "--date_field_name", "date",
        "--include_fields", "open,high,low,close,pre_close,volume,amount,pct_chg",
    ]
    print(f"[update] 转换格式: dump_bin dump_all ...")
    result2 = subprocess.run(dump_cmd, cwd=PROJECT_DIR, capture_output=True, text=True, timeout=300)
    if result2.returncode != 0:
        print(f"[update] 转换失败: {result2.stderr[-500:]}")
        return False

    elapsed = time.time() - start
    print(f"[update] 更新完成，耗时 {elapsed:.0f}s")

    # 刷新状态
    fresh, local, latest_td = is_data_fresh()
    save_status(local, latest_td, fresh)

    return fresh


def save_status(local_date: str, latest_td: str, fresh: bool):
    """保存数据状态"""
    status = {
        "local_latest": local_date,
        "latest_trading_day": latest_td,
        "fresh": fresh,
        "checked_at": datetime.now().isoformat(),
    }
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


def main():
    force = "--force" in sys.argv
    check_only = "--check-only" in sys.argv

    fresh, local, latest_td = is_data_fresh()

    print(f"[check] 本地最新: {local}")
    print(f"[check] 最新交易日: {latest_td}")
    print(f"[check] 数据新鲜: {'是' if fresh else '否'}")

    if fresh and not force:
        print("[check] 数据已是最新，跳过更新。")
        save_status(local, latest_td, True)
        return 0

    if check_only:
        print("[check] --check-only 模式，不执行更新。")
        save_status(local, latest_td, fresh)
        return 0 if fresh else 1

    print(f"[check] 需要更新 (原因: {'强制' if force else '数据过旧'})")
    ok = run_update()

    if not ok:
        print("[check] 更新失败！")
        return 1

    fresh, local, latest_td = is_data_fresh()
    save_status(local, latest_td, fresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
