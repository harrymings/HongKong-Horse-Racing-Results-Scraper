from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
import os
from datetime import datetime, timedelta, date
import pandas as pd
import time
from selenium import webdriver
import threading
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

app = Flask(__name__)
app.secret_key = "dev-secret"

BASE_URL_TEMPLATE = "https://racing.hkjc.com/racing/information/English/racing/LocalResults.aspx?RaceDate={date}"
# Save CSV outputs into a dedicated folder
OUTPUT_DIR = os.path.join(os.path.abspath('.'), 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Headless Chrome options
def make_driver():
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)


# In-memory job status
job = {
    'running': False,
    'total': 0,
    'done': 0,
    'current': '',
    'log': [],
    'output_file': None,
}


def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)


def get_safe_text(element, by, value, default="N/A"):
    try:
        return element.find_element(by, value).text.strip()
    except NoSuchElementException:
        return default


def extract_horse_jockey_trainer_info(cell_element):
    name, link = "N/A", "N/A"
    try:
        link_element = cell_element.find_element(By.TAG_NAME, "a")
        name = link_element.text.strip()
        link = link_element.get_attribute("href")
    except NoSuchElementException:
        name = cell_element.text.strip()
    return name, link


def scrape_date(driver, meet_date_str):
    meet_data_for_csv = []
    initial_url = BASE_URL_TEMPLATE.format(date=meet_date_str)
    driver.get(initial_url)

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((
                By.XPATH, "//div[contains(@class, 'top_races')]//table | //div[contains(text(), 'No race meeting.') ]"
            ))
        )
    except TimeoutException:
        return meet_data_for_csv

    try:
        driver.find_element(By.XPATH, "//div[contains(text(), 'No race meeting.')]")
        return meet_data_for_csv
    except NoSuchElementException:
        pass

    race_links_elements = driver.find_elements(By.XPATH, "//div[contains(@class, 'top_races')]//table//td/a[contains(@href, 'LocalResults.aspx')]")
    race_page_urls = {initial_url}
    for elem in race_links_elements:
        href = elem.get_attribute("href")
        if href and "LocalResults.aspx" in href and "ResultsAll.aspx" not in href:
            race_page_urls.add(href)

    sorted_race_urls = sorted(
        list(race_page_urls),
        key=lambda url: int(url.split("RaceNo=")[-1]) if "RaceNo=" in url and url.split("RaceNo=")[-1].isdigit() else 0
    )

    for race_url in sorted_race_urls:
        try:
            driver.get(race_url)
            performance_table_locator = (By.XPATH, "//div[@class='performance']/table[contains(@class, 'draggable')]")
            WebDriverWait(driver, 20).until(EC.presence_of_element_located(performance_table_locator))
        except TimeoutException:
            continue

        race_header_full, race_details_text, race_specific_name, race_going, race_course = ("N/A",) * 5
        try:
            race_info_element = driver.find_element(By.XPATH, "//div[contains(@class, 'race_tab')]/table")
            race_header_full = get_safe_text(race_info_element, By.XPATH, ".//thead/tr/td[1]")
            race_details_text = get_safe_text(race_info_element, By.XPATH, ".//tbody/tr[2]/td[1]")
            race_specific_name = get_safe_text(race_info_element, By.XPATH, ".//tbody/tr[3]/td[1]")
            race_going = get_safe_text(race_info_element, By.XPATH, ".//tbody/tr[2]/td[3]")
            race_course = get_safe_text(race_info_element, By.XPATH, ".//tbody/tr[3]/td[3]")
        except NoSuchElementException:
            pass

        try:
            performance_table = driver.find_element(*performance_table_locator)
            body_rows = performance_table.find_elements(By.XPATH, "./tbody/tr")
            for row in body_rows:
                cols = row.find_elements(By.TAG_NAME, "td")
                if len(cols) < 12:
                    continue
                placing = cols[0].text.strip()
                horse_name, horse_link = extract_horse_jockey_trainer_info(cols[2])
                horse_data = {
                    "MeetDate": meet_date_str,
                    "RaceURL": race_url,
                    "RaceHeader": race_header_full,
                    "RaceDetails": race_details_text,
                    "RaceSpecificName": race_specific_name,
                    "RaceGoing": race_going,
                    "RaceCourse": race_course,
                    "Placing": placing,
                    "HorseNo": cols[1].text.strip(),
                    "HorseName": horse_name,
                    "HorseLink": horse_link,
                    "JockeyName": extract_horse_jockey_trainer_info(cols[3])[0],
                    "JockeyLink": extract_horse_jockey_trainer_info(cols[3])[1],
                    "TrainerName": extract_horse_jockey_trainer_info(cols[4])[0],
                    "TrainerLink": extract_horse_jockey_trainer_info(cols[4])[1],
                    "ActualWt": cols[5].text.strip(),
                    "DeclarHorseWt": cols[6].text.strip(),
                    "Draw": cols[7].text.strip(),
                    "LBW": cols[8].text.strip(),
                    "RunningPosition": " ".join([rp.text.strip() for rp in cols[9].find_elements(By.XPATH, ".//div/div") if rp.text.strip()]),
                    "FinishTime": cols[10].text.strip(),
                    "WinOdds": cols[11].text.strip(),
                }
                meet_data_for_csv.append(horse_data)
        except Exception:
            pass

    return meet_data_for_csv


def run_scrape_job(start_date, end_date, output_mode='single'):
    job['running'] = True
    job['log'].append(f"Job started: {start_date} to {end_date}")
    dates = [d for d in daterange(start_date, end_date)]
    job['total'] = len(dates)
    job['done'] = 0
    job['output_file'] = None
    all_results = []
    driver = None
    try:
        driver = make_driver()
        for d in dates:
            if not job['running']:
                job['log'].append('Job cancelled')
                break
            meet_date = d.strftime('%d/%m/%Y')
            job['current'] = meet_date
            job['log'].append(f"Scraping {meet_date}")
            results = scrape_date(driver, meet_date)
            if results:
                df = pd.DataFrame(results)
                filename = f"races_{d.strftime('%Y-%m-%d')}.csv"
                df.to_csv(os.path.join(OUTPUT_DIR, filename), index=False, encoding='utf-8-sig')
                all_results.extend(results)
                job['log'].append(f"Saved {filename} ({len(results)} rows)")
            else:
                job['log'].append(f"No data for {meet_date}")
            job['done'] += 1
            time.sleep(0.1)

        if all_results:
            if output_mode == 'single':
                df_all = pd.DataFrame(all_results)
                all_name = f"races_{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}.csv"
                df_all.to_csv(os.path.join(OUTPUT_DIR, all_name), index=False, encoding='utf-8-sig')
                job['output_file'] = all_name
                job['log'].append(f"Combined CSV: {all_name}")
            else:
                # separate files were saved per day; report number of files
                job['output_file'] = None
                job['log'].append(f"Saved separate files for {start_date} to {end_date}")
    except Exception as e:
        job['log'].append(f"Error: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        job['running'] = False
        job['current'] = ''
        job['log'].append('Job finished')


@app.route('/')
def index():
    # defaults: past 30 days
    today = date.today()
    default_start = today - timedelta(days=30)
    csv_files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith('.csv')]
    csv_files.sort(reverse=True)
    return render_template('index.html', default_start=default_start, default_end=today, csv_files=csv_files)


@app.route('/run', methods=['POST'])
def run_scrape():
    start_str = request.form.get('start_date')
    end_str = request.form.get('end_date')
    if not start_str or not end_str:
        flash('Start and end date are required')
        return redirect(url_for('index'))

    start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
    end_date = datetime.strptime(end_str, '%Y-%m-%d').date()

    if job['running']:
        flash('A job is already running')
        return redirect(url_for('index'))

    output_mode = request.form.get('output_mode', 'single')
    t = threading.Thread(target=run_scrape_job, args=(start_date, end_date, output_mode), daemon=True)
    t.start()
    flash('Scraping started in background')
    return redirect(url_for('index'))


@app.route('/status')
def status():
    return jsonify({
        'running': job['running'],
        'total': job['total'],
        'done': job['done'],
        'current': job['current'],
        'log': job['log'][-30:],
        'output_file': job['output_file'],
    })


@app.route('/stop', methods=['POST'])
def stop():
    if job['running']:
        job['running'] = False
        job['log'].append('Stop requested by user')
        flash('Stop requested')
    else:
        flash('No job is running')
    return redirect(url_for('index'))


@app.route('/merge', methods=['POST'])
def merge_existing():
    start_str = request.form.get('merge_start')
    end_str = request.form.get('merge_end')
    if not start_str or not end_str:
        flash('Start and end date are required for merge')
        return redirect(url_for('index'))
    start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
    end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
    files = []
    for d in daterange(start_date, end_date):
        fname = os.path.join(OUTPUT_DIR, f"races_{d.strftime('%Y-%m-%d')}.csv")
        if os.path.exists(fname):
            files.append(fname)
    if not files:
        flash('No CSV files found for that date range')
        return redirect(url_for('index'))
    dfs = [pd.read_csv(f) for f in files]
    df_all = pd.concat(dfs, ignore_index=True)
    outname = f"merged_{start_date.strftime('%Y-%m-%d')}_to_{end_date.strftime('%Y-%m-%d')}.csv"
    df_all.to_csv(os.path.join(OUTPUT_DIR, outname), index=False, encoding='utf-8-sig')
    flash(f'Merged {len(files)} files into {outname}')
    return redirect(url_for('index'))


@app.route('/preview')
def preview():
    fname = request.args.get('file')
    if not fname or not os.path.exists(fname):
        flash('File not found')
        return redirect(url_for('index'))
    df = pd.read_csv(fname)
    preview_html = df.head(50).to_html(index=False)
    return render_template('preview.html', table=preview_html, filename=fname)


@app.route('/download/<path:filename>')
def download(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == '__main__':
    app.run(port=8501, debug=True)
