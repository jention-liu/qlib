#!/usr/bin/env python3
"""
从 tushare 同花顺概念板块获取政策相关概念 → 股票多标签映射。
只拉和十五五方向相关的 ~50 个概念，避免全量 1725 个。
结果保存到 data/concept_tags.json
"""
import sys, os, json, time
import tushare as ts

from tushare_config import get_tushare_token

pro = ts.pro_api(get_tushare_token())

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
OUTPUT_PATH = os.path.join(PROJECT_DIR, "data", "concept_tags.json")

# ── 和十五五方向相关的概念板块（关键词匹配） ──
POLICY_KEYWORDS = [
    # AI算力
    "AI", "人工智能", "算力", "芯片", "光模块", "服务器", "数据中心",
    # 半导体
    "半导体", "集成电路", "光刻", "先进封装", "EDA",
    # 数字中国/信创
    "信创", "国产软件", "数据", "云计算", "大数据", "区块链",
    # 新能源
    "新能源", "储能", "充电桩", "特高压", "光伏", "风电", "氢能", "固态电池",
    # 高端装备/工业母机
    "机器人", "工业母机", "高端装备", "数控", "高端制造",
    # 稀缺资源
    "稀土", "小金属", "锂", "钴", "镍",
    # 创新药
    "创新药", "生物医药", "基因",
    # 高端医疗器械
    "医疗器械", "医疗设备",
    # 绿色低碳
    "节能环保", "碳中和", "碳交易",
    # 新材料
    "新材料", "碳纤维", "石墨烯",
    # 低空经济
    "低空", "无人机", "航空航天",
    # 通信
    "5G", "6G", "卫星",
]

print("=== 获取概念板块 → 股票多标签映射 ===\n")

# Step 1: 获取全量概念板块列表
print("[1/3] 加载同花顺概念板块...")
df_all = pro.ths_index()
print(f"  全量: {len(df_all)} 个概念板块")

# Step 2: 按关键词筛选
print("[2/3] 按十五五方向关键词筛选...")
matched = set()
for _, row in df_all.iterrows():
    name = row["name"]
    code = row["ts_code"]
    for kw in POLICY_KEYWORDS:
        if kw.lower() in name.lower():
            matched.add((code, name))
            break

concept_list = sorted(matched, key=lambda x: x[1])
print(f"  匹配到 {len(concept_list)} 个相关概念板块:")
for code, name in concept_list:
    print(f"    {code} - {name}")

# Step 3: 逐个拉成分股，反建 stock→tags
print(f"\n[3/3] 拉取成分股 ({len(concept_list)} 个概念)...")
stock_tags = {}  # ts_code -> [concept_names]
error_count = 0

for i, (concept_code, concept_name) in enumerate(concept_list):
    try:
        df_member = pro.ths_member(ts_code=concept_code)
        if df_member is not None and not df_member.empty:
            for _, row in df_member.iterrows():
                stock_code = row["con_code"]
                if stock_code not in stock_tags:
                    stock_tags[stock_code] = []
                stock_tags[stock_code].append(concept_name)
    except Exception as e:
        error_count += 1
        print(f"  ⚠ {concept_name}: {e}")
    time.sleep(0.12)  # 控制频率
    if (i + 1) % 10 == 0:
        print(f"  进度: {i+1}/{len(concept_list)}")

print(f"\n  完成: {len(stock_tags)} 只股票有概念标签, {error_count} 次错误")

# 去重
for code in stock_tags:
    stock_tags[code] = sorted(set(stock_tags[code]))

# 统计
tagged = sum(1 for v in stock_tags.values() if v)
avg_tags = sum(len(v) for v in stock_tags.values()) / max(len(stock_tags), 1)
print(f"  有标签: {tagged} 只, 平均标签数: {avg_tags:.1f}")

# 保存
output = {
    "stocks": stock_tags,
    "concepts_used": {c: n for c, n in concept_list},
    "count": len(stock_tags),
}
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n✅ 已保存: {OUTPUT_PATH}")
