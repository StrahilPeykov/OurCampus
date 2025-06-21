import time
import random
import requests
import json
import os
import sqlite3
import psutil
import argparse
import webbrowser
import subprocess
import platform
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
from pathlib import Path
import sys
import io

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    # Fallback for older Python versions
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

# Try to import dotenv (but don't fail if it's not installed)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("dotenv not installed. Environment variables must be set manually.")

# Configuration
URL = "https://book-ourcampus.securerc.co.uk/onlineleasing/ourcampus-amsterdam-diemen/floorplans.aspx"

# SPEED MODE: Ultra-fast checking intervals
SPEED_MODE_INTERVAL_MIN = 0.5  # 500ms minimum
SPEED_MODE_INTERVAL_MAX = 1.5  # 1.5s maximum

# Time window configurations with sensible defaults (can be overridden via .env)
HIGH_PRIORITY_MIN = int(os.getenv("HIGH_PRIORITY_MIN", 20))  # seconds
HIGH_PRIORITY_MAX = int(os.getenv("HIGH_PRIORITY_MAX", 40))  # seconds
MEDIUM_PRIORITY_MIN = int(os.getenv("MEDIUM_PRIORITY_MIN", 45))  # seconds
MEDIUM_PRIORITY_MAX = int(os.getenv("MEDIUM_PRIORITY_MAX", 75))  # seconds
NORMAL_CHECK_INTERVAL_MIN = int(os.getenv("NORMAL_CHECK_INTERVAL_MIN", 1))  # minutes
NORMAL_CHECK_INTERVAL_MAX = int(os.getenv("NORMAL_CHECK_INTERVAL_MAX", 4))  # minutes

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

# Apartment booking URLs for speed mode
APARTMENT_URLS = {
    "1 Person Apartment": "https://book-ourcampus.securerc.co.uk/onlineleasing/ourcampus-amsterdam-diemen/availableunits.aspx?myOlePropertyId=182358&MoveInDate=undefined&t=0.34374300842116357&floorPlans=1100004",
    "2 Person Apartment": "https://book-ourcampus.securerc.co.uk/onlineleasing/ourcampus-amsterdam-diemen/availableunits.aspx?myOlePropertyId=182358&MoveInDate=undefined&t=0.34374300842116357&floorPlans=1100005"
}

# Telegram notification settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Database settings
DB_DIR = os.getenv("DB_DIR", "data")
DB_FILE = os.getenv("DB_FILE", "apartment_history.db")
DATABASE_PATH = os.path.join(DB_DIR, DB_FILE)

# Health check settings
HEALTH_CHECK_ENABLED = os.getenv("HEALTH_CHECK_ENABLED", "false").lower() == "true"
HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", 8080))

# Global variables for status tracking
start_time = None
last_check_time = None
next_check_time = None
last_command_update_id = 0  # Track the last processed command ID
health_metrics = {}  # For storing health metrics
speed_mode = False
apartments_found_this_session = set()

# Create necessary directories
os.makedirs("logs", exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)

# Configure console output for Windows
if sys.platform.startswith('win'):
    # Use UTF-8 for console output on Windows
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Set up logging (console and file) with Windows-compatible encoding
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"logs/watch_units_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# List of user agents to rotate
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Edg/91.0.864.59',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
]

def init_database():
    """Initialize SQLite database for tracking apartment availability history."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
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
    
    # Create health metrics table
    c.execute('''
    CREATE TABLE IF NOT EXISTS health_metrics (
        timestamp TEXT,
        cpu_percent REAL,
        memory_percent REAL,
        uptime_seconds INTEGER,
        checks_since_start INTEGER,
        errors_since_start INTEGER
    )
    ''')
    
    conn.commit()
    return conn

def log_availability(conn, check_id, apartment_type, availability_text, button_text, available):
    """Log apartment availability to database."""
    if not conn:
        return
        
    try:
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        c.execute(
            "INSERT INTO availability_history VALUES (?, ?, ?, ?, ?, ?)",
            (timestamp, check_id, apartment_type, availability_text, button_text, 1 if available else 0)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error logging availability: {e}")

def log_notification(conn, message, sent_successfully):
    """Log notification to database."""
    if not conn:
        return
        
    try:
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        c.execute(
            "INSERT INTO notifications VALUES (?, ?, ?)",
            (timestamp, message, 1 if sent_successfully else 0)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error logging notification: {e}")

def update_stats(conn, found_availability=False, error=False):
    """Update daily statistics."""
    if not conn:
        return
        
    try:
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
    except Exception as e:
        logger.error(f"Error updating stats: {e}")

def log_health_metrics(conn):
    """Log system health metrics to the database."""
    if not conn:
        return
    
    try:
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        
        # Get CPU and memory usage
        cpu_percent = psutil.cpu_percent()
        memory_percent = psutil.virtual_memory().percent
        
        # Calculate uptime
        uptime_seconds = (datetime.now() - start_time).total_seconds()
        
        # Count checks and errors since start
        c.execute("SELECT COUNT(*) FROM availability_history WHERE timestamp > ?", (start_time.isoformat(),))
        checks_since_start = c.fetchone()[0]
        
        c.execute("SELECT SUM(errors) FROM stats WHERE date >= ?", (start_time.strftime('%Y-%m-%d'),))
        errors_since_start = c.fetchone()[0] or 0
        
        # Update global health metrics
        global health_metrics
        health_metrics = {
            "timestamp": timestamp,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "uptime_seconds": uptime_seconds,
            "checks_since_start": checks_since_start,
            "errors_since_start": errors_since_start
        }
        
        # Insert into database
        c.execute(
            "INSERT INTO health_metrics VALUES (?, ?, ?, ?, ?, ?)",
            (timestamp, cpu_percent, memory_percent, uptime_seconds, checks_since_start, errors_since_start)
        )
        
        # Keep only the last 1000 records to prevent database bloat
        c.execute("DELETE FROM health_metrics WHERE rowid NOT IN (SELECT rowid FROM health_metrics ORDER BY timestamp DESC LIMIT 1000)")
        
        conn.commit()
    except Exception as e:
        logger.error(f"Error logging health metrics: {e}")

def setup_driver(headless=True):
    """Setup Selenium WebDriver with flexible configurations. Works both locally and on servers."""
    chrome_options = Options()
    
    if headless:
        chrome_options.add_argument("--headless")
    
    # Common options for better performance
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1366,768")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    
    # Performance optimizations
    chrome_options.add_argument("--disable-images")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    
    # Rotate user agent
    user_agent = random.choice(USER_AGENTS)
    chrome_options.add_argument(f"--user-agent={user_agent}")
    
    # Get ChromeDriver path from environment (if set)
    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
    
    try:
        # Try different approaches to create the driver
        if chromedriver_path and os.path.exists(chromedriver_path):
            # Use specified chromedriver path
            driver = webdriver.Chrome(service=Service(chromedriver_path), options=chrome_options)
            logger.info(f"Using specified ChromeDriver at {chromedriver_path}")
        else:
            try:
                # Try to use webdriver manager
                from webdriver_manager.chrome import ChromeDriverManager
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
                logger.info("Using ChromeDriver from webdriver_manager")
            except Exception as e:
                logger.warning(f"Failed to use webdriver_manager: {e}. Trying common paths.")
                # Try common Linux paths
                for path in ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver"]:
                    if os.path.exists(path):
                        driver = webdriver.Chrome(service=Service(path), options=chrome_options)
                        logger.info(f"Using ChromeDriver from {path}")
                        break
                else:
                    # Fallback to local chromedriver
                    driver = webdriver.Chrome(options=chrome_options)
                    logger.info("Using default ChromeDriver")
    except Exception as e:
        logger.error(f"Error creating Chrome driver: {e}")
        raise
    
    # Set page load timeout to prevent hanging
    driver.set_page_load_timeout(30)
    
    return driver

def setup_speed_driver(headless=False):
    """Setup ultra-fast Chrome driver optimized for speed."""
    chrome_options = Options()
    
    if headless:
        chrome_options.add_argument("--headless")
    
    # Speed optimizations (but keep JavaScript enabled!)
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-images")
    # Don't disable JavaScript - we need it for the apartment tabs
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--aggressive-cache-discard")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--disable-sync")
    
    # Suppress GPU warnings
    chrome_options.add_argument("--disable-logging")
    chrome_options.add_argument("--log-level=3")
    
    # Minimal window size for speed
    chrome_options.add_argument("--window-size=800,600")
    
    # Random user agent
    user_agent = random.choice(USER_AGENTS)
    chrome_options.add_argument(f"--user-agent={user_agent}")
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        logger.info("Speed-optimized ChromeDriver created")
    except Exception as e:
        logger.error(f"Error creating Chrome driver: {e}")
        raise
    
    # Ultra-short timeouts for speed
    driver.set_page_load_timeout(15)  # Slightly longer to allow JS to load
    driver.implicitly_wait(3)  # Slightly longer for element finding
    
    return driver

def open_booking_page(apartment_type):
    """Open the apartment booking page in a NEW BROWSER INSTANCE (very noticeable)."""
    if apartment_type not in APARTMENT_URLS:
        logger.error(f"Unknown apartment type: {apartment_type}")
        return False
    
    url = APARTMENT_URLS[apartment_type]
    
    try:
        # Method 1: Use subprocess to start completely new browser instance
        system = platform.system().lower()
        
        if system == "windows":
            # Windows: Start new Chrome instance
            try:
                subprocess.Popen([
                    "start", 
                    "chrome", 
                    "--new-window",
                    "--start-maximized",
                    "--window-position=0,0",
                    url
                ], shell=True)
                logger.info(f"SUCCESS: Opened NEW BROWSER INSTANCE for {apartment_type}")
                return True
            except Exception as e:
                logger.warning(f"Chrome subprocess failed: {e}, trying alternative...")
                
                # Fallback: Try Edge
                try:
                    subprocess.Popen([
                        "start", 
                        "msedge", 
                        "--new-window",
                        "--start-maximized",
                        url
                    ], shell=True)
                    logger.info(f"SUCCESS: Opened NEW EDGE INSTANCE for {apartment_type}")
                    return True
                except Exception as e2:
                    logger.warning(f"Edge subprocess failed: {e2}, trying default browser...")
        
        elif system == "darwin":  # macOS
            try:
                subprocess.Popen([
                    "open", 
                    "-na", 
                    "Google Chrome", 
                    "--args", 
                    "--new-window",
                    "--start-maximized",
                    url
                ])
                logger.info(f"SUCCESS: Opened NEW BROWSER INSTANCE for {apartment_type}")
                return True
            except Exception as e:
                logger.warning(f"Chrome subprocess failed: {e}, trying Safari...")
                try:
                    subprocess.Popen(["open", "-a", "Safari", url])
                    logger.info(f"SUCCESS: Opened NEW SAFARI INSTANCE for {apartment_type}")
                    return True
                except Exception as e2:
                    logger.warning(f"Safari failed: {e2}, trying default browser...")
        
        else:  # Linux
            try:
                subprocess.Popen([
                    "google-chrome", 
                    "--new-window",
                    "--start-maximized",
                    url
                ])
                logger.info(f"SUCCESS: Opened NEW BROWSER INSTANCE for {apartment_type}")
                return True
            except Exception as e:
                logger.warning(f"Chrome subprocess failed: {e}, trying Firefox...")
                try:
                    subprocess.Popen(["firefox", "--new-window", url])
                    logger.info(f"SUCCESS: Opened NEW FIREFOX INSTANCE for {apartment_type}")
                    return True
                except Exception as e2:
                    logger.warning(f"Firefox failed: {e2}, trying default browser...")
        
        # Final fallback: Use webbrowser module (opens new window when possible)
        try:
            # Register a new browser instance that opens new windows
            webbrowser.open_new(url)  # open_new() opens new window instead of tab
            logger.info(f"SUCCESS: Opened booking page (fallback method) for {apartment_type}")
            return True
        except Exception as e:
            logger.error(f"All browser opening methods failed: {e}")
            return False
        
    except Exception as e:
        logger.error(f"Failed to open booking page: {e}")
        return False

def wait_for_element(driver, by, selector, timeout=15, poll_frequency=0.3):
    """Faster wait with lower timeout."""
    try:
        return WebDriverWait(driver, timeout, poll_frequency).until(
            EC.presence_of_element_located((by, selector))
        )
    except Exception as e:
        logger.error(f"Element not found: {selector}")
        return None

def safely_click(driver, element, retries=2):
    """Attempt to safely click an element with fewer retries for speed."""
    for attempt in range(retries):
        try:
            # Click directly without scrolling first to save time
            element.click()
            return True
        except StaleElementReferenceException:
            if attempt < retries - 1:
                time.sleep(0.5)
                continue
            else:
                return False
        except Exception as e:
            # If normal click fails, try JavaScript click
            try:
                driver.execute_script("arguments[0].click();", element)
                return True
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                else:
                    return False
    return False

def add_random_delay():
    """Shorter random delay for faster checks."""
    base_delay = random.uniform(0.3, 1.0)
    # Only occasionally add longer delay (10% chance)
    if random.random() < 0.1:
        base_delay += random.uniform(0.5, 1.5)
    time.sleep(base_delay)

def get_check_interval():
    """Determine check interval based on current time. Returns check interval in seconds with randomization."""
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
            logger.info(f"HIGH PRIORITY TIME WINDOW - checking every {interval} seconds")
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
            logger.info(f"MEDIUM PRIORITY TIME WINDOW - checking every {interval} seconds")
            return interval
    
    # Otherwise use normal priority with randomized interval (in minutes, convert to seconds)
    interval = random.randint(NORMAL_CHECK_INTERVAL_MIN * 60, NORMAL_CHECK_INTERVAL_MAX * 60)
    logger.info(f"NORMAL PRIORITY TIME - checking every {interval//60} minutes")
    return interval

def get_speed_interval():
    """Get check interval for speed mode."""
    return random.uniform(SPEED_MODE_INTERVAL_MIN, SPEED_MODE_INTERVAL_MAX)

def check_availability(driver, db_conn):
    """Check for apartment availability on the website with improved speed."""
    global last_check_time
    
    logger.info("Checking for apartment availability...")
    last_check_time = datetime.now()  # Update the last check time
    check_id = datetime.now().strftime('%Y%m%d%H%M%S')  # Unique ID for this check
    
    try:
        # Load the page directly
        driver.get(URL)
        
        # Wait for the main container to load with shorter timeout
        container = wait_for_element(driver, By.ID, "floorPlanDataContainer", timeout=20)
        if not container:
            logger.error("Main container not found - page may have changed structure")
            update_stats(db_conn, error=True)
            return []
        
        apartments_available = []
        
        # Try multiple selectors in order of specificity
        tab_selector_options = [
            {"by": By.CSS_SELECTOR, "selector": "a[href='#FP_Detail_1100004']"},
            {"by": By.XPATH, "selector": "//a[contains(@href, '#FP_Detail_1100004')]"},
            {"by": By.XPATH, "selector": "//li[contains(@class, 'FPTabLi')]/a[1]"}
        ]
        
        # Check the first apartment type (1 Person)
        try:
            # Try different selectors until one works
            one_person_tab = None
            for selector_option in tab_selector_options:
                try:
                    one_person_tab = driver.find_element(selector_option["by"], selector_option["selector"])
                    if one_person_tab:
                        break
                except NoSuchElementException:
                    continue
            
            if not one_person_tab:
                raise Exception("Could not find 1-person apartment tab")
            
            # Click the tab to show the apartment details
            safely_click(driver, one_person_tab)
            time.sleep(0.5)  # Short fixed delay
            
            # Try to get availability text and button text with faster direct selectors
            try:
                availability_text = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100004']//div[@class='availability-count']").text.strip()
            except Exception:
                availability_text = "Unknown"
            
            try:
                button_text = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100004']//button[contains(@class, 'btn')]").text.strip()
            except Exception:
                button_text = "Unknown"
            
            logger.info(f"1 Person Apartment - Button text: '{button_text}'")
            
            # Log to database in separate thread to avoid slowing down the main flow
            threading.Thread(
                target=log_availability,
                args=(db_conn, check_id, "1 Person Apartment", availability_text, button_text, 
                    button_text != "CONTACT US" and button_text != "Contact Us")
            ).start()
            
            # Consider apartment available if button text is NOT "CONTACT US"
            if button_text and button_text != "CONTACT US" and button_text != "Contact Us":
                apartments_available.append(f"1 Person Apartment - Button says: {button_text}")
        except Exception as e:
            logger.error(f"Error checking 1-person apartment: {e}")
            threading.Thread(
                target=log_availability,
                args=(db_conn, check_id, "1 Person Apartment", "Error", "Error", False)
            ).start()
        
        # Reset selectors for the second apartment type
        tab_selector_options = [
            {"by": By.CSS_SELECTOR, "selector": "a[href='#FP_Detail_1100005']"},
            {"by": By.XPATH, "selector": "//a[contains(@href, '#FP_Detail_1100005')]"},
            {"by": By.XPATH, "selector": "//li[contains(@class, 'FPTabLi')]/a[2]"}
        ]
        
        # Check the second apartment type (2 Person)
        try:
            # Try different selectors until one works
            two_person_tab = None
            for selector_option in tab_selector_options:
                try:
                    two_person_tab = driver.find_element(selector_option["by"], selector_option["selector"])
                    if two_person_tab:
                        break
                except NoSuchElementException:
                    continue
            
            if not two_person_tab:
                raise Exception("Could not find 2-person apartment tab")
            
            # Click the tab to show the apartment details
            safely_click(driver, two_person_tab)
            time.sleep(0.5)  # Short fixed delay
            
            # Try to get availability text and button text with faster direct selectors
            try:
                availability_text = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100005']//div[@class='availability-count']").text.strip()
            except Exception:
                availability_text = "Unknown"
            
            try:
                button_text = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100005']//button[contains(@class, 'btn')]").text.strip()
            except Exception:
                button_text = "Unknown"
            
            logger.info(f"2 Person Apartment - Button text: '{button_text}'")
            
            # Log to database in separate thread to avoid slowing down the main flow
            threading.Thread(
                target=log_availability,
                args=(db_conn, check_id, "2 Person Apartment", availability_text, button_text, 
                    button_text != "CONTACT US" and button_text != "Contact Us")
            ).start()
            
            # Consider apartment available if button text is NOT "CONTACT US"
            if button_text and button_text != "CONTACT US" and button_text != "Contact Us":
                apartments_available.append(f"2 Person Apartment - Button says: {button_text}")
        except Exception as e:
            logger.error(f"Error checking 2-person apartment: {e}")
            threading.Thread(
                target=log_availability,
                args=(db_conn, check_id, "2 Person Apartment", "Error", "Error", False)
            ).start()
        
        # Update stats in a thread to avoid slowing down main execution
        threading.Thread(
            target=update_stats,
            args=(db_conn, bool(apartments_available), False)
        ).start()
        
        # Log health metrics occasionally (20% of checks)
        if random.random() < 0.2:
            threading.Thread(
                target=log_health_metrics,
                args=(db_conn,)
            ).start()
        
        return apartments_available
        
    except TimeoutException:
        logger.error("Timeout waiting for page to load")
        threading.Thread(target=update_stats, args=(db_conn, False, True)).start()
        return []
    except WebDriverException as e:
        logger.error(f"WebDriver error: {e}")
        threading.Thread(target=update_stats, args=(db_conn, False, True)).start()
        return []
    except Exception as e:
        logger.error(f"Unexpected error during availability check: {e}")
        threading.Thread(target=update_stats, args=(db_conn, False, True)).start()
        return []

def check_availability_speed(driver):
    """Ultra-fast availability check optimized for speed."""
    global last_check_time
    
    logger.info("SPEED: Checking apartments...")
    last_check_time = datetime.now()
    
    try:
        # Load page with minimal timeout
        driver.get(URL)
        
        # Wait for container with short timeout
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.ID, "floorPlanDataContainer"))
            )
        except TimeoutException:
            logger.warning("Container not found quickly, continuing anyway...")
        
        apartments_available = []
        
        # Check 1 Person Apartment - try multiple approaches
        try:
            # Wait a bit for JavaScript to load the tabs
            time.sleep(0.5)
            
            # Try multiple selectors for the first tab
            tab1 = None
            for selector in ["a[href='#FP_Detail_1100004']", "li.FPTabLi:first-child a", ".FPTabLi a"]:
                try:
                    tab1 = driver.find_element(By.CSS_SELECTOR, selector)
                    break
                except NoSuchElementException:
                    continue
            
            if not tab1:
                logger.warning("Could not find 1P apartment tab")
            else:
                # Click first tab
                driver.execute_script("arguments[0].click();", tab1)
                time.sleep(0.3)  # Wait for content to load
                
                # Get button text
                button = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100004']//button[contains(@class, 'btn')]")
                button_text = button.text.strip()
                
                logger.info(f"1P: '{button_text}'")
                
                if button_text and button_text not in ["CONTACT US", "Contact Us"]:
                    apartments_available.append("1 Person Apartment")
                    
        except Exception as e:
            logger.warning(f"Error checking 1P: {e}")
        
        # Check 2 Person Apartment
        try:
            # Try multiple selectors for the second tab
            tab2 = None
            for selector in ["a[href='#FP_Detail_1100005']", "li.FPTabLi:nth-child(2) a", ".FPTabLi:nth-child(2) a"]:
                try:
                    tab2 = driver.find_element(By.CSS_SELECTOR, selector)
                    break
                except NoSuchElementException:
                    continue
            
            if not tab2:
                logger.warning("Could not find 2P apartment tab")
            else:
                # Click second tab
                driver.execute_script("arguments[0].click();", tab2)
                time.sleep(0.3)  # Wait for content to load
                
                # Get button text
                button = driver.find_element(By.XPATH, "//div[@id='FP_Detail_1100005']//button[contains(@class, 'btn')]")
                button_text = button.text.strip()
                
                logger.info(f"2P: '{button_text}'")
                
                if button_text and button_text not in ["CONTACT US", "Contact Us"]:
                    apartments_available.append("2 Person Apartment")
                    
        except Exception as e:
            logger.warning(f"Error checking 2P: {e}")
        
        return apartments_available
        
    except Exception as e:
        logger.error(f"Speed check error: {e}")
        return []

def send_telegram_notification(message, db_conn=None):
    """Use a direct, simple HTTP request with minimal overhead."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram notifications disabled: missing token or chat ID")
        return False
        
    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        # Faster request with shorter timeout
        response = requests.post(telegram_api_url, data=payload, timeout=5)
        success = response.status_code == 200
        
        # Log to database outside of critical path
        if db_conn:
            threading.Thread(target=log_notification, 
                           args=(db_conn, message, success)).start()
        
        return success
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        if db_conn:
            threading.Thread(target=log_notification, 
                           args=(db_conn, message, False)).start()
        return False

def send_speed_notification(message):
    """Send notification only if Telegram is configured."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
        
    try:
        telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(telegram_api_url, data=payload, timeout=3)
        return response.status_code == 200
    except:
        return False

def send_startup_notification(db_conn=None):
    """Send a startup notification to confirm Telegram is working."""
    startup_message = f"OurCampus Monitor Started\n\n" + \
                      f"Monitoring started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n" + \
                      f"Priority-based checking:\n" + \
                      f"• High Priority: {HIGH_PRIORITY_MIN}-{HIGH_PRIORITY_MAX} seconds (Wednesdays 12:00-15:30)\n" + \
                      f"• Medium Priority: {MEDIUM_PRIORITY_MIN}-{MEDIUM_PRIORITY_MAX} seconds (Weekday afternoons)\n" + \
                      f"• Normal Priority: {NORMAL_CHECK_INTERVAL_MIN}-{NORMAL_CHECK_INTERVAL_MAX} minutes (All other times)\n\n" + \
                      f"Available commands:\n" + \
                      f"• /last - Show last check time\n" + \
                      f"• /status - Show full status\n" + \
                      f"• /stats - Show statistics\n" + \
                      f"• /help - Show commands\n\n" + \
                      f"Health check enabled: {HEALTH_CHECK_ENABLED}\n" + \
                      f"Health check port: {HEALTH_CHECK_PORT}"
    
    return send_telegram_notification(startup_message, db_conn)

def process_telegram_commands(db_conn=None):
    """Process incoming Telegram commands with minimal overhead."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return  # Skip if Telegram is not configured
        
    global last_command_update_id
    
    try:
        # Get updates from Telegram with short timeout
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": last_command_update_id + 1, "timeout": 1},
            timeout=3
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
        
        message = f"Last Apartment Check\n\n"
        message += f"Last checked: {last_check_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        message += f"({int(minutes_ago)} minutes ago)"
        
        # Add most recent availability from database if available
        if db_conn:
            try:
                c = db_conn.cursor()
                c.execute("""
                    SELECT apartment_type, button_text, available 
                    FROM availability_history 
                    ORDER BY timestamp DESC 
                    LIMIT 2
                """)
                results = c.fetchall()
                
                if results:
                    message += "\n\nLatest apartment status:\n"
                    for result in results:
                        apt_type, btn_text, is_available = result
                        status = "AVAILABLE" if is_available else "Not available"
                        message += f"• {apt_type}: {status}\n"
            except Exception as e:
                logger.error(f"Error getting last availability from database: {e}")
    else:
        message = "No checks have been performed yet"
    
    send_telegram_notification(message, db_conn)

def handle_status_command(chat_id, db_conn=None):
    """Handle the /status command: Show full status of the monitoring script."""
    global last_check_time, start_time, next_check_time, health_metrics
    
    uptime = datetime.now() - start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
    
    message = f"OurCampus Monitor Status\n\n"
    message += f"• Script is running: ✅\n"
    message += f"• Started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    message += f"• Uptime: {uptime_str}\n"
    
    if last_check_time:
        time_ago = datetime.now() - last_check_time
        minutes_ago = time_ago.total_seconds() // 60
        message += f"• Last check: {last_check_time.strftime('%H:%M:%S')}\n"
        message += f"• Time since: {int(minutes_ago)} minutes\n"
    else:
        message += f"• Last check: None yet\n"
    
    if next_check_time:
        if datetime.now() > next_check_time:
            message += f"• Next check: In progress or coming shortly\n"
        else:
            time_until = next_check_time - datetime.now()
            seconds_until = time_until.total_seconds()
            message += f"• Next check: {next_check_time.strftime('%H:%M:%S')}\n"
            message += f"• Time until next: {int(seconds_until)} seconds\n"
    
    # Add system metrics if available
    if health_metrics:
        message += f"\nSystem Health:\n"
        message += f"• CPU Usage: {health_metrics.get('cpu_percent', 'N/A')}%\n"
        message += f"• Memory Usage: {health_metrics.get('memory_percent', 'N/A')}%\n"
        message += f"• Checks since start: {health_metrics.get('checks_since_start', 'N/A')}\n"
        message += f"• Errors since start: {health_metrics.get('errors_since_start', 'N/A')}\n"
    
    # Add health check status
    if HEALTH_CHECK_ENABLED:
        message += f"\nHealth Check:\n"
        message += f"• Enabled: ✅\n"
        message += f"• Port: {HEALTH_CHECK_PORT}\n"
        message += f"• URL: http://localhost:{HEALTH_CHECK_PORT}/health"
    
    send_telegram_notification(message, db_conn)

def handle_stats_command(chat_id, db_conn=None):
    """Handle the /stats command: Show simplified statistics."""
    if not db_conn:
        message = "Database not available for statistics."
        send_telegram_notification(message)
        return
    
    try:
        c = db_conn.cursor()
        
        # Get total checks
        c.execute("SELECT COUNT(*) FROM availability_history")
        total_checks = c.fetchone()[0]
        
        # Get total availabilities
        c.execute("SELECT COUNT(*) FROM availability_history WHERE available = 1")
        total_availabilities = c.fetchone()[0]
        
        # Create the message
        message = f"Statistics\n\n"
        message += f"• Total checks: {total_checks}\n"
        message += f"• Total availabilities: {total_availabilities}\n"
        
        # Get availability by apartment type (simplified)
        c.execute("""
            SELECT apartment_type, SUM(available) as times_available
            FROM availability_history
            GROUP BY apartment_type
        """)
        apartment_stats = c.fetchall()
        
        if apartment_stats:
            message += f"\nBy Apartment Type:\n"
            for apt_type, available in apartment_stats:
                message += f"• {apt_type}: {available} times available\n"
        
        # Get statistics for today
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute("SELECT * FROM stats WHERE date = ?", (today,))
        today_stats = c.fetchone()
        
        if today_stats:
            message += f"\nToday's Activity:\n"
            message += f"• Checks: {today_stats[1]}\n"
            message += f"• Availabilities: {today_stats[2]}\n"
            message += f"• Errors: {today_stats[3]}\n"
        
        send_telegram_notification(message, db_conn)
    except Exception as e:
        logger.error(f"Error generating stats: {e}")
        message = f"Error generating statistics: {e}"
        send_telegram_notification(message, db_conn)

def handle_help_command(chat_id, db_conn=None):
    """Handle the /help command: Show available commands."""
    message = f"Available Commands\n\n"
    message += f"• /last - Last check time\n"
    message += f"• /status - Monitor status\n"
    message += f"• /stats - Show statistics\n"
    message += f"• /restart - Restart info\n"
    message += f"• /help - This help message"
    
    send_telegram_notification(message, db_conn)

def handle_restart_command(chat_id, db_conn=None):
    """Handle the /restart command: Suggest manual restart procedures."""
    message = f"Restart Request\n\n"
    message += f"To restart manually:\n"
    message += f"1. Connect to server\n"
    message += f"2. Stop current process: sudo supervisorctl stop ourcampus_monitor\n"
    message += f"3. Start the process: sudo supervisorctl start ourcampus_monitor\n"
    message += f"4. Check status: sudo supervisorctl status ourcampus_monitor"
    
    send_telegram_notification(message, db_conn)

def start_health_check_server():
    """Start the health check server if enabled."""
    if not HEALTH_CHECK_ENABLED:
        logger.info("Health check server not enabled")
        return
    
    # Dynamically import health_check.py only if needed
    try:
        import health_check
        from multiprocessing import Process
        
        process = Process(target=health_check.run_server)
        process.daemon = True  # This ensures the process will exit when the main process exits
        process.start()
        logger.info(f"Health check server started on port {HEALTH_CHECK_PORT}")
    except ImportError:
        logger.warning("Health check enabled but health_check.py not found")
    except Exception as e:
        logger.error(f"Failed to start health check server: {e}")

def debug_page_structure(driver):
    """Debug function to see what's actually on the page."""
    try:
        logger.info("DEBUGGING: Loading page...")
        driver.get(URL)
        
        # Wait a bit for page to load
        time.sleep(3)
        
        # Get page title
        logger.info(f"DEBUGGING: Page title: {driver.title}")
        
        # Get page source length
        logger.info(f"DEBUGGING: Page source length: {len(driver.page_source)} characters")
        
        # Look for the main container
        try:
            container = driver.find_element(By.ID, "floorPlanDataContainer")
            logger.info("DEBUGGING: Found floorPlanDataContainer!")
            logger.info(f"DEBUGGING: Container text (first 200 chars): {container.text[:200]}")
        except:
            logger.info("DEBUGGING: floorPlanDataContainer NOT FOUND")
            
            # Try to find any container
            containers = driver.find_elements(By.TAG_NAME, "div")
            logger.info(f"DEBUGGING: Found {len(containers)} div elements")
            
            # Look for any element with "floor" or "plan" in the ID
            elements_with_floor = driver.find_elements(By.XPATH, "//*[contains(@id,'floor') or contains(@id,'plan') or contains(@id,'Floor') or contains(@id,'Plan')]")
            logger.info(f"DEBUGGING: Found {len(elements_with_floor)} elements with 'floor' or 'plan' in ID")
            for elem in elements_with_floor[:3]:  # Show first 3
                logger.info(f"DEBUGGING: Element ID: {elem.get_attribute('id')}, Tag: {elem.tag_name}")
        
        # Look for any tabs or links
        all_links = driver.find_elements(By.TAG_NAME, "a")
        logger.info(f"DEBUGGING: Found {len(all_links)} link elements")
        
        # Look for links that might be apartment tabs
        apartment_links = []
        for link in all_links:
            href = link.get_attribute("href") or ""
            text = link.text.strip()
            if "1100004" in href or "1100005" in href or "person" in text.lower() or "apartment" in text.lower():
                apartment_links.append(f"Link: '{text}' -> {href}")
        
        if apartment_links:
            logger.info("DEBUGGING: Found potential apartment links:")
            for link in apartment_links[:5]:  # Show first 5
                logger.info(f"DEBUGGING: {link}")
        else:
            logger.info("DEBUGGING: No apartment-related links found")
        
        # Look for any buttons
        buttons = driver.find_elements(By.TAG_NAME, "button")
        logger.info(f"DEBUGGING: Found {len(buttons)} button elements")
        for button in buttons[:3]:  # Show first 3 buttons
            logger.info(f"DEBUGGING: Button text: '{button.text.strip()}' Class: {button.get_attribute('class')}")
        
        # Look for elements with specific classes
        fp_elements = driver.find_elements(By.XPATH, "//*[contains(@class,'FP') or contains(@class,'fp')]")
        logger.info(f"DEBUGGING: Found {len(fp_elements)} elements with 'FP' or 'fp' in class")
        
        # Save page source to file for inspection
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        with open(f"logs/debug_page_source_{timestamp}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        logger.info(f"DEBUGGING: Page source saved to logs/debug_page_source_{timestamp}.html")
        
    except Exception as e:
        logger.error(f"DEBUGGING: Error during page debug: {e}")

def simple_test_browser_opening():
    """Simple test to just open browser tabs without checking website."""
    logger.info("SIMPLE TEST: Testing NEW BROWSER INSTANCE opening...")
    logger.info("SIMPLE TEST: You should see completely new browser windows open (not just tabs)!")
    
    logger.info("SIMPLE TEST: Opening 1 Person Apartment booking page in NEW BROWSER INSTANCE...")
    success1 = open_booking_page("1 Person Apartment")
    
    time.sleep(2)  # Brief delay between opens so you can see them separately
    
    logger.info("SIMPLE TEST: Opening 2 Person Apartment booking page in NEW BROWSER INSTANCE...")
    success2 = open_booking_page("2 Person Apartment")
    
    if success1 and success2:
        logger.info("SIMPLE TEST: SUCCESS! Two NEW BROWSER INSTANCES should have opened (not tabs)!")
        logger.info("SIMPLE TEST: These should be maximized and very noticeable!")
    else:
        logger.error("SIMPLE TEST: Some browser instances failed to open.")
    
    return success1 and success2

def speed_mode_main(test_mode=False):
    """Main function optimized for maximum speed."""
    global start_time, next_check_time, apartments_found_this_session
    
    start_time = datetime.now()
    
    if test_mode:
        logger.info("TEST MODE: Starting speed mode for testing!")
        logger.info("TEST: Will simulate apartment availability in 15 seconds to test browser opening")
    else:
        logger.info("SPEED MODE: Starting maximum performance monitoring!")
        
    logger.info(f"Check interval: {SPEED_MODE_INTERVAL_MIN}-{SPEED_MODE_INTERVAL_MAX} seconds")
    
    # Send startup notification if Telegram is configured
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        if test_mode:
            startup_msg = f"TEST MODE ACTIVATED\n\n"
            startup_msg += f"Testing browser tab opening in 15 seconds!\n"
        else:
            startup_msg = f"SPEED MODE ACTIVATED\n\n"
            startup_msg += f"Ultra-fast monitoring started!\n"
        startup_msg += f"Check interval: {SPEED_MODE_INTERVAL_MIN}-{SPEED_MODE_INTERVAL_MAX}s\n"
        startup_msg += f"Browser instances will open automatically when apartments are found!"
        send_speed_notification(startup_msg)
    
    driver = None
    test_triggered = False
    
    try:
        if not test_mode:
            driver = setup_speed_driver(headless=True)  # Headless for speed
        check_count = 0
        
        while True:
            try:
                # TEST MODE: Simulate apartment availability after 15 seconds
                if test_mode and not test_triggered:
                    uptime = (datetime.now() - start_time).total_seconds()
                    if uptime >= 15:
                        logger.info("TEST: Simulating apartment availability!")
                        available_apartments = ["1 Person Apartment", "2 Person Apartment"]  # Simulate both apartments available
                        test_triggered = True
                    else:
                        available_apartments = []
                        logger.info(f"TEST: Waiting for 15 seconds... ({int(15-uptime)} seconds remaining)")
                else:
                    # Normal mode: Actually check the website
                    available_apartments = check_availability_speed(driver) if not test_mode else []
                
                check_count += 1
                
                if available_apartments:
                    new_apartments = set(available_apartments) - apartments_found_this_session
                    
                    if new_apartments:
                        logger.info(f"SUCCESS: New apartments found: {new_apartments}")
                        
                        # MAXIMUM ATTENTION: Open booking pages in NEW BROWSER INSTANCES
                        for apt_type in new_apartments:
                            logger.info(f"APARTMENT ALERT: Opening {apt_type} booking page!")
                            success = open_booking_page(apt_type)
                            if success:
                                logger.info(f"SUCCESS: NEW BROWSER INSTANCE opened for {apt_type}")
                                
                                # Additional attention-getting measures
                                try:
                                    # Try to make a system beep sound (Windows)
                                    if platform.system().lower() == "windows":
                                        import winsound
                                        winsound.Beep(1000, 500)  # 1000Hz for 500ms
                                except:
                                    pass
                                    
                            else:
                                logger.error(f"ERROR: Failed to open browser instance for {apt_type}")
                        
                        # Send notification as backup
                        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                            msg = f"APARTMENTS AVAILABLE!\n\n"
                            for apt in new_apartments:
                                msg += f"• {apt}\n"
                            msg += f"\nNew browser instances should have opened automatically!"
                            send_speed_notification(msg)
                        
                        # Update found apartments
                        apartments_found_this_session.update(new_apartments)
                    else:
                        logger.info(f"Same apartments still available: {available_apartments}")
                else:
                    # Reset if no apartments found
                    if apartments_found_this_session:
                        logger.info("No apartments available now - resetting tracker")
                        apartments_found_this_session = set()
                
                # Calculate next check time
                if test_mode:
                    interval = 2.0  # Faster checking in test mode
                else:
                    interval = get_speed_interval()
                next_check_time = datetime.now() + timedelta(seconds=interval)
                
                # Status update every 50 checks (or every 5 in test mode)
                status_interval = 5 if test_mode else 50
                if check_count % status_interval == 0:
                    uptime = datetime.now() - start_time
                    if test_mode:
                        logger.info(f"TEST Check #{check_count} | Uptime: {uptime} | Next: {interval:.1f}s")
                    else:
                        logger.info(f"STATUS Check #{check_count} | Uptime: {uptime} | Next: {interval:.1f}s")
                
                time.sleep(interval)
                
                # In test mode, exit after successful test
                if test_mode and test_triggered and available_apartments:
                    logger.info("TEST COMPLETED! Browser instances should have opened.")
                    logger.info("TEST: Test mode finished. Press Ctrl+C to exit or wait for auto-exit...")
                    time.sleep(5)  # Give user time to see the opened tabs
                    break
                
                # Restart browser every 100 checks to prevent issues (skip in test mode)
                if not test_mode and check_count % 100 == 0:
                    logger.info("MAINTENANCE: Restarting browser for performance...")
                    driver.quit()
                    driver = setup_speed_driver(headless=True)
                
            except Exception as e:
                logger.error(f"Error during speed check: {e}")
                if not test_mode:
                    time.sleep(2)  # Brief pause before retry
                    
                    # Try to restart browser on error
                    try:
                        if driver:
                            driver.quit()
                        driver = setup_speed_driver(headless=True)
                    except Exception as browser_error:
                        logger.error(f"Browser restart failed: {browser_error}")
                        time.sleep(5)
                else:
                    # In test mode, just continue
                    time.sleep(1)
                
    except KeyboardInterrupt:
        if test_mode:
            logger.info("Test mode stopped by user")
        else:
            logger.info("Speed mode stopped by user")
    finally:
        if driver:
            driver.quit()
        if test_mode:
            logger.info("TEST MODE: Test mode ended")
        else:
            logger.info("SPEED MODE: Speed mode ended")

def main(headless=True):
    """Main function to monitor apartment availability with improved speed."""
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
    
    # Send startup notification
    send_startup_notification(db_conn)
    
    # Start health check server if enabled
    start_health_check_server()
    
    driver = None
    
    try:
        driver = setup_driver(headless=headless)
        last_notified = set()  # Keep track of apartments we've already notified about
        command_check_time = 0  # Track when we last checked for commands
        browser_restart_counter = 0  # Counter for browser restarts
        consecutive_errors = 0  # Track consecutive errors
        
        while True:
            try:
                available_apartments = check_availability(driver, db_conn)
                
                # Reset error counter on successful check
                consecutive_errors = 0
                
                if available_apartments:
                    current_available = set(available_apartments)
                    new_available = current_available - last_notified
                    
                    if new_available:
                        logger.info(f"New apartments available! {new_available}")
                        
                        message = "OurCampus Apartments Available!\n\n"
                        message += "The following apartments are now available:\n\n"
                        for apt in new_available:
                            message += f"• {apt}\n"
                        message += f"\nClick here to apply now: {URL}"
                        
                        send_telegram_notification(message, db_conn)
                        last_notified = current_available
                    else:
                        logger.info("No new apartments since last check.")
                else:
                    logger.info("No apartments available currently.")
                    # Only reset notification tracking if we've previously found something
                    if last_notified:
                        # Notify about apartments no longer available
                        message = "OurCampus Update\n\n"
                        message += "Previously available apartments are no longer listed."
                        send_telegram_notification(message, db_conn)
                        last_notified = set()
                
                # Check for Telegram commands every 10 seconds
                current_time = time.time()
                if current_time - command_check_time > 10:
                    process_telegram_commands(db_conn)
                    command_check_time = current_time
                
                # Determine the next check interval based on current time
                check_interval = get_check_interval()
                next_check_time = datetime.now().replace(microsecond=0)
                next_check_time = datetime.fromtimestamp(next_check_time.timestamp() + check_interval)
                
                logger.info(f"Next check at {next_check_time.strftime('%H:%M:%S')}")
                
                # Sleep in shorter intervals while checking for commands
                remaining_sleep = check_interval
                while remaining_sleep > 0:
                    sleep_interval = min(5, remaining_sleep)  # Sleep for 5 seconds at a time (faster command response)
                    time.sleep(sleep_interval)
                    remaining_sleep -= sleep_interval
                    
                    # Check for commands during sleep periods
                    process_telegram_commands(db_conn)
                
                # Increment browser restart counter
                browser_restart_counter += 1
                
                # Restart the browser every 15 checks to avoid memory issues
                if browser_restart_counter >= 15:
                    logger.info("Scheduled browser restart")
                    browser_restart_counter = 0
                    driver.quit()
                    driver = setup_driver(headless=headless)
                
            except Exception as e:
                logger.error(f"Error during check: {e}")
                consecutive_errors += 1
                
                # If we have too many consecutive errors, send an alert
                if consecutive_errors >= 5:
                    error_message = f"Critical Error\n\n"
                    error_message += f"Encountered {consecutive_errors} consecutive errors.\n"
                    error_message += f"Last error: {str(e)}\n\n"
                    error_message += f"Attempting to recover..."
                    
                    send_telegram_notification(error_message, db_conn)
                    
                time.sleep(30)  # Wait 30 seconds before trying again after an error
                
                # Restart the browser after errors
                try:
                    if driver:
                        driver.quit()
                    driver = setup_driver(headless=headless)
                    browser_restart_counter = 0
                except Exception as browser_error:
                    logger.error(f"Error restarting browser: {browser_error}")
                    time.sleep(30)
                
    finally:
        if driver:
            driver.quit()
        
        if db_conn:
            db_conn.close()
            
        logger.info("OurCampus monitor stopped")

if __name__ == "__main__":
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='OurCampus Amsterdam Diemen apartment monitor')
    parser.add_argument('--speed-mode', action='store_true', 
                       help='Run in ultra-fast speed mode (for local use during apartment releases)')
    parser.add_argument('--test-mode', action='store_true',
                       help='Test mode: Simulate apartment availability after 15 seconds to test browser opening')
    parser.add_argument('--debug-page', action='store_true',
                       help='Debug mode: Show what elements are actually on the page')
    parser.add_argument('--test-browser', action='store_true',
                       help='Simple test: Just test opening browser tabs without checking website')
    parser.add_argument('--test-full', action='store_true',
                       help='Full test: Simulate finding apartments and test complete workflow')
    parser.add_argument('--no-headless', action='store_true', help='Run Chrome in visible mode (not headless)')
    args = parser.parse_args()
    
    # Run the monitor
    if args.debug_page:
        print("DEBUG MODE ACTIVATED!")
        print("Will analyze the website structure and save details to logs/")
        print("This will help identify why apartment tabs aren't being found")
        print("-" * 60)
        
        # Run debug mode
        driver = None
        try:
            driver = setup_speed_driver(headless=False)  # Visible mode for debugging
            debug_page_structure(driver)
            logger.info("DEBUG: Analysis complete! Check the logs for details.")
        finally:
            if driver:
                driver.quit()
                
    elif args.test_browser:
        print("BROWSER TEST MODE ACTIVATED!")
        print("Will test opening NEW BROWSER INSTANCES (not tabs) in 3 seconds...")
        print("You should see maximized browser windows open!")
        print("-" * 60)
        
        time.sleep(3)  # Give user time to see message
        simple_test_browser_opening()
        
    elif args.test_full:
        print("FULL TEST MODE ACTIVATED!")
        print("Will simulate finding apartments and test complete workflow in 5 seconds...")
        print("This tests: Website checking + Browser opening + Notifications")
        print("-" * 60)
        
        # Simulate the complete apartment finding workflow
        time.sleep(5)
        logger.info("FULL TEST: Simulating apartment discovery...")
        
        # Simulate finding both apartments
        new_apartments = {"1 Person Apartment", "2 Person Apartment"}
        logger.info(f"SUCCESS: New apartments found: {new_apartments}")
        
        # Open booking pages with all the bells and whistles
        for apt_type in new_apartments:
            logger.info(f"APARTMENT ALERT: Opening {apt_type} booking page!")
            success = open_booking_page(apt_type)
            if success:
                logger.info(f"SUCCESS: NEW BROWSER INSTANCE opened for {apt_type}")
                
                # Audio alert
                try:
                    if platform.system().lower() == "windows":
                        import winsound
                        winsound.Beep(1000, 500)  # 1000Hz for 500ms
                except:
                    pass
            else:
                logger.error(f"ERROR: Failed to open browser instance for {apt_type}")
            
            time.sleep(1)  # Brief delay between openings
        
        # Send Telegram notification if configured
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            msg = f"APARTMENTS AVAILABLE!\n\n"
            for apt in new_apartments:
                msg += f"• {apt}\n"
            msg += f"\nNew browser instances should have opened automatically!"
            send_speed_notification(msg)
            logger.info("FULL TEST: Telegram notification sent!")
        
        logger.info("FULL TEST: Complete workflow test finished!")
        
    elif args.speed_mode or args.test_mode:
        if args.test_mode:
            print("TEST MODE ACTIVATED!")
            print("Will simulate apartment availability in 15 seconds")
            print("Browser tabs should open automatically when apartments are 'found'")
            print("Press Ctrl+C to stop")
        else:
            print("SPEED MODE ACTIVATED!")
            print("Ultra-fast checking enabled")
            print("Browser instances will open automatically when apartments are found")
            print("Press Ctrl+C to stop")
        print("-" * 60)
        speed_mode_main(test_mode=args.test_mode)
    else:
        main(headless=not args.no_headless)