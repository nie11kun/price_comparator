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
import re

# --- Global state for Exchange Rate API Circuit Breaker ---
api_failure_counts = {} # Dictionary to store consecutive failures per currency pair (e.g., {'CAD_CNY': 1})
api_circuit_breaker_open = False # Flag to indicate if the breaker is open
API_FAILURE_THRESHOLD = 1 # Number of consecutive failures for a pair to open the breaker for the current run

# --- Exchange Rate Conversion ---
FALLBACK_RATES_TO_CNY = {
    'USD': 7.25, 'EUR': 7.80, 'GBP': 9.10, 'JPY': 0.048, 'CAD': 5.35, 'AUD': 4.80, 'HKD': 0.93,
    'KRW': 0.0053, 'SGD': 5.38, 'CHF': 8.00, 'INR': 0.087, 'BRL': 1.45, 'TRY': 0.22, 'RUB': 0.078,
    'MXN': 0.43, 'NZD': 4.35, 'SEK': 0.67, 'NOK': 0.66, 'DKK': 1.05, 'PLN': 1.80, 'ZAR': 0.39,
    'AED': 1.97, 'SAR': 1.93, 'THB': 0.198, 'IDR': 0.00045, 'MYR': 1.53, 'PHP': 0.126,'VND': 0.000285,
    'CZK': 0.031, 'HUF': 0.020, 'ILS': 1.95, 'CLP': 0.0077, 'COP': 0.00185,'PEN': 1.95, 'ARS': 0.008,
    'TWD': 0.225, 'PKR': 0.026, 'EGP': 0.15, 'QAR': 1.99, 'KZT': 0.016, 'RON': 1.57, 'BGN': 4.00,
    'TZS': 0.0028,
    # Add others as needed
}

# --- Configuration ---
load_dotenv() # Load environment variables from .env file
DATABASE_URL = os.getenv("DATABASE_URL")
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")
TARGET_REGIONS = [
    # North America
    'us', 'ca',

    # Europe
    'gb', 'de', 'fr', 'it', 'es', # Major EU
    # 'be', 'ie', 'at', 'ch', # Benelux, Ireland, Austria, Switzerland
    # 'se', 'no', 'dk', 'fi',       # Nordics
    # 'pl', 'cz', 'hu', 'pt', 'gr', # Central/Southern/Eastern Europe
    'tr',                          # Turkey

    # Asia Pacific
    # 'cn', # 'jp', 'kr', 'hk', 'tw', 'sg', # East Asia / Singapore
    'au', 'nz',                          # Oceania
    # 'in', 'id', 'my', 'th', 'vn', 'ph', # South/Southeast Asia

    # Latin America
    # 'br', 'ar', 'cl', 'co', 'pe',

    # Middle East / Africa
    # 'ae', 'sa', 'il', 'eg',       # Middle East
    'za', 'ng',                   # Africa (South Africa, Nigeria)

    # Consider adding others like 'ru' (Russia) based on needs,
    # but be aware of potential availability/payment system differences.
]

# Remove duplicates (just in case) and sort alphabetically for readability
TARGET_REGIONS = sorted(list(set(TARGET_REGIONS)))

# You can print the final list length during startup for confirmation
logging.info(f"Initialized with {len(TARGET_REGIONS)} target regions for App Store scraping.")

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

def get_exchange_rate(from_currency, to_currency="CNY", region_code=None): # Pass region_code for context
    """
    Fetches the exchange rate from an external API, with hardcoded fallback
    and a simple circuit breaker (opens after threshold consecutive failures for a pair).
    """
    global api_failure_counts, api_circuit_breaker_open # Declare modification of globals

    from_currency_upper = from_currency.upper()
    to_currency_upper = to_currency.upper()
    currency_pair = f"{from_currency_upper}_{to_currency_upper}"

    if from_currency_upper == to_currency_upper:
        return 1.0

    # --- 1. Circuit Breaker Check ---
    if api_circuit_breaker_open:
        logging.warning(f"[CB OPEN] Circuit Breaker is open. Skipping API call for {currency_pair}.")
        # Go directly to fallback
        if from_currency_upper in FALLBACK_RATES_TO_CNY:
            fallback_rate = FALLBACK_RATES_TO_CNY[from_currency_upper]
            logging.warning(f"[CB OPEN] Using FALLBACK rate {fallback_rate} for {currency_pair}")
            return fallback_rate
        else:
            logging.error(f"[CB OPEN] No API rate and no fallback rate found for currency: {from_currency_upper}")
            return None

    # --- 2. Attempt API Call ---
    api_rate = None
    api_call_succeeded = False # Flag to track success/failure

    if EXCHANGE_RATE_API_KEY:
        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/pair/{from_currency_upper}/{to_currency_upper}"
        try:
            response = requests.get(url, timeout=10) # Adjust timeout as needed
            response.raise_for_status()
            data = response.json()
            if data.get("result") == "success" and "conversion_rate" in data:
                api_rate = float(data["conversion_rate"])
                api_call_succeeded = True
                logging.info(f"API: Got rate {api_rate} for {currency_pair}")
            else:
                error_type = data.get("error-type", "API Error")
                logging.error(f"Exchange rate API error for {currency_pair}: {error_type}")
        except requests.exceptions.Timeout:
             logging.error(f"Timeout connecting to exchange rate API for {currency_pair}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching exchange rate API for {currency_pair}: {e}")
        except Exception as e: # Catch other potential errors like JSONDecodeError
            logging.error(f"Unexpected error during API exchange rate fetching for {currency_pair}: {e}")
    else:
        logging.warning("Exchange Rate API Key is missing! Cannot fetch live rates.")
        # Don't count as failure if key is missing, just proceed to fallback

    # --- 3. Update Failure Count and Circuit Breaker State ---
    if api_call_succeeded:
        # Reset failure count for this specific pair on success
        if api_failure_counts.get(currency_pair, 0) > 0: # Log only if it was failing before
             logging.info(f"API call successful for {currency_pair}. Resetting failure count.")
        api_failure_counts[currency_pair] = 0
    elif EXCHANGE_RATE_API_KEY: # Only count failures if we actually tried the API
        fail_count = api_failure_counts.get(currency_pair, 0) + 1
        api_failure_counts[currency_pair] = fail_count
        logging.warning(f"API call failed for {currency_pair}. Consecutive failure count: {fail_count} (Region context: {region_code})")
        # Check if threshold reached to open the breaker
        if fail_count >= API_FAILURE_THRESHOLD and not api_circuit_breaker_open:
             logging.error(f"API Failure threshold ({API_FAILURE_THRESHOLD}) reached for {currency_pair}. Opening circuit breaker for the rest of this update run!")
             api_circuit_breaker_open = True # OPEN THE BREAKER (affects subsequent calls in this run)

    # --- 4. Return Result or Fallback ---
    if api_rate is not None:
        return api_rate
    else:
        # Fallback needed (API failed, key missing, or breaker open)
        logging.warning(f"API rate unavailable for {currency_pair}. Attempting fallback rate.")
        if from_currency_upper in FALLBACK_RATES_TO_CNY:
            fallback_rate = FALLBACK_RATES_TO_CNY[from_currency_upper]
            logging.warning(f"Using FALLBACK rate {fallback_rate} for {currency_pair}")
            return fallback_rate
        else:
            # Log error if even fallback isn't available
            logging.error(f"No API rate and no fallback rate found for currency: {from_currency_upper}")
            return None
    
# --- Scraping Functions ---

# Utility to clean price strings (basic)
import re # Ensure re is imported
import unicodedata # Add for potential normalization

def clean_price(price_text, region_code=None): # Add region_code
    """Attempts to extract a float value and currency code from a price string,
       handling various separators and potential encoding issues. V5"""
    if not price_text: return None, None

    price_text_original = price_text
    currency_symbol_raw = None # Raw symbol extracted
    currency_code = None # Standard ISO code

    # --- Phase 1: Pre-cleaning ---
    normalized_text = price_text_original
    # Normalize unicode, remove common spaces, remove specific garbage
    try:
        normalized_text = unicodedata.normalize('NFC', normalized_text)
    except Exception:
        pass # Ignore normalization errors
    normalized_text = normalized_text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u2009', ' ')
    # Explicitly remove observed Mojibake/garbage sequences FIRST
    garbage_sequences = ['â¬Â', 'Â¬', 'Â', 'Ä'] # Add more if observed
    for seq in garbage_sequences:
         normalized_text = normalized_text.replace(seq, '')
    normalized_text = normalized_text.strip() # Strip again after replacements

    # --- Phase 2: Separate Number and Potential Symbol ---
    # Try to find the first part that looks like a number (allowing separators)
    # Regex: Start, optional non-digits (symbol), digits/separators, optional non-digits (symbol)
    # This is complex; let's try a simpler split based on first digit found
    match = re.search(r'(\d)', normalized_text) # Find first digit
    numeric_part = normalized_text
    potential_symbol_prefix = ''
    potential_symbol_suffix = ''

    if match:
        first_digit_index = match.start()
        potential_symbol_prefix = normalized_text[:first_digit_index].strip()
        numeric_part = normalized_text[first_digit_index:].strip() # Part starting with the first digit

        # Now check if there's a symbol *after* the number part
        # Find last digit/separator
        last_num_match = re.search(r'[\d.,]$', numeric_part)
        if last_num_match:
             # Anything after the last numeric character might be a suffix symbol
             # This separation is heuristic
             pass # numeric_part should contain number + suffix for now
        # Let's refine numeric part extraction to remove trailing non-numeric chars
        match_num_end = re.search(r'[\d.,]+', numeric_part) # Find the core number part
        if match_num_end:
             core_numeric = match_num_end.group(0)
             potential_symbol_suffix = numeric_part[len(core_numeric):].strip()
             numeric_part = core_numeric # Keep only the number-like part

    else:
        # No digits found, maybe it's "Free"?
        if "free" in normalized_text.lower() or "gratis" in normalized_text.lower():
             # Try to map currency based on region for context, but price is 0
             currency_code = map_currency(None, region_code)
             return 0.0, currency_code
        # Otherwise, cannot parse
        logging.warning(f"No digits found in price text: '{price_text_original}'")
        return None, None

    # Determine the best guess for the symbol (prefix usually more reliable)
    currency_symbol_raw = potential_symbol_prefix if potential_symbol_prefix else potential_symbol_suffix


    # --- Phase 3: Number Cleaning & Separator Standardization ---
    # Remove spaces used as thousands separators
    numeric_part = numeric_part.replace(' ', '')

    last_comma = numeric_part.rfind(',')
    last_dot = numeric_part.rfind('.')

    if last_comma > last_dot: # Comma is decimal
        numeric_part = numeric_part.replace('.', '').replace(',', '.')
    elif last_dot > last_comma: # Dot is decimal
        numeric_part = numeric_part.replace(',', '')
    else: # Only dots or only commas or neither
        if last_dot != -1 and last_comma == -1 and numeric_part.count('.') > 1:
             numeric_part = numeric_part.replace('.', '')
        elif last_comma != -1 and last_dot == -1 and numeric_part.count(',') > 1:
             numeric_part = numeric_part.replace(',', '')
        elif last_comma != -1 and last_dot == -1 and numeric_part.count(',') == 1:
             numeric_part = numeric_part.replace(',', '.')

    # --- Phase 4: Conversion & Currency Mapping ---
    try:
        final_num_str = re.sub(r'[^\d.]', '', numeric_part)
        if final_num_str.count('.') > 1:
             final_num_str = final_num_str.replace('.', '')
        if not final_num_str: raise ValueError("Numeric string empty after cleaning")

        price = float(final_num_str)

        # Get standard currency code using symbol AND region
        currency_code = map_currency(currency_symbol_raw, region_code)

        return price, currency_code

    except ValueError as e:
        logging.warning(f"Could not parse price from final numeric string: '{final_num_str}' (derived from '{price_text_original}'). Error: {e}")
        # Try mapping currency even if price fails
        currency_code = map_currency(currency_symbol_raw, region_code)
        return None, currency_code
    except Exception as e:
        logging.error(f"Unexpected error in clean_price for '{price_text_original}': {e}")
        return None, None

# --- Ensure map_currency handles empty symbol gracefully and uses region ---
def map_currency(symbol, region_code):
    """Maps common symbols or uses region code to guess currency. V4"""
    symbol_cleaned = symbol.upper().strip() if symbol else "" # Clean symbol
    region_code_upper = region_code.upper() if region_code else None

    # Prioritize direct symbol mapping if symbol is valid
    symbol_map = {
        'HK$': 'HKD', 'R$': 'BRL', 'S/.': 'PEN', 'NZ$': 'NZD', 'zł': 'PLN', 'lei': 'RON',
        'FT': 'HUF', 'лв': 'BGN', 'KR': 'SEK', # Default kr to SEK, region corrects below
        'Kč': 'CZK', 'KÄ': 'CZK', '₪': 'ILS', '﷼': 'SAR', 'TL': 'TRY', '₺': 'TRY', 'âº': 'TRY',
        'p.': 'RUB', '฿': 'THB', 'à¸¿': 'THB', '₦': 'NGN', '₫': 'VND', 'Ä': 'VND',
        'USD': 'USD', '$': 'USD', 'CNY': 'CNY', '¥': 'CNY', '￥': 'CNY', 'RMB': 'CNY',
        'EUR': 'EUR', '€': 'EUR', 'â¬': 'EUR', 'GBP': 'GBP', '£': 'GBP', 'JPY': 'JPY',
        'CAD': 'CAD', 'AUD': 'AUD', 'INR': 'INR', '₹': 'INR', 'RUB': 'RUB', '₽': 'RUB',
        'KRW': 'KRW', '₩': 'KRW', 'CHF': 'CHF', 'SGD': 'SGD', 'MXN': 'MXN',
        'ZAR': 'ZAR', 'R': 'ZAR', 'NOK': 'NOK', 'DKK': 'DKK', 'SEK': 'SEK', 'PLN': 'PLN',
        'ILS': 'ILS', 'QAR': 'QAR', 'SAR': 'SAR', 'AED': 'AED', 'HKD': 'HKD', 'PHP': 'PHP',
        '₱': 'PHP', 'IDR': 'IDR', 'RP': 'IDR', 'MYR': 'MYR', 'RM': 'MYR',
        'THB': 'THB', 'VND': 'VND',
    }
    if symbol_cleaned and symbol_cleaned in symbol_map:
        # Correct known ambiguities using region if available
        if symbol_cleaned in ['¥', '￥'] and region_code_upper == 'JP': return 'JPY'
        # Add correction for kr based on region
        if symbol_cleaned == 'KR':
             if region_code_upper == 'DK': return 'DKK'
             if region_code_upper == 'NO': return 'NOK'
             # Default to SEK if region is SE or unknown but symbol is kr
             if region_code_upper == 'SE' or not region_code_upper: return 'SEK'
        # Default $ based on region
        if symbol_cleaned == '$':
            if region_code_upper == 'CA': return 'CAD'
            if region_code_upper == 'AU': return 'AUD'
            if region_code_upper == 'SG': return 'SGD'
            if region_code_upper == 'MX': return 'MXN'
            # ... other $ regions ...
            if region_code_upper == 'US': return 'USD' # Explicit US default for $
        # Rial correction
        if symbol_cleaned == '﷼' and region_code_upper == 'QA': return 'QAR' # Default was SAR

        # Return the direct symbol mapping if no ambiguity correction needed
        return symbol_map[symbol_cleaned]


    # Fallback to region code IF symbol mapping failed OR symbol was empty/ambiguous
    region_map = { 'VN': 'VND', 'NL': 'EUR', # Ensure NL -> EUR is present
                  # ... (rest of the comprehensive region_map from previous step) ...
                   'US': 'USD', 'CN': 'CNY', 'JP': 'JPY', 'GB': 'GBP', 'DE': 'EUR', 'FR': 'EUR', 'AU': 'AUD', 'CA': 'CAD', 'IN': 'INR', 'BR': 'BRL', 'TR': 'TRY', 'NG': 'NGN', 'MX': 'MXN', 'KR': 'KRW', 'HK': 'HKD', 'SG': 'SGD', 'IT': 'EUR', 'ES': 'EUR', 'RU': 'RUB', 'CH': 'CHF', 'NZ': 'NZD', 'SE': 'SEK', 'NO': 'NOK', 'DK': 'DKK', 'PL': 'PLN', 'ZA': 'ZAR', 'AE': 'AED', 'SA': 'SAR', 'ID': 'IDR', 'MY': 'MYR', 'TH': 'THB', 'PH': 'PHP', 'CL': 'CLP', 'CO': 'COP', 'PE': 'PEN', 'AR': 'ARS', 'IL': 'ILS', 'EG': 'EGP', 'IE': 'EUR', 'AT': 'EUR', 'BE': 'EUR', 'PT': 'EUR', 'FI': 'EUR', 'GR': 'EUR', 'CZ': 'CZK', 'HU': 'HUF', 'TW': 'TWD', 'RO': 'RON', 'BG': 'BGN', 'HR': 'EUR', 'QA': 'QAR', 'KZ': 'KZT', 'TZ': 'TZS', 'PK': 'PKR', 'CY': 'EUR', 'EE': 'EUR', 'LV': 'EUR', 'LT': 'EUR', 'LU': 'EUR', 'MT': 'EUR', 'SK': 'EUR', 'SI': 'EUR', 'BH': 'USD', 'BY': 'USD', 'IS': 'USD', 'AL': 'USD', 'AM': 'USD', 'GE': 'USD', 'MD': 'USD', 'KG': 'USD', 'TJ': 'USD', 'UZ': 'USD', 'ZM': 'USD', 'ZW': 'USD', 'SN': 'USD', 'UG': 'USD', 'KE': 'USD', 'GH': 'USD', 'CM': 'USD', 'CI': 'USD', 'BJ': 'USD', 'NP': 'USD', 'KH': 'USD', 'SR': 'USD', 'BB': 'USD', 'BS': 'USD',
                 }
    if region_code_upper and region_code_upper in region_map:
        logging.info(f"Using region map for region '{region_code_upper}' as symbol '{symbol}' was insufficient.")
        return region_map[region_code_upper]

    # Final failure
    logging.warning(f"Could not determine currency from symbol '{symbol}' or region '{region_code}'")
    return None

def scrape_icloud_prices():
    """Scrapes iCloud+ prices from the Apple Support page (Revised Parsing Logic V2)."""
    logging.info("Attempting to scrape iCloud+ prices from support page...")
    url = "https://support.apple.com/en-us/108047"
    # { "Tier": [ {region_code, currency, price}, ... ] }
    scraped_data = {"iCloud+": {}}
    tier_map = {"50GB": "50GB", "200GB": "200GB", "2TB": "2TB", "6TB": "6TB", "12TB": "12TB"}

    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        main_container = soup.select_one('div#sections')
        if not main_container:
            logging.error("Could not find main container ('div#sections'). Update selector.")
            return None

        # Find all region headers (h3 with id like nasalac, emea, ap)
        region_headers = main_container.select('h3[id]')

        if not region_headers:
             logging.error("Could not find region headers (h3 with id). Check page structure.")
             return None

        current_country_name = None
        current_currency = None
        current_region_code = None

        # Iterate through each region section defined by headers
        for header in region_headers:
            logging.info(f"Processing region section: {header.get_text(strip=True)}")
            # Iterate through paragraph siblings *after* this header
            for sibling in header.find_next_siblings():
                # Stop if we hit the next region header or irrelevant tag type
                if sibling.name == 'h3':
                    break
                # Only process paragraph tags with class 'gb-paragraph'
                if sibling.name != 'p' or not sibling.has_attr('class') or 'gb-paragraph' not in sibling.get('class', []):
                    continue # Skip notes, divs, other paragraphs etc.

                p_tag = sibling
                p_text = p_tag.get_text(strip=True)
                b_tag = p_tag.find('b')

                # --- Stricter Identification Logic ---
                # Regex to identify country lines like "Country Name (CODE)" or "Country Name" (with optional footnote)
                # Allows more characters in country names, requires start/end match
                country_regex = r'^([\w\s.,/\'-]+?)\s*(?:\((\w{3})\))?\s*(?:<sup>[\d,]+</sup>)?$'
                country_match = re.fullmatch(country_regex, p_text) # Use fullmatch for stricter line matching

                # Condition 1: Is it a country definition line?
                # It should NOT contain a <b> tag AND the regex should match a plausible name.
                is_country_line = False
                if not b_tag and country_match:
                    country_name_extracted = country_match.group(1).strip()
                    # Basic sanity check: avoid matching things like footnotes directly
                    if len(country_name_extracted) > 2 and not country_name_extracted[0].isdigit() and '(' not in country_name_extracted[-3:]: # Avoid matching footnote text starting with numbers or ending with (CODE) only
                        is_country_line = True
                        currency_code_extracted = country_match.group(2)

                        current_country_name = country_name_extracted # Update context
                        current_region_code = map_country_to_code(current_country_name) # Map to code
                        if currency_code_extracted:
                            current_currency = currency_code_extracted.upper()
                        else:
                            current_currency = map_currency_for_icloud(current_country_name, current_region_code)
                            # Apply defaults if needed ONLY IF mapping failed
                            if current_country_name in ["Armenia", "Belarus", "Iceland", "Albania"] and not current_currency:
                                current_currency = "USD"
                                logging.info(f"Applying USD default for {current_country_name}")

                        if current_region_code and current_currency:
                             logging.info(f"Context Updated -> Country: {current_country_name}, Region: {current_region_code}, Currency: {current_currency}")
                        else:
                             logging.warning(f"Context partially updated for '{p_text}' -> Country: {current_country_name}, Region: {current_region_code}, Currency: {current_currency} (Mapping may have failed for this line)")
                             # Don't reset context, assume previous country context persists until a new one is found

                # Condition 2: Is it a price line for the current context?
                # It SHOULD contain a <b> tag matching a tier, AND we MUST have valid current context.
                elif b_tag and current_country_name and current_currency and current_region_code:
                    tier_text = b_tag.get_text(strip=True)
                    if tier_text in tier_map:
                        tier = tier_map[tier_text]

                        # --- Revised Price String Extraction ---
                        price_string = ""
                        # Method 1: Get text directly following the </b> tag
                        if b_tag.next_sibling and isinstance(b_tag.next_sibling, str):
                             # Get the immediate text sibling, strip leading/trailing whitespace AND colons
                             price_string = b_tag.next_sibling.strip().lstrip(':').strip()

                        # Method 2: (Fallback or Alternative) Get all text in parent <p> after <b>
                        # This might be safer if there are unexpected nodes
                        if not price_string:
                             try:
                                 # Concatenate all text nodes following the <b> tag within the <p>
                                 following_text = "".join(node.get_text(strip=True) for node in b_tag.find_next_siblings(string=True))
                                 price_string = following_text.strip().lstrip(':').strip()
                             except Exception:
                                 # If getting text fails somehow, fall back to empty string
                                 price_string = ""
                                 logging.warning(f"Could not extract price string using find_next_siblings for {tier}/{current_country_name}")


                        # --- End Revised Extraction ---

                        if price_string:
                            # Pass region_code, but we primarily trust current_currency here
                            price, _symbol_guess = clean_price(price_string, current_region_code)

                            if price is not None:
                                # ... (rest of the data appending logic) ...
                                if tier not in scraped_data["iCloud+"]: scraped_data["iCloud+"][tier] = []
                                if current_currency:
                                     scraped_data["iCloud+"][tier].append({
                                         "region": current_region_code,
                                         "currency": current_currency,
                                         "price": price
                                     })
                                else:
                                     logging.warning(f"Skipping price for {tier}/{current_country_name} because currency could not be determined.")
                            # else: Price parsing failed, warning logged in clean_price
                        else:
                             # This warning should now only trigger if extraction truly failed
                             logging.warning(f"Found tier '{tier}' for {current_country_name} but price string was empty or missing after processing.")
        if not scraped_data["iCloud+"]:
            logging.warning("Parsing finished, but no valid iCloud+ price data was extracted. Check parsing logic and selectors against current page structure.")
            return {"iCloud+": {}}

        logging.info(f"Successfully scraped and processed iCloud+ data structure. Found {len(scraped_data['iCloud+'])} tiers with prices.")
        return scraped_data

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching iCloud+ URL {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"Error parsing iCloud+ page {url}: {e}", exc_info=True)
        return None

# --- Helper function map_country_to_code needs robust footnote removal ---
# 确保文件顶部有 import re

def map_country_to_code(country_name_raw):
    """
    Cleans the raw country name string extracted from HTML (removing footnotes, currency codes)
    and maps it to a 2-letter ISO region code. V3: More robust cleaning.
    """
    if not country_name_raw:
        return None

    normalized_name = country_name_raw.strip()
    original_for_log = normalized_name # Keep original after first strip

    # 1. Remove trailing <sup> footnote tags and their content first
    # Handles digits and commas within the tag
    normalized_name = re.sub(r'\s*<sup>[\d,]+</sup>$', '', normalized_name).strip()

    # 2. Remove trailing currency code in parentheses (e.g., " (USD)")
    normalized_name = re.sub(r'\s*\(\w{3}\)$', '', normalized_name).strip()

    # 3. Remove trailing digits OR digits-with-commas (like '4' or '2,3')
    # This regex should now reliably catch these cases at the end of the string
    # It looks for one or more digits or commas at the very end ($)
    normalized_name = re.sub(r'[\d,]+$', '', normalized_name).strip()

    # 4. Handle specific known name variations AFTER cleaning
    lookup_name = normalized_name
    if lookup_name == "Türkiye":
         lookup_name = "Türkiye" # Ensure exact match with map key
    # Add case-insensitive matching as a fallback if needed, but requires adjusting the map keys or lookup process.

    # --- The comprehensive name_map ---
    # Ensure keys here EXACTLY match the expected *cleaned* country names
    name_map = {
        'United States': 'US', 'Canada': 'CA', 'Mexico': 'MX', 'Brazil': 'BR', 'Chile': 'CL',
        'Colombia': 'CO', 'Peru': 'PE', 'Suriname': 'SR', 'Barbados': 'BB', 'Bahamas': 'BS',
        'Argentina': 'AR', 'United Kingdom': 'GB', 'Germany': 'DE', 'France': 'FR', 'Italy': 'IT',
        'Spain': 'ES', 'Netherlands': 'NL', 'Belgium': 'BE', 'Ireland': 'IE', 'Austria': 'AT',
        'Switzerland': 'CH', 'Sweden': 'SE', 'Norway': 'NO', 'Denmark': 'DK', 'Finland': 'FI',
        'Poland': 'PL', 'Czechia': 'CZ', 'Hungary': 'HU', 'Portugal': 'PT', 'Greece': 'GR',
        'Romania': 'RO', 'Bulgaria': 'BG', 'Croatia': 'HR', 'Iceland': 'IS', 'Belarus': 'BY',
        'Albania': 'AL', 'Armenia': 'AM', 'Moldova': 'MD', 'Russia': 'RU', 'Türkiye': 'TR',
        'Euro': 'EU', 'Cyprus': 'CY', 'Estonia': 'EE', 'Latvia': 'LV', 'Lithuania': 'LT',
        'Luxembourg': 'LU', 'Malta': 'MT', 'Slovakia': 'SK', 'Slovenia': 'SI', 'China mainland': 'CN',
        'Japan': 'JP', 'Republic of Korea': 'KR', 'Australia': 'AU', 'New Zealand': 'NZ',
        'Hong Kong': 'HK', 'Singapore': 'SG', 'Taiwan': 'TW', 'Thailand': 'TH', 'Malaysia': 'MY',
        'Philippines': 'PH', 'Vietnam': 'VN', 'Indonesia': 'ID', 'India': 'IN', 'Kazakhstan': 'KZ',
        'Kyrgyzstan': 'KG', 'Nepal': 'NP', 'Pakistan': 'PK', 'Tajikistan': 'TJ', 'Uzbekistan': 'UZ',
        'Cambodia': 'KH', 'United Arab Emirates': 'AE', 'Saudi Arabia': 'SA', 'Israel': 'IL',
        'Egypt': 'EG', 'South Africa': 'ZA', 'Nigeria': 'NG', 'Qatar': 'QA', 'Bahrain': 'BH',
        'Georgia': 'GE', 'Ivory Coast': 'CI', 'Cameroon': 'CM', 'Ghana': 'GH', 'Kenya': 'KE',
        'Senegal': 'SN', 'Tanzania': 'TZ', 'Uganda': 'UG', 'Zambia': 'ZM', 'Zimbabwe': 'ZW', 'Benin': 'BJ',
    }

    code = name_map.get(lookup_name)
    if not code:
         # Log the original (after initial strip) AND the final cleaned name attempt
         logging.warning(f"Could not map country name '{original_for_log}' (Cleaned Attempt: '{lookup_name}') to region code.")
    return code

def map_currency_for_icloud(country_name, region_code):
    # Priority to region_code based map (more reliable)
    # Ensure region_map is comprehensive
    region_map = {
        'US': 'USD', 'CN': 'CNY', 'JP': 'JPY', 'GB': 'GBP', 'DE': 'EUR', 'FR': 'EUR', 'AU': 'AUD', 'CA': 'CAD',
        'IN': 'INR', 'BR': 'BRL', 'TR': 'TRY', 'NG': 'NGN', 'MX': 'MXN', 'KR': 'KRW', 'HK': 'HKD', 'SG': 'SGD',
        'IT': 'EUR', 'ES': 'EUR', 'RU': 'RUB', 'CH': 'CHF', 'NZ': 'NZD', 'SE': 'SEK', 'NO': 'NOK', 'DK': 'DKK',
        'PL': 'PLN', 'ZA': 'ZAR', 'AE': 'AED', 'SA': 'SAR', 'ID': 'IDR', 'MY': 'MYR', 'TH': 'THB', 'VN': 'VND',
        'PH': 'PHP', 'CL': 'CLP', 'CO': 'COP', 'PE': 'PEN', 'AR': 'ARS', 'IL': 'ILS', 'EG': 'EGP', 'IE': 'EUR',
        'AT': 'EUR', 'BE': 'EUR', 'PT': 'EUR', 'FI': 'EUR', 'GR': 'EUR', 'CZ': 'CZK', 'HU': 'HUF', 'TW': 'TWD',
        'RO': 'RON', 'BG': 'BGN', 'HR': 'EUR', 'QA': 'QAR', 'KZ': 'KZT', 'TZ': 'TZS', 'PK': 'PKR', 'CY': 'EUR',
        'EE': 'EUR', 'LV': 'EUR', 'LT': 'EUR', 'LU': 'EUR', 'MT': 'EUR', 'SK': 'EUR', 'SI': 'EUR',
        # Countries listed explicitly with USD on the page
        'BH': 'USD', 'BY': 'USD', 'IS': 'USD', 'AL': 'USD', 'AM': 'USD', 'GE': 'USD', 'MD': 'USD', 'KG': 'USD',
        'TJ': 'USD', 'UZ': 'USD', 'ZM': 'USD', 'ZW': 'USD', 'SN': 'USD', 'UG': 'USD', 'KE': 'USD', 'GH': 'USD',
        'CM': 'USD', 'CI': 'USD', 'BJ': 'USD', 'NP': 'USD', 'KH': 'USD', 'SR': 'USD', 'BB': 'USD', 'BS': 'USD',
    }
    # Use uppercase for reliable matching
    region_code_upper = region_code.upper() if region_code else None

    if region_code_upper and region_code_upper in region_map:
        return region_map[region_code_upper]

    # Fallback based on country name - less reliable, add specific cases if needed
    # name = country_name.lower() if country_name else ""
    # if 'euro' in name: return 'EUR'

    logging.warning(f"Could not map iCloud country '{country_name}' (code: {region_code}) to currency code using region map.")
    return None # Explicitly return None if no mapping found

def scrape_app_store_price(app_name, region_code, app_id):
    """
    Scrapes In-App Purchase prices from an App Store page.
    V3: Uses CSS class selectors for list items directly, ignoring header text.
    """
    logging.info(f"Attempting to scrape {app_name} in {region_code} (ID: {app_id}) using class selectors...")
    url = f"https://apps.apple.com/{region_code}/app/id{app_id}"
    # { "Plan Name": [ {region, currency, price}, ... ] }
    app_data = {}
    # Flag to track if we found list items but failed to parse price/currency
    found_items_but_parsing_failed = False

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36',
            'Accept-Language': f'{region_code}-{region_code.upper()},en-US;q=0.9,en;q=0.8'
        }
        response = requests.get(url, headers=headers, timeout=20)
        logging.info(f"Request URL: {url} | Status Code: {response.status_code}")
        response.raise_for_status() # Check for HTTP errors (like 404)

        soup = BeautifulSoup(response.text, 'html.parser')

        # --- New Logic: Directly find all list items based on class ---
        # Target 'li' elements with the class 'list-with-numbers__item'
        items = soup.select('li.list-with-numbers__item')

        if not items:
            # If the primary selector fails, maybe try looking for the container structure?
            # Example: Find the <dd class="information-list__item__definition"> containing the list
            dd_container = soup.select_one('dd.information-list__item__definition ol.list-with-numbers')
            if dd_container:
                items = dd_container.select('li.list-with-numbers__item')

        if not items:
            logging.warning(f"Could not find any elements matching 'li.list-with-numbers__item' for {app_name} in {region_code}. Structure might differ or no IAPs listed this way.")
            return None # Exit if no potential items found using primary selector

        logging.info(f"Found {len(items)} potential IAP list items using class selector for {app_name} in {region_code}")

        for item in items:
            # Extract plan name and price using specific class selectors within the item
            # Using select_one which returns None if not found, safer than direct access
            title_span_container = item.select_one('span.list-with-numbers__item__title')
            price_el = item.select_one('span.list-with-numbers__item__price')

            # The title might be nested further, e.g., inside another span
            title_el = title_span_container.select_one('span') if title_span_container else None

            if title_el and price_el:
                plan_name = title_el.get_text(strip=True)
                price_text = price_el.get_text(strip=True)

                if plan_name and price_text:
                # --- 检查这里：确保传递了 region_code ---
                    price, symbol = clean_price(price_text, region_code)
                    # map_currency 现在也接收 region_code 作为辅助
                    currency = map_currency(symbol, region_code)

                    if price is not None and currency is not None:
                        found_valid_item = True
                        if plan_name not in app_data: app_data[plan_name] = []
                        app_data[plan_name].append({
                            "region": region_code.upper(),
                            "currency": currency, # 使用 map_currency 的结果
                            "price": price
                        })
                    else:
                        found_items_but_parsing_failed = True
                        logging.warning(f"Parsed plan '{plan_name}' but failed to parse price/currency '{price_text}' or map currency for {app_name} in {region_code}")            # else: Silently ignore list items that don't contain both expected title and price elements

        # --- Return Data ---
        if app_data:
             # We successfully extracted at least one complete price entry
             logging.info(f"Successfully extracted {len(app_data)} plans with prices for {app_name} in {region_code} using class selectors.")
             return {app_name: app_data}
        elif found_items_but_parsing_failed:
             # We found list items, but none resulted in valid price/currency after parsing
             logging.warning(f"Found IAP items via class selectors but failed to parse/map price/currency for all of them for {app_name} in {region_code}.")
             return None
        else:
             # We either found no list items, or the items found didn't contain recognizable title/price elements
             logging.warning(f"No list items found or no valid IAP data could be extracted using class selectors for {app_name} in {region_code}.")
             return None

    # --- Exception Handling (same as before) ---
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
        logging.error(f"Error parsing App Store page for {app_name} in {region_code}: {e}", exc_info=True)
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
    global api_failure_counts, api_circuit_breaker_open # Declare intent to modify globals

    logging.info("--- Starting Price Update Task ---")
    # --- Reset API failure state at the start of each run ---
    api_failure_counts = {}
    api_circuit_breaker_open = False
    logging.info(f"API Circuit Breaker status reset (OPEN = {api_circuit_breaker_open}). Failure counts cleared.")
    # --- End Reset ---

    all_scraped_data = []
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

    # 3. Convert prices and prepare for DB insertion, EXCLUDING specific regions
    logging.info(f"Starting currency conversion phase... Total items scraped: {len(all_scraped_data)}")
    db_rows = []
    excluded_regions = {'EG', 'PH', 'CZ'} # Set of regions to exclude

    for item in all_scraped_data:
        # --- Add region exclusion check ---
        region_code = item.get("region") # Get region code safely
        if region_code in excluded_regions:
            logging.debug(f"Skipping excluded region: {region_code} for {item.get('app_name')}/{item.get('plan_name')}")
            continue # Skip to the next item in all_scraped_data
        # --- End region exclusion check ---

        # Proceed only if region is not excluded
        price_cny = convert_to_cny(item["price"], item["currency"], region_code) # Pass region_code

        if price_cny is not None:
            # Ensure required fields are present before appending
            if all(k in item for k in ("app_name", "plan_name", "region", "currency", "price")):
                 db_rows.append((
                     item["app_name"],
                     item["plan_name"],
                     item["region"], # Should not be EG or PH here
                     item["currency"],
                     item["price"],
                     price_cny,
                     now_utc # last_updated timestamp
                 ))
            else:
                 logging.warning(f"Skipping DB insert for item due to missing keys: {item}")

        else:
            # Log warning if conversion failed for a non-excluded region
            logging.warning(f"Skipping DB insert for {item.get('app_name','?')}/{item.get('plan_name','?')}/{item.get('region','?')} due to failed CNY conversion.")

    logging.info(f"Prepared {len(db_rows)} rows for database insertion after exclusions and conversions.")

    # 4. Database Update
    conn = get_db_connection()
    if not conn:
        logging.error("Cannot update database - connection failed.")
        return # Exit if DB connection fails

    # --- SAFETY CHECK: Only proceed if new data was actually collected ---
    if not db_rows:
         logging.warning("No valid data scraped or converted in this run. Database will NOT be cleared or updated.")
         conn.close()
         return # Exit without changing the database

    # If we have new data, proceed with clearing and inserting
    try:
        cursor = conn.cursor()
        logging.info(f"Attempting to replace existing data with {len(db_rows)} new price records...")

        # --- MODIFIED STRATEGY: Delete ALL old data first ---
        delete_query = "DELETE FROM prices;"
        cursor.execute(delete_query)
        # Log differently as we are deleting all, regardless of which apps were updated
        logging.info(f"Deleted ALL old records from 'prices' table (Rows affected: {cursor.rowcount})")
        # --- End Deletion Modification ---

        # Insert new data using executemany
        insert_query = """
            INSERT INTO prices (app_name, plan_name, region, currency, price, price_cny, last_updated)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cursor.executemany(insert_query, db_rows)
        logging.info(f"Inserted {cursor.rowcount} new price records.")

        conn.commit() # Commit transaction
        logging.info("Database update successful (replaced old data).")

    except Exception as e:
        logging.error(f"Database update error during DELETE/INSERT: {e}", exc_info=True)
        conn.rollback() # Rollback on error
    finally:
        # Ensure connection is closed even if commit/rollback failed
        if 'cursor' in locals() and cursor:
             cursor.close()
        if conn:
             conn.close()

    # Store last updated time globally (simple approach)
    global last_update_timestamp
    last_update_timestamp = now_utc.isoformat() # Use timestamp from start of update process
    logging.info(f"--- Price Update Task Finished at {datetime.datetime.now(datetime.timezone.utc).isoformat()} ---")

# --- 修改 convert_to_cny 以接收并传递 region_code ---
def convert_to_cny(price, currency, region_code=None): # Add region_code
    """Converts a price from its local currency to CNY using the fetched rate."""
    currency_upper = currency.upper() if currency else None
    if not currency_upper: return None
    if currency_upper == "CNY":
        return price
    # 将 region_code 传递给 get_exchange_rate
    rate = get_exchange_rate(currency_upper, "CNY", region_code) # Pass region_code here
    if rate:
        return round(price * rate, 2)
    # Log warning if rate is None AFTER trying fallback
    # logging.warning(f"Could not get exchange rate for {currency_upper} to CNY (Region: {region_code}).") # Moved logging inside get_exchange_rate
    return None

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
    """API endpoint to retrieve ALL available filtered and sorted prices."""
    app_name = flask.request.args.get('app')
    plan_name = flask.request.args.get('plan') # Optional plan filter

    if not app_name:
        return jsonify({"error": "Missing 'app' parameter"}), 400

    # Fetch data from database using your existing function
    db_results, update_time = query_prices_from_db(app_name, plan_name if plan_name else None)

    # Handle database query failure
    if not db_results and update_time is None:
         return jsonify({"error": "Failed to query database"}), 500

    # Handle case where query succeeded but no data found
    if not db_results:
        logging.info(f"No price data found in DB for app='{app_name}', plan='{plan_name}'")
        return jsonify({
            "app": app_name,
            "plan_filter": plan_name,
            "prices": [], # Return empty list
            "last_updated": update_time or "N/A"
        })

    # --- Sorting Logic (Still useful for display) ---
    # Filter out entries where CNY conversion might have failed (price_cny is None)
    valid_prices = [p for p in db_results if p.get("price_cny") is not None]

    # Sort ALL valid prices by CNY price (ascending)
    sorted_prices = sorted(valid_prices, key=lambda x: x["price_cny"])
    # --- End Sorting Logic ---

    # --- The "Top 10 + US/CN" filtering logic is REMOVED ---

    # --- Prepare final list to return ---
    # We now return the *entire* sorted list of valid prices
    final_list_to_return = sorted_prices

    # Convert datetime objects in results to ISO strings for JSON compatibility
    for item in final_list_to_return:
         if isinstance(item.get('last_updated'), datetime.datetime):
              item['last_updated'] = item['last_updated'].isoformat()

    logging.info(f"API returning {len(final_list_to_return)} prices for app='{app_name}', plan='{plan_name}' (Full List)")

    return jsonify({
        "app": app_name,
        "plan_filter": plan_name,
        "prices": final_list_to_return, # Return the FULL sorted list
        "last_updated": update_time or "N/A"
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