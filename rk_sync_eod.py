import csv
import io
import json
import os
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

IST = ZoneInfo("Asia/Kolkata")
OUTPUT_FILE = "eod_prices.json"

AMFI_URLS = [
    "https://portal.amfiindia.com/spages/NAVAll.txt",
    "https://www.amfiindia.com/spages/NAVAll.txt"
]

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,text/plain,*/*"
}

def load_existing_data():
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("data", {})
        except Exception:
            return {}
    return {}

def fetch_amfi_nav():
    records = {}
    content = None

    # 1. Read local file if downloaded via curl in GitHub workflow
    if os.path.exists("amfi_nav.txt") and os.path.getsize("amfi_nav.txt") > 5000:
        with open("amfi_nav.txt", "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

    # 2. Otherwise download directly
    if not content:
        for url in AMFI_URLS:
            try:
                resp = requests.get(url, headers=HTTP_HEADERS, timeout=25)
                if resp.status_code == 200 and "Net Asset Value" in resp.text:
                    content = resp.text
                    break
            except Exception:
                continue

    if not content:
        print("[AMFI] Error: Unable to retrieve AMFI data feed.")
        return records

    # 3. Parse lines using negative indexing for column flexibility
    for line in content.splitlines():
        parts = line.split(";")
        if len(parts) >= 6:
            growth_isin = parts[1].strip()
            reinv_isin = parts[2].strip()
            name = parts[3].strip()

            # Date is always the last column; NAV is always second to last
            nav_str = parts[-2].strip()
            date = parts[-1].strip()

            try:
                nav = float(nav_str)
            except ValueError:
                continue  # Skips header row or empty entries

            for isin in (growth_isin, reinv_isin):
                if len(isin) == 12 and isin.startswith("INF"):
                    records[isin] = {
                        "type": "MUTUAL_FUND",
                        "name": name,
                        "price": nav,
                        "date": date
                    }

    print(f"[AMFI] Successfully ingested {len(records)} mutual fund ISINs.")
    return records

def fetch_nse_bhavcopy(days_lookback=5):
    records = {}
    today = datetime.now(IST).date()

    for i in range(days_lookback):
        target_date = today - timedelta(days=i)
        date_str = target_date.strftime("%Y%m%d")
        url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"

        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=20)
            if resp.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                    csv_name = z.namelist()[0]
                    with z.open(csv_name) as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                        for row in reader:
                            clean_row = {k.strip(): v.strip() for k, v in row.items() if k}
                            isin = clean_row.get("ISIN", "")
                            cls_pric = clean_row.get("ClsPric", "")

                            if len(isin) == 12 and cls_pric:
                                try:
                                    records[isin] = {
                                        "type": "EQUITY",
                                        "symbol": clean_row.get("TckrSymb", ""),
                                        "price": float(cls_pric),
                                        "date": clean_row.get("TradDt", target_date.isoformat())
                                    }
                                except ValueError:
                                    continue
                if records:
                    print(f"[NSE] Successfully parsed {len(records)} stock ISINs for {target_date}.")
                    break
        except Exception:
            continue

    return records

def main():
    combined = load_existing_data()

    # Ingest Mutual Funds
    mf_data = fetch_amfi_nav()
    combined.update(mf_data)

    # Ingest Stocks
    stock_data = fetch_nse_bhavcopy()
    combined.update(stock_data)

    output = {
        "updated_at": datetime.now(IST).isoformat(),
        "total_instruments": len(combined),
        "data": combined
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    print(f"[SUCCESS] Exported {len(combined)} total instruments to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
