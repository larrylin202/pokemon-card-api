import os
import json
import time
import requests
from datetime import datetime, timezone

# 1. Configuration
API_KEY = os.getenv("POKEMON_TCG_API_KEY", "")
BASE_URL = "https://api.pokemontcg.io/v2"
OUTPUT_DIR = "api_data"
TRACKER_FILE = f"{OUTPUT_DIR}/sync_tracker.json"
CACHE_EXPIRY_HOURS = 24

HEADERS = {
    "User-Agent": "PokemonCardScanner/1.0",
    "Accept": "application/json"
}
if API_KEY:
    HEADERS["X-Api-Key"] = API_KEY

os.makedirs(f"{OUTPUT_DIR}/sets", exist_ok=True)
os.makedirs(f"{OUTPUT_DIR}/prices", exist_ok=True)

def load_sync_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_sync_tracker(tracker):
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(tracker, f, indent=2)

def is_set_stale(set_id, tracker, force=False):
    if force:
        return True
        
    price_file = f"{OUTPUT_DIR}/prices/{set_id}_prices.json"
    set_file = f"{OUTPUT_DIR}/sets/{set_id}.json"

    if not (os.path.exists(price_file) and os.path.exists(set_file)):
        return True

    last_synced_str = tracker.get(set_id)
    if not last_synced_str:
        return True

    try:
        last_synced = datetime.fromisoformat(last_synced_str)
        elapsed_seconds = (datetime.now(timezone.utc) - last_synced).total_seconds()
        return elapsed_seconds > (CACHE_EXPIRY_HOURS * 3600)
    except Exception:
        return True

def fetch_set_data(set_id, tracker, force=False):
    # Pure tracker check: NEVER check os.path.getmtime!
    if not is_set_stale(set_id, tracker, force=force):
        print(f"Skipping {set_id}: Updated within the last {CACHE_EXPIRY_HOURS} hours.")
        return

    print(f"Processing set: {set_id}...")
    cards = []
    page = 1
    page_size = 100
    session = requests.Session()
    session.headers.update(HEADERS)

    while True:
        url = f"{BASE_URL}/cards?q=set.id:{set_id}&page={page}&pageSize={page_size}"
        response = None
        max_attempts = 15
        backoff = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                res = session.get(url, timeout=15)
                if res.status_code == 200:
                    response = res
                    break
                elif res.status_code in [429, 500, 502, 503, 504]:
                    time.sleep(backoff)
                    backoff += 1.5
                else:
                    break
            except requests.exceptions.RequestException:
                time.sleep(backoff)
                backoff += 1.5

        if not response or response.status_code != 200:
            print(f"Aborting {set_id} at page {page}.")
            return

        data = response.json().get("data", [])
        if not data:
            break

        cards.extend(data)
        page += 1
        time.sleep(0.5)

    cleaned_cards = []
    price_map = {}

    for card in cards:
        card_num = card.get("number", "")
        images = card.get("images", {})
        small_img = images.get("small", "")
        large_img = images.get("large", "")

        tcg = card.get("tcgplayer", {}).get("prices", {})
        
        # Track prices per variant and record which variants exist
        available_variants = []
        card_prices = {}

        for variant in ["normal", "holofoil", "reverseHolofoil", "1stEditionHolofoil"]:
            if variant in tcg:
                available_variants.append(variant)
                market_val = tcg[variant].get("market")
                if market_val is not None:
                    card_prices[variant] = market_val

        cleaned_cards.append({
            "id": card.get("id"),
            "name": card.get("name"),
            "number": card_num,
            "images": {
                "small": small_img,
                "large": large_img
            },
            "availableVariants": available_variants
        })

        if card_prices:
            price_map[card_num] = card_prices

    with open(f"{OUTPUT_DIR}/sets/{set_id}.json", "w", encoding="utf-8") as f:
        json.dump(cleaned_cards, f, separators=(",", ":"))

    with open(f"{OUTPUT_DIR}/prices/{set_id}_prices.json", "w", encoding="utf-8") as f:
        json.dump(price_map, f, separators=(",", ":"))

    tracker[set_id] = datetime.now(timezone.utc).isoformat()
    save_sync_tracker(tracker)
    print(f"Saved {len(cleaned_cards)} cards and variant prices for {set_id}.")

if __name__ == "__main__":
    import sys
    force_sync = "--force" in sys.argv
    sync_tracker = load_sync_tracker()
    
    set_ids = ["sv1", "sv2", "sv3", "sv3pt5", "sv4", "sv4pt5", "sv5", "sv6", "sv6pt5", "sv7", "sv8", "sv8pt5", "sv9", "sv10", "zsv10pt5", "rsv10pt5", "me1", "me2", "me2pt5", "me3", "me4", "me5"]

    for s_id in set_ids:
        fetch_set_data(s_id, sync_tracker, force=force_sync)
        time.sleep(1)