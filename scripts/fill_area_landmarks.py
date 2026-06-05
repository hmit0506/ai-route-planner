#!/usr/bin/env python3
"""
Refine area field for landmarks_cleaned.csv from district-level (e.g. 油尖旺區)
to neighbourhood-level (e.g. 尖沙咀) using DeepSeek.

Usage:
    PYTHONPATH=. .venv/bin/python3 scripts/fill_area_landmarks.py
"""
import csv
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CSV_PATH   = Path("route_planner/data/HK/landmarks_cleaned.csv")
CSV_OUT    = Path("route_planner/data/HK/landmarks_area_filled.csv")
CHECKPOINT = Path("scripts/fill_area_landmarks_checkpoint.json")
BATCH_SIZE = 80

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

_SYSTEM = """\
你是香港地理专家。
根据 POI 的地址文本、经纬度和所在行政区，判断每个地点属于香港哪个具体社区/地区（繁體中文）。

优先返回社区级别的具体地名，不要返回行政区（如"中西區"、"東區"、"油尖旺區"、"九龍城區"），
要返回社区名称，例如：
- 中環、上環、西營盤、堅尼地城、石塘咀、薄扶林、山頂
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
- 東涌、大嶼山、昂坪、大澳、梅窩
- 香港仔、鴨脷洲、田灣、薄扶林
- 赤柱、淺水灣、深水灣、石澳

對於跨越大片地區的景點（如主題公園、郊野公園、大學校園），可以使用較大的地區名，
例如：竹篙灣（迪士尼）、黃竹坑（海洋公園）、薄扶林（港大）、昂坪（昂坪360）。

输入：JSON 数组，每项含 id、name、address、lat、lng、district（行政区，仅供参考）。
输出：严格 JSON 对象，key=id，value=繁體中文社区/地区名。只输出 JSON，无多余文字。\
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
    return json.loads(resp.choices[0].message.content.strip())


def main() -> None:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    # All rows need refinement (they have district-level names)
    to_fix_ids = {r["id"] for r in rows}
    print(f"Rows to refine: {len(to_fix_ids)} / {len(rows)}")

    # Load checkpoint
    area_updates: dict[str, str] = {}
    if CHECKPOINT.exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            area_updates = json.load(f)
        print(f"Resumed from checkpoint: {len(area_updates)} already done")

    remaining = [r for r in rows if r["id"] not in area_updates]
    batches = [remaining[i:i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    print(f"Remaining: {len(remaining)} rows → {len(batches)} batch(es)")

    for idx, batch in enumerate(batches, start=1):
        payload = [
            {
                "id":       r["id"],
                "name":     r["name"],
                "address":  r["address"],
                "lat":      r["lat"],
                "lng":      r["lng"],
                "district": r["area"],
            }
            for r in batch
        ]
        try:
            result = _call_deepseek(payload)
            area_updates.update(result)
            print(f"[{idx}/{len(batches)}] +{len(result)} areas (total {len(area_updates)})")
        except Exception as e:
            print(f"[{idx}/{len(batches)}] FAILED: {e}")
            time.sleep(3)

        with open(CHECKPOINT, "w", encoding="utf-8") as f:
            json.dump(area_updates, f, ensure_ascii=False)

        time.sleep(0.35)

    # Apply updates
    updated = 0
    for row in rows:
        new_area = area_updates.get(row["id"])
        if new_area:
            row["area"] = new_area
            updated += 1

    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Updated {updated} rows.")
    print(f"Output → {CSV_OUT}")
    if CHECKPOINT.exists():
        CHECKPOINT.unlink()
        print("Checkpoint removed.")

    # Spot check
    import random; random.seed(7)
    print("\nSample areas after refinement:")
    for r in random.sample(rows, min(8, len(rows))):
        print(f"  {r['name'][:22]:<24} → {r['area']}")


if __name__ == "__main__":
    main()
