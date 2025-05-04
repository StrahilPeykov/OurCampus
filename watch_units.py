import time
import random
import requests
import json
import os
import sqlite3
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException, NoSuchElementException
import logging
import threading

# Configuration
URL = "https://book-ourcampus.securerc.co.uk/onlineleasing/ourcampus-amsterdam-diemen/floorplans.aspx"

# Time window configurations
HIGH_PRIORITY_MIN = 45  # seconds (45 seconds minimum)
HIGH_PRIORITY_MAX = 75  # seconds (75 seconds maximum)
MEDIUM_PRIORITY_MIN = 90  # seconds (1.5 minutes minimum)
MEDIUM_PRIORITY_MAX = 150  # seconds (2.5 minutes maximum)
NORMAL_CHECK_INTERVAL_MIN = 3  # minutes (minimum for normal priority)
NORMAL_CHECK_INTERVAL_MAX = 8  # minutes (maximum for normal priority)

# Define priority time windows
HIGH_PRIORITY_WINDOWS = [
    {"day": 2, "start_hour": 12, "start_minute": 0, "end_hour": 15, "end_minute": 30},  # Wednesday 12pm-3:30pm
]

MEDIUM_PRIORITY_WINDOWS = [
    {"day": 2, "start_hour": 15, "start_minute": 30, "end_hour": 19, "end_minute": 0},  # Wednesday 3:30pm-7pm
    {"day": 1, "start_hour": 13, "start_minute": 0, "end_hour": 19, "end_minute": 0},  # Tuesday 1pm-7pm
    {"day": 3, "start_hour": 13, "start_minute": 0, "end_hour": 19, "end_minute": 0},  # Thursday 1pm-7pm
    {"day": 4, "start_hour": 13, "start_minute": 0, "end_hour": 19, "end_minute": 0},  # Friday 1pm-7pm
]

# Telegram notification settings
TELEGRAM_TOKEN = "7943333387:AAHg0p5-JaGsNEHQBLUPhSrp4ny-b49_2gc"
TELEGRAM_CHAT_ID = "2008207882"

# Global variables for status tracking
start_time = None
last_check_time = None
next_check_time = None
last_command_update_id = 0  # Track the last processed command ID

# Create logs directory if it doesn't exist
os.makedirs("logs", exist_ok=True)

# Set up logging (console and file)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/apartment_monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Enhanced list of user agents to rotate
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Edg/91.0.864.59',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
]

# Optional: Proxy list (UNCOMMENT AND ADD YOUR PROXIES)
"""
PROXIES = [
    # Add your proxies here in the format "http://ip:port" or "http://username:password@ip:port"
    # "http://123.45.67.89:8080",
]
"""

def init_database():
    """Initialize SQLite database for tracking apartment availability history."""
    conn = sqlite3.connect('apartment_history.db')
    c = conn.cursor()
    
    # Create availability history table
    c.execute('''
    CREATE TABLE IF NOT EXISTS availability_history (
        timestamp TEXT,
        check_id TEXT,
        apartment_type TEXT,
        availability_text TEXT,
        button_text TEXT,
        available INTEGER
    )
    ''')
    
    # Create notifications table
    c.execute('''
    CREATE TABLE IF NOT EXISTS notifications (
        timestamp TEXT,
        message TEXT,
        sent_successfully INTEGER
    )
    ''')
    
    # Create a stats table
    c.execute('''
    CREATE TABLE IF NOT EXISTS stats (
        date TEXT,
        num_checks INTEGER,
        num_availability_found INTEGER,
        errors INTEGER
    )
    ''')
    
    conn.commit()
    return conn

def log_availability(conn, check_id, apartment_type, availability_text, button_text, available):
    """Log apartment availability to database."""
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute(
        "INSERT INTO availability_history VALUES (?, ?, ?, ?, ?, ?)",
        (timestamp, check_id, apartment_type, availability_text, button_text, 1 if available else 0)
    )
    conn.commit()

def log_notification(conn, message, sent_successfully):
    """Log notification to database."""
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute(
        "INSERT INTO notifications VALUES (?, ?, ?)",
        (timestamp, message, 1 if sent_successfully else 0)
    )
    conn.commit()

def update_stats(conn, found_availability=False, error=False):
    """Update daily statistics."""
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Check if we have a record for today
    c.execute("SELECT * FROM stats WHERE date = ?", (today,))
    record = c.fetchone()
    
    if record:
        # Update existing record
        c.execute(
            "UPDATE stats SET num_checks = num_checks + 1, num_availability_found = num_availability_found + ?, errors = errors + ? WHERE date = ?",
            (1 if found_availability else 0, 1 if error else 0, today)
        )
    else:
        # Create new record
        c.execute(
            "INSERT INTO stats VALUES (?, ?, ?, ?)",
            (today, 1, 1 if found_availability else 0, 1 if error else 0)
        )
    
    conn.commit()

def setup_driver():
    """Set up and return a Chrome WebDriver instance with enhanced anti-detection measures."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in headless mode (no browser UI)
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    
    # Randomize user agent to avoid detection
    user_agent = random.choice(USER_AGENTS)
    chrome_options.add_argument(f"--user-agent={user_agent}")
    
    # Additional settings to make the browser look more like a regular user
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    # Add random viewport size (common desktop resolutions)
    viewports = [
        "1920,1080", "1366,768", "1536,864", "1440,900", 
        "1280,720", "1600,900", "1280,800", "1280,1024"
    ]
    chrome_options.add_argument(f"--window-size={random.choice(viewports)}")
    
    # Optional: Add proxy if available (UNCOMMENT IF USING PROXIES)
    """
    if 'PROXIES' in globals() and PROXIES:
        proxy = random.choice(PROXIES)
        chrome_options.add_argument(f'--proxy-server={proxy}')
        logger.info(f"Using proxy: {proxy}")
    """
    
    # Set timezone to match common European settings
    chrome_options.add_argument("--timezone=Europe/Amsterdam")
    
    # Add language settings common for the target site
    chrome_options.add_argument("--lang=en-GB,en-US;q=0.9,en;q=0.8,nl;q=0.7")
    
    try:
        # Try to use webdriver manager to automatically download the driver
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    except Exception as e:
        logger.warning(f"Failed to use webdriver_manager: {e}. Falling back to local chromedriver.")
        # Fallback to local chromedriver if webdriver_manager is not available
        driver = webdriver.Chrome(options=chrome_options)
    
    # Additional settings to avoid detection
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    # Add additional navigator properties to appear more like a real browser
    driver.execute_script("""
    if (navigator.plugins) {
        Object.defineProperty(navigator, 'plugins', {
            get: function() { return [1, 2, 3, 4, 5]; }
        });
    }
    
    if (navigator.languages) {
        Object.defineProperty(navigator, 'languages', {
            get: function() { return ['en-GB', 'en', 'nl']; }
        });
    }
    """)
    
    # Set cookies to appear like a returning visitor
    driver.get(URL)  # Need to navigate to the domain before setting cookies
    
    # Add some common cookies
    cookie_names = ["_ga", "_gid", "visitor_id", "session_id"]
    for name in cookie_names:
        value = ''.join(random.choices('0123456789abcdef', k=16))
        
        # Random expiration between 1 day and 1 year from now
        expiry = datetime.now() + timedelta(days=random.randint(1, 365))
        expiry_seconds = int(expiry.timestamp())
        
        cookie = {
            'name': name,
            'value': value,
            'domain': '.securerc.co.uk',  # Domain should match the site
            'path': '/',
            'expiry': expiry_seconds,
            'secure': True
        }
        
        try:
            driver.add_cookie(cookie)
        except Exception as e:
            # Just log and continue if adding a cookie fails
            logger.debug(f"Failed to add cookie {name}: {e}")
    
    return driver

def wait_for_element(driver, by, selector, timeout=30, poll_frequency=0.5):
    """Wait for an element to be present with better error handling."""
    try:
        return WebDriverWait(driver, timeout, poll_frequency).until(
            EC.presence_of_element_located((by, selector))
        )
    except TimeoutException:
        logger.error(f"Element not found: {selector}")
        return None
    except Exception as e:
        logger.error(f"Error waiting for element {selector}: {e}")
        return None

def safely_click(driver, element, retries=3):
    """Attempt to safely click an element with retries for common issues."""
    for attempt in range(retries):
        try:
            # First try to scroll the element into view
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            time.sleep(0.5)  # Give the page time to settle after scrolling
            
            # Try to click it normally first
            element.click()
            return True
        except StaleElementReferenceException:
            if attempt < retries - 1:
                logger.debug(f"StaleElementReference, retrying click... (attempt {attempt+1})")
                time.sleep(1)
                continue
            else:
                logger.error("Element became stale, max retries exceeded")
                return False
        except Exception as e:
            # If normal click fails, try JavaScript click
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception as js_error:
                if attempt < retries - 1:
                    logger.debug(f"Click failed, retrying... (attempt {attempt+1}): {e}")
                    time.sleep(1)
                    continue
                else:
                    logger.error(f"Failed to click element after {retries} attempts: {js_error}")
                    return False
    return False

def add_random_delay():
    """Add a slightly randomized delay to appear more human-like."""
    base_delay = random.uniform(1, 3)
    # Occasionally add a longer delay
    if random.random() < 0.2:  # 20% chance
        base_delay += random.uniform(1, 3)
    time.sleep(base_delay)

def get_check_interval():
    """
    Determine check interval based on current time.
    Returns check interval in seconds with randomization.
    """
    now = datetime.now()
    current_day = now.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
    current_hour = now.hour
    current_minute = now.minute
    
    # Check if current time falls within any high priority window
    for window in HIGH_PRIORITY_WINDOWS:
        if (current_day == window["day"] and
            (current_hour > window["start_hour"] or 
             (current_hour == window["start_hour"] and current_minute >= window["start_minute"])) and
            (current_hour < window["end_hour"] or
             (current_hour == window["end_hour"] and current_minute <= window["end_minute"]))):
            
            # Randomize within high priority range
            interval = random.randint(HIGH_PRIORITY_MIN, HIGH_PRIORITY_MAX)
            logger.info(f"🔥 HIGH PRIORITY TIME WINDOW - checking every {interval} seconds")
            return interval
    
    # Check if current time falls within any medium priority window
    for window in MEDIUM_PRIORITY_WINDOWS:
        if (current_day == window["day"] and
            (current_hour > window["start_hour"] or 
             (current_hour == window["start_hour"] and current_minute >= window["start_minute"])) and
            (current_hour < window["end_hour"] or
             (current_hour == window["end_hour"] and current_minute <= window["end_minute"]))):
            
            # Randomize within medium priority range
            interval = random.randint(MEDIUM_PRIORITY_MIN, MEDIUM_PRIORITY_MAX) 
            logger.info(f"⚡ MEDIUM PRIORITY TIME WINDOW - checking every {interval} seconds")
            return interval
    
    # Otherwise use normal priority with randomized interval (in minutes, convert to seconds)
    interval = random.randint(NORMAL_CHECK_INTERVAL_MIN * 60, NORMAL_CHECK_INTERVAL_MAX * 60)
    logger.info(f"🕙 NORMAL PRIORITY TIME - checking every {interval//60} minutes")
    return interval

def check_availability(driver, db_conn):
    """Check for apartment availability on the website with improved error handling and data storage."""
    global last_check_time
    
    logger.info("Checking for apartment availability...")
    last_check_time = datetime.now()  # Update the last check time
    check_id = datetime.now().strftime('%Y%m%d%H%M%S')  # Unique ID for this check
    
    try:
        driver.get(URL)
        add_random_delay()
        
        # Wait for the main container to load
        container = wait_for_element(driver, By.ID, "floorPlanDataContainer", timeout=45)
        if not container:
            logger.error("Main container not found - page may have changed structure")
            update_stats(db_conn, error=True)
            return []
        
        # Make some random mouse movements to appear more human-like
        driver.execute_script("""
            var event = new MouseEvent('mousemove', {
                'view': window,
                'bubbles': true,
                'cancelable': true,
                'clientX': Math.floor(Math.random() * window.innerWidth),
                'clientY': Math.floor(Math.random() * window.innerHeight)
            });
            document.dispatchEvent(event);
        """)
        
        apartments_available = []
        
        # Try multiple selector approaches to find the floor plan tabs
        # This makes the script more resilient to website changes
        tab_selector_options = [
            {"by": By.CSS_SELECTOR, "selector": "a[href='#FP_Detail_1100004']"},
            {"by": By.XPATH, "selector": "//a[contains(@href, '#FP_Detail_1100004')]"},
            {"by": By.XPATH, "selector": "//li[contains(@class, 'FPTabLi')]/a[1]"},
            {"by": By.XPATH, "selector": "//ul[@id='floorplansLink']/li/a[1]"}
        ]
        
        # Check the first apartment type (1 Person)
        try:
            # Try different selectors until one works
            one_person_tab = None
            for selector_option in tab_selector_options:
                try:
                    one_person_tab = driver.find_element(selector_option["by"], selector_option["selector"])
                    if one_person_tab:
                        logger.debug(f"Found 1-person tab using selector: {selector_option['selector']}")
                        break
                except NoSuchElementException:
                    continue
            
            if not one_person_tab:
                raise Exception("Could not find 1-person apartment tab using any selector")
            
            # Click the tab to show the apartment details
            safely_click(driver, one_person_tab)
            add_random_delay()
            
            # Try multiple approaches to get availability and button text
            try:
                # First attempt with specific selectors
                availability_text = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100004']//div[@class='availability-count']").text.strip()
            except Exception:
                try:
                    # Try a more general approach
                    availability_elements = driver.find_elements(By.CLASS_NAME, "availability-count")
                    availability_text = availability_elements[0].text.strip() if availability_elements else "Unknown"
                except Exception as e:
                    logger.warning(f"Could not get availability text using any method: {e}")
                    availability_text = "Unknown"
            
            try:
                # First attempt with specific selectors
                button_text = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100004']//button[contains(@class, 'btn')]").text.strip()
            except Exception:
                try:
                    # Try a more general approach
                    button_elements = driver.find_elements(By.XPATH, "//button[contains(@class, 'btn')]")
                    button_text = button_elements[0].text.strip() if button_elements else "Unknown"
                except Exception as e:
                    logger.warning(f"Could not get button text using any method: {e}")
                    button_text = "Unknown"
            
            logger.info(f"1 Person Apartment - Availability text: '{availability_text}', Button text: '{button_text}'")
            
            # Log this information regardless of availability
            log_availability(db_conn, check_id, "1 Person Apartment", availability_text, button_text, 
                           button_text != "CONTACT US" and button_text != "Contact Us")
            
            # Consider apartment available if button text is NOT "CONTACT US"
            if button_text and button_text != "CONTACT US" and button_text != "Contact Us":
                apartments_available.append(f"1 Person Apartment - Button says: {button_text}")
        except Exception as e:
            logger.error(f"Error checking 1-person apartment: {e}")
            log_availability(db_conn, check_id, "1 Person Apartment", "Error", "Error", False)
        
        # Similar logic for 2-person apartment with enhanced error handling
        try:
            # Reset selectors for the second apartment type
            tab_selector_options = [
                {"by": By.CSS_SELECTOR, "selector": "a[href='#FP_Detail_1100005']"},
                {"by": By.XPATH, "selector": "//a[contains(@href, '#FP_Detail_1100005')]"},
                {"by": By.XPATH, "selector": "//li[contains(@class, 'FPTabLi')]/a[2]"},
                {"by": By.XPATH, "selector": "//ul[@id='floorplansLink']/li/a[2]"}
            ]
            
            # Try different selectors until one works
            two_person_tab = None
            for selector_option in tab_selector_options:
                try:
                    two_person_tab = driver.find_element(selector_option["by"], selector_option["selector"])
                    if two_person_tab:
                        logger.debug(f"Found 2-person tab using selector: {selector_option['selector']}")
                        break
                except NoSuchElementException:
                    continue
            
            if not two_person_tab:
                raise Exception("Could not find 2-person apartment tab using any selector")
            
            # Click the tab to show the apartment details
            safely_click(driver, two_person_tab)
            add_random_delay()
            
            # Try multiple approaches to get availability and button text
            try:
                # First attempt with specific selectors
                availability_text = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100005']//div[@class='availability-count']").text.strip()
            except Exception:
                try:
                    # Try a more general approach
                    availability_elements = driver.find_elements(By.CLASS_NAME, "availability-count")
                    availability_text = availability_elements[1].text.strip() if len(availability_elements) > 1 else "Unknown"
                except Exception as e:
                    logger.warning(f"Could not get availability text using any method: {e}")
                    availability_text = "Unknown"
            
            try:
                # First attempt with specific selectors
                button_text = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100005']//button[contains(@class, 'btn')]").text.strip()
            except Exception:
                try:
                    # Try a more general approach
                    button_elements = driver.find_elements(By.XPATH, "//button[contains(@class, 'btn')]")
                    button_text = button_elements[1].text.strip() if len(button_elements) > 1 else "Unknown"
                except Exception as e:
                    logger.warning(f"Could not get button text using any method: {e}")
                    button_text = "Unknown"
                    
            logger.info(f"2 Person Apartment - Availability text: '{availability_text}', Button text: '{button_text}'")
            
            # Log this information regardless of availability
            log_availability(db_conn, check_id, "2 Person Apartment", availability_text, button_text, 
                           button_text != "CONTACT US" and button_text != "Contact Us")
            
            # Consider apartment available if button text is NOT "CONTACT US"
            if button_text and button_text != "CONTACT US" and button_text != "Contact Us":
                apartments_available.append(f"2 Person Apartment - Button says: {button_text}")
        except Exception as e:
            logger.error(f"Error checking 2-person apartment: {e}")
            log_availability(db_conn, check_id, "2 Person Apartment", "Error", "Error", False)
        
        # Update stats
        update_stats(db_conn, found_availability=bool(apartments_available))
        
        return apartments_available
        
    except TimeoutException:
        logger.error("Timeout waiting for page to load")
        update_stats(db_conn, error=True)
        return []
    except WebDriverException as e:
        logger.error(f"WebDriver error: {e}")
        update_stats(db_conn, error=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error during availability check: {e}")
        update_stats(db_conn, error=True)
        return []

def send_telegram_notification(message, db_conn=None):
    """Send a notification message via Telegram with enhanced error handling."""
    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    # Maximum retries for sending notification
    max_retries = 3
    success = False
    
    for attempt in range(max_retries):
        try:
            response = requests.post(telegram_api_url, data=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Telegram notification sent successfully!")
                success = True
                break
            else:
                logger.error(f"Failed to send Telegram notification. Status code: {response.status_code}, Response: {response.text}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in 5 seconds... (attempt {attempt+1}/{max_retries})")
                    time.sleep(5)
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error sending Telegram notification: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in 5 seconds... (attempt {attempt+1}/{max_retries})")
                time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram notification: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in 5 seconds... (attempt {attempt+1}/{max_retries})")
                time.sleep(5)
    
    # Log notification to database if provided
    if db_conn:
        log_notification(db_conn, message, success)
    
    return success

def test_telegram(db_conn=None):
    """Test the Telegram notification system."""
    logger.info("Testing Telegram notification...")
    test_message = "🏠 OurCampus Monitor: This is a test message. The script is running correctly!"
    success = send_telegram_notification(test_message, db_conn)
    if success:
        logger.info("Telegram test successful!")
    else:
        logger.error("Telegram test failed!")
    return success

def process_telegram_commands(db_conn=None):
    """Process incoming Telegram commands with improved error handling."""
    global last_command_update_id
    
    try:
        # Get updates from Telegram
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": last_command_update_id + 1, "timeout": 1},
            timeout=10
        )
        
        if response.status_code == 200:
            updates = response.json()
            
            if updates.get("ok") and updates.get("result"):
                for update in updates["result"]:
                    # Update the last processed update ID
                    last_command_update_id = max(last_command_update_id, update["update_id"])
                    
                    # Check if this is a message with text
                    if "message" in update and "text" in update["message"]:
                        chat_id = update["message"]["chat"]["id"]
                        message_text = update["message"]["text"].lower()
                        
                        # Only process if it's from our chat ID
                        if str(chat_id) == TELEGRAM_CHAT_ID:
                            # Handle /last command
                            if message_text == "/last":
                                handle_last_command(chat_id, db_conn)
                            
                            # Handle /status command
                            elif message_text == "/status":
                                handle_status_command(chat_id, db_conn)
                            
                            # Handle /help command
                            elif message_text == "/help":
                                handle_help_command(chat_id, db_conn)
                                
                            # Handle /stats command
                            elif message_text == "/stats":
                                handle_stats_command(chat_id, db_conn)
                            
                            # Handle /restart command
                            elif message_text == "/restart":
                                handle_restart_command(chat_id, db_conn)
                                
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error processing Telegram commands: {e}")
    except Exception as e:
        logger.error(f"Error processing Telegram commands: {e}")

def handle_last_command(chat_id, db_conn=None):
    """Handle the /last command: Show when the script last checked for apartments."""
    global last_check_time
    
    if last_check_time:
        time_ago = datetime.now() - last_check_time
        minutes_ago = time_ago.total_seconds() // 60
        
        message = f"✅ <b>Last Apartment Check</b>\n\n"
        message += f"Last checked: {last_check_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        message += f"({int(minutes_ago)} minutes ago)"
        
        # Add most recent availability from database if available
        if db_conn:
            try:
                c = db_conn.cursor()
                c.execute("""
                    SELECT apartment_type, availability_text, button_text, available 
                    FROM availability_history 
                    ORDER BY timestamp DESC 
                    LIMIT 4
                """)
                results = c.fetchall()
                
                if results:
                    message += "\n\n<b>Latest availability info:</b>\n"
                    for result in results:
                        apt_type, avail_text, btn_text, is_available = result
                        status = "✅ AVAILABLE" if is_available else "❌ Not available"
                        message += f"• {apt_type}: {status} ({btn_text})\n"
            except Exception as e:
                logger.error(f"Error getting last availability from database: {e}")
    else:
        message = "❓ No checks have been performed yet"
    
    send_telegram_notification(message, db_conn)

def handle_status_command(chat_id, db_conn=None):
    """Handle the /status command: Show full status of the monitoring script."""
    global last_check_time, start_time, next_check_time
    
    uptime = datetime.now() - start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
    
    message = f"🏠 <b>OurCampus Monitor Status</b>\n\n"
    message += f"• Script is running: ✅\n"
    message += f"• Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    message += f"• Uptime: {uptime_str}\n"
    
    if last_check_time:
        time_ago = datetime.now() - last_check_time
        minutes_ago = time_ago.total_seconds() // 60
        message += f"• Last check: {last_check_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        message += f"• Time since last check: {int(minutes_ago)} minutes\n"
    else:
        message += f"• Last check: None yet\n"
    
    if next_check_time:
        if datetime.now() > next_check_time:
            message += f"• Next check: In progress or coming shortly\n"
        else:
            time_until = next_check_time - datetime.now()
            seconds_until = time_until.total_seconds()
            message += f"• Next check: {next_check_time.strftime('%H:%M:%S')}\n"
            message += f"• Time until next check: {int(seconds_until)} seconds\n"
    
    # Get current priority window
    now = datetime.now()
    current_day = now.weekday()
    current_hour = now.hour
    current_minute = now.minute
    
    # Check if current time falls within any high priority window
    in_high_priority = False
    for window in HIGH_PRIORITY_WINDOWS:
        if (current_day == window["day"] and
            (current_hour > window["start_hour"] or 
             (current_hour == window["start_hour"] and current_minute >= window["start_minute"])) and
            (current_hour < window["end_hour"] or
             (current_hour == window["end_hour"] and current_minute <= window["end_minute"]))):
            in_high_priority = True
            break
    
    # Check if current time falls within any medium priority window
    in_medium_priority = False
    if not in_high_priority:
        for window in MEDIUM_PRIORITY_WINDOWS:
            if (current_day == window["day"] and
                (current_hour > window["start_hour"] or 
                 (current_hour == window["start_hour"] and current_minute >= window["start_minute"])) and
                (current_hour < window["end_hour"] or
                 (current_hour == window["end_hour"] and current_minute <= window["end_minute"]))):
                in_medium_priority = True
                break
    
    if in_high_priority:
        message += f"• Current priority: 🔥 HIGH (checking every {HIGH_PRIORITY_MIN/60}-{HIGH_PRIORITY_MAX/60} minutes)\n"
    elif in_medium_priority:
        message += f"• Current priority: ⚡ MEDIUM (checking every {MEDIUM_PRIORITY_MIN/60}-{MEDIUM_PRIORITY_MAX/60} minutes)\n"
    else:
        message += f"• Current priority: 🕙 NORMAL (checking every {NORMAL_CHECK_INTERVAL_MIN}-{NORMAL_CHECK_INTERVAL_MAX} minutes)\n"
    
    # Add database stats if available
    if db_conn:
        try:
            c = db_conn.cursor()
            
            # Total checks today
            today = datetime.now().strftime('%Y-%m-%d')
            c.execute("SELECT num_checks, num_availability_found, errors FROM stats WHERE date = ?", (today,))
            today_stats = c.fetchone()
            
            if today_stats:
                checks, found, errors = today_stats
                message += f"\n<b>Today's Stats:</b>\n"
                message += f"• Checks performed: {checks}\n"
                message += f"• Availabilities found: {found}\n"
                message += f"• Errors encountered: {errors}\n"
            
            # All-time stats
            c.execute("SELECT COUNT(*) FROM availability_history")
            all_checks = c.fetchone()[0]
            
            c.execute("SELECT COUNT(*) FROM availability_history WHERE available = 1")
            all_available = c.fetchone()[0]
            
            message += f"\n<b>All-time Stats:</b>\n"
            message += f"• Total checks: {all_checks}\n"
            message += f"• Total availabilities: {all_available}\n"
            
        except Exception as e:
            logger.error(f"Error getting stats from database: {e}")
    
    send_telegram_notification(message, db_conn)

def handle_stats_command(chat_id, db_conn=None):
    """Handle the /stats command: Show detailed statistics."""
    if not db_conn:
        message = "⚠️ Database not available for statistics."
        send_telegram_notification(message)
        return
    
    try:
        c = db_conn.cursor()
        
        # Get total statistics
        c.execute("SELECT COUNT(*) FROM availability_history")
        total_checks = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM availability_history WHERE available = 1")
        total_availabilities = c.fetchone()[0]
        
        c.execute("SELECT COUNT(DISTINCT date) FROM stats")
        days_running = c.fetchone()[0]
        
        # Get last 5 days of stats
        c.execute("""
            SELECT date, num_checks, num_availability_found, errors
            FROM stats
            ORDER BY date DESC
            LIMIT 5
        """)
        daily_stats = c.fetchall()
        
        # Get availability by apartment type
        c.execute("""
            SELECT apartment_type, COUNT(*) as total_checks, SUM(available) as times_available
            FROM availability_history
            GROUP BY apartment_type
        """)
        apartment_stats = c.fetchall()
        
        # Create the message
        message = f"📊 <b>OurCampus Monitor Statistics</b>\n\n"
        message += f"<b>Overview:</b>\n"
        message += f"• Days monitored: {days_running}\n"
        message += f"• Total checks: {total_checks}\n"
        message += f"• Total availabilities found: {total_availabilities}\n"
        message += f"• Availability rate: {round(total_availabilities/total_checks*100, 2)}%\n\n"
        
        message += f"<b>By Apartment Type:</b>\n"
        for apt_type, checks, available in apartment_stats:
            rate = round(available/checks*100, 2) if checks > 0 else 0
            message += f"• {apt_type}: {available}/{checks} ({rate}%)\n"
        
        message += f"\n<b>Last 5 Days:</b>\n"
        for date, checks, found, errors in daily_stats:
            message += f"• {date}: {checks} checks, {found} available, {errors} errors\n"
        
        send_telegram_notification(message, db_conn)
    except Exception as e:
        logger.error(f"Error generating stats: {e}")
        message = f"⚠️ Error generating statistics: {e}"
        send_telegram_notification(message, db_conn)

def handle_help_command(chat_id, db_conn=None):
    """Handle the /help command: Show available commands."""
    message = f"🏠 <b>OurCampus Monitor Help</b>\n\n"
    message += f"Available commands:\n"
    message += f"• /last - Show last check time and latest availability\n"
    message += f"• /status - Show full status of the monitor\n"
    message += f"• /stats - Show detailed statistics\n"
    message += f"• /restart - Prompt for restart (if script is set up with restart capability)\n"
    message += f"• /help - Show this help message"
    
    send_telegram_notification(message, db_conn)

def handle_restart_command(chat_id, db_conn=None):
    """Handle the /restart command: Suggest manual restart procedures."""
    message = f"⚠️ <b>Restart Request</b>\n\n"
    message += f"The script doesn't have auto-restart capability.\n\n"
    message += f"To restart the script manually:\n"
    message += f"1. Connect to your server\n"
    message += f"2. Stop the current process\n"
    message += f"3. Run 'python3 apartment_monitor.py'\n\n"
    message += f"You can also set up a systemd service or cron job to automatically restart the script if it crashes."
    
    send_telegram_notification(message, db_conn)

def main(test_mode=False):
    """Main function to monitor apartment availability with improved database support."""
    global start_time, next_check_time
    
    start_time = datetime.now()  # Track when the script started
    logger.info("Starting OurCampus Amsterdam Diemen apartment monitor")
    
    # Initialize database
    try:
        db_conn = init_database()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        db_conn = None
    
    # Test Telegram notification first
    if not test_telegram(db_conn):
        logger.error("Failed to send test notification. Exiting.")
        return
    
    # Send startup notification with timestamp
    startup_message = f"🏠 <b>OurCampus Monitor Started</b> 🏠\n\n" + \
                     f"Monitoring started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n" + \
                     f"Priority-based checking:\n" + \
                     f"• High Priority: {HIGH_PRIORITY_MIN/60}-{HIGH_PRIORITY_MAX/60} minutes (Wednesdays 12:00-15:30)\n" + \
                     f"• Medium Priority: {MEDIUM_PRIORITY_MIN/60}-{MEDIUM_PRIORITY_MAX/60} minutes (Weekday afternoons)\n" + \
                     f"• Normal Priority: {NORMAL_CHECK_INTERVAL_MIN}-{NORMAL_CHECK_INTERVAL_MAX} minutes (All other times)\n\n" + \
                     f"I'll notify you as soon as apartments become available!\n\n" + \
                     f"Available commands:\n" + \
                     f"• /last - Show last check time\n" + \
                     f"• /status - Show full status\n" + \
                     f"• /stats - Show detailed statistics\n" + \
                     f"• /help - Show available commands"
    
    send_telegram_notification(startup_message, db_conn)
    
    if test_mode:
        logger.info("Running in TEST MODE - will check once and exit")
    
    driver = None
    
    try:
        driver = setup_driver()
        last_notified = set()  # Keep track of apartments we've already notified about
        command_check_time = 0  # Track when we last checked for commands
        browser_restart_counter = 0  # Counter for browser restarts
        
        while True:
            try:
                available_apartments = check_availability(driver, db_conn)
                
                if available_apartments:
                    current_available = set(available_apartments)
                    new_available = current_available - last_notified
                    
                    if new_available:
                        logger.info(f"New apartments available! {new_available}")
                        
                        message = "🎉 <b>OurCampus Apartments Available!</b> 🎉\n\n"
                        message += "The following apartments are now available:\n\n"
                        for apt in new_available:
                            message += f"• {apt}\n"
                        message += f"\n🔗 <a href='{URL}'>Click here to apply now!</a>"
                        
                        send_telegram_notification(message, db_conn)
                        last_notified = current_available
                    else:
                        logger.info("No new apartments since last check.")
                else:
                    logger.info("No apartments available currently.")
                    # Only reset notification tracking if we've previously found something
                    if last_notified:
                        # Notify about apartments no longer available
                        message = "ℹ️ <b>OurCampus Update</b> ℹ️\n\n"
                        message += "Previously available apartments are no longer listed."
                        send_telegram_notification(message, db_conn)
                        last_notified = set()
                
                # Exit after one check if in test mode
                if test_mode:
                    logger.info("Test completed. Exiting.")
                    break
                
                # Check for Telegram commands every 10 seconds
                current_time = time.time()
                if current_time - command_check_time > 10:
                    process_telegram_commands(db_conn)
                    command_check_time = current_time
                
                # Determine the next check interval based on current time
                check_interval = get_check_interval()
                next_check_time = datetime.now()
                next_check_time = next_check_time.replace(microsecond=0)  # Remove microseconds for cleaner display
                next_timestamp = next_check_time.timestamp() + check_interval
                next_check_time = datetime.fromtimestamp(next_timestamp)
                
                logger.info(f"Next check at {next_check_time.strftime('%H:%M:%S')}")
                
                # Sleep in shorter intervals while checking for commands
                remaining_sleep = check_interval
                while remaining_sleep > 0:
                    sleep_interval = min(10, remaining_sleep)  # Sleep for 10 seconds at a time to check for commands
                    time.sleep(sleep_interval)
                    remaining_sleep -= sleep_interval
                    
                    # Check for commands during long sleep periods
                    process_telegram_commands(db_conn)
                
                # Increment browser restart counter
                browser_restart_counter += 1
                
                # Restart the browser periodically to avoid memory issues
                if browser_restart_counter >= 10:  # Restart every 10 checks
                    logger.info("Scheduled browser restart to avoid detection/memory issues")
                    browser_restart_counter = 0
                    driver.quit()
                    driver = setup_driver()
                
            except Exception as e:
                logger.error(f"Error during check: {e}")
                time.sleep(60)  # Wait a minute before trying again after an error
                
                # Restart the browser after errors
                try:
                    if driver:
                        driver.quit()
                    driver = setup_driver()
                    browser_restart_counter = 0  # Reset counter after restart
                except Exception as browser_error:
                    logger.error(f"Error restarting browser: {browser_error}")
                    time.sleep(60)
                
    finally:
        if driver:
            driver.quit()
        
        if db_conn:
            db_conn.close()
            
        logger.info("OurCampus monitor stopped")

if __name__ == "__main__":
    # Run in continuous mode (set to True for test mode)
    main(test_mode=False)