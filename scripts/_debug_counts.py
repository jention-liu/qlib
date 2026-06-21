import pandas as pd

df = pd.read_csv('output/latest_screening.csv')

print("=== layer1_pass 分布 ===")
print(df['layer1_pass'].value_counts())
print()

print("=== layer2_result 分布 ===")
print(df['layer2_result'].value_counts())
print()

for col in ['policy_result', 'layer1_downgrade', 'layer1_exclude']:
    if col in df.columns:
        print(f"=== {col} 分布 ===")
        print(df[col].value_counts())
        print()
    else:
        print(f"列 {col} 不存在")

has_l2 = df['layer2_result'].notna() & (df['layer2_result'] != '')
print(f"有 layer2_result: {has_l2.sum()}")
l1_no = df['layer1_pass'] == False
l2_yes = has_l2
both = df[l1_no & l2_yes]
print(f"第1层未通过但出现在第2层输出: {len(both)} 只")

print(f"\n总行数: {len(df)}")
print(f"唯一 ts_code: {df['ts_code'].nunique()}")
