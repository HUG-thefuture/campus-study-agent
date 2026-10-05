# -*- coding: utf-8 -*-
"""种子数据保鲜：把作业的 due/week 重算到教学周网格上，演示数据永不过季。

背景（为什么有这个脚本）：种子数据此前把截止日期硬编码在 9 月，
日历一过就出现"这周没有作业 / 学习计划无未来任务"的空转演示。
所有作业按 `due = semester_start + (week-1)*7 + 日内偏移` 网格管理，
本脚本负责校验与修复这张网格，并可整体平移。

用法：
  python scripts/refresh_seed.py             # 校验：以 due 为准重算 week，修复不一致
  python scripts/refresh_seed.py --check     # 只报告不写入（CI 可用）
  python scripts/refresh_seed.py --shift 2   # 全部作业整体后移 2 周（过季时延展演示窗口）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / "app" / "data"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写入")
    ap.add_argument("--shift", type=int, default=0, help="整体平移的周数（可为负）")
    args = ap.parse_args()

    courses = json.loads((DATA / "courses.json").read_text(encoding="utf-8"))
    semester_start = date.fromisoformat(courses["semester_start"])
    path = DATA / "assignments.json"
    items = json.loads(path.read_text(encoding="utf-8"))

    changed, stale = [], []
    for a in items:
        due = date.fromisoformat(a["due"])
        if args.shift:
            due += timedelta(weeks=args.shift)
        true_week = (due - semester_start).days // 7 + 1
        if args.shift or true_week != a["week"]:
            stale.append((a["assignment_id"], a["week"], true_week))
            a["week"], a["due"] = true_week, due.isoformat()
            changed.append(a)

    if args.check:
        status = "INCONSISTENT" if stale else "CONSISTENT"
        print(f"seed-check: {status} ({len(stale)} mismatched)")
        for aid, w, tw in stale:
            print(f"  {aid}: week {w} -> {tw}")
        return 1 if stale else 0

    if changed:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    mode = f"shift {args.shift} weeks" if args.shift else "normalize week"
    print(f"refreshed: {len(changed)}/{len(items)} assignments ({mode})")
    newest = max(date.fromisoformat(a["due"]) for a in items)
    today = date.today()
    print(f"coverage: {min(date.fromisoformat(a['due']) for a in items)} ~ {newest}"
          f" | today={today} -> "
          f"{'IN-WINDOW' if newest >= today else 'STALE: 考虑 --shift 前移'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
