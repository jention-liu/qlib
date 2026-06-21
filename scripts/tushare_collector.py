#!/usr/bin/env python3
"""
Tushare 数据采集器 — 拉取 A 股日线数据，输出为 qlib 兼容的 CSV。
用法：
    python tushare_collector.py download --stock_list sh600000,sz000001 --start 20200101
    python tushare_collector.py download --index csi300 --start 20200101
    python tushare_collector.py download_all --start 20200101    # 全市场
"""

import os
import sys
import time
import signal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import fire
import pandas as pd
import tushare as ts
from loguru import logger

# qlib 工具：code_to_fname("sh600000") → "sh600000", fname_to_code("sh600000") → "SH600000"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from qlib.utils import code_to_fname
except ImportError:
    def code_to_fname(code):
        return code.lower()


TUSHARE_TOKEN = "f799de4003e7bee1c425795940df6d0d59e9c41265e430106a66f271"
STOCK_TIMEOUT = 30  # 单只股票 API 超时（秒）
MAX_RETRIES = 3  # 失败重试次数

# 日志文件路径
LOG_DIR = Path(__file__).resolve().parent.parent / "data"
LOG_FILE = LOG_DIR / "download.log"
PROGRESS_FILE = LOG_DIR / "progress.txt"

# qlib 日线字段映射: tushare 字段 → qlib CSV 列名
FIELD_MAP = {
    "ts_code": "symbol",
    "trade_date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "pre_close": "pre_close",
    "vol": "volume",
    "amount": "amount",
    "pct_chg": "pct_chg",
}

# 需要保留的字段
OUTPUT_COLS = ["symbol", "date", "open", "high", "low", "close", "pre_close", "volume", "amount", "pct_chg"]


class TushareCollector:
    def __init__(self, save_dir: str = "~/.qlib/csv_data/cn", token: str = None):
        self.save_dir = Path(save_dir).expanduser()
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.token = token or TUSHARE_TOKEN
        self.pro = None
        self._setup_logging()

    def _setup_logging(self):
        """确保日志写入文件"""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger.add(LOG_FILE, rotation="10 MB", retention="7 days", level="INFO")

    def _init_api(self):
        if self.pro is None:
            ts.set_token(self.token)
            self.pro = ts.pro_api()
        return self.pro

    def _normalize_symbol(self, raw: str) -> str:
        """将 tushare 格式 600000.SH → qlib 格式 sh600000"""
        raw = raw.strip().upper()
        if "." in raw:
            code, exchange = raw.split(".")
            return f"{exchange.lower()}{code.lower()}"
        return raw.lower()

    def _fetch_with_timeout(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        """
        带超时和重试的单只股票数据拉取。
        返回 DataFrame 或抛出异常。
        """
        pro = self._init_api()

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(pro.daily, ts_code=ts_code, start_date=start, end_date=end)
                    df = future.result(timeout=STOCK_TIMEOUT)
                return df
            except FuturesTimeoutError:
                last_error = f"超时 ({STOCK_TIMEOUT}s)"
                logger.warning(f"{ts_code} 第{attempt}次尝试超时")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"{ts_code} 第{attempt}次失败: {e}")

            if attempt < MAX_RETRIES:
                wait = 2 ** attempt  # 指数退避: 2s, 4s
                time.sleep(wait)

        raise RuntimeError(last_error)

    def _write_progress(self, current: int, total: int, success: int, fail: int):
        """写入进度文件"""
        pct = current * 100 // total if total > 0 else 0
        PROGRESS_FILE.write_text(f"{current}/{total} ({pct}%) | 成功:{success} 失败:{fail} | {pd.Timestamp.now()}")

    def _tushare_to_csv(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """转换 tushare 返回的 DataFrame 为 qlib CSV 格式"""
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        # 只保留需要的列
        available_cols = [c for c in FIELD_MAP.keys() if c in df.columns]
        df = df[available_cols]
        # 重命名
        df = df.rename(columns=FIELD_MAP)
        # 规范化 symbol
        df["symbol"] = symbol
        # 日期格式化
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
        # 排序
        df = df.sort_values("date")
        # 确保数字类型
        for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def download(
        self,
        stock_list: str = "",
        index: str = "",
        start: str = "20100101",
        end: str = None,
        delay: float = 0.3,
    ):
        """
        下载股票日线数据。

        stock_list: 逗号分隔的 tushare 格式 stock codes，如 '600000.SH,000001.SZ'
        index: 指数名称，如 'csi300'（沪深300成分股）
        start: 起始日期，YYYYMMDD
        end: 截止日期，默认今天
        delay: 请求间隔（秒），避免触发频率限制
        """
        pro = self._init_api()
        if end is None:
            end = pd.Timestamp.now().strftime("%Y%m%d")

        # 获取股票列表
        codes = []
        if stock_list:
            codes = [s.strip() for s in stock_list.split(",") if s.strip()]
        elif index:
            codes = self._get_index_stocks(pro, index)
        else:
            logger.error("请指定 --stock_list 或 --index")
            return

        logger.info(f"共 {len(codes)} 只股票，日期范围 {start} ~ {end}")

        success = 0
        fail = 0
        for i, ts_code in enumerate(codes):
            qlib_symbol = self._normalize_symbol(ts_code)
            fname = code_to_fname(qlib_symbol)
            file_path = self.save_dir / f"{fname}.csv"

            try:
                df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
                df = self._tushare_to_csv(df, qlib_symbol)

                if df.empty:
                    logger.warning(f"[{i+1}/{len(codes)}] {ts_code} → {qlib_symbol}: 无数据")
                    fail += 1
                else:
                    # 如果已有旧数据，合并后去重
                    if file_path.exists():
                        old_df = pd.read_csv(file_path, parse_dates=["date"])
                        df = pd.concat([old_df, df], ignore_index=True)
                        df = df.drop_duplicates("date", keep="last")
                    df.to_csv(file_path, index=False)
                    logger.info(f"[{i+1}/{len(codes)}] {ts_code} → {qlib_symbol}: {len(df)} 条, 已保存")
                    success += 1

            except Exception as e:
                logger.error(f"[{i+1}/{len(codes)}] {ts_code} 失败: {e}")
                fail += 1

            time.sleep(delay)

        logger.info(f"完成: 成功 {success}, 失败 {fail}")

    def download_all(self, start: str = "20100101", end: str = None, delay: float = 0.3):
        """下载全市场 A 股数据"""
        pro = self._init_api()
        if end is None:
            end = pd.Timestamp.now().strftime("%Y%m%d")

        logger.info("获取全市场股票列表...")
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,area,industry,list_date")
        symbols = df["ts_code"].tolist()
        logger.info(f"共 {len(symbols)} 只股票")
        self.download(stock_list=",".join(symbols), start=start, end=end, delay=delay)

    def _get_index_stocks(self, pro, index_name: str):
        """获取指数成分股"""
        index_map = {
            "csi300": "399300.SZ",   # or 000300.SH
            "csi500": "399905.SZ",
            "csi1000": "399852.SZ",
            "sz50": "000016.SH",
            "csi100": "399903.SZ",
        }
        index_code = index_map.get(index_name.lower(), index_name)

        logger.info(f"获取 {index_name} 成分股...")
        # 尝试多种方式
        try:
            df = pro.index_weight(index_code=index_code, trade_date="")
            if df is not None and not df.empty:
                latest_date = df["trade_date"].max()
                symbols = df[df["trade_date"] == latest_date]["con_code"].tolist()
                logger.info(f"{index_name} 成分股共 {len(symbols)} 只 (日期: {latest_date})")
                return symbols
        except Exception as e:
            logger.warning(f"index_weight 失败: {e}")

        # 降级：用 stock_basic 的 hs300/is_member 字段
        try:
            if index_name.lower() == "csi300":
                df = pro.stock_basic(list_status="L", fields="ts_code,hs300")
                symbols = df[df["hs300"] == "N"]["ts_code"].tolist()  # don't remember exact value
                logger.info(f"hs300 成分股共 {len(symbols)} 只")
                return symbols
        except Exception:
            pass

        logger.error(f"无法获取 {index_name} 成分股")
        return []

    def batch(self, stock_file: str, start: str = "20240101", end: str = None, delay: float = 0.3, batch_size: int = 50, batch_delay: float = 60):
        """
        从文件读取股票列表，批量下载。每 batch_size 只后暂停 batch_delay 秒，避免触发 tushare 频率限制。

        stock_file: 股票列表文件，每行一个 ts_code (如 600000.SH)
        start: 起始日期 YYYYMMDD
        end: 截止日期，默认今天
        delay: 请求间隔（秒）
        batch_size: 每批股票数
        batch_delay: 每批后暂停（秒）
        """
        if end is None:
            end = pd.Timestamp.now().strftime("%Y%m%d")

        with open(stock_file) as f:
            codes = [line.strip() for line in f if line.strip()]

        total = len(codes)
        logger.info(f"批量下载: {total} 只股票, {start}~{end}, 每批{batch_size}只, 间隔{batch_delay}s, 超时{STOCK_TIMEOUT}s, 重试{MAX_RETRIES}次")

        success = 0
        fail = 0
        failed_list = []
        for i, ts_code in enumerate(codes):
            qlib_symbol = self._normalize_symbol(ts_code)
            fname = code_to_fname(qlib_symbol)
            file_path = self.save_dir / f"{fname}.csv"

            try:
                # 使用超时保护的 API 调用
                df = self._fetch_with_timeout(ts_code, start, end)
                df = self._tushare_to_csv(df, qlib_symbol)

                if df.empty:
                    logger.warning(f"[{i+1}/{total}] {ts_code}: 无数据")
                    fail += 1
                    failed_list.append(ts_code)
                else:
                    if file_path.exists():
                        old_df = pd.read_csv(file_path, parse_dates=["date"])
                        df = pd.concat([old_df, df], ignore_index=True)
                        df = df.drop_duplicates("date", keep="last")
                    df.to_csv(file_path, index=False)
                    success += 1

            except Exception as e:
                logger.error(f"[{i+1}/{total}] {ts_code} 最终失败: {e}")
                fail += 1
                failed_list.append(ts_code)

            # 进度文件
            self._write_progress(i + 1, total, success, fail)

            # 每 100 只输出日志
            if (i + 1) % 100 == 0:
                logger.info(f"[{i+1}/{total}] 进度: {success} 成功, {fail} 失败")

            time.sleep(delay)

            # 每批后暂停
            if (i + 1) % batch_size == 0 and i + 1 < total:
                logger.info(f"批次完成 ({i+1}/{total}), 暂停 {batch_delay}s...")
                time.sleep(batch_delay)

        logger.info(f"全部完成: {success} 成功, {fail} 失败")
        if failed_list:
            fail_file = LOG_DIR / "failed_stocks.txt"
            fail_file.write_text("\n".join(failed_list))
            logger.info(f"失败列表已保存到 {fail_file}")

    def update(self, stock_file: str, days: int = 5, delay: float = 0.3, batch_size: int = 50, batch_delay: float = 60):
        """
        增量更新：只拉取最近 N 天的数据。用于每日定时任务。

        stock_file: 股票列表文件，每行一个 ts_code (如 600000.SH)
        days: 拉取最近 N 天数据
        delay: 请求间隔（秒）
        batch_size: 每批股票数
        batch_delay: 每批后暂停（秒）
        """
        pro = self._init_api()
        today = pd.Timestamp.now()
        # tushare daily 接口按自然日算，多拉几天覆盖停牌/节假日
        end = today.strftime("%Y%m%d")
        start = (today - pd.Timedelta(days=days + 5)).strftime("%Y%m%d")

        with open(stock_file) as f:
            codes = [line.strip() for line in f if line.strip()]

        total = len(codes)
        logger.info(f"增量更新: {total} 只股票, 拉取最近 {days} 天 (实际 {start}~{end}), 每批{batch_size}只")

        success = 0
        fail = 0
        new_rows = 0
        for i, ts_code in enumerate(codes):
            qlib_symbol = self._normalize_symbol(ts_code)
            fname = code_to_fname(qlib_symbol)
            file_path = self.save_dir / f"{fname}.csv"

            try:
                df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
                df = self._tushare_to_csv(df, qlib_symbol)

                if df.empty:
                    fail += 1
                else:
                    before_count = 0
                    if file_path.exists():
                        old_df = pd.read_csv(file_path, parse_dates=["date"])
                        before_count = len(old_df)
                        df = pd.concat([old_df, df], ignore_index=True)
                        df = df.drop_duplicates("date", keep="last")
                    df.to_csv(file_path, index=False)
                    added = len(df) - before_count
                    new_rows += max(added, 0)
                    success += 1

            except Exception as e:
                logger.error(f"[{i+1}/{total}] {ts_code} 失败: {e}")
                fail += 1

            time.sleep(delay)

            if (i + 1) % batch_size == 0 and i + 1 < total:
                logger.info(f"批次完成 ({i+1}/{total}), 新增 {new_rows} 行, 暂停 {batch_delay}s...")
                time.sleep(batch_delay)

        logger.info(f"增量更新完成: 成功 {success}, 失败 {fail}, 新增数据 {new_rows} 行")

    def count(self):
        """统计已下载数据"""
        files = sorted(self.save_dir.glob("*.csv"))
        if not files:
            print(f"无数据。CSV 目录: {self.save_dir}")
            return
        total_rows = 0
        dates = []
        for f in files:
            df = pd.read_csv(f)
            total_rows += len(df)
            if "date" in df.columns and not df.empty:
                dates.append(pd.to_datetime(df["date"].iloc[0]))
                dates.append(pd.to_datetime(df["date"].iloc[-1]))
        all_dates = [d for d in dates if pd.notna(d)]
        print(f"股票数: {len(files)}")
        print(f"总行数: {total_rows}")
        print(f"CSV 目录: {self.save_dir}")
        if all_dates:
            print(f"日期范围: {min(all_dates).strftime('%Y-%m-%d')} ~ {max(all_dates).strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    fire.Fire(TushareCollector)