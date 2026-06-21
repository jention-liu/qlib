#!/bin/bash
# 查看下载进度
QLIB_DIR=/Volumes/work/StockWork/qlib
DATA_DIR=$QLIB_DIR/data
CSV_DIR=~/.qlib/csv_data/cn

echo "=== 下载进度 ==="
if [ -f "$DATA_DIR/progress.txt" ]; then
    cat "$DATA_DIR/progress.txt"
    echo ""
else
    echo "进度文件尚未生成（任务可能未启动）"
fi

echo ""
echo "=== CSV 统计 ==="
csv_count=$(ls "$CSV_DIR"/*.csv 2>/dev/null | wc -l | tr -d ' ')
echo "已下载: $csv_count 只"

echo ""
echo "=== 最近日志 (tail -15) ==="
if [ -f "$DATA_DIR/download.log" ]; then
    tail -15 "$DATA_DIR/download.log"
else
    echo "日志文件不存在"
fi

echo ""
echo "=== 进程状态 ==="
pgrep -fl "tushare_collector" 2>/dev/null || echo "无运行中的下载进程"