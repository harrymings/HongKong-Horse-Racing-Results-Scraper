import time
import json
import pathlib
import hashlib
import os
import argparse
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional
import httpx
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from dotenv import load_dotenv
from entity_resolution import map_entity

# Load .env if present
load_dotenv()


WORKDIR = pathlib.Path(__file__).parent
DATA_DIR = WORKDIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CHECKPOINT = WORKDIR / "checkpoint.json"
DEFAULT_BASE_URL = "https://racing.hkjc.com/en-us/local/information/localresults"


class RaceNotPublished(Exception):
    """Raised when a race page exists but its results are not published yet."""


def _load_checkpoint():
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {"last_race_id": None}


def _save_checkpoint(state: Dict):
    CHECKPOINT.write_text(json.dumps(state))


def deterministic_runner_id(race_id: str, horse_name: str) -> str:
    return hashlib.sha1(f"{race_id}|{horse_name}".encode()).hexdigest()


def _query_value(query: Dict[str, List[str]], name: str) -> Optional[str]:
    return next((values[0] for key, values in query.items() if key.lower() == name.lower() and values), None)


def _race_identity(race_url: str):
    parsed = urlparse(race_url)
    query = parse_qs(parsed.query)
    race_date = _query_value(query, "racedate")
    race_number = _query_value(query, "raceno")
    course = _query_value(query, "racecourse")
    meeting_date = None
    if race_date:
        for date_format in ("%Y/%m/%d", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                meeting_date = datetime.strptime(race_date, date_format).date().isoformat()
                break
            except ValueError:
                continue
    identity = "|".join((meeting_date or race_date or "", course or "", race_number or ""))
    race_id = hashlib.sha1(identity.encode()).hexdigest() if race_number and identity.strip("|") else hashlib.sha1(race_url.encode()).hexdigest()
    return race_id, meeting_date, course, int(race_number) if race_number and race_number.isdigit() else None


def _as_float(value: str):
    try:
        return float(value.replace(",", "").strip())
    except (AttributeError, ValueError):
        return None


def _margin_in_lengths(value: str):
    text = value.strip().lower()
    if not text:
        return None
    if text == "-":
        return 0.0
    named_margins = {"n": 0.05, "nose": 0.05, "short head": 0.1, "sh": 0.1, "head": 0.2, "hd": 0.2, "neck": 0.25}
    if text in named_margins:
        return named_margins[text]
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:-(\d+)/(\d+))?", text)
    if not match:
        return None
    result = float(match.group(1))
    if match.group(2) and int(match.group(3)):
        result += int(match.group(2)) / int(match.group(3))
    return result


def _finish_time_seconds(value: str):
    match = re.fullmatch(r"(?:(\d+):)?(\d+)(?:\.(\d+))?", value.strip())
    if not match:
        return None
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2))
    fraction = float(f"0.{match.group(3)}") if match.group(3) else 0.0
    return minutes * 60 + seconds + fraction


def _result_rows(soup: BeautifulSoup):
    for row in soup.select("table tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th, td")]
        if len(cells) >= 12 and cells[1].isdigit() and cells[2]:
            yield row, cells


def _course_details(page_text: str):
    match = re.search(r"Course\s*:\s*(.+?)\s+(?:HK\$|Time\s*:)", page_text, re.IGNORECASE)
    description = match.group(1).strip() if match else None
    if not description:
        return None, None, None
    if "ALL WEATHER" in description.upper():
        return description, "All Weather Track", None
    rail_match = re.search(r"[\"']?([A-Z](?:\+\d+)?)[\"']?\s+COURSE\b", description, re.IGNORECASE)
    return description, "Turf" if "TURF" in description.upper() or rail_match else None, rail_match.group(1).upper() if rail_match else None


def _parse_dividends(soup: BeautifulSoup):
    for table in soup.select("table"):
        headers = " ".join(cell.get_text(" ", strip=True) for cell in table.select("thead td, thead th"))
        if not all(label in headers for label in ("Pool", "Winning Combination", "Dividend")):
            continue
        dividends = []
        current_pool = None
        for row in table.select("tbody tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) == 3:
                current_pool = cells[0].get_text(" ", strip=True)
                combination_cell, amount_cell = cells[1], cells[2]
            elif len(cells) == 2 and current_pool:
                combination_cell, amount_cell = cells
            else:
                continue
            dividends.append({
                "pool": current_pool,
                "winning_combination": combination_cell.get_text(" ", strip=True),
                "dividend_hkd": _as_float(amount_cell.get_text(" ", strip=True)),
            })
        return dividends
    return []


def _parse_sectionals(soup: BeautifulSoup):
    for table in soup.select("table.race_table"):
        header_text = " ".join(table.select_one("thead").stripped_strings) if table.select_one("thead") else ""
        if "Running Position & Margin Behind Leader" not in header_text:
            continue
        section_names = [cell.get_text(" ", strip=True) for cell in table.select("thead tr:last-child td, thead tr:last-child th")]
        section_names = [name for name in section_names if name]
        by_horse_number = {}
        for row in table.select("tbody tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) < 5:
                continue
            horse_number_text = cells[1].get_text(" ", strip=True)
            if not horse_number_text.isdigit():
                continue
            sectionals = []
            for section_number, cell in enumerate(cells[3:-1], start=1):
                position_block = cell.select_one("p.f_clear")
                position_text = position_block.select_one("span").get_text(" ", strip=True) if position_block and position_block.select_one("span") else None
                margin_text = position_block.select_one("i").get_text(" ", strip=True) if position_block and position_block.select_one("i") else None
                paragraphs = cell.find_all("p")
                time_block = cell.select_one("p.sectional_200") or (paragraphs[-1] if paragraphs else None)
                time_parts = list(time_block.stripped_strings) if time_block else []
                if not position_block and not time_parts:
                    continue
                section_time_text = time_parts[0] if time_parts else None
                split_texts = time_parts[1:] if time_block and "sectional_200" in (time_block.get("class") or []) else []
                sectionals.append({
                    "section_number": section_number,
                    "section_label": section_names[section_number - 1] if section_number <= len(section_names) else f"Section {section_number}",
                    "running_position": int(position_text) if position_text and position_text.isdigit() else None,
                    "margin_behind_leader": margin_text,
                    "margin_behind_leader_lengths": _margin_in_lengths(margin_text) if margin_text else None,
                    "section_time_text": section_time_text,
                    "section_time_seconds": _finish_time_seconds(section_time_text) if section_time_text else None,
                    "split_200m_texts": split_texts,
                    "split_200m_seconds": [_finish_time_seconds(value) for value in split_texts],
                })
            by_horse_number[int(horse_number_text)] = sectionals
        return by_horse_number
    return {}


def _sectional_page_url(race_url: str, soup: BeautifulSoup, meeting_date: str, race_number: int):
    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if "displaysectionaltime" in href.lower():
            return urljoin(race_url, href)
    parsed = urlparse(race_url)
    path = "/en-us/local/information/displaysectionaltime"
    query = urlencode({
        "racedate": datetime.strptime(meeting_date, "%Y-%m-%d").strftime("%d/%m/%Y"),
        "RaceNo": race_number,
    })
    return urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))


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
        races = {}
        for a in soup.select("a[href]"):
            href = a.attrs.get("href")
            if not href:
                continue
            full = urljoin(self.base_url, href)
            parsed = urlparse(full)
            query = parse_qs(parsed.query)
            course = (_query_value(query, "racecourse") or "").upper()
            race_number = _query_value(query, "raceno")
            race_date = _query_value(query, "racedate")
            if "localresults" in parsed.path.lower() and course in {"ST", "HV"} and race_number and race_number.isdigit() and race_date:
                races[(race_date, course, int(race_number))] = full
        parsed_base = urlparse(self.base_url)
        base_query = parse_qs(parsed_base.query)
        base_course = (_query_value(base_query, "racecourse") or "").upper()
        base_number = _query_value(base_query, "raceno")
        base_date = _query_value(base_query, "racedate")
        if "localresults" in parsed_base.path.lower() and base_course in {"ST", "HV"} and base_number and base_number.isdigit() and base_date:
            races[(base_date, base_course, int(base_number))] = self.base_url

        if not races:
            return []
        max_by_meeting = {}
        for race_date, course, race_number in races:
            key = (race_date, course)
            max_by_meeting[key] = max(race_number, max_by_meeting.get(key, 0))
        for (race_date, course), max_race in max_by_meeting.items():
            for race_number in range(1, max_race + 1):
                key = (race_date, course, race_number)
                if key not in races:
                    query = urlencode({"racedate": race_date, "Racecourse": course, "RaceNo": race_number})
                    races[key] = urlunparse((parsed_base.scheme or "https", parsed_base.netloc or "racing.hkjc.com", parsed_base.path, "", query, ""))
        return [races[key] for key in sorted(races, key=lambda race: (race[0], race[1], race[2]))]

    def parse_and_save(self, race_url: str):
        html = self._get(race_url)
        soup = BeautifulSoup(html, "lxml")
        race_id, meeting_date, course, race_number = _race_identity(race_url)
        page_text = soup.get_text(" ", strip=True)
        if not meeting_date:
            date_match = re.search(r"Race Meeting:\s*(\d{2}/\d{2}/\d{4})", page_text)
            if date_match:
                meeting_date = datetime.strptime(date_match.group(1), "%d/%m/%Y").date().isoformat()
        if not meeting_date:
            raise ValueError(f"Could not determine meeting date from {race_url}")

        course_description, track_type, rail_position = _course_details(page_text)

        venue_names = {"ST": "Sha Tin", "HV": "Happy Valley"}
        venue = venue_names.get((course or "").upper())
        if not venue:
            meeting_match = re.search(r"Race Meeting:\s*\d{2}/\d{2}/\d{4}\s+(.+?)(?:\s+(?:Information|Race|S\d+))", page_text)
            venue = meeting_match.group(1).strip() if meeting_match else None

        class_match = re.search(r"Class\s+[\w]+\s*[-–]\s*\d{3,4}M", page_text, re.IGNORECASE)
        distance_match = re.search(r"Class\s+[\w]+\s*[-–]\s*(\d{3,4})M", page_text, re.IGNORECASE)
        going_label = re.search(r"Going\s*:\s*", page_text, re.IGNORECASE)
        course_label = re.search(r"\s+Course\s*:", page_text, re.IGNORECASE)
        metadata_block = None
        if going_label and course_label and going_label.end() < course_label.start():
            metadata_block = page_text[going_label.end():course_label.start()].strip()
        if metadata_block:
            going_options = ("GOOD TO FIRM", "GOOD TO YIELDING", "WET FAST", "GOOD", "YIELDING", "SOFT", "SLOW", "FAST", "FIRM")
            going = None
            race_name = metadata_block
            for going_option in going_options:
                if metadata_block.upper().startswith(going_option + " "):
                    going = going_option
                    race_name = metadata_block[len(going_option):].strip()
                    break
        else:
            going_match = re.search(r"Going\s*:\s*([A-Z][A-Z ]+?)(?:\s+Course\s*:|\s+Time\s*:|$)", page_text, re.IGNORECASE)
            going = going_match.group(1).strip() if going_match else None
            race_name_match = re.search(r"([A-Z][A-Z0-9 '’.-]+(?:HANDICAP|CUP|STAKES))\s+Course\s*:", page_text)
            race_name = race_name_match.group(1).strip() if race_name_match else None

        sectional_source_url = _sectional_page_url(race_url, soup, meeting_date, race_number)
        sectional_error = None
        try:
            sectional_soup = BeautifulSoup(self._get(sectional_source_url), "lxml")
            sectionals_by_horse = _parse_sectionals(sectional_soup)
        except Exception as exc:
            sectionals_by_horse = {}
            sectional_error = str(exc)

        runners = []
        for row, cells in _result_rows(soup):
            horse_name = re.sub(r"\s*\([A-Z]\d+\)$", "", cells[2]).strip()
            jockey_name = cells[3]
            trainer_name = cells[4]
            horse_no = int(cells[1])
            runner_id = hashlib.sha1(f"{race_id}|{horse_no}".encode()).hexdigest()
            horse_map = map_entity("horse", horse_name)
            jockey_map = map_entity("jockey", jockey_name)
            trainer_map = map_entity("trainer", trainer_name)
            horse_link = row.select_one("td:nth-of-type(3) a")
            jockey_link = row.select_one("td:nth-of-type(4) a")
            trainer_link = row.select_one("td:nth-of-type(5) a")
            runners.append({
                "runner_id": runner_id,
                "race_id": race_id,
                "horse_no": horse_no,
                "horse_name": horse_name,
                "horse_id": horse_map["canonical_id"],
                "jockey_name": jockey_name,
                "jockey_id": jockey_map["canonical_id"],
                "trainer_name": trainer_name,
                "trainer_id": trainer_map["canonical_id"],
                "horse_url": urljoin(race_url, horse_link.get("href")) if horse_link else None,
                "jockey_url": urljoin(race_url, jockey_link.get("href")) if jockey_link else None,
                "trainer_url": urljoin(race_url, trainer_link.get("href")) if trainer_link else None,
                "finishing_pos": int(cells[0]) if cells[0].isdigit() else None,
                "finishing_pos_text": cells[0],
                "actual_weight": _as_float(cells[5]),
                "declared_horse_weight": _as_float(cells[6]),
                "draw": int(cells[7]) if cells[7].isdigit() else None,
                "margin": _margin_in_lengths(cells[8]),
                "margin_text": cells[8],
                "running_position": cells[9],
                "finishing_time": _finish_time_seconds(cells[10]),
                "finishing_time_text": cells[10],
                "win_odds": _as_float(cells[11]),
                "final_win_odds": _as_float(cells[11]),
                "initial_win_odds": None,
                "gear": None,
                "jockey_allowance": None,
                "sectionals": sectionals_by_horse.get(horse_no, []),
            })

        if not runners:
            raise RaceNotPublished(f"No result rows found at {race_url}; the race may not have been run yet")

        out = {
            "race_id": race_id,
            "meeting_date": meeting_date,
            "venue": venue,
            "race_number": race_number,
            "race_name": race_name,
            "distance_m": int(distance_match.group(1)) if distance_match else None,
            "grade": class_match.group(0).split("-")[0].strip() if class_match else None,
            "going": going,
            "course_description": course_description,
            "track_type": track_type,
            "rail_position": rail_position,
            "dividends": _parse_dividends(soup),
            "sectional_source_url": sectional_source_url,
            "sectional_error": sectional_error,
            "source_url": race_url,
            "runners": runners,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        fname = DATA_DIR / f"race_{race_id}.json"
        fname.write_text(json.dumps(out, indent=2))
        return out

    def run_incremental(self):
        checkpoint = _load_checkpoint()
        urls = self.list_recent_race_urls()
        new_count = 0
        pending = 0
        errors = []
        for url in urls:
            try:
                race_id, _, _, _ = _race_identity(url)
                output_path = DATA_DIR / f"race_{race_id}.json"
                is_new = not output_path.exists()
                self.parse_and_save(url)
                if is_new:
                    new_count += 1
                checkpoint["last_race_id"] = race_id
                time.sleep(self.rate_limit)
            except RaceNotPublished:
                pending += 1
                time.sleep(self.rate_limit)
            except Exception as e:
                print("scrape error", e)
                errors.append(e)
        # update checkpoint (simple: timestamp)
        checkpoint["last_run_at"] = datetime.now(timezone.utc).isoformat()
        _save_checkpoint(checkpoint)
        if errors:
            raise RuntimeError(f"{len(errors)} of {len(urls)} race pages failed; first error: {errors[0]}")
        if pending:
            print(f"Skipped {pending} of {len(urls)} races with no published results yet")
        return new_count


def _main():
    parser = argparse.ArgumentParser(description="Incremental scraper runner")
    parser.add_argument("--base-url", dest="base_url", help="Base listing URL to scrape (overrides SCRAPER_BASE_URL env var)")
    parser.add_argument("--date", dest="date", help="Meeting date to scrape, e.g. 2026-09-27 (defaults to the site's current meeting)")
    args = parser.parse_args()
    base_url = args.base_url or os.getenv("SCRAPER_BASE_URL") or DEFAULT_BASE_URL
    if args.date:
        try:
            meeting_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid --date value {args.date!r}; expected YYYY-MM-DD")
            return 2
        parsed = urlparse(base_url)
        base_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode({"racedate": meeting_date.strftime("%Y/%m/%d")}), ""))
    s = IncrementalScraper(base_url)
    try:
        found = s.run_incremental()
        print("Found", found, "new races")
        return 0
    except Exception as e:
        print("Scraper failed:", e)
        return 1
    finally:
        s.client.close()


if __name__ == "__main__":
    raise SystemExit(_main())
