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
    "Accept": "text/html,text/plain,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.bseindia.com/"
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
        
        while parts and parts[-1] == "":
            parts.pop()

        if len(parts) < 5:
            continue

        date = parts[-1]

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

def parse_bse_csv_stream(csv_file, target_date_iso):
    """Helper to parse BSE rows supporting both UDiFF and legacy headers."""
    records = {}
    reader = csv.DictReader(csv_file)
    for row in reader:
        clean_row = {k.strip(): v.strip() for k, v in row.items() if k}
        
        # Support UDiFF ('ISIN') or legacy ('ISIN_CODE')
        isin = (clean_row.get("ISIN") or clean_row.get("ISIN_CODE") or "").upper()
        # Support UDiFF ('ClsPric') or legacy ('CLOSE')
        close_price = clean_row.get("ClsPric") or clean_row.get("CLOSE") or ""
        # Support symbol/name variations
        symbol = clean_row.get("TckrSymb") or clean_row.get("SC_NAME") or clean_row.get("SC_CODE") or ""

        if len(isin) == 12 and close_price:
            try:
                records[isin] = {
                    "type": "EQUITY",
                    "symbol": symbol,
                    "price": float(close_price),
                    "date": clean_row.get("TradDt", target_date_iso)
                }
            except ValueError:
                continue
    return records

def fetch_bse_bhavcopy(days_lookback=5):
    """Fetches BSE Bhavcopy to cover BSE-exclusive securities like NSDL."""
    records = {}
    today = datetime.now(IST).date()
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)

    for i in range(days_lookback):
        target_date = today - timedelta(days=i)
        date_iso = target_date.isoformat()
        ddmmyyyy = target_date.strftime("%d%m%Y")
        ddmmyy = target_date.strftime("%d%m%y")
        yyyymmdd = target_date.strftime("%Y%m%d")

        # Candidate endpoints across current and legacy BSE formats
        candidate_urls = [
            f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV",
            f"https://www.bseindia.com/download/BhavCopy/Equity/BSE_EQ_BHAVCOPY_{ddmmyyyy}_T0.ZIP",
            f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{ddmmyy}_CSV.ZIP",
            f"https://www.bseindia.com/BSEDATA/gross/{target_date.year}/SCBhavCopy{ddmmyy}.zip"
        ]

        for url in candidate_urls:
            try:
                resp = session.get(url, timeout=20)
                if resp.status_code == 200 and len(resp.content) > 500:
                    # Case 1: Plain CSV download
                    if url.endswith(".CSV"):
                        text_stream = io.StringIO(resp.text)
                        records = parse_bse_csv_stream(text_stream, date_iso)
                    # Case 2: Zip archive
                    else:
                        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                            for filename in z.namelist():
                                if filename.lower().endswith(".csv"):
                                    with z.open(filename) as f:
                                        text_stream = io.TextIOWrapper(f, encoding="utf-8", errors="ignore")
                                        records = parse_bse_csv_stream(text_stream, date_iso)
                                    break

                    if records:
                        print(f"[BSE] Successfully parsed {len(records)} stock ISINs from {url} for {target_date}.")
                        return records
            except Exception:
                continue

    return records

def main():
    combined = load_existing_data()

    # 1. Ingest Mutual Funds
    mf_data = fetch_amfi_nav()
    combined.update(mf_data)

    # 2. Ingest NSE Stocks
    stock_data_nse = fetch_nse_bhavcopy()
    combined.update(stock_data_nse)

    # 3. Ingest BSE Stocks (covers BSE-exclusive securities like NSDL)
    stock_data_bse = fetch_bse_bhavcopy()
    bse_added_count = 0
    for isin, bse_item in stock_data_bse.items():
        # Prefer NSE data if dual-listed; append if listed exclusively on BSE
        if isin not in combined:
            combined[isin] = bse_item
            bse_added_count += 1

    print(f"[BSE] Ingested {bse_added_count} BSE-exclusive securities.")

    output = {
        "updated_at": datetime.now(IST).isoformat(),
        "total_instruments": len(combined),
        "data": combined
    }

    # indent=2 formats each ISIN on its own line for scannability
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"[SUCCESS] Exported {len(combined)} total instruments to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
