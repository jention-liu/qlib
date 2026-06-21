"""获取沪深主板股票列表"""
import tushare as ts

pro = ts.pro_api('f799de4003e7bee1c425795940df6d0d59e9c41265e430106a66f271')

# 获取所有上市A股
df = pro.stock_basic(exchange='', list_status='L',
                     fields='ts_code,symbol,name,area,industry,market,list_date')

print('=== 市场分布 ===')
for m in sorted(df['market'].unique()):
    subset = df[df['market'] == m]
    print(f'  {m}: {len(subset)} 只')

# 沪深主板: 主板 + 中小板(002已并入深证主板)
main_board = df[df['market'].isin(['主板', '中小板'])]
print(f'\n=== 沪深主板(含原中小板): {len(main_board)} 只 ===')

# symbol前缀分布
print('\n=== 主板 symbol 前缀 ===')
prefixes = main_board['symbol'].str[:3].value_counts().sort_index()
for p, c in prefixes.items():
    print(f'  {p}**: {c}')

# 保存列表
ts_codes = main_board['ts_code'].tolist()
print(f'\n股票代码列表已准备，共 {len(ts_codes)} 只')
print(f'前5只: {ts_codes[:5]}')
print(f'后5只: {ts_codes[-5:]}')

# 保存到文件
with open('/Volumes/work/StockWork/qlib/data/main_board_stocks.txt', 'w') as f:
    f.write('\n'.join(ts_codes))
print(f'\n已保存到 data/main_board_stocks.txt')