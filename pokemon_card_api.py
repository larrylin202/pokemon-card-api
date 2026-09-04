import os
import json
import time
import requests
from datetime import datetime, date

# 1. Configuration
API_KEY = os.getenv("POKEMON_TCG_API_KEY", "")
BASE_URL = "https://api.pokemontcg.io/v2"
OUTPUT_DIR = "api_data"

HEADERS = {
    "User-Agent": "PokemonCardScanner/1.0",
    "Accept": "application/json"
}
if API_KEY:
    HEADERS["X-Api-Key"] = API_KEY

os.makedirs(f"{OUTPUT_DIR}/sets", exist_ok=True)
os.makedirs(f"{OUTPUT_DIR}/prices", exist_ok=True)

# 2. Fetch All Sets Metadata (Resilient to 500 errors)
def fetch_all_sets():
    print("Fetching all set metadata...")
    manifest_path = f"{OUTPUT_DIR}/sets_manifest.json"
    sets = []
    
    max_attempts = 5
    backoff = 2.0

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(
                f"{BASE_URL}/sets?pageSize=250", 
                headers=HEADERS, 
                timeout=20
            )
            
            if response.status_code == 200:
                sets = response.json().get("data", [])
                break
            elif response.status_code in [429, 500, 502, 503, 504]:
                print(f"Set metadata fetch busy ({response.status_code}). Retrying in {backoff}s (Attempt {attempt}/{max_attempts})...")
                time.sleep(backoff)
                backoff *= 1.5
            else:
                print(f"Unexpected HTTP {response.status_code} on sets endpoint.")
                break
        except requests.exceptions.RequestException as e:
            print(f"Network error fetching sets ({type(e).__name__}). Retrying in {backoff}s...")
            time.sleep(backoff)
            backoff *= 1.5

    # If the API gave us valid sets, write/update the local manifest
    if sets:
        manifest = [
            {
                "id": s["id"],
                "name": s["name"],
                "printedTotal": s["printedTotal"],
                "total": s["total"],
                "releaseDate": s.get("releaseDate", "")
            }
            for s in sets
        ]
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        
        print(f"Successfully saved {len(manifest)} sets to manifest.")
        return [s["id"] for s in sets]

    # Fallback: API failed, try loading the existing manifest already in the repo
    print("Warning: Failed to fetch fresh sets metadata. Attempting fallback to existing manifest...")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                cached_manifest = json.load(f)
            cached_ids = [s["id"] for s in cached_manifest if "id" in s]
            print(f"Loaded {len(cached_ids)} set IDs from existing local manifest.")
            return cached_ids
        except Exception as e:
            print(f"Failed to parse cached manifest: {e}")

    # Ultimate fallback if no cache exists yet
    print("No local manifest available. Falling back to default active sets.")
    return ["sv1", "sv2", "sv3", "sv3pt5", "sv4", "sv4pt5", "sv5", "sv6", "sv6pt5", "sv7", "sv8", "sv8pt5"]

# 3. Fetch Cards & Prices for a Specific Set
def fetch_set_data(set_id):
    price_file = f"{OUTPUT_DIR}/prices/{set_id}_prices.json"
    
    # Check if the file exists and was modified today
    if os.path.exists(price_file) and os.path.getsize(price_file) > 0:
        modified_timestamp = os.path.getmtime(price_file)
        modified_date = datetime.fromtimestamp(modified_timestamp).date()
        
        if modified_date == date.today():
            print(f"Skipping {set_id}: Already updated today.")
            return

    print(f"Processing set: {set_id}...")
    cards = []
    page = 1
    page_size = 100  # <--- Changed from 250 to 100 to prevent API 500 timeouts

    session = requests.Session()
    session.headers.update(HEADERS)

    while True:
        url = f"{BASE_URL}/cards?q=set.id:{set_id}&page={page}&pageSize={page_size}"
        
        response = None
        max_attempts = 25
        backoff = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                # Set a strict 15-second timeout so it never hangs indefinitely
                res = session.get(url, timeout=15)
                
                if res.status_code == 200:
                    print(f"[{set_id}] Page {page} loaded.")
                    response = res
                    break
                elif res.status_code in [429, 500, 502, 503, 504]:
                    print(f"[{set_id}] Server busy ({res.status_code}) on page {page}. Retrying in {backoff}s (Attempt {attempt}/{max_attempts})...")
                    time.sleep(backoff)
                    backoff += 1.5  # Linear backoff
                else:
                    print(f"[{set_id}] Permanent HTTP Error {res.status_code} on page {page}")
                    break
            except requests.exceptions.RequestException as e:
                print(f"[{set_id}] Network hiccup ({type(e).__name__}) on page {page}. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff += 1.5

        if not response or response.status_code != 200:
            print(f"Aborting {set_id} at page {page}: Failed after {max_attempts} attempts.")
            break

        data = response.json().get("data", [])
        if not data:
            break

        cards.extend(data)
        page += 1
        time.sleep(0.6)  # Healthy baseline pause between successful pages

    cleaned_cards = []
    price_map = {}

    for card in cards:
        card_num = card.get("number", "")
        
        images = card.get("images", {})
        small_img = images.get("small", "")

        tcg = card.get("tcgplayer", {}).get("prices", {})
        market_price = None
        for variant in ["holofoil", "normal", "reverseHolofoil", "1stEditionHolofoil"]:
            if variant in tcg and tcg[variant].get("market") is not None:
                market_price = tcg[variant]["market"]
                break

        cleaned_cards.append({
            "id": card.get("id"),
            "name": card.get("name"),
            "number": card_num,
            "images": {"small": small_img}
        })

        if market_price is not None:
            price_map[card_num] = market_price

    # Write output files
    with open(f"{OUTPUT_DIR}/sets/{set_id}.json", "w", encoding="utf-8") as f:
        json.dump(cleaned_cards, f, separators=(",", ":"))

    with open(f"{OUTPUT_DIR}/prices/{set_id}_prices.json", "w", encoding="utf-8") as f:
        json.dump(price_map, f, separators=(",", ":"))

    print(f"Saved {len(cleaned_cards)} cards and {len(price_map)} prices for {set_id}.")

# 4. Main Execution
if __name__ == "__main__":
    # 1. Fetch all set IDs and save the manifest
    all_set_ids = fetch_all_sets()
    print(f"Discovered {len(all_set_ids)} total sets to download.")

    # 2. Loop through EVERY set returned by the API
    for s_id in all_set_ids:
        fetch_set_data(s_id)
        time.sleep(1)  # Courtesy delay between sets