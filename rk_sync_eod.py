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
AMFI_LOCAL_FILE = "amfi_nav.txt"

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

def parse_local_amfi():
    records = {}
    if not os.path.exists(AMFI_LOCAL_FILE):
        print("[AMFI] Local file not found.")
        return records

    with open(AMFI_LOCAL_FILE, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.split(";")
            # Format: Scheme Code;ISIN Growth;ISIN Reinv;Scheme Name;NAV;Date
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
                    if len(isin) == 12 and isin.startswith("INF"):
                        records[isin] = {
                            "type": "MUTUAL_FUND",
                            "name": name,
                            "price": nav,
                            "date": date
                        }

    print(f"[AMFI] Parsed {len(records)} mutual fund ISINs.")
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
                    print(f"[NSE] Parsed {len(records)} stock ISINs for {target_date}.")
                    break
        except Exception:
            continue

    return records

def main():
    combined = load_existing_data()

    # 1. Parse AMFI Mutual Funds from local curl file
    mf_data = parse_local_amfi()
    combined.update(mf_data)

    # 2. Parse NSE Stocks from archives
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
