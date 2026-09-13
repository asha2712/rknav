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

AMFI_URLS = [
    "https://portal.amfiindia.com/spages/NAVAll.txt",
    "https://www.amfiindia.com/spages/NAVAll.txt"
]

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.amfiindia.com/net-asset-value/nav-history"
}

def load_existing_data():
    """Preserves existing records in case a market holiday or timeout occurs."""
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

    # 1. Read local file if downloaded via curl in GitHub Actions
    if os.path.exists(AMFI_LOCAL_FILE) and os.path.getsize(AMFI_LOCAL_FILE) > 5000:
        with open(AMFI_LOCAL_FILE, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

    # 2. Fallback: Download directly if curl step didn't run
    if not content:
        session = requests.Session()
        session.headers.update(HTTP_HEADERS)
        for url in AMFI_URLS:
            try:
                resp = session.get(url, timeout=30)
                if resp.status_code == 200 and "Net Asset Value" in resp.text:
                    content = resp.text
                    break
            except Exception as e:
                print(f"[AMFI] Request failed for {url}: {e}")

    if not content:
        print("[AMFI] Error: No content retrieved.")
        return records

    # 3. Parse lines dynamically
    for line in content.splitlines():
        line = line.strip()
        if not line or ";" not in line:
            continue

        parts = [p.strip() for p in line.split(";")]
        
        # Remove trailing empty columns if lines end with a semicolon
        while parts and parts[-1] == "":
            parts.pop()

        if len(parts) < 5:
            continue

        # Date is always the final column
        date = parts[-1]

        # Scan backwards to locate the NAV float
        nav = None
        for col in reversed(parts[:-1]):
            try:
                nav = float(col)
                break
            except ValueError:
                continue

        if nav is None:
            continue

        name = parts[3] if len(parts) > 3 else ""

        # Map both Growth (slot 1) and Reinvestment (slot 2) ISINs
        for col_idx in (1, 2):
            if len(parts) > col_idx:
                isin = parts[col_idx].upper()
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
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)

    for i in range(days_lookback):
        target_date = today - timedelta(days=i)
        date_str = target_date.strftime("%Y%m%d")
        url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"

        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200:
                with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                    csv_name = z.namelist()[0]
                    with z.open(csv_name) as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                        for row in reader:
                            clean_row = {k.strip(): v.strip() for k, v in row.items() if k}
                            isin = clean_row.get("ISIN", "").upper()
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

    # indent=2 formats each ISIN on its own line for reliable searching
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"[SUCCESS] Exported {len(combined)} total instruments to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
