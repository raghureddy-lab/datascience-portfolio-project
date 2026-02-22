import json
import re
import time
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


def _strip_html(html: str) -> str:
    """Convert HTML to readable text (best-effort)."""
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _parse_json_ld(driver) -> dict:
    """
    Extract job info from JSON-LD if present.
    Returns dict with optional keys: title, company, location, description.
    """
    result = {}

    scripts = driver.find_elements(By.CSS_SELECTOR, "script[type='application/ld+json']")
    for s in scripts:
        raw = (s.get_attribute("innerHTML") or "").strip()
        if not raw:
            continue

        # Sometimes JSON-LD contains multiple objects or @graph
        try:
            data = json.loads(raw)
        except Exception:
            continue

        candidates = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                candidates = data["@graph"]
            else:
                candidates = [data]

        for obj in candidates:
            if not isinstance(obj, dict):
                continue

            # We want the JobPosting object
            t = obj.get("@type")
            if isinstance(t, list):
                is_job = "JobPosting" in t
            else:
                is_job = (t == "JobPosting")

            if not is_job:
                continue

            # Title
            title = obj.get("title") or obj.get("name")
            if title:
                result["title"] = title

            # Company
            org = obj.get("hiringOrganization") or {}
            if isinstance(org, dict):
                company = org.get("name")
                if company:
                    result["company"] = company

            # Location
            # jobLocation can be dict or list of dicts. We’ll build "City, State" if possible.
            loc = obj.get("jobLocation")
            location_text = None
            locs = []
            if isinstance(loc, list):
                locs = loc
            elif isinstance(loc, dict):
                locs = [loc]

            for l in locs:
                if not isinstance(l, dict):
                    continue
                addr = l.get("address")
                if isinstance(addr, dict):
                    city = addr.get("addressLocality")
                    region = addr.get("addressRegion")
                    if city and region:
                        location_text = f"{city}, {region}"
                        break
                    if city:
                        location_text = city
                        break
            if location_text:
                result["location"] = location_text

            # Description (often HTML)
            desc = obj.get("description")
            if desc:
                result["description"] = _strip_html(desc)

            return result  # first JobPosting wins

    return result


def get_jobs(keyword, num_jobs, verbose=False):
    # ----------------------------
    # Chrome Setup
    # ----------------------------
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    wait = WebDriverWait(driver, 20)

    # ----------------------------
    # Open Search Page
    # ----------------------------
    search_url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={keyword.replace(' ', '%20')}"
    driver.get(search_url)

    input("Press ENTER once job listings are visible...")

    time.sleep(2)

    # ----------------------------
    # Collect Job URLs
    # ----------------------------
    all_links = driver.find_elements(By.TAG_NAME, "a")
    job_urls = []
    for link in all_links:
        href = link.get_attribute("href")
        if href and "/job-listing/" in href:
            job_urls.append(href)

    # de-dupe, preserve order
    seen = set()
    job_urls_unique = []
    for u in job_urls:
        if u not in seen:
            seen.add(u)
            job_urls_unique.append(u)

    job_urls_unique = job_urls_unique[:num_jobs]

    print(f"Found {len(job_urls_unique)} job URLs")
    if verbose:
        print(job_urls_unique)

    # ----------------------------
    # Visit each Job URL
    # ----------------------------
    rows = []

    for url in job_urls_unique:
        driver.get(url)

        # Wait for page to load (at least title)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
        except Exception:
            # If title doesn't load, skip
            if verbose:
                print("⚠️ Could not load job page:", url)
            continue

        time.sleep(1)

        # 1) JSON-LD (best)
        ld = _parse_json_ld(driver)

        # Title
        title = ld.get("title")
        if not title:
            try:
                title = driver.find_element(By.TAG_NAME, "h1").text.strip()
            except Exception:
                title = "-1"

        # Company
        company = ld.get("company")
        if not company:
            # DOM fallback (varies a lot, best-effort)
            try:
                # often the company is a link near top that goes to /Overview/
                company = driver.find_element(By.CSS_SELECTOR, "a[href*='/Overview/']").text.strip()
            except Exception:
                company = "-1"

        # Location
        location = ld.get("location")
        if not location:
            # You already reliably got this before; keep a simple fallback:
            try:
                # pick a short "City, ST" looking text near header area
                location = driver.find_element(
                    By.XPATH,
                    "(//div[contains(text(), ',') and string-length(normalize-space(text())) < 40])[1]"
                ).text.strip()
            except Exception:
                location = "-1"

        # Description
        description = ld.get("description")
        if not description:
            # DOM fallback
            try:
                description = driver.find_element(
                    By.XPATH,
                    "//div[contains(@class,'jobDescriptionContent') or @id='JobDescriptionContainer']"
                ).text.strip()
            except Exception:
                description = "-1"

        print(title)

        rows.append({
            "Job Title": title,
            "Company Name": company,
            "Location": location,
            "Job Description": description
        })

    driver.quit()
    return pd.DataFrame(rows)