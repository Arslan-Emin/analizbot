"""Deterministik sentetik OHLCV üreteci (tek seferlik çalıştırılır).

Amaç: testlerin ağa BAĞIMLI OLMADAN, her zaman aynı veriyle çalışması.
İlk 60 bar yukarı trend, son 60 bar aşağı trend; küçük salınımlar (wiggle)
RSI'yı uçlara yapışmaktan korur. Sadece stdlib kullanır (pandas gerekmez).

Çalıştır:  python tests/fixtures/generate_fixture.py
"""

from __future__ import annotations

import csv
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path


def build_rows() -> list[tuple]:
    rows: list[tuple] = []
    t = datetime(2024, 1, 1, tzinfo=UTC)
    prev_close = 100.0

    def add_segment(count: int, trend: float, vol_base: float, vol_step: float) -> None:
        nonlocal t, prev_close
        for i in range(count):
            # trend = bar başına ortalama yön; wiggle = düzenli salınım
            wiggle = math.sin(i / 3.0) * 1.0
            close = round(prev_close + trend + wiggle, 2)
            open_ = round(prev_close, 2)
            high = round(max(open_, close) + 0.5, 2)
            low = round(min(open_, close) - 0.5, 2)
            volume = round(vol_base + i * vol_step, 2)
            rows.append((t.isoformat(), open_, high, low, close, volume))
            prev_close = close
            t += timedelta(hours=1)

    add_segment(60, trend=0.6, vol_base=1000.0, vol_step=5.0)    # yukarı trend
    add_segment(60, trend=-0.6, vol_base=1300.0, vol_step=-5.0)  # aşağı trend
    return rows


def main() -> None:
    out_path = Path(__file__).with_name("ohlcv_btcusdt.csv")
    rows = build_rows()
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        writer.writerows(rows)
    print(f"{len(rows)} satir yazildi -> {out_path}")


if __name__ == "__main__":
    main()
