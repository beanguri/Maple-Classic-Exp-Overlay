"""Refresh assets/loot_prices.json from MeowDB NPC sell-back prices.

Etc monster drops + Use potions/food/buffs. Source:
  https://meowdb.com/msclassic/api/items?type=Etc|Use
Price field: stats.price (same as npcSellBack.price on item detail pages).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "loot_prices.json"
BASE = "https://meowdb.com/msclassic/api/items"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Use subtypes that are drinkable / edible loot (not scrolls, arrows, boxes…).
USE_SUBTYPES = {"Potion", "Food", "Buff", "Pet Food"}

SPOT_CHECK = (
    ("items", "Evil Eye Tail"),
    ("items", "Curse Eye Tail"),
    ("items", "Lorang Claw"),
    ("items", "Charm of the Undead"),
    ("potions", "Red Potion"),
    ("potions", "Orange Potion"),
    ("potions", "White Potion"),
    ("potions", "Blue Potion"),
    ("potions", "Apple"),
    ("potions", "Warrior Potion"),
)


def get_json(url: str, retries: int = 4):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    raise last


def parse_price(stats) -> int | None:
    if stats is None:
        return None
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except Exception:
            return None
    if not isinstance(stats, dict):
        return None
    price = stats.get("price")
    if type(price) is int and price >= 0:
        return price
    if isinstance(price, str) and price.isdigit():
        return int(price)
    return None


def fetch_type(item_type: str, page_size: int = 100) -> list[dict]:
    items = []
    page = 1
    want = item_type.lower()
    while True:
        q = urllib.parse.urlencode({"type": item_type, "limit": page_size, "page": page})
        data = get_json(f"{BASE}?{q}")
        batch = [it for it in (data.get("items") or []) if (it.get("type") or "").lower() == want]
        items.extend(batch)
        print(
            f"{item_type} page {page}: +{len(batch)} (running {len(items)}; "
            f"hasMore={data.get('hasMore')} total={data.get('total')})",
            flush=True,
        )
        if not batch or not data.get("hasMore"):
            break
        page += 1
        if page > 50:
            break
        time.sleep(0.15)
    return items


def priced_map(rows: list[dict], keep=None) -> tuple[dict, int]:
    priced, skipped = {}, 0
    for row in rows:
        if keep is not None and not keep(row):
            skipped += 1
            continue
        name = row.get("name") or row.get("displayName")
        price = parse_price(row.get("stats"))
        if not isinstance(name, str) or not name.strip():
            skipped += 1
            continue
        if price is None or price <= 0:
            skipped += 1
            continue
        priced[name.strip()] = price
    return priced, skipped


def keep_use(row: dict) -> bool:
    sub = row.get("subtype")
    if sub in USE_SUBTYPES:
        return True
    # untitled consumables (e.g. Supreme Sniper Potion)
    if not sub:
        stats = row.get("stats")
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except Exception:
                stats = {}
        if isinstance(stats, dict) and any(
            k in stats for k in ("hpRecover", "mpRecover", "pad", "mad", "speed", "jump", "acc", "eva")
        ):
            return True
        name = (row.get("name") or "").lower()
        return any(w in name for w in ("potion", "elixir", "pill", "tonic", "juice", "soup", "tea"))
    return False


def main():
    etc, etc_skip = priced_map(fetch_type("Etc"))
    pots, pot_skip = priced_map(fetch_type("Use"), keep=keep_use)
    items = {k: etc[k] for k in sorted(etc, key=str.lower)}
    potions = {k: pots[k] for k in sorted(pots, key=str.lower)}
    out = {
        "about": (
            "NPC sell-back prices (mesos) for notice-mode loot value. "
            "items = Etc drops; potions = Use potions/food/buffs. Synced from "
            "meowdb.com/msclassic (stats.price / npcSellBack). Runtime still fills "
            "gaps with half the potion-bar shop buy from potion_table.json. "
            "Unpriced names are listed in the tracker, not guessed."
        ),
        "source": "https://meowdb.com/msclassic/item-db",
        "items": items,
        "potions": potions,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {len(items)} etc + {len(potions)} potions to {OUT} "
        f"(skipped etc={etc_skip} use={pot_skip})"
    )
    for section, want in SPOT_CHECK:
        bag = items if section == "items" else potions
        print(f"  {want}: {bag.get(want, 'MISSING')}")


if __name__ == "__main__":
    main()
