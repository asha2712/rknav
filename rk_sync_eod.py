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
AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*"
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
    try:
        resp = requests.get(AMFI_URL, headers=HTTP_HEADERS, timeout=20)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            parts = line.split(";")
            if len(parts) >= 6:
                growth_isin = parts[1].strip()
                reinv_isin = parts[2].strip()
                name = parts[3].strip()
                try:
                    nav = float(parts[4].strip())
                except ValueError:
                    continue
                date = parts[5].strip()
                for isin in (growth_isin, reinv_isin):
                    if len(isin) == 12:
                        records[isin] = {"type": "MF", "name": name, "price": nav, "date": date}
    except Exception as e:
        print(f"AMFI fetch error: {e}")
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
                    break
        except Exception:
            continue
    return records

def main():
    combined = load_existing_data()
    combined.update(fetch_amfi_nav())
    combined.update(fetch_nse_bhavcopy())
    output = {
        "updated_at": datetime.now(IST).isoformat(),
        "total_instruments": len(combined),
        "data": combined
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))
    print(f"Done. Processed {len(combined)} instruments.")

if __name__ == "__main__":
    main()
