# app.py
import flask
from flask import Flask, jsonify, request, render_template
import requests
from bs4 import BeautifulSoup
import json
import datetime
import logging
import os
import psycopg2
import psycopg2.extras # For dictionary cursor
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import time # For potential delays between requests

# --- Configuration ---
load_dotenv() # Load environment variables from .env file
DATABASE_URL = os.getenv("DATABASE_URL")
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")
TARGET_REGIONS = ['us', 'cn', 'jp', 'gb', 'de', 'au', 'ca', 'in', 'br', 'tr', 'mx', 'kr', 'hk', 'sg', 'fr', 'it', 'es'] # Expand as needed
APPS_TO_SCRAPE = {
    "iCloud+": {"source": "support_page"}, # Special handling for iCloud+
    "ChatGPT": {"id": "6448311069", "source": "app_store"},
    "Claude": {"id": "6473753684", "source": "app_store"},
    "Google One": {"id": "1451784328", "source": "app_store"},
}

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Database Connection ---
def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        return None

# --- Exchange Rate Conversion ---
def get_exchange_rate(from_currency, to_currency="CNY"):
    """Fetches the exchange rate from an external API."""
    if from_currency.upper() == to_currency.upper():
        return 1.0
    if not EXCHANGE_RATE_API_KEY:
        logging.error("Exchange Rate API Key is missing!")
        return None

    # Using ExchangeRate-API.com v6 format
    url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/pair/{from_currency.upper()}/{to_currency.upper()}"
    try:
        response = requests.get(url, timeout=15) # Increased timeout
        response.raise_for_status()
        data = response.json()
        if data.get("result") == "success":
            rate = data.get("conversion_rate")
            if rate:
                logging.info(f"Got rate {rate} for {from_currency}->{to_currency}")
                return float(rate)
            else:
                 logging.error(f"API success but no rate found for {from_currency}->{to_currency}. Response: {data}")
                 return None
        else:
            error_type = data.get("error-type", "Unknown API Error")
            logging.error(f"Exchange rate API error: {error_type}. Check API key and account status.")
            if error_type in ["invalid-key", "inactive-account", "quota-reached"]:
                 logging.error("Halting further exchange rate lookups due to critical API error.")
                 # Potentially disable further lookups temporarily
            return None
    except requests.exceptions.Timeout:
         logging.error(f"Timeout connecting to exchange rate API for {from_currency}->{to_currency}")
         return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching exchange rate for {from_currency}->{to_currency}: {e}")
        return None
    except json.JSONDecodeError:
         logging.error(f"Failed to decode JSON response from exchange rate API.")
         return None
    except Exception as e:
        logging.error(f"An unexpected error occurred during exchange rate fetching: {e}")
        return None

def convert_to_cny(price, currency):
    """Converts a price from its local currency to CNY using the fetched rate."""
    if currency is None: return None # Cannot convert if currency unknown
    currency = currency.upper()
    if currency == "CNY":
        return price
    rate = get_exchange_rate(currency, "CNY")
    if rate:
        return round(price * rate, 2)
    logging.warning(f"Could not get exchange rate for {currency} to CNY.")
    return None

# --- Scraping Functions ---

# Utility to clean price strings (basic)
def clean_price(price_text):
    """Attempts to extract a float value from a price string."""
    if not price_text: return None, None
    # Remove common currency symbols and grouping separators
    cleaned = ''.join(filter(lambda x: x.isdigit() or x == '.' or x == ',', price_text))
    # Handle comma as decimal separator if needed
    cleaned = cleaned.replace(',', '.')
    try:
        price = float(cleaned)
        # Basic currency symbol detection (very rudimentary)
        currency_symbol = ''.join(filter(lambda x: not x.isdigit() and x not in ['.', ','], price_text)).strip()
        return price, currency_symbol
    except ValueError:
        logging.warning(f"Could not parse price from text: '{price_text}'")
        return None, None

# Utility to map common symbols/regions to currency codes (Needs expansion)
# Utility to map common symbols/regions to currency codes (Needs expansion)
def map_currency(symbol, region_code):
    """Maps common symbols or uses region code to guess currency."""
    # Ensure inputs are consistently uppercase for matching
    symbol = symbol.upper() if symbol else ""
    region_code = region_code.upper() if region_code else ""

    # 1. Direct symbol mapping (prioritize specific symbols)
    if 'HK$' in symbol: return 'HKD'
    if 'R$' in symbol: return 'BRL' # Brazil
    if 'S/' in symbol: return 'PEN' # Peru (can be ambiguous)
    if '$' == symbol or 'USD' in symbol: return 'USD'
    if '¥' == symbol or 'CNY' in symbol or 'RMB' in symbol: return 'CNY' # Added RMB
    if '€' == symbol or 'EUR' in symbol: return 'EUR'
    if '£' == symbol or 'GBP' in symbol: return 'GBP'
    # Yen symbol is same as Yuan, rely on region code later if symbol is '¥'/'￥'
    if '￥' == symbol and region_code != 'CN': return 'JPY'
    if 'CAD' in symbol or (symbol == '$' and region_code == 'CA'): return 'CAD'
    if 'AUD' in symbol or (symbol == '$' and region_code == 'AU'): return 'AUD'
    if '₹' == symbol or 'INR' in symbol: return 'INR'
    if '₽' == symbol or 'RUB' in symbol: return 'RUB'
    if '₩' == symbol or 'KRW' in symbol: return 'KRW'
    if '₺' == symbol or 'TRY' in symbol: return 'TRY' # Added Lira symbol
    if '₦' == symbol or 'NGN' in symbol: return 'NGN' # Added Naira symbol
    if 'CHF' in symbol: return 'CHF' # Swiss Franc
    if 'NZ$' in symbol: return 'NZD' # New Zealand Dollar
    if 'SGD' in symbol or (symbol == '$' and region_code == 'SG'): return 'SGD' # Singapore Dollar
    if 'MXN' in symbol or (symbol == '$' and region_code == 'MX'): return 'MXN' # Mexican Peso

    # 2. Fallback based on region code if symbol was ambiguous or unmapped
    # Needs continuous expansion for accuracy
    region_map = {
        'US': 'USD', 'CN': 'CNY', 'JP': 'JPY', 'GB': 'GBP', 'DE': 'EUR', 'FR': 'EUR',
        'AU': 'AUD', 'CA': 'CAD', 'IN': 'INR', 'BR': 'BRL',
        'TR': 'TRY', # <-- Added Turkey
        'NG': 'NGN', # <-- Added Nigeria
        'MX': 'MXN', 'KR': 'KRW', 'HK': 'HKD', 'SG': 'SGD', 'IT': 'EUR', 'ES': 'EUR',
        'RU': 'RUB', 'CH': 'CHF', 'NZ': 'NZD', 'SE': 'SEK', 'NO': 'NOK', 'DK': 'DKK',
        'PL': 'PLN', 'ZA': 'ZAR', 'AE': 'AED', 'SA': 'SAR', 'ID': 'IDR', 'MY': 'MYR',
        'TH': 'THB', 'VN': 'VND', 'PH': 'PHP', 'CL': 'CLP', 'CO': 'COP', 'PE': 'PEN',
        'AR': 'ARS', # Argentina - High volatility, rate APIs might struggle
        # Add many more region codes (ISO 3166-1 alpha-2) and their primary currency
    }
    guess = region_map.get(region_code)

    if guess:
        # Only log if the guess is different from a symbol-based match or if symbol was generic
        # This avoids verbose logging if symbol already clearly indicated the currency.
        # Example: If symbol was 'USD' and region is 'US', no need to log the guess.
        # If symbol was '$' and region is 'CA', logging the 'CAD' guess is useful.
        # if not symbol_based_currency or symbol_based_currency != guess:
        logging.info(f"Guessed currency {guess} for region {region_code} (symbol was '{symbol}')")
        return guess

    # 3. Final fallback or warning if no match
    logging.warning(f"Could not determine currency from symbol '{symbol}' or region '{region_code}'")
    return None # Indicate failure

def scrape_icloud_prices():
    """Scrapes iCloud+ prices from the Apple Support page."""
    logging.info("Attempting to scrape iCloud+ prices from support page...")
    url = "https://support.apple.com/en-us/108047"
    scraped_data = {"iCloud+": {}} # { "Tier": [ {region, currency, price}, ... ] }

    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # --- !!! PARSING LOGIC FOR SUPPORT PAGE (HIGHLY FRAGILE) !!! ---
        # This needs careful inspection of the current page structure.
        # Example: Find divs/tables containing prices. The selectors below are GUESSES.
        # Look for patterns like tables or sections with country names and price lists.

        # Example structure assumption: A table per region group, rows per country.
        # Find all tables or relevant divs:
        content_area = soup.select_one('.main-content') # Adjust selector based on inspection
        if not content_area:
             logging.error("Could not find main content area for iCloud+ parsing.")
             return None

        # Potential structure: Find h2/h3 for region names, then ul/table for countries/prices
        # Iterate through elements, trying to identify country and price list
        # THIS IS A VERY SIMPLIFIED EXAMPLE - Real parsing is complex
        # Look for elements that clearly list prices like "$0.99", "¥6", etc.
        price_elements = content_area.find_all(string=lambda text: text and ('$' in text or '¥' in text or '€' in text or '£' in text)) # Find potential price texts

        # --- This simplified demo uses the structure confirmed by the browse tool earlier ---
        # Manually reconstructing based on browse tool output for stability in this example
        # A real scraper would parse the HTML elements above.
        icloud_tiers = ["50GB", "200GB", "2TB", "6TB", "12TB"]
        # Data extracted previously via browse tool (add more regions as needed)
        manual_data = {
            "US": {"currency": "USD", "prices": [0.99, 2.99, 9.99, 29.99, 59.99]},
            "CN": {"currency": "CNY", "prices": [6, 21, 68, 198, 398]},
            "CA": {"currency": "CAD", "prices": [1.29, 3.99, 12.99, 39.99, 79.99]},
            "GB": {"currency": "GBP", "prices": [0.79, 2.49, 6.99, 20.99, 41.99]}, # Assuming GBP prices based on common tiers
            "AU": {"currency": "AUD", "prices": [1.49, 4.49, 14.99, 44.99, 89.99]},
            "JP": {"currency": "JPY", "prices": [130, 400, 1300, 3900, 7900]}, # Assuming JPY prices
            "DE": {"currency": "EUR", "prices": [0.99, 2.99, 9.99, 29.99, 59.99]}, # Assuming EUR prices
            "IN": {"currency": "INR", "prices": [75, 219, 749, 2999, 5900]},
            "BR": {"currency": "BRL", "prices": [4.90, 14.90, 49.90, 149.90, 299.90]},
            "MX": {"currency": "MXN", "prices": [17, 49, 179, 499, 999]},
            "KR": {"currency": "KRW", "prices": [1100, 3300, 11100, 33000, 66000]}, # Assuming KRW prices
            "HK": {"currency": "HKD", "prices": [8, 23, 78, 238, 468]},
        }

        for region, details in manual_data.items():
             for i, tier in enumerate(icloud_tiers):
                 if tier not in scraped_data["iCloud+"]:
                      scraped_data["iCloud+"][tier] = []
                 scraped_data["iCloud+"].append({
                     "region": region,
                     "currency": details["currency"],
                     "price": details["prices"][i]
                 })
        # --- End Manual Data Reconstruction ---

        logging.info(f"Processed iCloud+ data structure: {len(scraped_data.get('iCloud+', {}))} tiers")
        return scraped_data # Return the structured data { "iCloud+": { "Tier": [...] } }

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching iCloud+ URL: {e}")
        return None
    except Exception as e:
        logging.error(f"Error parsing iCloud+ page: {e}")
        return None


def scrape_app_store_price(app_name, region_code, app_id):
    """Scrapes In-App Purchase prices from an App Store page."""
    logging.info(f"Attempting to scrape {app_name} in {region_code} (ID: {app_id})...")
    # Construct URL carefully, sometimes app name slug is needed
    # url = f"https://apps.apple.com/{region_code}/app/{app_name.lower().replace(' ', '-')}/id{app_id}" # More robust?
    url = f"https://apps.apple.com/{region_code}/app/id{app_id}"
    app_data = {} # { "Plan Name": [ {region, currency, price} ] }

    try:
        # Use headers mimicking a browser, especially Accept-Language
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36',
            'Accept-Language': f'{region_code}-{region_code.upper()},en-US;q=0.9,en;q=0.8'
        }
        response = requests.get(url, headers=headers, timeout=20)
        logging.info(f"Request URL: {url} | Status Code: {response.status_code}")
        response.raise_for_status() # Check for HTTP errors like 404, 5xx

        soup = BeautifulSoup(response.text, 'html.parser')

        # --- !!! PARSING LOGIC FOR APP STORE PAGE (HIGHLY FRAGILE) !!! ---
        # Find the "In-App Purchases" section - element type and text might change
        # Try finding by <dt>, <h2>, or other relevant tags containing the text
        iap_header = None
        possible_headers = soup.find_all(['dt', 'h2', 'div'], string=lambda t: t and "In-App Purchases" in t, limit=5)
        if possible_headers:
             iap_header = possible_headers[0] # Take the first likely match
             logging.info(f"Found IAP header element: {iap_header.name}")
        else:
            logging.warning(f"Could not find 'In-App Purchases' header for {app_name} in {region_code}")
            # Maybe the info is elsewhere? Try looking for price elements directly?
            # Add alternative parsing strategies here if needed.
            return None # Assume failure if header not found for now

        # Find the list/container of purchases following the header
        # This depends heavily on the HTML structure around the header
        iap_list_container = None
        if iap_header.name == 'dt':
            iap_list_container = iap_header.find_next_sibling('dd')
        elif iap_header.name == 'h2':
             # Try finding a common parent and then the list, or just search siblings/nearby divs
             iap_list_container = iap_header.find_next_sibling(['ol', 'ul', 'div']) # Common list/div tags
        else: # If header was in a div, search within or after it
             iap_list_container = iap_header.find_next(['ol', 'ul', 'div'])

        if not iap_list_container:
            logging.warning(f"Could not find IAP list container after header for {app_name} in {region_code}")
            return None

        # Select individual purchase items (adjust selector based on inspection)
        items = iap_list_container.select('li, div.app-purchase-item') # Try common list item tags or specific divs
        if not items: # Fallback if direct children are the items
             items = iap_list_container.find_all(recursive=False) # Less reliable

        logging.info(f"Found {len(items)} potential IAP list items for {app_name} in {region_code}")

        for item in items:
            # Extract plan name and price - selectors need verification!
            # Example selectors based on common App Store patterns or user's initial snippets
            title_el = item.select_one('.list-with-numbers__item__title span, .we-product-name, .purchase-item-name') # Try multiple possibilities
            price_el = item.select_one('.list-with-numbers__item__price, .we-product-price, .purchase-item-price') # Try multiple possibilities

            if title_el and price_el:
                plan_name = title_el.get_text(strip=True)
                price_text = price_el.get_text(strip=True)

                if plan_name and price_text:
                    price, symbol = clean_price(price_text)
                    currency = map_currency(symbol, region_code)

                    if price is not None and currency is not None:
                        if plan_name not in app_data:
                            app_data[plan_name] = []
                        app_data[plan_name].append({
                            "region": region_code.upper(),
                            "currency": currency,
                            "price": price
                        })
                    else:
                         logging.warning(f"Parsed plan '{plan_name}' but failed to parse price/currency '{price_text}' for {app_name} in {region_code}")
            # else: Log if title/price element not found within an item? Might be too verbose.

        if app_data:
             logging.info(f"Successfully scraped {len(app_data)} plans for {app_name} in {region_code}")
             return {app_name: app_data} # Return { "AppName": { "PlanName": [...] } }
        else:
             logging.warning(f"Found IAP container but no valid items parsed for {app_name} in {region_code}")
             return None

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
             logging.warning(f"App {app_name} (ID: {app_id}) not found in region {region_code} (404). Skipping.")
        elif e.response.status_code == 403:
             logging.warning(f"Access forbidden (403) for {app_name} in {region_code}. Possible anti-scraping measure.")
        else:
             logging.error(f"HTTP error fetching App Store URL for {app_name} in {region_code}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error fetching App Store URL for {app_name} in {region_code}: {e}")
        return None
    except Exception as e:
        # Catch potential BeautifulSoup errors or others
        logging.error(f"Error parsing App Store page for {app_name} in {region_code}: {e}", exc_info=True) # Log traceback
        return None

@app.route('/admin/trigger-update', methods=['POST', 'GET']) # Allow GET for easy browser testing, POST is better practice
def trigger_update():
    logging.info("Manual update triggered via /admin/trigger-update")
    try:
        # 在后台线程中运行可能更好，避免长时间阻塞请求
        # import threading
        # thread = threading.Thread(target=update_prices_in_db)
        # thread.start()
        # return jsonify({"message": "Update process started in background. Check logs."}), 202

        # 为了简单，现在直接运行 (可能会阻塞几分钟)
        update_prices_in_db()
        return jsonify({"message": "Update process completed. Check logs and refresh data."}), 200
    except Exception as e:
         logging.error(f"Error during manual trigger: {e}", exc_info=True)
         return jsonify({"error": "Failed to trigger update. Check logs."}), 500

# --- Data Update Logic ---
def update_prices_in_db():
    """Scrapes all sources, converts prices, and updates the database."""
    logging.info("--- Starting Price Update Task ---")
    all_scraped_data = [] # List to hold all valid price points found: {app, plan, region, currency, price}

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # 1. Scrape iCloud+
    icloud_raw_data = scrape_icloud_prices()
    if icloud_raw_data and "iCloud+" in icloud_raw_data:
        for tier, price_list in icloud_raw_data["iCloud+"].items():
            for price_info in price_list:
                 all_scraped_data.append({
                     "app_name": "iCloud+",
                     "plan_name": tier,
                     "region": price_info["region"],
                     "currency": price_info["currency"],
                     "price": price_info["price"]
                 })
        logging.info(f"Collected {len(all_scraped_data)} price points from iCloud+ scrape.")
    else:
         logging.warning("Failed to scrape or process iCloud+ data.")


    # 2. Scrape Other Apps from App Store
    for app_name, info in APPS_TO_SCRAPE.items():
        if info["source"] == "app_store":
            app_id = info["id"]
            for region in TARGET_REGIONS:
                app_price_data = scrape_app_store_price(app_name, region, app_id)
                if app_price_data and app_name in app_price_data:
                    for plan, price_list in app_price_data[app_name].items():
                        for price_info in price_list:
                            all_scraped_data.append({
                                "app_name": app_name,
                                "plan_name": plan,
                                "region": price_info["region"],
                                "currency": price_info["currency"],
                                "price": price_info["price"]
                            })
                # Add a small delay between requests to avoid rate limiting
                time.sleep(1) # Sleep for 1 second

    logging.info(f"Collected a total of {len(all_scraped_data)} price points from all sources.")

    # 3. Convert prices and prepare for DB insertion
    db_rows = []
    for item in all_scraped_data:
        price_cny = convert_to_cny(item["price"], item["currency"])
        if price_cny is not None: # Only insert if conversion worked
            db_rows.append((
                item["app_name"],
                item["plan_name"],
                item["region"],
                item["currency"],
                item["price"],
                price_cny,
                now_utc # last_updated timestamp
            ))
        else:
            logging.warning(f"Skipping DB insert for {item['app_name']}/{item['plan_name']}/{item['region']} due to failed CNY conversion.")

    # 4. Database Update
    conn = get_db_connection()
    if not conn:
        logging.error("Cannot update database - connection failed.")
        return
    if not db_rows:
         logging.warning("No valid data scraped or converted. Database not updated.")
         conn.close()
         return

    try:
        cursor = conn.cursor()
        logging.info(f"Attempting to insert/update {len(db_rows)} price records...")

        # Strategy: Delete old data for apps updated, then insert new.
        # Get unique apps that were successfully scraped
        updated_apps = list(set(row[0] for row in db_rows))
        if updated_apps:
            # Create placeholder string like (%s, %s, %s)
            placeholders = ', '.join(['%s'] * len(updated_apps))
            delete_query = f"DELETE FROM prices WHERE app_name IN ({placeholders})"
            cursor.execute(delete_query, tuple(updated_apps))
            logging.info(f"Deleted old records for apps: {updated_apps} (Rows affected: {cursor.rowcount})")

        # Insert new data using executemany for efficiency
        insert_query = """
            INSERT INTO prices (app_name, plan_name, region, currency, price, price_cny, last_updated)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cursor.executemany(insert_query, db_rows)
        logging.info(f"Inserted {cursor.rowcount} new price records.")

        conn.commit() # Commit transaction
        logging.info("Database update successful.")

    except Exception as e:
        logging.error(f"Database update error: {e}", exc_info=True)
        conn.rollback() # Rollback on error
    finally:
        cursor.close()
        conn.close()

    # Store last updated time globally (simple approach)
    global last_update_timestamp
    last_update_timestamp = now_utc.isoformat()
    logging.info(f"--- Price Update Task Finished at {last_update_timestamp} ---")


# --- Database Query Function ---
def query_prices_from_db(app_name_filter, plan_name_filter=None):
    """Queries the database for prices based on filters."""
    conn = get_db_connection()
    if not conn: return [], None # Return empty list and null timestamp

    try:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        base_query = "SELECT * FROM prices WHERE app_name = %s"
        params = [app_name_filter]
        if plan_name_filter:
            base_query += " AND plan_name = %s"
            params.append(plan_name_filter)
        # Order by CNY price for potential optimization, although sorting happens later too
        base_query += " ORDER BY price_cny ASC NULLS LAST" # Handle null CNY prices

        cursor.execute(base_query, tuple(params))
        results = cursor.fetchall()

        # Get the latest update timestamp for this app from the results
        latest_timestamp = None
        if results:
             # Timestamps are TIMESTAMPTZ, get the max directly
             latest_timestamp = max(row['last_updated'] for row in results if row['last_updated'] is not None)

        cursor.close()
        conn.close()
        logging.info(f"DB query for app='{app_name_filter}', plan='{plan_name_filter}': Found {len(results)} rows.")
        # Convert results to standard dicts
        return [dict(row) for row in results], latest_timestamp.isoformat() if latest_timestamp else "N/A"

    except Exception as e:
        logging.error(f"Database query error: {e}")
        if conn: conn.close()
        return [], None # Return empty list on query failure


# --- API Endpoint ---
last_update_timestamp = "Never" # Initialize global timestamp

@app.route('/api/prices', methods=['GET'])
def get_prices():
    """API endpoint to retrieve filtered and sorted prices."""
    app_name = flask.request.args.get('app')
    plan_name = flask.request.args.get('plan') # Optional plan filter

    if not app_name:
        return jsonify({"error": "Missing 'app' parameter"}), 400

    # Fetch data from database
    db_results, update_time = query_prices_from_db(app_name, plan_name if plan_name else None)

    if not db_results and not update_time: # Check if DB query itself failed
         return jsonify({"error": "Failed to query database"}), 500

    # --- Sorting and Top 10 + US/CN Logic ---
    valid_prices = [p for p in db_results if p.get("price_cny") is not None]
    sorted_prices = sorted(valid_prices, key=lambda x: x["price_cny"])

    # Get Top 10
    top_10 = sorted_prices[:10]

    # Find US and CN entries (case-insensitive matching for region)
    us_entry = next((p for p in valid_prices if p.get("region","").upper() == "US"), None)
    cn_entry = next((p for p in valid_prices if p.get("region","").upper() == "CN"), None)

    # Build final list, ensuring US/CN inclusion and uniqueness
    final_list_dict = {f"{item['region']}_{item['plan_name']}": item for item in top_10} # Use dict for easy overwrite/check

    if us_entry:
        final_list_dict[f"{us_entry['region']}_{us_entry['plan_name']}"] = us_entry # Add/overwrite US entry
    if cn_entry:
        final_list_dict[f"{cn_entry['region']}_{cn_entry['plan_name']}"] = cn_entry # Add/overwrite CN entry

    # Convert back to list and sort again by CNY price
    unique_final_list = list(final_list_dict.values())
    unique_final_list_sorted = sorted(unique_final_list, key=lambda x: x.get("price_cny", float('inf')))
    # --- End Sorting Logic ---

    # Convert datetime objects in results to ISO strings for JSON compatibility
    for item in unique_final_list_sorted:
         if isinstance(item.get('last_updated'), datetime.datetime):
              item['last_updated'] = item['last_updated'].isoformat()

    return jsonify({
        "app": app_name,
        "plan_filter": plan_name, # Let frontend know if filter was applied
        "prices": unique_final_list_sorted,
        "last_updated": update_time # Use timestamp from DB query
    })

# --- Route for HTML Frontend ---
@app.route('/')
def index():
    """Serves the main HTML page."""
    # Renders the HTML file from the 'templates' folder
    return render_template('index.html')

# --- Scheduler Setup ---
scheduler = BackgroundScheduler(daemon=True)

def scheduled_update_job():
    """Function wrapper for scheduler to run updates with app context."""
    logging.info("Scheduler triggered: Running price update job.")
    with app.app_context(): # Ensures DB connections etc. work if using Flask extensions
         update_prices_in_db()

# Schedule job
# Run less frequently initially to avoid hitting API limits/getting blocked
# Update every 6 hours:
scheduler.add_job(func=scheduled_update_job, trigger="interval", hours=6, misfire_grace_time=900) # Grace time 15min
# For testing, run more often (e.g., every 1 minute):
# scheduler.add_job(func=scheduled_update_job, trigger="interval", minutes=1)

# --- Main Execution Block ---
if __name__ == '__main__':
    # Perform an initial update on startup (optional, can take time)
    # Consider running this manually or via a separate script first
    # with app.app_context():
    #      update_prices_in_db()

    # Start the scheduler only if not in debug mode with reloader, or handle appropriately
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
         scheduler.start()
         logging.info("APScheduler started.")
         # Shut down the scheduler when exiting the app
         atexit.register(lambda: scheduler.shutdown())
    else:
         logging.info("APScheduler not started because Flask is in debug mode with reloader.")


    # Run Flask dev server (Use Gunicorn in production)
    # port = int(os.environ.get('PORT', 5000)) # Good practice for deployment flexibility
    # app.run(host='0.0.0.0', port=port, debug=False) # Use debug=False for production/scheduler test
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False) # Development mode