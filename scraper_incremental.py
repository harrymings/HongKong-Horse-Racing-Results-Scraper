import time
import json
import pathlib
import hashlib
import os
import argparse
from typing import Dict, List
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dotenv import load_dotenv
from entity_resolution import map_entity

# Load .env if present
load_dotenv()


WORKDIR = pathlib.Path(__file__).parent
DATA_DIR = WORKDIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CHECKPOINT = WORKDIR / "checkpoint.json"


def _load_checkpoint():
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {"last_race_id": None}


def _save_checkpoint(state: Dict):
    CHECKPOINT.write_text(json.dumps(state))


def deterministic_runner_id(race_id: str, horse_name: str) -> str:
    return hashlib.sha1(f"{race_id}|{horse_name}".encode()).hexdigest()


class IncrementalScraper:
    def __init__(self, base_url: str, rate_limit: float = 1.0, max_retries: int = 3):
        if not base_url:
            raise ValueError("base_url must be provided (set SCRAPER_BASE_URL in .env or pass --base-url)")
        self.base_url = base_url
        self.rate_limit = rate_limit
        self.max_retries = max_retries
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        self.client = httpx.Client(headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}, timeout=30.0, follow_redirects=True)

    def _get(self, url: str):
        last_exc = None
        for attempt in range(self.max_retries):
            try:
                r = self.client.get(url)
                r.raise_for_status()
                return r.text
            except Exception as e:
                last_exc = e
                time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"Failed to fetch {url}: {last_exc}")

    def list_recent_race_urls(self) -> List[str]:
        html = self._get(self.base_url)
        soup = BeautifulSoup(html, "lxml")
        links = []
        for a in soup.select("a[href]"):
            href = a.attrs.get("href")
            if href and ("race" in href or "racereport" in href or "racecard" in href):
                full = urljoin(self.base_url, href)
                links.append(full)
        return links

    def parse_and_save(self, race_url: str):
        html = self._get(race_url)
        soup = BeautifulSoup(html, "lxml")
        # Very lightweight parser; users should extend selectors
        race_id = hashlib.sha1(race_url.encode()).hexdigest()
        runners = []
        for row in soup.select(".runner-row"):
            name = row.select_one(".horse-name").get_text(strip=True) if row.select_one(".horse-name") else ""
            jockey = row.select_one(".jockey").get_text(strip=True) if row.select_one(".jockey") else ""
            trainer = row.select_one(".trainer").get_text(strip=True) if row.select_one(".trainer") else ""
            runner_id = deterministic_runner_id(race_id, name)
            # resolve entities
            horse_map = map_entity("horse", name)
            jockey_map = map_entity("jockey", jockey)
            trainer_map = map_entity("trainer", trainer)
            runners.append({
                "runner_id": runner_id,
                "race_id": race_id,
                "horse_name": name,
                "horse_id": horse_map["canonical_id"],
                "jockey_id": jockey_map["canonical_id"],
                "trainer_id": trainer_map["canonical_id"],
            })

        out = {"race_id": race_id, "source_url": race_url, "runners": runners, "scraped_at": __import__("datetime").datetime.utcnow().isoformat()}
        fname = DATA_DIR / f"race_{race_id}.json"
        fname.write_text(json.dumps(out, indent=2))
        return out

    def run_incremental(self):
        checkpoint = _load_checkpoint()
        urls = self.list_recent_race_urls()
        new_count = 0
        for url in urls:
            try:
                self.parse_and_save(url)
                new_count += 1
                time.sleep(self.rate_limit)
            except Exception as e:
                print("scrape error", e)
        # update checkpoint (simple: timestamp)
        checkpoint["last_run_at"] = __import__("datetime").datetime.utcnow().isoformat()
        _save_checkpoint(checkpoint)
        return new_count


def _main():
    parser = argparse.ArgumentParser(description="Incremental scraper runner")
    parser.add_argument("--base-url", dest="base_url", help="Base listing URL to scrape (overrides SCRAPER_BASE_URL env var)")
    args = parser.parse_args()
    base_url = args.base_url or os.getenv("SCRAPER_BASE_URL")
    if not base_url:
        print("ERROR: SCRAPER_BASE_URL not set. Please set it in .env or pass --base-url.")
        return 2
    s = IncrementalScraper(base_url)
    try:
        found = s.run_incremental()
        print("Found", found, "new races")
        return 0
    except Exception as e:
        print("Scraper failed:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
