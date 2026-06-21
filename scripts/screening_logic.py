"""
第 0 层 — 初始股票池过滤
==========================

从沪深主板 3200 只股票中，按 8 条规则过滤出基础池。
输出：CSV 文件，包含所有股票的过滤结果和详细原因。

数据来源：
  - tushare stock_basic: market, name, list_date
  - tushare daily_basic: pe_ttm, total_mv
  - qlib 日线数据: close, amount (成交额)
"""

import sys
import os
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np

from tushare_config import get_tushare_token

# ---- 配置 ----

# 规则参数
MIN_AVG_AMOUNT = 50_000      # 近 20 日平均成交额 >= 5000 万（单位：千元，tushare amount 字段）
MIN_TOTAL_MV = 500_000          # 总市值 >= 50 亿（tushare 单位：万元）
MIN_LIST_YEARS = 5              # 上市满 5 年
CONSECUTIVE_LIMIT_DAYS = 5      # 连续一字板判定天数
SUSPENSION_GAP_DAYS = 10        # 最近 N 个交易日内无数据视为停牌


def get_latest_trade_date():
    """获取最近的交易日（考虑周末）"""
    today = date.today()
    # 简单回退到最近的交易日（tushare 数据 T+1 更新）
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # 跳过周末
        d = d - timedelta(days=1)
    return d.strftime("%Y%m%d")


def run_screening(output_csv: str = None):
    """
    主筛选函数。

    Args:
        output_csv: 输出 CSV 路径，默认 data/screening_result.csv
    """
    import tushare as ts
    import qlib
    from qlib.data import D

    pro = ts.pro_api(get_tushare_token())
    latest_date_str = get_latest_trade_date()

    if output_csv is None:
        output_csv = os.path.join(
            os.path.dirname(__file__), "..", "data", "screening_result.csv"
        )
    output_csv = os.path.abspath(output_csv)

    print(f"=== 第 0 层初始股票池筛选 ===")
    print(f"参考日期: {latest_date_str}")
    print()

    # ============================================================
    # Step 1: tushare stock_basic — 基础信息
    # ============================================================
    print("[1/5] 获取股票基础信息...")
    df_basic = pro.stock_basic(
        exchange="", list_status="L",
        fields="ts_code,symbol,name,market,list_date"
    )
    print(f"  全市场: {len(df_basic)} 只")

    # ============================================================
    # Step 2: 规则 1+2 — 沪深主板
    # ============================================================
    print("[2/5] 应用规则 1-3 (主板 / 非ST / 上市年限)...")
    df = df_basic.copy()

    # 规则1: 属于A股沪深主板
    # 规则2: 不属于科创板、创业板、北交所
    df["rule_main_board"] = df["market"].isin(["主板", "中小板"])
    print(f"  规则1+2 (沪深主板): {df['rule_main_board'].sum()} 通过")

    # 规则3: 不是 ST、*ST、退市风险股
    # 名称中不含 'ST' 或 '退'
    df["rule_not_st"] = ~df["name"].str.contains("ST|退", na=False)
    print(f"  规则3 (非ST/退市): {df['rule_not_st'].sum()} 通过")

    # 规则7: 上市满 5 年
    df["list_date_dt"] = pd.to_datetime(df["list_date"], format="%Y%m%d", errors="coerce")
    cutoff_date = pd.Timestamp.now() - pd.DateOffset(years=MIN_LIST_YEARS)
    df["rule_list_years"] = df["list_date_dt"] <= cutoff_date
    print(f"  规则7 (上市 ≥ {MIN_LIST_YEARS}年): {df['rule_list_years'].sum()} 通过")

    # ============================================================
    # Step 3: tushare daily_basic — PE 和市值
    # ============================================================
    print("[3/5] 获取 PE TTM / 市值数据...")
    df_fin = pro.daily_basic(
        trade_date=latest_date_str,
        fields="ts_code,trade_date,pe_ttm,total_mv"
    )
    if df_fin is None or df_fin.empty:
        # 如果最新日期还没数据，往前推一天
        for delta in range(1, 5):
            alt_date = (date.today() - timedelta(days=delta)).strftime("%Y%m%d")
            df_fin = pro.daily_basic(
                trade_date=alt_date,
                fields="ts_code,trade_date,pe_ttm,total_mv"
            )
            if df_fin is not None and not df_fin.empty:
                latest_date_str = alt_date
                break

    if df_fin is None or df_fin.empty:
        print("  ⚠ 无法获取 daily_basic 数据，跳过 PE/市值规则")
        df["rule_pe_positive"] = True
        df["rule_market_cap"] = True
    else:
        print(f"  获取到 {len(df_fin)} 条的财务数据 (date={latest_date_str})")
        df_fin = df_fin.rename(columns={
            "pe_ttm": "pe_ttm_val",
            "total_mv": "total_mv_val"
        })
        df = df.merge(
            df_fin[["ts_code", "pe_ttm_val", "total_mv_val"]],
            on="ts_code", how="left"
        )

        # 规则4: PE TTM > 0
        df["rule_pe_positive"] = df["pe_ttm_val"] > 0
        df["rule_pe_positive"] = df["rule_pe_positive"].fillna(False)
        print(f"  规则4 (PE TTM > 0): {df['rule_pe_positive'].sum()} 通过")

        # 规则6: 总市值 ≥ 50 亿（万元）
        df["rule_market_cap"] = df["total_mv_val"] >= MIN_TOTAL_MV
        df["rule_market_cap"] = df["rule_market_cap"].fillna(False)
        print(f"  规则6 (市值 ≥ {MIN_TOTAL_MV//10000}亿): {df['rule_market_cap'].sum()} 通过")

    # ============================================================
    # Step 4: qlib 日线数据 — 成交额 + 停牌检测
    # ============================================================
    print("[4/5] 加载 qlib 日线数据...")
    
    # macOS Python 3.13+ spawn 模式兼容：
    # 必须在主模块保护下设置 multiprocessing start method
    import multiprocessing
    try:
        multiprocessing.set_start_method("fork", force=True)
    except RuntimeError:
        pass  # 已经设置过了
    
    qlib.init(
        provider_uri="~/.qlib/qlib_data/cn_data_tushare",
        region="cn",
        expression_cache=None,  # 禁用缓存避免多进程问题
    )

    # 获取所有主板股票的最近 30 个交易日数据
    main_board_codes = sorted(df[df["rule_main_board"]]["symbol"].tolist())
    # 转换为 qlib 格式 (sh600000, sz000001)

    # 用表达式批量拉取
    lookback_start = (pd.Timestamp.now() - pd.DateOffset(days=60)).strftime("%Y-%m-%d")

    instruments = []
    for sym in main_board_codes:
        if sym.startswith("6"):
            instruments.append(f"sh{sym}")
        else:
            instruments.append(f"sz{sym}")

    print(f"  加载 {len(instruments)} 只股票的日线数据...")

    # 分批加载 (每批 200 只)，避免单次调用超时
    BATCH_SIZE = 200
    amount_map = {}
    suspension_map = {}
    limit_lock_map = {}

    total_batches = (len(instruments) + BATCH_SIZE - 1) // BATCH_SIZE
    for b in range(total_batches):
        batch_insts = instruments[b * BATCH_SIZE : (b + 1) * BATCH_SIZE]
        try:
            df_qlib = D.features(
                batch_insts,
                ["$close", "$high", "$low", "$amount"],
                start_time=lookback_start,
                end_time=None,
            )
        except Exception as e:
            print(f"    批次 {b+1}/{total_batches} 失败: {e}，跳过")
            for inst in batch_insts:
                sym = inst[2:]
                amount_map[sym] = 0
                suspension_map[sym] = False
                limit_lock_map[sym] = True
            continue

        for inst in batch_insts:
            sym = inst[2:]
            try:
                sub = df_qlib.loc[inst]
                if isinstance(sub, pd.DataFrame) and not sub.empty:
                    sub_sorted = sub.sort_index(ascending=False)
                    recent = sub_sorted.head(30)

                    amount_20 = recent["$amount"].head(20)
                    if len(amount_20) >= 10:
                        avg_amount = amount_20.mean()
                    else:
                        avg_amount = 0
                    amount_map[sym] = avg_amount

                    recent_10 = sub_sorted.head(10)
                    suspension_map[sym] = len(recent_10) >= 5

                    limit_lock_map[sym] = True
                    if len(recent) >= CONSECUTIVE_LIMIT_DAYS:
                        recent_c = recent.head(CONSECUTIVE_LIMIT_DAYS)
                        all_limit = (recent_c["$high"] == recent_c["$low"]).all()
                        if all_limit:
                            limit_lock_map[sym] = False
                else:
                    amount_map[sym] = 0
                    suspension_map[sym] = False
                    limit_lock_map[sym] = True
            except Exception:
                amount_map[sym] = 0
                suspension_map[sym] = False
                limit_lock_map[sym] = True

        # 进度提示
        if (b + 1) % 5 == 0 or b == total_batches - 1:
            done = min((b + 1) * BATCH_SIZE, len(instruments))
            print(f"    日线数据: {done}/{len(instruments)} ...")

    df["avg_amount_20"] = df["symbol"].map(amount_map)
    df["rule_amount"] = df["avg_amount_20"] >= MIN_AVG_AMOUNT
    df["rule_amount"] = df["rule_amount"].fillna(False)
    print(f"  规则5 (20日均成交额 ≥ {MIN_AVG_AMOUNT//10000}万): {df['rule_amount'].sum()} 通过")

    # 规则8
    df["rule_not_suspended"] = df["symbol"].map(suspension_map).fillna(False)
    df["rule_no_limit_lock"] = df["symbol"].map(limit_lock_map).fillna(True)
    # PE 在合理区间内也算无财务异常（排除 PE > 500 极端情况）
    if "pe_ttm_val" in df.columns:
        df["rule_pe_reasonable"] = (df["pe_ttm_val"] <= 500) | df["pe_ttm_val"].isna()
    else:
        df["rule_pe_reasonable"] = True
    df["rule_no_anomaly"] = (
        df["rule_not_suspended"] &
        df["rule_no_limit_lock"] &
        df["rule_pe_reasonable"]
    )
    print(f"  规则8 (无停牌/一字板/财务异常): {df['rule_no_anomaly'].sum()} 通过")

    # ============================================================
    # Step 5: 汇总输出
    # ============================================================
    print("[5/5] 汇总结果...")

    all_rules = [
        "rule_main_board",
        "rule_not_st",
        "rule_list_years",
        "rule_pe_positive",
        "rule_amount",
        "rule_market_cap",
        "rule_no_anomaly",
    ]

    df["passed"] = df[all_rules].all(axis=1)

    # 找出未通过的规则
    def failed_rules(row):
        failed = [r for r in all_rules if not row[r]]
        return ", ".join(failed) if failed else ""

    df["failed_reason"] = df.apply(failed_rules, axis=1)

    n_pass = df["passed"].sum()
    n_total = len(df)
    print(f"\n  通过: {n_pass} / {n_total} ({n_pass/n_total*100:.1f}%)")

    # 输出
    output_cols = [
        "ts_code", "symbol", "name", "market", "list_date",
        "pe_ttm_val", "total_mv_val", "avg_amount_20",
    ] + all_rules + ["passed", "failed_reason"]

    output_cols = [c for c in output_cols if c in df.columns]
    result = df[output_cols].copy()

    # 按通过排前面
    result = result.sort_values(["passed", "symbol"], ascending=[False, True])

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    result.to_csv(output_csv, index=False, encoding="utf-8-sig")

    # 打印摘要
    print(f"\n  结果已保存: {output_csv}")
    print(f"\n  通过列表 (前 20):")
    passed = result[result["passed"]]
    for _, r in passed.head(20).iterrows():
        pe_str = f"PE={r['pe_ttm_val']:.1f}" if pd.notna(r.get('pe_ttm_val')) else "PE=N/A"
        mv_str = f"MV={r['total_mv_val']/10000:.0f}亿" if pd.notna(r.get('total_mv_val')) else "MV=N/A"
        amt_str = f"AMT={r['avg_amount_20']*1000/1e8:.2f}亿" if pd.notna(r.get('avg_amount_20')) else "AMT=N/A"
        print(f"    {r['ts_code']} {r['name']:8s} | {pe_str} | {mv_str} | {amt_str}")

    print(f"\n  不通过原因分布:")
    reason_counts = result[~result["passed"]]["failed_reason"].value_counts()
    for reason, count in reason_counts.head(10).items():
        print(f"    {reason}: {count}")

    return result


# ============================================================
# 第 1 层 — 十五五政策过滤层
# ============================================================
#
# 策略：从 data/industry_mapping/policy_map.json 加载申万行业 → 政策分类映射
# 该文件手动维护，覆盖全部 110 个申万行业。
#
# 分类：whitelist (score≥80) / graylist (score 60-79) / blacklist (score<40) / watching (40-59)
# 策略定义参见 StockFree 第 1 层文档。

_POLICY_MAP = None


def _load_policy_map():
    """懒加载政策映射文件"""
    global _POLICY_MAP
    if _POLICY_MAP is not None:
        return _POLICY_MAP
    import json
    map_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "industry_mapping", "policy_map.json"
    )
    with open(map_path, "r", encoding="utf-8") as f:
        _POLICY_MAP = json.load(f)
    return _POLICY_MAP


def _industry_to_policy(industry: str) -> dict:
    """将申万行业映射到政策方向（基于 policy_map.json 精确匹配）"""
    if not industry or pd.isna(industry):
        return {"policy_score": 40, "policy_result": "降权观察", "policy_direction": "未知"}

    policy_map = _load_policy_map()

    # 精确匹配：申万行业名完全等于 JSON 的 key
    if industry in policy_map:
        entry = policy_map[industry]
        category = entry["category"]
        score = entry["score"]
        sub = entry.get("sub", category)

        if category == "whitelist":
            return {"policy_score": score, "policy_result": "政策通过", "policy_direction": sub}
        elif category == "graylist":
            return {"policy_score": score, "policy_result": "政策降权", "policy_direction": sub}
        elif category == "blacklist":
            return {"policy_score": score, "policy_result": "政策排除", "policy_direction": sub}
        elif category == "watching":
            return {"policy_score": score, "policy_result": "降权观察", "policy_direction": sub}

    # 未在映射表中 → 降权观察
    return {"policy_score": 40, "policy_result": "降权观察(未映射)", "policy_direction": industry if industry else "未知"}


# ── V2 多标签缓存 ──
_CONCEPT_TAGS = None
_CONCEPT_MAP = None


def _load_concept_data():
    """懒加载概念标签 + 概念→政策映射"""
    global _CONCEPT_TAGS, _CONCEPT_MAP
    if _CONCEPT_TAGS is not None:
        return _CONCEPT_TAGS, _CONCEPT_MAP
    import json
    tags_path = os.path.join(os.path.dirname(__file__), "..", "data", "concept_tags.json")
    map_path = os.path.join(os.path.dirname(__file__), "..", "data", "industry_mapping", "concept_policy_map.json")
    with open(tags_path, "r", encoding="utf-8") as f:
        _CONCEPT_TAGS = json.load(f)["stocks"]
    with open(map_path, "r", encoding="utf-8") as f:
        _CONCEPT_MAP = json.load(f)["concepts"]
    return _CONCEPT_TAGS, _CONCEPT_MAP


def _tags_to_policy(ts_code: str) -> dict:
    """
    V2: 基于多概念标签的综合打分。
    
    一只股票可能有多个概念标签（如「人工智能」「芯片概念」「算力租赁」），
    取最高分作为 policy_score，返回所有命中方向。
    """
    tags_data, concept_map = _load_concept_data()
    
    tags = tags_data.get(ts_code, [])
    if not tags:
        # 无概念标签 → 降权观察
        return {"policy_score": 40, "policy_result": "降权观察(无概念标签)", "policy_direction": "未知", "matched_tags": []}
    
    # 匹配每个标签到政策方向
    best_score = 0
    best_dir = ""
    directions_seen = {}
    matched = []
    
    for tag in tags:
        if tag in concept_map:
            d = concept_map[tag]["direction"]
            s = concept_map[tag]["score"]
            matched.append(f"{tag}→{d}({s})")
            if s > best_score:
                best_score = s
                best_dir = d
            directions_seen[d] = max(directions_seen.get(d, 0), s)
    
    if best_score >= 80:
        result = "政策通过(多标签)"
    elif best_score >= 60:
        result = "政策降权(多标签)"
    else:
        result = "政策排除(多标签)"
    
    # 合并所有方向（去重）
    all_dirs = [d for d, _ in sorted(directions_seen.items(), key=lambda x: -x[1])]
    
    return {
        "policy_score": best_score,
        "policy_result": result,
        "policy_direction": " | ".join(all_dirs[:3]),  # 最多显示 3 个方向
        "matched_tags": matched,
    }


def _layer1_filter(df: pd.DataFrame) -> pd.DataFrame:
    """
    第 1 层：十五五政策过滤（原申万行业版，保留兼容）。

    对通过第 0 层的股票，根据申万行业判断政策方向，
    白名单 pass / 灰名单降权 / 黑名单排除。
    """
    import tushare as ts
    pro = ts.pro_api(get_tushare_token())

    # 获取所有主板股票的行业（复用已有的 ts_code）
    codes = df["ts_code"].tolist()
    all_industries = {}
    for i in range(0, len(codes), 800):
        batch = codes[i:i+800]
        df_ind = pro.stock_basic(
            ts_code=",".join(batch),
            fields="ts_code,industry"
        )
        if df_ind is not None and not df_ind.empty:
            for _, r in df_ind.iterrows():
                all_industries[r["ts_code"]] = r["industry"]

    df["industry"] = df["ts_code"].map(all_industries)

    # 逐行映射
    policy_scores = []
    policy_results = []
    policy_dirs = []
    for _, row in df.iterrows():
        p = _industry_to_policy(row.get("industry"))
        policy_scores.append(p["policy_score"])
        policy_results.append(p["policy_result"])
        policy_dirs.append(p["policy_direction"])

    df["policy_score"] = policy_scores
    df["policy_result"] = policy_results
    df["policy_direction"] = policy_dirs

    # 第 1 层过滤规则
    df["layer1_pass"] = df["policy_score"] >= 80  # 白名单直接通过
    df["layer1_downgrade"] = (df["policy_score"] >= 60) & (df["policy_score"] < 80)  # 灰名单降权
    df["layer1_exclude"] = df["policy_score"] < 60  # 排除

    return df


def _layer1_filter_v2(df: pd.DataFrame) -> pd.DataFrame:
    """
    第 1 层 V2：基于同花顺多概念标签 + 概念→政策映射的综合打分。
    
    一只股票有多个概念标签（如「人工智能」「芯片概念」），
    取最高分作为 policy_score，并展示所有命中方向。
    无标签的股票回退到申万行业映射。
    """
    import tushare as ts
    pro = ts.pro_api(get_tushare_token())
    tags_data, concept_map = _load_concept_data()
    
    # 先获取申万行业（用于回退 + 展示）
    codes = df["ts_code"].tolist()
    all_industries = {}
    for i in range(0, len(codes), 800):
        batch = codes[i:i+800]
        df_ind = pro.stock_basic(ts_code=",".join(batch), fields="ts_code,industry")
        if df_ind is not None and not df_ind.empty:
            for _, r in df_ind.iterrows():
                all_industries[r["ts_code"]] = r["industry"]
    df["industry"] = df["ts_code"].map(all_industries)
    
    policy_scores = []
    policy_results = []
    policy_dirs = []
    matched_tags_list = []
    
    for _, row in df.iterrows():
        ts_code = row["ts_code"]
        tags = tags_data.get(ts_code, [])
        
        if not tags:
            # 无概念标签 → 回退申万行业
            p = _industry_to_policy(row.get("industry"))
            policy_scores.append(p["policy_score"])
            policy_results.append(p["policy_result"] + "(申万)")
            policy_dirs.append(p["policy_direction"])
            matched_tags_list.append("")
        else:
            # 多标签打分
            best_score = 0
            best_dir = ""
            dirs = {}
            matched = []
            
            for tag in tags:
                if tag in concept_map:
                    d = concept_map[tag]["direction"]
                    s = concept_map[tag]["score"]
                    matched.append(f"{tag}({s})")
                    if s > best_score:
                        best_score = s
                        best_dir = d
                    dirs[d] = max(dirs.get(d, 0), s)
            
            if best_score >= 90:
                result = "政策通过"
            elif best_score >= 70:
                result = "政策降权"
            else:
                result = "政策排除"
            
            # 合并方向（按分数倒序）
            sorted_dirs = sorted(dirs.items(), key=lambda x: -x[1])
            all_dirs = " | ".join(d for d, _ in sorted_dirs[:3])
            
            policy_scores.append(best_score)
            policy_results.append(result)
            policy_dirs.append(all_dirs if all_dirs else "未匹配")
            matched_tags_list.append("; ".join(matched[:5]))  # 最多存 5 个标签
    
    df["policy_score"] = policy_scores
    df["policy_result"] = policy_results
    df["policy_direction"] = policy_dirs
    df["matched_tags"] = matched_tags_list
    
    # 第 1 层过滤规则（门槛提高：白名单 ≥90，灰名单 70-89，排除 <70）
    df["layer1_pass"] = df["policy_score"] >= 90
    df["layer1_downgrade"] = (df["policy_score"] >= 70) & (df["policy_score"] < 90)
    df["layer1_exclude"] = df["policy_score"] < 70
    
    return df


def _run_all_layers(output_csv: str = None):
    """运行第 0 + 1 + 2 层联合筛选 (V2: 财务指标版第2层)"""
    from datetime import datetime

    if output_csv is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(os.path.dirname(__file__), "..", "output")
        os.makedirs(output_dir, exist_ok=True)
        output_csv = os.path.join(output_dir, f"screening_{ts}.csv")

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    # ---- 第 0 层 ----
    print("=" * 60)
    print("第 0 层：初始股票池过滤")
    print("=" * 60)
    temp_csv = output_csv.replace(".csv", "_layer0.csv")
    df0 = run_screening(output_csv=temp_csv)

    layer0_pass = df0[df0["passed"]]
    print(f"\n第 0 层通过: {len(layer0_pass)} 只")

    # ---- 第 1 层 ----
    print("\n" + "=" * 60)
    print("第 1 层：十五五政策过滤")
    print("=" * 60)
    df1 = _layer1_filter_v2(layer0_pass.copy())

    white = df1[df1["layer1_pass"]]
    gray = df1[df1["layer1_downgrade"]]
    excluded = df1[df1["layer1_exclude"]]

    print(f"\n  白名单 (policy_score≥80): {len(white)} 只 → 进入第 2 层")
    print(f"  灰名单 (policy_score 60-79): {len(gray)} 只 → 降权，后续条件更严格")
    print(f"  排除 (policy_score<60): {len(excluded)} 只 → 不进入主模型")

    # 白名单方向分布（摘要）
    dir_counts = white["policy_direction"].value_counts()
    print(f"\n  白名单方向 Top 5:")
    for d, c in dir_counts.head(5).items():
        print(f"    {d}: {c}")

    # ---- 第 2 层 V2: 财务指标产业链判断 ----
    print("\n" + "=" * 60)
    print("第 2 层 V2：财务指标产业链位置判断")
    print("=" * 60)
    df2 = _layer2_filter_v2(white.copy())

    # 统计
    l2_pass = df2[df2["layer2_pass"]]
    l2_excluded = df2[df2["layer2_exclude"]]
    l2_high_risk = df2[df2["layer2_high_risk"]]
    l2_safe = df2[df2["layer2_safe"]]

    print(f"\n  ✅ 通过: {len(l2_pass)} 只 (优质 {len(l2_safe)} + 高风险观察 {len(l2_high_risk)})")
    print(f"  ❌ 排除 (末端低壁垒): {len(l2_excluded)} 只")
    print(f"  ⚠️  高风险标签 (单独命中4/5): {len(l2_high_risk)} 只")

    # 5项命中分布
    print(f"\n  5项判断命中分布:")
    for i in range(1, 6):
        col = f"c{i}_hit"
        if col in df2.columns:
            n = df2[col].sum()
            labels = {1: "末端挤压", 2: "无定价权", 3: "无壁垒/渠道", 4: "低毛利渠道", 5: "代工风险"}
            print(f"    C{i} {labels.get(i, '?')}: {n} 只")

    # 保存完整结果
    output_cols = [
        "ts_code", "symbol", "name", "industry", "market",
        "pe_ttm_val", "total_mv_val", "avg_amount_20",
        "policy_score", "policy_result", "policy_direction", "matched_tags",
        "gross_margin", "ar_turn", "rd_rate", "sell_rate",
        "c1_hit", "c2_hit", "c3_hit", "c4_hit", "c5_hit",
        "layer2_result", "layer2_detail",
        "layer1_pass", "layer2_pass", "layer2_exclude", "layer2_safe", "layer2_high_risk",
    ]
    output_cols = [c for c in output_cols if c in df2.columns]
    result = df2[output_cols].sort_values(
        ["layer2_pass", "layer2_safe", "policy_score", "total_mv_val"],
        ascending=[False, False, False, False]
    )
    result.to_csv(output_csv, index=False, encoding="utf-8-sig")

    # 同步保存 latest_screening.csv（固定入口，每次覆盖）
    latest_path = os.path.join(os.path.dirname(output_csv), "latest_screening.csv")
    result.to_csv(latest_path, index=False, encoding="utf-8-sig")

    print(f"\n  完整结果: {output_csv}")
    print(f"  最新入口: {latest_path}")

    # 通过列表前20
    print(f"\n  通过股票 Top 20 (优质优先):")
    for _, r in result[result["layer2_pass"]].head(20).iterrows():
        pe_str = f"PE={r['pe_ttm_val']:.1f}" if pd.notna(r.get("pe_ttm_val")) else "PE=N/A"
        mv_str = f"MV={r['total_mv_val']/10000:.0f}亿" if pd.notna(r.get("total_mv_val")) else "MV=N/A"
        gm_str = f"GM={r.get('gross_margin','?')}"
        res = r.get("layer2_result", "?")
        print(f"    {r['ts_code']} {r['name']:8s} | {res:12s} | {r['policy_direction']:20s} | {gm_str} | {pe_str} | {mv_str}")

    # 排除列表前10
    if len(l2_excluded) > 0:
        print(f"\n  排除股票 Top 10:")
        for _, r in l2_excluded.head(10).iterrows():
            detail = r.get("layer2_detail", "?")
            gm_str = f"GM={r.get('gross_margin','?')}"
            print(f"    {r['ts_code']} {r['name']:8s} | {gm_str} | {detail}")

    return result


def _layer2_filter(df):
    """第 2 层：产业链位置过滤 (legacy, 保留兼容)。
    基于申万行业 → 上游/中游/下游映射。"""
    import json

    mapping_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "industry_mapping", "supply_chain_position.json"
    )
    with open(mapping_path) as f:
        pos_map = json.load(f)

    upstream = set(pos_map["upstream"])
    midstream = set(pos_map["midstream"])
    downstream = set(pos_map["downstream"])

    positions = []
    for _, row in df.iterrows():
        ind = row.get("industry", "")
        if ind in upstream:
            positions.append("上游")
        elif ind in midstream:
            positions.append("中游")
        elif ind in downstream:
            positions.append("下游")
        else:
            positions.append("未分类")

    df["supply_chain"] = positions
    df["layer2_pass"] = df["supply_chain"].isin(["上游", "中游"])
    df["layer2_upstream"] = df["supply_chain"] == "上游"
    df["layer2_exclude"] = df["supply_chain"] == "下游"

    upstream_n = (df["supply_chain"] == "上游").sum()
    midstream_n = (df["supply_chain"] == "中游").sum()
    downstream_n = (df["supply_chain"] == "下游").sum()
    unclassified_n = (df["supply_chain"] == "未分类").sum()

    print(f"\n  上游 (原材料/关键技术): {upstream_n} 只 → 优先关注")
    print(f"  中游 (制造/加工/组装): {midstream_n} 只 → 保留，需更严格后续条件")
    print(f"  下游 (终端/应用/服务): {downstream_n} 只 → 排除")
    if unclassified_n > 0:
        print(f"  未分类: {unclassified_n} 只 → 默认保留(归入中游)")

    return df


def _layer2_filter_v2(df: pd.DataFrame) -> pd.DataFrame:
    """第 2 层 V2：基于5项财务指标的产业链位置判断。

    5个判断维度（来自 Open WebUI 方案）：
    C1: 处于产业链最末端 — 毛利率 < 20% AND 应收账款周转慢(ar_turn < 4)
    C2: 没有定价权 — 毛利率 < 15% OR 毛利率连续3年下滑
    C3: 没有技术壁垒/品牌渠道 — 研发费用率 < 3% AND 销售费用率 < 5%
    C4: 低毛利渠道商 — 毛利率 < 10% AND 营收增长 < 5%
    C5: 无品牌代工 — 毛利率 < 20% AND 客户集中度高
         (注: tushare 无客户集中度数据，使用应收账款占比 > 30% 近似)

    排除规则：
    - C1+C2+C3 全中 → 直接排除（末端低壁垒，保守AND逻辑）
    - C4 或 C5 单独命中 → 高风险观察标签
    - 其余 → 通过
    """
    import tushare as ts
    pro = ts.pro_api(get_tushare_token())

    codes = df["ts_code"].tolist()
    n = len(codes)
    print(f"\n  待判断: {n} 只 (第0+1层白名单)")

    # ============================================================
    # Step 1: 拉取最新一期财务指标 (fina_indicator)
    # ============================================================
    print(f"  [1/3] 拉取 fina_indicator (毛利率/周转率/营收增速/销售费率)...")
    latest_period = "20241231"  # 最新年报

    all_fina = {}
    BATCH = 120
    for i in range(0, n, BATCH):
        batch = codes[i:i+BATCH]
        try:
            df_fina = pro.fina_indicator(
                ts_code=",".join(batch),
                period=latest_period,
                fields="ts_code,grossprofit_margin,ar_turn,or_yoy,saleexp_to_gr"
            )
            if df_fina is not None and not df_fina.empty:
                for _, r in df_fina.iterrows():
                    all_fina[r["ts_code"]] = r.to_dict()
        except Exception as e:
            print(f"    批次 {i//BATCH+1} 失败: {e}")
        if (i + BATCH) % 360 == 0 or i + BATCH >= n:
            print(f"    {min(i+BATCH, n)}/{n} ...")

    print(f"    获取到 {len(all_fina)} 只的财务指标")

    # ============================================================
    # Step 2: 拉取研发费用 (income 表 — 逐只查询，批量模式不支持)
    # ============================================================
    print(f"  [2/3] 拉取 income 表 (研发费用率, 逐只查询)...")
    rd_map = {}
    for i, code in enumerate(codes):
        try:
            df_inc = pro.income(
                ts_code=code,
                period=latest_period,
                fields="ts_code,revenue,rd_exp"
            )
            if df_inc is not None and not df_inc.empty:
                r = df_inc.iloc[0]
                revenue = r.get("revenue")
                rd_exp = r.get("rd_exp")
                if revenue and revenue > 0:
                    rd_map[code] = (rd_exp or 0) / revenue * 100
                else:
                    rd_map[code] = 0
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{n} ...")
    print(f"    获取到 {len(rd_map)} 只的研发费用率")

    # ============================================================
    # Step 3: 拉取历史毛利率 (3年趋势，用于 C2)
    # ============================================================
    print(f"  [3/3] 拉取近3年毛利率 (判断定价权趋势)...")
    margin_history = {}  # {ts_code: [gm_2024, gm_2023, gm_2022]}
    for period in ["20241231", "20231231", "20221231"]:
        for i in range(0, n, BATCH):
            batch = codes[i:i+BATCH]
            try:
                df_fina = pro.fina_indicator(
                    ts_code=",".join(batch),
                    period=period,
                    fields="ts_code,grossprofit_margin"
                )
                if df_fina is not None and not df_fina.empty:
                    for _, r in df_fina.iterrows():
                        code = r["ts_code"]
                        gm = r.get("grossprofit_margin")
                        if code not in margin_history:
                            margin_history[code] = []
                        margin_history[code].append(gm if pd.notna(gm) else None)
            except Exception:
                pass
    print(f"    获取到 {len(margin_history)} 只的历史毛利率")

    # ============================================================
    # Step 4: 逐只应用5项判断
    # ============================================================
    print(f"\n  应用5项判断...")

    results = []
    for _, row in df.iterrows():
        code = row["ts_code"]
        fina = all_fina.get(code, {})

        # 提取指标
        gm = fina.get("grossprofit_margin")  # 毛利率
        gm = float(gm) if pd.notna(gm) else None
        ar_turn = fina.get("ar_turn")  # 应收账款周转率
        ar_turn = float(ar_turn) if pd.notna(ar_turn) else None
        or_yoy = fina.get("or_yoy")  # 营收同比增速
        or_yoy = float(or_yoy) if pd.notna(or_yoy) else None
        sell_rate = fina.get("saleexp_to_gr")  # 销售费用率
        sell_rate = float(sell_rate) if pd.notna(sell_rate) else None
        rd_rate = rd_map.get(code, None)  # 研发费用率

        # 历史毛利率
        gm_hist = margin_history.get(code, [])
        gm_vals = [v for v in gm_hist if v is not None]

        # ---- C1: 产业链末端 ----
        c1 = False
        c1_reason = ""
        if gm is not None and ar_turn is not None:
            if gm < 20 and ar_turn < 4:
                c1 = True
                c1_reason = f"GM={gm:.1f}%, AR周转={ar_turn:.1f}(慢)"
            elif gm >= 20 and ar_turn >= 4:
                c1_reason = "OK"
            elif gm < 20:
                c1_reason = f"GM={gm:.1f}%低但周转正常({ar_turn:.1f})"
            else:
                c1_reason = f"周转慢({ar_turn:.1f})但GM={gm:.1f}%尚可"
        else:
            c1_reason = "数据不足"

        # ---- C2: 没有定价权 ----
        c2 = False
        c2_reason = ""
        if gm is not None:
            # 条件a: 毛利率 < 15%
            if gm < 15:
                c2 = True
                c2_reason = f"GM={gm:.1f}% <15%"
            # 条件b: 毛利率连续3年下滑
            elif len(gm_vals) >= 3:
                if gm_vals[0] is not None and gm_vals[1] is not None and gm_vals[2] is not None:
                    if gm_vals[0] < gm_vals[1] < gm_vals[2]:
                        c2 = True
                        c2_reason = f"GM连续下滑: {gm_vals[2]:.1f}%→{gm_vals[1]:.1f}%→{gm_vals[0]:.1f}%"
                if not c2:
                    c2_reason = "OK"
            else:
                c2_reason = "OK(历史数据不足)"
        else:
            c2_reason = "数据不足"

        # ---- C3: 没有技术壁垒/品牌渠道 ----
        c3 = False
        c3_reason = ""
        if rd_rate is not None and sell_rate is not None:
            if rd_rate < 3 and sell_rate < 5:
                c3 = True
                c3_reason = f"研发费率={rd_rate:.1f}% <3% & 销售费率={sell_rate:.1f}% <5%"
            else:
                c3_reason = "OK"
        elif rd_rate is not None:
            if rd_rate < 3 and (sell_rate is None or sell_rate >= 5):
                c3_reason = f"研发费率={rd_rate:.1f}% <3%(销售费率缺)"
            else:
                c3_reason = "OK"
        elif sell_rate is not None:
            if sell_rate < 5 and (rd_rate is None or rd_rate >= 3):
                c3_reason = f"销售费率={sell_rate:.1f}% <5%(研发费率缺)"
            else:
                c3_reason = "OK"
        else:
            c3_reason = "数据不足"

        # ---- C4: 低毛利渠道商 ----
        c4 = False
        c4_reason = ""
        if gm is not None and or_yoy is not None:
            if gm < 10 and or_yoy < 5:
                c4 = True
                c4_reason = f"GM={gm:.1f}% <10% & 营收增速={or_yoy:.1f}% <5%"
            else:
                c4_reason = "OK"
        else:
            c4_reason = "数据不足"

        # ---- C5: 无品牌代工 ----
        c5 = False
        c5_reason = ""
        if gm is not None:
            if gm < 20:
                # 使用应收账款占比作为客户集中度的近似
                # 应收账款占比 = (365/ar_turn) / 365 的倒数... 
                # 简化: 如果毛利率 < 20% 且 应收账款周转极慢(ar_turn < 2)，视为代工风险
                if ar_turn is not None and ar_turn < 2:
                    c5 = True
                    c5_reason = f"GM={gm:.1f}% <20% & AR周转极慢({ar_turn:.1f})→代工风险"
                else:
                    c5_reason = f"GM低但周转{ar_turn if ar_turn else '?'}暂不触发"
            else:
                c5_reason = "OK"
        else:
            c5_reason = "数据不足"

        # ---- 综合判断 ----
        # 数据完备性检查
        has_data = (gm is not None)
        if not has_data:
            # 无财务数据 → 保守处理：通过但标记
            layer2_result = "通过(无财务数据)"
            layer2_pass = True
            layer2_exclude = False
            layer2_high_risk = False
            layer2_safe = True
            layer2_detail = "缺少最新财务数据，保守通过"
        elif c1 and c2 and c3:
            # 三条件全中 → 排除
            layer2_result = "排除"
            layer2_pass = False
            layer2_exclude = True
            layer2_high_risk = False
            layer2_safe = False
            reasons = []
            if c1: reasons.append("末端挤压")
            if c2: reasons.append("无定价权")
            if c3: reasons.append("无壁垒/渠道")
            layer2_detail = " + ".join(reasons)
        elif c4 or c5:
            # 单独命中C4或C5 → 高风险
            layer2_result = "高风险观察"
            layer2_pass = True  # 仍通过但不安全
            layer2_exclude = False
            layer2_high_risk = True
            layer2_safe = False
            reasons = []
            if c4: reasons.append("低毛利渠道")
            if c5: reasons.append("代工风险")
            layer2_detail = " + ".join(reasons)
        else:
            # 通过
            layer2_result = "优质通过"
            layer2_pass = True
            layer2_exclude = False
            layer2_high_risk = False
            layer2_safe = True
            layer2_detail = "财务指标健康"

        results.append({
            "ts_code": code,
            "gross_margin": round(gm, 1) if gm is not None else None,
            "ar_turn": round(ar_turn, 1) if ar_turn is not None else None,
            "rd_rate": round(rd_rate, 1) if rd_rate is not None else None,
            "sell_rate": round(sell_rate, 1) if sell_rate is not None else None,
            "or_yoy": round(or_yoy, 1) if or_yoy is not None else None,
            "c1_hit": c1, "c1_reason": c1_reason,
            "c2_hit": c2, "c2_reason": c2_reason,
            "c3_hit": c3, "c3_reason": c3_reason,
            "c4_hit": c4, "c4_reason": c4_reason,
            "c5_hit": c5, "c5_reason": c5_reason,
            "layer2_result": layer2_result,
            "layer2_detail": layer2_detail,
            "layer2_pass": layer2_pass,
            "layer2_exclude": layer2_exclude,
            "layer2_high_risk": layer2_high_risk,
            "layer2_safe": layer2_safe,
        })

    df2 = pd.DataFrame(results)

    # 合并回原 df
    merge_cols = [c for c in df2.columns if c != "ts_code"]
    df = df.merge(df2, on="ts_code", how="left")

    # 补默认值（merge 失败的）
    for col in merge_cols:
        if col in df.columns:
            if col in ["c1_hit", "c2_hit", "c3_hit", "c4_hit", "c5_hit",
                       "layer2_pass", "layer2_exclude", "layer2_high_risk", "layer2_safe"]:
                df[col] = df[col].fillna(False)
            elif col == "layer2_result":
                df[col] = df[col].fillna("获取失败")
            elif col == "layer2_detail":
                df[col] = df[col].fillna("API获取失败")
            else:
                pass  # 数值列保留 NaN

    return df


def print_results(passed_df, full_df, output_csv):
    """回退方案的结果输出"""
    print("  部分规则未执行，仅输出基础过滤结果。")
    full_df["passed"] = full_df["rule_main_board"]
    full_df.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"  结果已保存: {output_csv}")


if __name__ == "__main__":
    run_screening()


# 别名：供 run_screening.py 动态导入使用
def screen():
    return _run_all_layers()
