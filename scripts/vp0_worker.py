#!/usr/bin/env python3
"""vp0_worker — ограниченная возобновляемая задача для доказательства recovery.

Обрабатывает N элементов по одному, атомарно фиксируя прогресс в state-файле
ПОСЛЕ каждого элемента (temp+rename+fsync). Сон стоит ДО фиксации, поэтому
прерывание во время сна оставляет элемент необработанным — граница чистая, а
после рестарта задача продолжается ровно с места обрыва без дублей.
"""

from __future__ import annotations

import argparse
import json
import os
import time


def load(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"n": None, "processed": [], "done": False}


def save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--items", type=int, required=True)
    ap.add_argument("--sleep", type=float, default=0.3)
    a = ap.parse_args()

    d = load(a.state)
    d["n"] = a.items
    start = len(d["processed"])
    for i in range(start, a.items):
        time.sleep(a.sleep)  # окно для прерывания до фиксации
        d["processed"].append(i)
        save(a.state, d)
        print(f"processed {i}", flush=True)
    d["done"] = True
    save(a.state, d)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
