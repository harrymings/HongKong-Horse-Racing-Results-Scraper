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
from persistence import atomic_json, save_race
from fractions import Fraction

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
    atomic_json(CHECKPOINT, state)


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


def _margin_in_lengths(value: str, finishing_pos=None):
    text = (value or "").strip().lower().replace("−", "-").replace("–", "-")
    if text in {"", "-", "---"}:
        return 0.0 if finishing_pos == 1 and text else None
    names = {"n": .05, "nose": .05, "sh": .1, "short head": .1,
             "hd": .2, "head": .2, "nk": .25, "neck": .25}
    if text in names:
        return names[text]
    text = re.sub(r"\s*/\s*", "/", text)
    try:
        mixed = re.fullmatch(r"(\d+)\s*[- ]\s*(\d+/\d+)", text)
        if mixed:
            return float(int(mixed[1]) + Fraction(mixed[2]))
        if re.fullmatch(r"\d+/\d+|\d+(?:\.\d+)?", text):
            return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        pass
    return None


def _finish_time_seconds(value: str):
    match = re.fullmatch(r"(?:(\d+):)?(\d+)(?:\.(\d+))?", value.strip())
    if not match:
        return None
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2))
    if match.group(1) and seconds >= 60:
        return None
    fraction = float(f"0.{match.group(3)}") if match.group(3) else 0.0
    return minutes * 60 + seconds + fraction


class RaceParseError(ValueError):
    pass


RESULT_HEADERS = [
    {"pla", "placing"}, {"horseno", "horsenumber"}, {"horse"}, {"jockey"}, {"trainer"},
    {"actwt", "actualweight"}, {"declarhorsewt", "declaredhorseweight", "ondatehorseweight"},
    {"dr", "draw"}, {"lbw", "lengthbehindwinner"}, {"runningposition"}, {"finishtime"}, {"winodds"},
]


def _visible_cells(row):
    return [c for c in row.find_all(["th", "td"], recursive=False)
            if not c.has_attr("hidden") and c.get("aria-hidden") != "true"
            and not re.search(r"display\s*:\s*none", c.get("style", ""), re.I)]


def _header_key(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _result_rows(soup):
    matches = []
    for table in soup.select("table"):
        for row in table.select("tr"):
            if row.find_parent("table") is not table:
                continue
            cells = _visible_cells(row)
            keys = [{_header_key(c.get_text(" ", strip=True)), _header_key(c.get("title", ""))} for c in cells]
            columns = [next((i for i, key in enumerate(keys) if key & aliases), None) for aliases in RESULT_HEADERS]
            if all(i is not None for i in columns):
                matches.append((table, row, columns))
                break
    if len(matches) != 1:
        raise RaceParseError(f"Expected one official result table, found {len(matches)}")
    table, header, columns = matches[0]
    seen = set()
    for row in table.select("tr"):
        if row is header or row.find_parent("table") is not table or row.find_parent("thead"):
            continue
        cells = _visible_cells(row)
        if not cells or not any(c.get_text(strip=True) for c in cells):
            continue
        if len(cells) <= max(columns) or any(int(c.get("rowspan", 1)) > 1 for c in cells):
            raise RaceParseError("Unsupported result row structure; refusing positional guesses")
        ordered = [cells[i] for i in columns]
        texts = [c.get_text(" ", strip=True) for c in ordered]
        if not texts[1].isdigit() or not texts[2]:
            raise RaceParseError("Missing horse number or name in result table")
        if int(texts[1]) in seen:
            raise RaceParseError(f"Duplicate horse number {texts[1]}")
        seen.add(int(texts[1]))
        yield ordered, texts
    if not seen:
        raise RaceParseError("Official result table contains no runners")


def _race_metadata(soup, race_number):
    headers = [t for t in soup.select(".race_tab table")
               if re.search(rf"\bRACE\s+{race_number}\b", t.get_text(" ", strip=True), re.I)]
    if len(headers) != 1:
        raise RaceParseError("Cannot identify the requested race metadata table")
    table = headers[0]
    rows = [r.find_all(["td", "th"], recursive=False) for r in table.select("tbody tr")]
    label = distance = name = going = course = rating = None
    for cells in rows:
        if not cells:
            continue
        text = cells[0].get_text(" ", strip=True)
        match = re.fullmatch(r"(.+?)\s*[-–]\s*(\d{3,4})\s*M(?:\s*[-–]\s*(.*))?", text, re.I)
        if match:
            label, distance, rating = match[1].strip(), int(match[2]), match[3]
        if len(cells) >= 3:
            field = cells[1].get_text(" ", strip=True).lower().strip(" :")
            if field == "going":
                going = cells[2].get_text(" ", strip=True)
            if field == "course":
                course = cells[2].get_text(" ", strip=True)
                name = text
    number = re.search(r"\bClass\s+(\d+)", label or "", re.I)
    _, track, rail = _course_details("Course : " + (course or "") + " Time :")
    return {"class_label": label, "grade": label, "class_number": int(number[1]) if number else None,
            "restricted": "restricted" in (label or "").lower(), "distance_m": distance,
            "rating_band": rating, "race_name": name, "going": going, "course_description": course,
            "track_type": track, "rail_position": rail,
            "parse_warnings": [] if label and distance else ["Missing race class/distance in race header"]}


def _horse_code(text, link=None):
    visible = re.search(r"\(([A-Z]\d{3,4})\)", text)
    linked = re.search(r"(?:HK_\d{4}_)([A-Z]\d{3,4})", link or "", re.I)
    if visible and linked and visible[1] != linked[1].upper():
        raise RaceParseError("Visible horse code and source link disagree")
    return visible[1] if visible else linked[1].upper() if linked else None


def _finish_status(text):
    if text.isdigit():
        return "FINISHED"
    return {"DNF": "DNF", "PU": "DNF", "DISQ": "DISQUALIFIED", "DQ": "DISQUALIFIED",
            "WV": "SCRATCHED", "WV-A": "SCRATCHED", "W": "SCRATCHED"}.get(text.upper(), "UNKNOWN")


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
                "dividend_text": amount_cell.get_text(" ", strip=True),
            })
        return dividends
    return []


def _parse_sectionals(soup: BeautifulSoup, race_number=None):
    if race_number is not None:
        containers = soup.select(f"#Race{int(race_number)}")
        if len(containers) != 1:
            raise RaceParseError("Sectional race container missing or ambiguous")
        soup = containers[0]
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
            number = int(horse_number_text)
            if number in by_horse_number:
                raise RaceParseError("Duplicate sectional horse number")
            code = _horse_code(cells[2].get_text(" ", strip=True), str(cells[2]))
            by_horse_number[number] = {"horse_code": code, "sections": sectionals}
        return by_horse_number
    return {}


def _sectional_page_url(race_url: str, soup: BeautifulSoup, meeting_date: str, race_number: int):
    parsed = urlparse(race_url)
    path = "/en-us/local/information/displaysectionaltime"
    query = urlencode({
        "racedate": datetime.strptime(meeting_date, "%Y-%m-%d").strftime("%d/%m/%Y"),
        "RaceNo": race_number,
    })
    return urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))


class IncrementalScraper:
    def __init__(self, base_url: str, rate_limit: float = 1.0, max_retries: int = 3, sectional_tolerance: float = 0.10):
        if not base_url:
            raise ValueError("base_url must be provided (set SCRAPER_BASE_URL in .env or pass --base-url)")
        if sectional_tolerance < 0:
            raise ValueError("sectional_tolerance must be non-negative")
        self.sectional_tolerance = sectional_tolerance
        self.base_url = base_url
        self.rate_limit = rate_limit
        self.max_retries = max_retries
        self._last_request = 0.0
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        self.client = httpx.Client(headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}, timeout=30.0, follow_redirects=True)

    def _get(self, url: str):
        last_exc = None
        for attempt in range(self.max_retries):
            try:
                delay = self.rate_limit - (time.monotonic() - self._last_request)
                if delay > 0:
                    time.sleep(delay)
                self._last_request = time.monotonic()
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
        requested_date = _race_identity(self.base_url)[1]
        return [races[key] for key in sorted(races, key=lambda race: (race[0], race[1], race[2]))
                if not requested_date or _race_identity(races[key])[1] == requested_date]

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

        venue_names = {"ST": "Sha Tin", "HV": "Happy Valley"}
        venue = venue_names.get((course or "").upper())
        if not venue:
            meeting_match = re.search(r"Race Meeting:\s*\d{2}/\d{2}/\d{4}\s+(.+?)(?:\s+(?:Information|Race|S\d+))", page_text)
            venue = meeting_match.group(1).strip() if meeting_match else None

        meeting = re.search(r"Race Meeting:\s*(\d{2}/\d{2}/\d{4})\s+(Sha Tin|Happy Valley)", page_text)
        if not meeting or datetime.strptime(meeting[1], "%d/%m/%Y").date().isoformat() != meeting_date or meeting[2] != venue:
            raise RaceParseError("Returned results meeting date/venue does not match requested race")
        metadata = _race_metadata(soup, race_number)
        sectional_status = "MISSING_AT_SOURCE"
        sectional_source_url = _sectional_page_url(race_url, soup, meeting_date, race_number)
        sectional_error = None
        try:
            sectional_html = self._get(sectional_source_url)
            sectional_soup = BeautifulSoup(sectional_html, "lxml")
            # The selected date and visible racecourse validate the returned document.
            selected = sectional_soup.select_one("select option[selected]")
            text = sectional_soup.get_text(" ", strip=True)
            source_date = selected.get("value") if selected else None
            expected_date = datetime.fromisoformat(meeting_date).strftime("%d/%m/%Y")
            if source_date != expected_date or venue not in text:
                sectional_status = "RACE_MISMATCH"
                raise RaceParseError("Returned sectional date or venue does not match result")
            sectional_status = "PARSE_FAILED"
            sectionals_by_horse = _parse_sectionals(sectional_soup, race_number)
            sectional_status = "VALID" if sectionals_by_horse else "MISSING_AT_SOURCE"
        except Exception as exc:
            sectionals_by_horse = {}
            sectional_error = str(exc)
            if sectional_status == "MISSING_AT_SOURCE":
                sectional_status = "FETCH_FAILED"

        runners = []
        for row, cells in _result_rows(soup):
            horse_name = re.sub(r"\s*\([A-Z]\d+\)$", "", cells[2]).strip()
            jockey_name = cells[3]
            trainer_name = cells[4]
            horse_no = int(cells[1])
            runner_id = hashlib.sha1(f"{race_id}|{horse_no}".encode()).hexdigest()
            horse_link = row[2].select_one("a[href]")
            horse_code = _horse_code(cells[2], horse_link.get("href") if horse_link else None)
            horse_map = map_entity("horse", horse_name, {"source_id": horse_code} if horse_code else None)
            jockey_link = row[3].select_one("a[href]")
            trainer_link = row[4].select_one("a[href]")
            def source_identity(link, key):
                return _query_value(parse_qs(urlparse(link.get("href")).query), key) if link else None
            jockey_code = source_identity(jockey_link, "jockeyid")
            trainer_code = source_identity(trainer_link, "trainerid")
            jockey_map = map_entity("jockey", jockey_name, {"source_id": jockey_code} if jockey_code else None)
            trainer_map = map_entity("trainer", trainer_name, {"source_id": trainer_code} if trainer_code else None)
            position = int(cells[0]) if cells[0].isdigit() else None
            sections = sectionals_by_horse.get(horse_no, {})
            runner_sectional_status = sectional_status
            if sections and (not horse_code or sections["horse_code"] != horse_code):
                sections = {}
                runner_sectional_status = "RACE_MISMATCH"
            segment_values = [v.get("section_time_seconds") for v in sections.get("sections", [])]
            if runner_sectional_status == "VALID":
                finish = _finish_time_seconds(cells[10])
                if not segment_values:
                    runner_sectional_status = "MISSING_AT_SOURCE"
                elif finish is None or any(v is None for v in segment_values) or abs(sum(segment_values) - finish) > self.sectional_tolerance + .000001:
                    runner_sectional_status = "TIME_MISMATCH"
            runners.append({
                "runner_id": runner_id,
                "race_id": race_id,
                "horse_no": horse_no,
                "horse_name": horse_name,
                "horse_code": horse_code,
                "legacy_horse_id": horse_map.get("legacy_id"),
                "identity_status": "SOURCE_CODE" if horse_code else "NAME_FALLBACK",
                "finish_status": _finish_status(cells[0]),
                "placing_text": cells[0],
                "odds_text": cells[11],
                "parse_warnings": [field for field, value in
                    [("finishing_time", _finish_time_seconds(cells[10])), ("win_odds", _as_float(cells[11])),
                     ("margin", _margin_in_lengths(cells[8], position))] if value is None],
                "sectional_status": runner_sectional_status,
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
                "margin": _margin_in_lengths(cells[8], position),
                "margin_text": cells[8],
                "running_position": cells[9],
                "finishing_time": _finish_time_seconds(cells[10]),
                "finishing_time_text": cells[10],
                "win_odds": _as_float(cells[11]),
                "final_win_odds": _as_float(cells[11]),
                "initial_win_odds": None,
                "gear": None,
                "jockey_allowance": None,
                "sectionals": sections.get("sections", []),
            })

        if not runners:
            raise RaceParseError(f"No result rows found at {race_url}")

        out = {
            "race_id": race_id,
            "meeting_date": meeting_date,
            "venue": venue,
            "race_number": race_number,
            "dividends": _parse_dividends(soup),
            "sectional_source_url": sectional_source_url,
            "sectional_error": sectional_error,
            "source_url": race_url,
            "runners": runners,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source_checksum": hashlib.sha256(html.encode()).hexdigest(),
        }
        if sectional_status == 'VALID' and any(r['sectional_status'] != 'VALID' for r in runners):
            sectional_status = next(r['sectional_status'] for r in runners if r['sectional_status'] != 'VALID')
        out['sectional_rounding_tolerance_seconds'] = self.sectional_tolerance
        out.update(metadata)
        out.update({"venue_code": course, "racecourse": venue, "race_status": "PUBLISHED",
                    "timezone": "Asia/Hong_Kong", "scheduled_start_at_hkt": None,
                    "actual_start_at_hkt": None, "start_time_source": None, "time_precision": None,
                    "sectional_status": sectional_status, "schema_version": 2})
        fname = DATA_DIR / f"race_{race_id}.json"
        return save_race(fname, out)

    def run_incremental(self, retry_failed=False, refresh=False):
        checkpoint = _load_checkpoint()
        urls = self.list_recent_race_urls()
        if not urls:
            raise RaceParseError("No official race links discovered; meeting coverage is unverified")
        records = checkpoint.setdefault("races", {})
        checkpoint["attempted_urls"] = []
        new_count = 0
        pending = 0
        errors = []
        for url in urls:
            try:
                race_id, _, _, _ = _race_identity(url)
                output_path = DATA_DIR / f"race_{race_id}.json"
                is_new = not output_path.exists()
                record = records.get(race_id, {})
                if retry_failed and record.get("status") == "SUCCESS" and record.get("verified"):
                    continue
                if not refresh and not retry_failed and not is_new and record.get("verified"):
                    last = datetime.fromisoformat(record["last_successful_fetch"])
                    age = (datetime.now(timezone.utc) - last).total_seconds()
                    if age < 30 * 86400:
                        continue
                checkpoint['attempted_urls'].append(url)
                result = self.parse_and_save(url)
                meeting_age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(result['meeting_date']).date()).days
                verified = meeting_age > 7 and bool(result.get('dividends')) and all(r.get('sectional_status') == 'VALID' for r in result['runners']) and not result.get('parse_warnings')
                records[race_id] = {'url': url, 'status': 'SUCCESS', 'verified': verified,
                    'last_successful_fetch': result['scraped_at'], 'content_checksum': result['source_checksum']}
                if is_new:
                    new_count += 1
                checkpoint["last_race_id"] = race_id
                time.sleep(self.rate_limit)
            except RaceNotPublished as exc:
                records[race_id] = {"url": url, "status": "NOT_PUBLISHED", "error": str(exc)}
                pending += 1
                time.sleep(self.rate_limit)
            except Exception as e:
                records[race_id] = {"url": url, "status": "FAILED", "error": str(e)}
                print(json.dumps({"url": url, "status": "FAILED", "error": str(e)}))
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
    parser.add_argument("--retry-failed", action="store_true", help="Retry incomplete/failed discovered races; skip prior successes")
    parser.add_argument("--refresh", action="store_true", help="Ignore the 30-day cache for verified older races")
    parser.add_argument("--validate", action="store_true", help="Validate the saved records after scraping")
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
        found = s.run_incremental(retry_failed=args.retry_failed, refresh=args.refresh)
        print("Found", found, "new races")
        if args.validate:
            from validate_racing_data import validate_races
            report = validate_races([json.loads(p.read_text()) for p in DATA_DIR.glob("race_*.json")])
            atomic_json(WORKDIR / "output" / "validation_report.json", report)
            return 1 if report['errors'] else 0
        return 0
    except Exception as e:
        print("Scraper failed:", e)
        return 1
    finally:
        s.client.close()


if __name__ == "__main__":
    raise SystemExit(_main())
