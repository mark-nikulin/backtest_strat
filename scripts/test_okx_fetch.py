#!/usr/bin/env python3
"""
OKX Historical Market Data Fetcher & Parser (Proof of Concept)

This script validates and demonstrates downloading historical market data from OKX:
1. Public daily trade archives (.zip with .csv)
2. Public daily L2 400-level order book archives (.tar.gz with .data JSON Lines)
3. Direct parsing and schema inspection of raw records.
"""

import argparse
import csv
import io
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"


def resolve_trades_url(inst_id: str, date_str: str) -> str:
    """
    Constructs the public CDN archive URL for historical trades.
    Format:
    https://static.okx.com/cdn/okex/traderecords/trades/daily/{YYYYMMDD}/{instId}-trades-{YYYY-MM-DD}.zip
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_compact = dt.strftime("%Y%m%d")
    return f"https://static.okx.com/cdn/okex/traderecords/trades/daily/{date_compact}/{inst_id}-trades-{date_str}.zip"


def resolve_orderbook_url(inst_id: str, date_str: str) -> str:
    """
    Constructs the public CDN archive URL for L2 400lv order book.
    OKX uses two CDN patterns:
    - Recent dates (>= 2025/2026): .../match/orderbook/pro/L2/400lv/daily/{YYYYMMDD}/...
    - Older dates (< 2025): .../match/orderbook/L2/400lv/daily/{YYYYMMDD}/...
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_compact = dt.strftime("%Y%m%d")

    # Try both pro and classic URL paths
    url_pro = f"https://static.okx.com/cdn/okx/match/orderbook/pro/L2/400lv/daily/{date_compact}/{inst_id}-L2orderbook-400lv-{date_str}.tar.gz"
    url_classic = f"https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily/{date_compact}/{inst_id}-L2orderbook-400lv-{date_str}.tar.gz"

    for url in [url_pro, url_classic]:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            continue
    # Default to pro if check fails
    return url_pro


def download_file(url: str, dest_path: Path) -> Path:
    """Downloads a remote file with progress reporting and user-agent."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n[HTTP GET] Fetching: {url}")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as out_file:
        content_length = resp.headers.get("Content-Length")
        total_size = int(content_length) if content_length else 0
        total_mb = f"{total_size / (1024 * 1024):.2f} MB" if total_size else "Unknown size"
        print(f"[DOWNLOAD] Content-Length: {total_mb} -> Saving to {dest_path}")

        downloaded = 0
        chunk_size = 64 * 1024
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                percent = (downloaded / total_size) * 100
                sys.stdout.write(f"\r[PROGRESS] {downloaded / (1024 * 1024):.2f} MB / {total_mb} ({percent:.1f}%)")
                sys.stdout.flush()

        print(f"\n[SUCCESS] Completed download ({dest_path.stat().st_size} bytes)")
    return dest_path


def parse_and_display_trades(zip_path: Path, max_rows: int = 10) -> None:
    """Unpacks trade ZIP archive, reads CSV, and displays schema and sample records."""
    print(f"\n{'='*70}")
    print(f"--- PARSING TRADES ARCHIVE: {zip_path.name} ---")
    print(f"{'='*70}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        file_names = zf.namelist()
        print(f"Archive contents: {file_names}")
        csv_name = file_names[0]

        with zf.open(csv_name, "r") as csv_file:
            text_stream = io.TextIOWrapper(csv_file, encoding="utf-8", errors="replace")
            reader = csv.reader(text_stream)

            header = next(reader, None)
            print(f"\n[CSV HEADER / COLUMNS]: {header}")
            print(f"Columns Count: {len(header) if header else 0}")

            print(f"\n[FIRST {max_rows} TRADE ROWS]:")
            for idx, row in enumerate(reader, start=1):
                if idx > max_rows:
                    break
                # Parse timestamp to human-readable UTC
                ts_ms = int(row[5]) if len(row) > 5 and row[5].isdigit() else None
                dt_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if ts_ms else "N/A"
                print(f"Row {idx:2d}: {row}  --> (timestamp_utc: {dt_str})")


def stream_and_display_orderbook(url: str, max_lines: int = 10) -> None:
    """Streams L2 order book archive (.tar.gz) from CDN and parses first JSON Lines without downloading entire multi-hundred MB file."""
    print(f"\n{'='*70}")
    print(f"--- STREAMING L2 ORDER BOOK ARCHIVE: {url} ---")
    print(f"{'='*70}")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp:
        with tarfile.open(fileobj=resp, mode="r|gz") as tar:
            for member in tar:
                print(f"[TAR MEMBER]: {member.name} (Uncompressed size: {member.size / (1024 * 1024):.2f} MB)")
                extracted = tar.extractfile(member)
                if not extracted:
                    continue

                print(f"\n[FIRST {max_lines} ORDER BOOK EVENTS (NDJSON)]:\n")
                for i in range(1, max_lines + 1):
                    raw_line = extracted.readline()
                    if not raw_line:
                        break
                    record = json.loads(raw_line.decode("utf-8"))
                    ts_ms = int(record.get("ts", 0))
                    dt_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if ts_ms else "N/A"

                    action = record.get("action")
                    asks_sample = record.get("asks", [])[:3]
                    bids_sample = record.get("bids", [])[:3]

                    print(f"Event {i:2d} [{action.upper()} | {dt_str} UTC]:")
                    print(f"   Fields: {list(record.keys())}")
                    print(f"   instId: {record.get('instId')}, action: {action}, ts: {record.get('ts')}")
                    print(f"   asks (top 3) [price, sz, orders]: {asks_sample}")
                    print(f"   bids (top 3) [price, sz, orders]: {bids_sample}\n")
                break


def main():
    parser = argparse.ArgumentParser(description="Test and fetch historical market data from OKX")
    parser.add_argument("--instId", default="BTC-USDT", help="Instrument ID, e.g. BTC-USDT, BTC-USDT-SWAP")
    parser.add_argument("--date", default="2024-01-01", help="Target date in YYYY-MM-DD format")
    parser.add_argument("--type", choices=["trades", "orderbook", "both"], default="both", help="Data type to test")
    parser.add_argument("--output-dir", default="data/raw", help="Target directory to save raw data")
    args = parser.parse_args()

    raw_dir = Path(args.output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] OKX Historical Data Proof of Concept")
    print(f"[*] Instrument: {args.instId}")
    print(f"[*] Target Date: {args.date}")
    print(f"[*] Type: {args.type}")

    if args.type in ["trades", "both"]:
        trades_url = resolve_trades_url(args.instId, args.date)
        zip_path = raw_dir / "trades" / f"{args.instId}-trades-{args.date}.zip"
        try:
            download_file(trades_url, zip_path)
            parse_and_display_trades(zip_path, max_rows=10)
        except urllib.error.HTTPError as e:
            print(f"[ERROR] Failed to fetch trades ({e.code} {e.reason}) at {trades_url}")
        except Exception as e:
            print(f"[ERROR] Trades fetch/parse exception: {e}")

    if args.type in ["orderbook", "both"]:
        ob_url = resolve_orderbook_url(args.instId, args.date)
        try:
            stream_and_display_orderbook(ob_url, max_lines=5)
        except urllib.error.HTTPError as e:
            print(f"[ERROR] Failed to fetch order book ({e.code} {e.reason}) at {ob_url}")
        except Exception as e:
            print(f"[ERROR] Order book stream exception: {e}")

    print("\n[DONE] OKX Historical Data Spike completed successfully.")


if __name__ == "__main__":
    main()
