#!/usr/bin/env python3
"""
选股策略 v2: MACD 金叉 + RSI 超卖（放宽条件）
三种信号模式:
  A: MACD 金叉 (近3天) + RSI < 50
  B: RSI 超卖反弹 (近5天RSI曾<35, 当前在回升) + MACD柱收缩
  C: MACD 零轴附近金叉 + 成交量放大 > 1.5倍
"""

import os
import sys
from pathlib import Path
from datetime import datetime
import warnings

import pandas as pd
import numpy as np
import pandas_ta as ta

warnings.filterwarnings("ignore")

CSV_DIR = Path.home() / ".qlib/csv_data/cn"
OUTPUT_DIR = Path("/Volumes/work/StockWork/qlib/output")


def load_stock(csv_path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(csv_path, parse_dates=["date"])
        if df.empty or len(df) < 120:
            return None
        df = df.sort_values("date").reset_index(drop=True)
        df = df[df["volume"] > 0].copy()
        return df
    except Exception:
        return None


def compute_indicators(df: pd.DataFrame):
    """计算全部技术指标"""
    # MACD
    m = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["macd"] = m["MACD_12_26_9"]
    df["macd_s"] = m["MACDs_12_26_9"]
    df["macd_h"] = m["MACDh_12_26_9"]

    # RSI
    df["rsi"] = ta.rsi(df["close"], length=14)

    # 均线
    df["ma5"] = ta.sma(df["close"], length=5)
    df["ma10"] = ta.sma(df["close"], length=10)
    df["ma20"] = ta.sma(df["close"], length=20)
    df["ma60"] = ta.sma(df["close"], length=60)

    # 成交量
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["amt_ma20"] = df["amount"].rolling(20).mean()

    # ATR (波动率)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["atr"] = atr

    # 涨跌幅
    df["ret_5d"] = df["close"].pct_change(5)
    df["ret_20d"] = df["close"].pct_change(20)

    return df


def check_signals(df: pd.DataFrame) -> dict | None:
    """检查三种信号模式"""
    last_idx = len(df) - 1

    # --- 基础过滤 ---
    last_close = df.iloc[-1]["close"]
    last_amt = df.iloc[-1]["amt_ma20"]
    # tushare amount 单位是千元，50000千元 = 5000万元
    if last_close < 2 or (pd.notna(last_amt) and last_amt < 50000):
        return None

    # --- 检查金叉 ---
    golden_cross_day = None  # 金叉发生在几天前 (0=今天, 1=昨天, 2=前天)
    golden_cross_strength = 0  # MACD柱强度

    for offset in range(3):
        idx = last_idx - offset
        if idx < 35:  # MACD 至少需要 35 根才能稳定
            break
        curr_m = df.iloc[idx]["macd"]
        curr_s = df.iloc[idx]["macd_s"]
        prev_m = df.iloc[idx - 1]["macd"]
        prev_s = df.iloc[idx - 1]["macd_s"]
        if pd.notna(prev_m) and pd.notna(curr_m):
            if prev_m < prev_s and curr_m > curr_s:
                golden_cross_day = offset
                golden_cross_strength = float(df.iloc[idx]["macd_h"])
                break

    # 当前指标
    rsi_now = float(df.iloc[-1]["rsi"])
    macd_h_now = float(df.iloc[-1]["macd_h"])
    macd_h_prev = float(df.iloc[-2]["macd_h"]) if last_idx >= 1 else 0
    macd_now = float(df.iloc[-1]["macd"])
    close_now = float(df.iloc[-1]["close"])

    # --- 5天内 RSI 最低点 ---
    rsi_min_5d = 100
    rsi_min_idx = -1
    for offset in range(5):
        idx = last_idx - offset
        if idx < 0:
            break
        v = df.iloc[idx]["rsi"]
        if pd.notna(v) and v < rsi_min_5d:
            rsi_min_5d = v
            rsi_min_idx = offset

    # --- 成交量放大 ---
    vol_ratio = 0
    if pd.notna(df.iloc[-1]["vol_ma5"]) and pd.notna(df.iloc[-1]["vol_ma20"]):
        vol_ratio = float(df.iloc[-1]["vol_ma5"] / df.iloc[-1]["vol_ma20"])

    signal_type = None
    score = 0
    reason = ""

    # == 模式 A: MACD 金叉 + RSI < 50 ==
    if golden_cross_day is not None and pd.notna(rsi_now) and rsi_now < 50:
        signal_type = "A"
        score = 50 - rsi_now  # RSI 越低分越高
        reason = f"金叉({golden_cross_day}d前) + RSI={rsi_now:.0f}"

    # == 模式 B: RSI 超卖反弹 + MACD 收敛 ==
    if signal_type is None and rsi_min_5d < 35 and rsi_now > rsi_min_5d + 3:
        if macd_h_now > macd_h_prev:  # MACD柱在回升
            signal_type = "B"
            score = (35 - rsi_min_5d) * 2 + (rsi_now - rsi_min_5d)
            reason = f"RSI反弹 {rsi_min_5d:.0f}→{rsi_now:.0f} + MACD柱↑"

    # == 模式 C: MACD 零轴附近金叉 + 放量 ==
    if signal_type is None and golden_cross_day is not None and abs(macd_now) < 0.5 and vol_ratio > 1.2:
        signal_type = "C"
        score = vol_ratio * 10
        reason = f"零轴金叉({golden_cross_day}d前) + 放量{vol_ratio:.1f}x"

    if signal_type is None:
        return None

    return {
        "symbol": str(df.iloc[0]["symbol"]),
        "date": str(df.iloc[-1]["date"].date()),
        "close": round(close_now, 2),
        "pct_chg": round(float(df.iloc[-1]["pct_chg"]), 2) if pd.notna(df.iloc[-1]["pct_chg"]) else 0,
        "rsi": round(rsi_now, 1),
        "rsi_5d_low": round(rsi_min_5d, 1) if rsi_min_5d < 100 else None,
        "macd": round(macd_now, 4),
        "macd_hist": round(macd_h_now, 4),
        "vol_ratio": round(vol_ratio, 2),
        "amt_avg_20d": round(float(df.iloc[-1]["amt_ma20"]) / 100000, 2),  # 千元→亿
        "ret_5d": round(float(df.iloc[-1]["ret_5d"]) * 100, 1) if pd.notna(df.iloc[-1]["ret_5d"]) else None,
        "ret_20d": round(float(df.iloc[-1]["ret_20d"]) * 100, 1) if pd.notna(df.iloc[-1]["ret_20d"]) else None,
        "ma20": round(float(df.iloc[-1]["ma20"]), 2) if pd.notna(df.iloc[-1]["ma20"]) else None,
        "signal_type": signal_type,
        "score": round(score, 1),
        "reason": reason,
    }


def main():
    csv_files = sorted(CSV_DIR.glob("*.csv"))
    print(f"共 {len(csv_files)} 只股票，计算中...")

    results = []
    for i, f in enumerate(csv_files):
        if (i + 1) % 300 == 0:
            print(f"  进度: {i+1}/{len(csv_files)}, 已命中: {len(results)}")

        df = load_stock(f)
        if df is None:
            continue

        df = compute_indicators(df)
        sig = check_signals(df)
        if sig:
            sig["name"] = f.stem.replace("sh", "").replace("sz", "")
            results.append(sig)

    print(f"\n命中: {len(results)} 只")

    if results:
        out_df = pd.DataFrame(results)
        out_df = out_df.sort_values("score", ascending=False)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        path = OUTPUT_DIR / f"stock_screen_{ts}.csv"
        out_df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"结果: {path}\n")

        # 按信号分组打印
        for stype in ["A", "B", "C"]:
            sub = out_df[out_df["signal_type"] == stype]
            if sub.empty:
                continue
            type_name = {"A": "MACD金叉+RSI<50", "B": "RSI超卖反弹+MACD收敛", "C": "零轴金叉+放量"}[stype]
            print(f"=== {type_name} ({len(sub)}只) ===")
            print(f"{'代码':<10} {'名称':<8} {'收盘':>7} {'RSI':>5} {'MACD':>8} {'得分':>6} {'成交(亿)':>8} {'理由'}")
            print("-" * 90)
            for _, r in sub.head(10).iterrows():
                print(
                    f"{r['symbol']:<10} {r.get('name',''):<8} {r['close']:>7.2f} {r['rsi']:>5.0f}"
                    f" {r['macd']:>8.4f} {r['score']:>6.1f} {r['amt_avg_20d']:>8.2f}  {r['reason']}"
                )
            print()

        # 统计
        print(f"A: {len(out_df[out_df['signal_type']=='A'])} | B: {len(out_df[out_df['signal_type']=='B'])} | C: {len(out_df[out_df['signal_type']=='C'])}")
    else:
        print("无命中。")


if __name__ == "__main__":
    main()