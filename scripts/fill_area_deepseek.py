#!/usr/bin/env python3
"""
Fill area field for poi.csv rows where area='香港' using DeepSeek.
Supports checkpoint/resume: progress saved to scripts/fill_area_checkpoint.json.

Usage:
    PYTHONPATH=. .venv/bin/python3 scripts/fill_area_deepseek.py
"""
import csv
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CSV_PATH       = Path("route_planner/data/poi.csv")
CSV_OUT        = Path("route_planner/data/poi_area_filled.csv")   # preview before merging
CHECKPOINT     = Path("scripts/fill_area_checkpoint.json")
BATCH_SIZE     = 80   # rows per DeepSeek call

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

_SYSTEM = """\
你是香港地理专家。
根据 POI 的地址文本和经纬度，判断每个地点属于香港哪个具体社区/地区（繁體中文）。

优先返回社区级别的具体地名，不要返回行政区（如"中西區"、"東區"、"油尖旺"、"九龍城區"），
要返回社区名称，例如：
- 中環、上環、西營盤、堅尼地城、石塘咀、薄扶林
- 灣仔、銅鑼灣、天后、炮台山、北角、鰂魚涌、太古、西灣河、筲箕灣、柴灣
- 跑馬地、大坑、掃桿埔、金鐘
- 尖沙咀、佐敦、油麻地、旺角、太子、深水埗、長沙灣、荔枝角、美孚
- 紅磡、土瓜灣、馬頭圍、九龍城、何文田、黃大仙、鑽石山、新蒲崗
- 牛頭角、九龍灣、觀塘、秀茂坪、坪石、藍田、油塘
- 荃灣、葵芳、葵興、青衣
- 沙田、大圍、火炭、馬鞍山
- 將軍澳、西貢
- 元朗、天水圍、屯門
- 上水、粉嶺、大埔、馬料水
- 東涌、大嶼山
- 香港仔、鴨脷洲、田灣、南區、薄扶林
- 將軍澳、坑口、調景嶺

若经纬度或地址显示位置不在香港任何已知社区，或无法精确判断，则返回距离最近的已知区域名称。

输入：JSON 数组，每项含 id、address、lat、lng。
输出：严格 JSON 对象，key=id，value=繁體中文区域名。只输出 JSON，无多余文字。\
"""


def _call_deepseek(batch: list[dict]) -> dict[str, str]:
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=2048,
    )
    raw = resp.choices[0].message.content.strip()
    return json.loads(raw)


def main() -> None:
    # ---------- load CSV ----------
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    to_fix_ids = {r["id"] for r in rows if r["area"] == "香港"}
    print(f"Rows to fix: {len(to_fix_ids):,} / {len(rows):,}")

    # ---------- load checkpoint ----------
    area_updates: dict[str, str] = {}
    if CHECKPOINT.exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            area_updates = json.load(f)
        print(f"Resumed from checkpoint: {len(area_updates):,} already done")

    # ---------- build batches (skip already done) ----------
    remaining = [r for r in rows if r["id"] in to_fix_ids and r["id"] not in area_updates]
    batches = [remaining[i:i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    total_batches = len(batches)
    print(f"Remaining: {len(remaining):,} rows → {total_batches} batches of {BATCH_SIZE}")

    failed_batches = 0

    for idx, batch in enumerate(batches, start=1):
        payload = [
            {"id": r["id"], "address": r["address"], "lat": r["lat"], "lng": r["lng"]}
            for r in batch
        ]
        try:
            result = _call_deepseek(payload)
            area_updates.update(result)
            got = len(result)
            print(f"[{idx:3d}/{total_batches}] +{got:3d} areas  (total {len(area_updates):,})")
        except Exception as e:
            print(f"[{idx:3d}/{total_batches}] FAILED: {e}")
            failed_batches += 1
            time.sleep(3)

        # save checkpoint every batch
        with open(CHECKPOINT, "w", encoding="utf-8") as f:
            json.dump(area_updates, f, ensure_ascii=False)

        # gentle rate-limit: ~3 requests/sec
        time.sleep(0.35)

    # ---------- apply updates ----------
    updated = 0
    still_hk = 0
    for row in rows:
        new_area = area_updates.get(row["id"])
        if new_area:
            row["area"] = new_area
            updated += 1
        elif row["area"] == "香港":
            still_hk += 1

    # ---------- write to new CSV (original untouched) ----------
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Updated {updated:,} rows.")
    print(f"Output → {CSV_OUT}  (original poi.csv untouched)")
    if still_hk:
        print(f"WARNING: {still_hk} rows still have area='香港' (failed batches: {failed_batches})")
    else:
        print("All rows updated successfully.")
        print("When satisfied, replace the original with:")
        print(f"  mv {CSV_OUT} {CSV_PATH}")

    if failed_batches == 0 and CHECKPOINT.exists():
        CHECKPOINT.unlink()
        print("Checkpoint file removed.")


if __name__ == "__main__":
    main()
