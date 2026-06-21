"""找出缺失的股票"""
import os

csv_dir = os.path.expanduser("~/.qlib/csv_data/cn")
downloaded = set(f.replace(".csv", "") for f in os.listdir(csv_dir) if f.endswith(".csv"))

all_stocks = set()
stock_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "main_board_stocks.txt")
with open(stock_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("总"):
            parts = line.split()
            if parts:
                all_stocks.add(parts[0])

# 股票列表是 600000.SH／000001.SZ 格式
# CSV 文件名是 sh600000／sz000001 格式
# 转换后匹配
def to_csv_name(ts_code):
    symbol, exchange = ts_code.split(".")
    prefix = "sh" if exchange == "SH" else "sz"
    return prefix + symbol

csv_stocks = {to_csv_name(s) for s in all_stocks}
missing_csv = csv_stocks - downloaded
# 转回 ts_code 格式
missing = sorted([s for s in all_stocks if to_csv_name(s) in missing_csv])

output_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "missing_stocks.txt")
with open(output_file, "w") as f:
    f.write("\n".join(missing))

print(f"已下载: {len(downloaded)}, 股票池: {len(all_stocks)}, 缺失: {len(missing)}")
if missing:
    print(f"前10只缺失: {missing[:10]}")
print(f"缺失列表已保存: {output_file}")