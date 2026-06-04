"""
Convert HK restaurant xlsx dataset → route_planner/data/poi.csv

Sources
-------
  route_planner/data/HK/restaurant_data_category_with_ratings_2021_2025.xlsx
      One row per POI: names, addresses, lat/lng, categories, averaged ratings
  route_planner/data/HK/Restaurant_20{21-25}.xlsx
      One row per review: used to compute per-year review counts for trend

Output
------
  route_planner/data/poi.csv  (overwrites existing file)

Missing-field strategy
----------------------
  avg_price_per_person  → mapped from primary English category tag
  queue_risk            → review_count==50(maxed) & taste>=4.0 → 高
                          review_count>=30 & taste>=3.8           → 中
                          else                                     → 低
  queue_minutes_peak    → 高:30  中:15  低:5
  queue_minutes_offpeak → 高:10  中:5   低:0
  trend_tag             → open_since 2023+ → 新晋
                          2024+2025 reviews >= 2×(2021+2022) & total>=10 → 火爆
                          else → 经典
  half_year_sales       → (2024_reviews + 2025_reviews) × 200  (proxy)
  recommend_count       → total reviews across all years
  has_group_buy         → 0 (no data)
  business_hours        → "" (no data)
"""

import ast
import os
import sys

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ROOT = os.path.join(os.path.dirname(__file__), "..")
_HK_DIR = os.path.join(_ROOT, "route_planner", "data", "HK")
_OUT_CSV = os.path.join(_ROOT, "route_planner", "data", "poi.csv")

_AGG_FILE = os.path.join(_HK_DIR, "restaurant_data_category_with_ratings_2021_2025.xlsx")
_YEAR_FILES = {
    yr: os.path.join(_HK_DIR, f"Restaurant_{yr}.xlsx")
    for yr in [2021, 2022, 2023, 2024, 2025]
}

# ---------------------------------------------------------------------------
# English category → Chinese sub_category (first match wins)
# ---------------------------------------------------------------------------
_CAT_MAP = {
    "Hong Kong Style":   "港式",
    "Tea Restaurant":    "茶餐廳",
    "Cha Chaan Teng":    "茶餐廳",
    "Dim Sum":           "點心",
    "Guangdong":         "廣東菜",
    "Cantonese":         "廣東菜",
    "Chiu Chow":         "潮州菜",
    "Shanghainese":      "上海菜",
    "Sichuan":           "川菜",
    "Peking Duck":       "北京菜",
    "Taiwanese":         "台灣菜",
    "Taiwan":            "台灣菜",
    "Japanese":          "日本料理",
    "Sushi":             "壽司",
    "Ramen":             "拉麵",
    "Korean":            "韓國料理",
    "Thai":              "泰國料理",
    "Vietnamese":        "越南菜",
    "Indian":            "印度菜",
    "Southeast Asian":   "東南亞菜",
    "Western":           "西餐",
    "French":            "法國菜",
    "Italian":           "意大利菜",
    "Spanish":           "西班牙菜",
    "Mediterranean":     "地中海菜",
    "Steakhouse":        "扒房",
    "American":          "美式餐廳",
    "International":     "國際料理",
    "Fusion":            "融合料理",
    "Noodles":           "麵食",
    "Rice Noodles":      "麵食",
    "Hot Pot":           "火鍋",
    "BBQ":               "燒烤",
    "Seafood":           "海鮮",
    "Vegetarian":        "素食",
    "Dessert":           "甜品",
    "Bakery":            "麵包店",
    "Cafe":              "咖啡店",
    "Coffee Shop":       "咖啡店",
    "Fast Food":         "快餐",
    "Brunch":            "早午餐",
    "Buffet":            "自助餐",
    "Bar":               "酒吧",
    "Izakaya":           "居酒屋",
}

# ---------------------------------------------------------------------------
# English neighborhood → Chinese area label
# The English address ends with the neighborhood name after the last comma.
# ---------------------------------------------------------------------------
_EN_AREA_MAP = {
    # Hong Kong Island — Central & Western
    "Central":          "中環", "Sheung Wan":     "中環", "Sai Ying Pun":  "中環",
    "Kennedy Town":     "中環", "Mid-Levels":     "中環", "The Peak":      "中環",
    "Admiralty":        "中環",
    # Wan Chai
    "Wan Chai":         "灣仔", "Causeway Bay":   "灣仔", "Happy Valley":  "灣仔",
    "Tai Hang":         "灣仔",
    # Eastern
    "North Point":      "東區", "Quarry Bay":     "東區", "Taikoo Shing": "東區",
    "Sai Wan Ho":       "東區", "Shau Kei Wan":   "東區", "Chai Wan":      "東區",
    "Heng Fa Chuen":    "東區",
    # Southern
    "Aberdeen":         "南區", "Stanley":        "南區", "Repulse Bay":   "南區",
    "Wong Chuk Hang":   "南區", "Pok Fu Lam":     "南區", "Deep Water Bay":"南區",
    "Ap Lei Chau":      "南區",
    # Kowloon — Yau Tsim Mong
    "Tsim Sha Tsui":    "旺角", "Jordan":         "旺角", "Yau Ma Tei":    "旺角",
    "Mong Kok":         "旺角", "Tai Kok Tsui":   "旺角",
    # Sham Shui Po
    "Sham Shui Po":     "深水埗", "Cheung Sha Wan":"深水埗", "Shek Kip Mei":"深水埗",
    "Lai Chi Kok":      "深水埗", "Mei Foo":       "深水埗",
    # Kowloon City
    "Kowloon City":     "九龍城", "To Kwa Wan":    "九龍城", "Ma Tau Wai":  "九龍城",
    "Kowloon Tong":     "九龍城", "Hung Hom":      "九龍城", "Ho Man Tin":  "九龍城",
    "Kai Tak":          "九龍城",
    # Wong Tai Sin
    "Wong Tai Sin":     "黃大仙", "Diamond Hill":  "黃大仙", "Tsz Wan Shan":"黃大仙",
    "San Po Kong":      "黃大仙",
    # Kwun Tong
    "Kwun Tong":        "觀塘", "Lam Tin":        "觀塘", "Ngau Tau Kok":  "觀塘",
    "Yau Tong":         "觀塘",
    # New Territories — Tseung Kwan O
    "Tseung Kwan O":    "將軍澳", "Po Lam":        "將軍澳", "Hang Hau":    "將軍澳",
    "Tiu Keng Leng":    "將軍澳",
    # Tsuen Wan / Kwai Tsing
    "Tsuen Wan":        "荃灣", "Kwai Chung":     "荃灣", "Tsing Yi":      "荃灣",
    "Kwai Fong":        "荃灣",
    # Tuen Mun
    "Tuen Mun":         "屯門",
    # Yuen Long
    "Yuen Long":        "元朗", "Tin Shui Wai":   "元朗",
    # Sha Tin
    "Sha Tin":          "沙田", "Ma On Shan":     "沙田", "Fo Tan":        "沙田",
    "Tai Wai":          "沙田",
    # Tai Po
    "Tai Po":           "大埔",
    # North
    "Sheung Shui":      "上水", "Fanling":        "上水",
    # Sai Kung
    "Sai Kung":         "西貢", "Clear Water Bay":"西貢",
    # Islands
    "Tung Chung":       "離島", "Lantau Island":  "離島", "Mui Wo":        "離島",
    "Cheung Chau":      "離島", "Peng Chau":      "離島",
    # Additional neighborhoods
    "Soho":             "中環", "SOHO":           "中環",
    "Lan Kwai Fong":    "中環", "SoHo":           "中環",
    "Shek Tong Tsui":   "中環",
    "Fortress Hill":    "東區",
    "Luen Wo Hui":      "上水",
    "Shek Mun":         "沙田",
    "The Whampoa":      "九龍城",
    "Shatin":           "沙田",
    "Tsuen Wan West":   "荃灣",
    "Airport":          "離島", "International Airport": "離島",
}

def _extract_area(chinese_address: str, english_address: str = "") -> str:
    # Try English address first — last comma-segment is the neighborhood
    if english_address:
        last = english_address.rsplit(",", 1)[-1].strip()
        # Sometimes it's "Neighbourhood, Hong Kong" — strip trailing "Hong Kong"
        last = last.replace("Hong Kong", "").strip().rstrip(",").strip()
        if last in _EN_AREA_MAP:
            return _EN_AREA_MAP[last]
        # Try partial match (e.g. "Tsim Sha Tsui East" → "旺角")
        for eng, zh in _EN_AREA_MAP.items():
            if eng in last:
                return zh

    # Street-name based lookup (major streets → area)
    _STREET_MAP = {
        "彌敦": "旺角", "廣東道": "旺角", "柯士甸": "旺角", "梳士巴利": "旺角",
        "麼地道": "旺角", "通菜": "旺角", "砵蘭": "旺角", "亞皆老": "旺角",
        "西洋菜": "旺角", "上海街": "旺角", "吳松": "旺角", "太子道": "旺角",
        "軒尼詩": "灣仔", "謝斐": "灣仔", "駱克": "灣仔", "告士打": "灣仔",
        "皇后大道東": "灣仔", "港灣道": "灣仔",
        "皇后大道中": "中環", "干諾道中": "中環", "德輔道中": "中環",
        "威靈頓": "中環", "金鐘道": "中環",
        "英皇道": "東區", "電氣道": "東區", "筲箕灣道": "東區", "太古城道": "東區",
        "小西灣道": "東區", "渣華道": "東區",
        "元州": "深水埗", "欽州": "深水埗", "長沙灣道": "深水埗", "荔枝角道": "深水埗",
        "開源道": "觀塘", "偉業": "觀塘", "楊屋": "觀塘", "牛頭角道": "觀塘",
        "海庭道": "旺角",                                  # 大角咀/旺角西
        "興芳": "荃灣", "葵富": "荃灣", "青敬": "荃灣",   # 葵涌/青衣 → 荃灣標籤
        "大河道": "荃灣",                                  # 荃灣市中心
        "天湖路": "元朗",                                  # 天水圍
        "唐德": "將軍澳",                                  # 將軍澳工業邨
        "青山公路": "屯門", "屯順": "屯門",
        "沙田正街": "沙田", "石門": "沙田", "鞍祿": "沙田", "沙田中心": "沙田",
        "馬頭圍道": "九龍城", "聯合道": "九龍城", "啟德協調": "九龍城",
        "大埔墟": "大埔",
        "聯和墟": "上水",
    }
    for street, area_label in _STREET_MAP.items():
        if street in chinese_address:
            return area_label

    # Fallback: scan Chinese address for known neighborhood names
    _ZH_KEYWORDS = [
        (["中環", "上環", "西營盤", "石塘咀", "堅尼地城", "半山", "山頂"], "中環"),
        (["灣仔", "銅鑼灣", "跑馬地", "大坑"],                            "灣仔"),
        (["北角", "鰂魚涌", "太古", "西灣河", "筲箕灣", "柴灣", "小西灣"],"東區"),
        (["赤柱", "淺水灣", "香港仔", "黃竹坑", "薄扶林"],                "南區"),
        (["尖沙咀", "佐敦", "油麻地", "旺角", "大角咀"],                  "旺角"),
        (["深水埗", "長沙灣", "石硤尾", "荔枝角", "美孚"],                "深水埗"),
        (["九龍城", "土瓜灣", "馬頭圍", "九龍塘", "紅磡", "何文田"],      "九龍城"),
        (["黃大仙", "鑽石山", "慈雲山", "新蒲崗"],                        "黃大仙"),
        (["觀塘", "藍田", "牛頭角", "秀茂坪"],                            "觀塘"),
        (["將軍澳", "坑口", "調景嶺", "寶琳", "寶林"],                    "將軍澳"),
        (["荃灣", "葵涌", "葵青", "青衣"],                                "荃灣"),
        (["屯門"],                                                        "屯門"),
        (["元朗", "天水圍"],                                              "元朗"),
        (["沙田", "馬鞍山", "火炭", "大圍"],                              "沙田"),
        (["大埔"],                                                        "大埔"),
        (["上水", "粉嶺"],                                                "上水"),
        (["西貢"],                                                        "西貢"),
        (["東涌", "大嶼山", "梅窩", "長洲"],                              "離島"),
    ]
    for keywords, label in _ZH_KEYWORDS:
        if any(kw in chinese_address for kw in keywords):
            return label
    return "香港"

# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------
def _parse_categories(raw) -> list:
    if not raw or (isinstance(raw, float)):
        return []
    try:
        cats = ast.literal_eval(raw) if isinstance(raw, str) else raw
        return [c.strip() for c in cats if c.strip()]
    except Exception:
        return []

def _sub_category(cats: list) -> str:
    for c in cats:
        for eng, zh in _CAT_MAP.items():
            if eng.lower() in c.lower():
                return zh
    # Fallback: return first category as-is
    return cats[0] if cats else "餐廳"

# ---------------------------------------------------------------------------
# Price defaults by sub_category
# ---------------------------------------------------------------------------
_PRICE_MAP = {
    "港式": 65, "茶餐廳": 65, "快餐": 50,
    "麵食": 65, "點心": 130, "廣東菜": 130, "潮州菜": 110,
    "上海菜": 120, "川菜": 100, "北京菜": 130, "台灣菜": 100,
    "日本料理": 200, "壽司": 250, "拉麵": 100, "居酒屋": 180,
    "韓國料理": 150, "泰國料理": 120, "越南菜": 100, "印度菜": 120,
    "東南亞菜": 110,
    "西餐": 250, "法國菜": 400, "意大利菜": 280, "西班牙菜": 300,
    "地中海菜": 280, "扒房": 500, "美式餐廳": 200,
    "國際料理": 220, "融合料理": 250,
    "火鍋": 160, "燒烤": 160, "海鮮": 280, "素食": 100,
    "自助餐": 300, "早午餐": 150,
    "甜品": 80, "麵包店": 70, "咖啡店": 80, "酒吧": 150,
}
_DEFAULT_PRICE = 130

# ---------------------------------------------------------------------------
# Queue risk
# ---------------------------------------------------------------------------
def _queue_risk(review_count, taste_rating):
    rc = review_count or 0
    tr = taste_rating or 0
    if rc >= 50 and tr >= 4.0:
        return "高", 30, 10
    if rc >= 30 and tr >= 3.8:
        return "中", 15, 5
    return "低", 5, 0

# ---------------------------------------------------------------------------
# Trend tag
# ---------------------------------------------------------------------------
def _trend(poi_id, open_since, yearly_counts: dict) -> tuple[str, int, int]:
    """Returns (trend_tag, half_year_sales_proxy, recommend_count)."""
    c21 = yearly_counts.get(poi_id, {}).get(2021, 0)
    c22 = yearly_counts.get(poi_id, {}).get(2022, 0)
    c24 = yearly_counts.get(poi_id, {}).get(2024, 0)
    c25 = yearly_counts.get(poi_id, {}).get(2025, 0)
    total = sum(yearly_counts.get(poi_id, {}).values())

    # open_since: "2023-11-08T00:00:00+08:00"
    is_new = False
    if open_since and isinstance(open_since, str):
        try:
            year = int(open_since[:4])
            is_new = year >= 2023
        except Exception:
            pass

    recent = c24 + c25
    early  = c21 + c22

    if is_new and total >= 5:
        tag = "新晋"
    elif recent >= 2 * max(early, 1) and recent >= 10:
        tag = "火爆"
    else:
        tag = "经典"

    half_year_sales = recent * 200   # proxy: each review ≈ 200 actual visits
    recommend_count = total * 150

    return tag, half_year_sales, recommend_count

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading aggregated file…")
    agg = pd.read_excel(_AGG_FILE)
    print(f"  {len(agg)} POIs loaded")

    # Filter invalid coordinates
    agg = agg[(agg["latitude"] > 1) & (agg["longitude"] > 1)]
    print(f"  {len(agg)} after removing zero coordinates")

    # Filter POIs with no ratings at all
    agg = agg[agg["taste_rating"].notna()]
    print(f"  {len(agg)} after requiring ratings")

    # Filter very low review_count (< 3)
    agg = agg[agg["review_count"].fillna(0) >= 3]
    print(f"  {len(agg)} after review_count >= 3")

    print("Loading yearly review files for trend computation…")
    yearly_counts: dict[int, dict[int, int]] = {}  # poi_id → {year → count}
    for year, path in _YEAR_FILES.items():
        print(f"  {year}…", end=" ", flush=True)
        df = pd.read_excel(path, usecols=["poi_id"])
        counts = df["poi_id"].value_counts().to_dict()
        for pid, cnt in counts.items():
            if pid not in yearly_counts:
                yearly_counts[pid] = {}
            yearly_counts[pid][year] = cnt
        print(f"{len(counts)} unique POIs")

    print("Building CSV rows…")
    rows = []
    for _, row in agg.iterrows():
        poi_id  = int(row["poi_id"])
        cats        = _parse_categories(row.get("categories"))
        sub_cat     = _sub_category(cats)
        address     = str(row.get("chinese_address") or "")
        address_en  = str(row.get("english_address") or "")
        area        = _extract_area(address, address_en)
        lat     = float(row["latitude"])
        lng     = float(row["longitude"])

        # Ratings (round to 1 decimal)
        def _r(col):
            v = row.get(col)
            return round(float(v), 1) if pd.notna(v) else 0.0

        taste   = _r("taste_rating")
        decor   = _r("decor_rating")
        service = _r("service_rating")
        hygiene = _r("hygiene_rating")
        value   = _r("value_rating")
        rc      = int(row.get("review_count") or 0)
        rating  = round((taste + decor + service + hygiene + value) / 5, 1) if taste else 0.0

        price   = _PRICE_MAP.get(sub_cat, _DEFAULT_PRICE)
        risk, peak, offpeak = _queue_risk(rc, taste)
        tag, hys, rec = _trend(poi_id, row.get("open_since"), yearly_counts)

        def _str(val) -> str:
            s = str(val).strip() if val is not None else ""
            return "" if s.lower() in ("nan", "none") else s

        rows.append({
            "id":                       f"hk_{poi_id}",
            "name":                     _str(row.get("chinese_name")),
            "name_en":                  _str(row.get("english_name")),
            "category":                 "餐饮",
            "sub_category":             sub_cat,
            "address":                  _str(row.get("chinese_address")),
            "address_en":               _str(row.get("english_address")),
            "city":                     "香港",
            "area":                     area,
            "lat":                      lat,
            "lng":                      lng,
            "rating":                   rating,
            "taste_rating":             taste,
            "decor_rating":             decor,
            "service_rating":           service,
            "hygiene_rating":           hygiene,
            "value_rating":             value,
            "review_count":             rc,
            "half_year_sales":          hys,
            "avg_price_per_person":     price,
            "queue_risk":               risk,
            "queue_minutes_peak":       peak,
            "queue_minutes_offpeak":    offpeak,
            "has_group_buy":            0,
            "group_buy_title":          "",
            "group_buy_original_price": 0,
            "group_buy_current_price":  0,
            "business_hours":           "",
            "trend_tag":                tag,
            "recommend_count":          rec,
        })

    df_out = pd.DataFrame(rows)
    df_out = df_out[df_out["name"].str.strip().astype(bool)]  # drop rows with empty name
    df_out.to_csv(_OUT_CSV, index=False, encoding="utf-8")
    print(f"\nDone: {len(df_out)} POIs written to {_OUT_CSV}")

    # Quick stats
    print("\nSub-category distribution (top 15):")
    print(df_out["sub_category"].value_counts().head(15).to_string())
    print("\nArea distribution (top 15):")
    print(df_out["area"].value_counts().head(15).to_string())
    print("\nTrend tag distribution:")
    print(df_out["trend_tag"].value_counts().to_string())
    print("\nQueue risk distribution:")
    print(df_out["queue_risk"].value_counts().to_string())


if __name__ == "__main__":
    main()
