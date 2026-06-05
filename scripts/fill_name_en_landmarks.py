#!/usr/bin/env python3
"""
Fill name_en for landmarks_area_filled.csv using DeepSeek.
Outputs landmarks_final.csv ready to merge.

Usage:
    PYTHONPATH=. .venv/bin/python3 scripts/fill_name_en_landmarks.py
"""
import csv
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CSV_IN     = Path("route_planner/data/HK/landmarks_area_filled.csv")
CSV_OUT    = Path("route_planner/data/HK/landmarks_final.csv")
CHECKPOINT = Path("scripts/fill_name_en_checkpoint.json")
BATCH_SIZE = 50

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

_SYSTEM = """\
You are a Hong Kong tourism expert. Given a list of Hong Kong POIs in Chinese, provide the official English name for each.

Rules:
- Use the official or most widely recognised English name
- For temples/shrines: use their official English names (e.g. 黃大仙祠 → "Wong Tai Sin Temple")
- For shopping areas/streets: use common English names (e.g. 石板街 → "Pottinger Street")
- For theme parks and landmarks, use their official English branding
- For university campuses, use the official English university name
- If a place has no established English name, provide a clean transliteration or descriptive translation
- Do NOT include district or city in the name
- Keep it concise

Input: JSON array, each item has id, name (Chinese), address, area.
Output: strict JSON object, key=id, value=English name only. No extra text.\
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
    with open(CSV_IN, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    print(f"Input rows: {len(rows)}")

    # Load checkpoint
    name_en_map: dict[str, str] = {}
    if CHECKPOINT.exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            name_en_map = json.load(f)
        print(f"Resumed: {len(name_en_map)} already done")

    remaining = [r for r in rows if r["id"] not in name_en_map]
    batches = [remaining[i:i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    print(f"Remaining: {len(remaining)} → {len(batches)} batch(es)")

    for idx, batch in enumerate(batches, start=1):
        payload = [
            {"id": r["id"], "name": r["name"], "address": r["address"], "area": r["area"]}
            for r in batch
        ]
        try:
            result = _call_deepseek(payload)
            name_en_map.update(result)
            print(f"[{idx}/{len(batches)}] +{len(result)} names (total {len(name_en_map)})")
        except Exception as e:
            print(f"[{idx}/{len(batches)}] FAILED: {e}")
            time.sleep(3)

        with open(CHECKPOINT, "w", encoding="utf-8") as f:
            json.dump(name_en_map, f, ensure_ascii=False)
        time.sleep(0.35)

    # Apply
    updated = 0
    for row in rows:
        en = name_en_map.get(row["id"])
        if en:
            row["name_en"] = en
            updated += 1

    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Updated {updated} name_en fields.")
    print(f"Output → {CSV_OUT}")
    if CHECKPOINT.exists():
        CHECKPOINT.unlink()

    # Spot check
    import random; random.seed(99)
    print("\nSample name_en:")
    for r in random.sample(rows, min(10, len(rows))):
        print(f"  {r['name'][:22]:<24} → {r['name_en']}")


if __name__ == "__main__":
    main()
