# 3rd party imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from time import sleep

# my files
from .parse import Parse
from .scrape import RedfinScraper, ZillowScraper # Make sure ZillowScraper is correctly imported if used later
from .search import CreateSearchUrl
from .models import BotRentalReport


class RedfinBot():
    """Redfin.com bot that gathers online property data"""
    def __init__(self, user):
        self.user = user
        self.url = CreateSearchUrl.get_complete_url(CreateSearchUrl(self.user))
        self.parsed_urls = []
        self.all_data = None
        self.parsed_data = None # Initialize parsed_data

    def webdriver(self):
        """Opens chrome webpage, downloads csv file"""
        # allows chrome to download csv file in headless mode
        chrome_options = Options()
        chrome_options.add_experimental_option("prefs", {
            "download.default_directory": "/Users/garrettlesher/Downloads/", # Ensure this path is correct for your system
            "download.prompt_for_download": False,
            "download.directory_upgrade": True, # Usually good to have
            "safeBrowse.enabled": True # Sometimes helps with downloads
        })
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox") # Often needed for headless in Docker/Linux
        chrome_options.add_argument("--disable-dev-shm-usage") # Overcomes limited resource problems

        self.driver = webdriver.Chrome(service=Service(), options=chrome_options)
        
        # Enable navigator.webdriver to be false to avoid bot detection (optional, may not be needed)
        # self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        #     "source": """
        #         Object.defineProperty(navigator, 'webdriver', {
        #           get: () => undefined
        #         })
        #     """
        # })

        self.driver.command_executor._commands["send_command"] = \
            ("POST", '/session/$sessionId/chromium/send_command')
        params = {
            'cmd': 'Page.setDownloadBehavior', 
            'params': 
                {
                    'behavior': 'allow', 
                    'downloadPath': "/Users/garrettlesher/Downloads/" # Ensure this path is correct
                }
            }
        self.driver.execute("send_command", params)

        print("Opening webpage with search URL...")
        self.driver.get(self.url)
        sleep(3) # Increased sleep a bit after page load

        # Add WebDriverWait to wait until the element is clickable
        wait = WebDriverWait(self.driver, 20)  # Wait for up to 20 seconds

        try:
            # Scroll to the bottom of the page to ensure the link is in view
            print("Scrolling to the bottom of the page...")
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            sleep(2) # Give time for any lazy-loaded content or for the scroll to complete

            # Locate the new download button by its link text
            # Using PARTIAL_LINK_TEXT is often safer if there are surrounding spaces or minor changes
            # but if "(Download All)" is exact and unique, LINK_TEXT is fine.
            # XPath is also a good alternative if text is not unique enough.
            # new_download_button_locator = (By.XPATH, "//a[contains(text(), '(Download All)')]")
            new_download_button_locator = (By.PARTIAL_LINK_TEXT, "Download All")


            print("Looking for the new download button...")
            download_button = wait.until(EC.element_to_be_clickable(new_download_button_locator))
            print("New download button found, attempting to click.")
            
            # Attempting JavaScript click as it can sometimes be more reliable
            self.driver.execute_script("arguments[0].click();", download_button)
            # download_button.click() # Standard click as a fallback or primary option

            print("Clicked the new download button.")
        except Exception as e:
            print(f"Error finding or clicking the new download button: {e}")
            self.driver.save_screenshot("error_screenshot.png") # Saves a screenshot for debugging
            print("Screenshot 'error_screenshot.png' saved for debugging.")
            self.driver.quit()
            return # Exit if button not found or click failed

        print("Downloading csv file...")
        sleep(10) # Increased sleep to ensure download completes, adjust as needed
        self.driver.quit()


    def parse_csv_data(self):
        """Parses csv file downloaded in 'webdriver' function above"""
        # Initialize Parse class with self.user
        parser = Parse(self.user)
        self.parsed_data = parser.run() # Call run method on the instance
        
        if not self.parsed_data:
            print("No data parsed from CSV. Exiting.")
            return

        current_urls = []
        # check current redfin urls. avoid scraping urls already in reports
        objs = BotRentalReport.objects.filter(owner=self.user).values()
        for obj in objs:
            current_urls.append(obj['redfin_listing_url'])
        
        # only scrape urls for reports not already added
        for key in list(self.parsed_data.keys()): # Use list(keys()) for safe iteration if modifying dict
            parsed_url = self.parsed_data[key].get('URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)')
            if parsed_url and parsed_url not in current_urls:
                self.parsed_urls.append(parsed_url)
            elif not parsed_url:
                print(f"Warning: URL missing for data entry key {key}")


    def scrape(self):
        """scrapes all webpages from list of parsed csv file urls"""
        if not self.parsed_urls:
            print("No new URLs to scrape from Redfin.")
            self.scraped_data = [] # Ensure scraped_data is an empty list if no URLs
            return

        print(f"Scraping {len(self.parsed_urls)} Redfin URLs...")
        scraper = RedfinScraper() # Initialize RedfinScraper
        self.scraped_data = scraper.run(self.parsed_urls) # Call run method on the instance

    def combine(self):
        """combines parsed and scraped data into one dictionary"""
        if self.parsed_data is None or self.scraped_data is None:
            print("Parsed data or scraped data is missing. Cannot combine.")
            self.all_data = {} # Or None, depending on how you want to handle this
            return

        final_copy = {}
        
        # Filter parsed_data to only include entries for which we have URLs and are new
        temp_parsed_data_for_combination = {}
        parsed_data_index = 0 # To map with scraped_data if lengths differ
        
        # Create a mapping from URL to its original key in parsed_data
        url_to_original_key = {}
        for original_key, data_item in self.parsed_data.items():
            url = data_item.get('URL (SEE https://www.redfin.com/buy-a-home/comparative-market-analysis FOR INFO ON PRICING)')
            if url in self.parsed_urls: # Only process items that were selected for scraping
                 url_to_original_key[url] = original_key


        # Iterate through scraped_data (which is a list of dicts, order matches self.parsed_urls)
        for index, scraped_item in enumerate(self.scraped_data):
            if index < len(self.parsed_urls):
                current_url = self.parsed_urls[index]
                original_parsed_data_key = url_to_original_key.get(current_url)

                if original_parsed_data_key is not None:
                    # Start with the base data from the CSV
                    combined_item = self.parsed_data[original_parsed_data_key].copy()
                    
                    # Add/update with scraped details
                    combined_item.update(scraped_item) # Merges scraped_item into combined_item

                    street = combined_item.get('ADDRESS', '').replace(' ', '-')
                    city = f"-{combined_item.get('CITY', '').replace(' ', '-')}"
                    state = f"-{combined_item.get('STATE OR PROVINCE', '')}"
                    zip_code = f"-{combined_item.get('ZIP OR POSTAL CODE', '')}" # ZIP already string from parse_csv
                    
                    addr_parts = [part for part in [street, city, state, zip_code] if part and part != "-"]
                    addr = "".join(addr_parts).lower()

                    zest_url = f'https://www.zillow.com/rental-manager/price-my-rental/results/{addr}/'
                    combined_item['ZESTIMATE URL'] = zest_url
                    
                    print(f"Scraping Zestimate for: {zest_url}")
                    zillow_scraper = ZillowScraper() # Initialize ZillowScraper
                    combined_item['ZESTIMATE'] = zillow_scraper.run(zest_url) # Call run method
                    
                    final_copy[original_parsed_data_key] = combined_item # Use original key for consistency
                else:
                    print(f"Warning: Could not find original parsed data for URL: {current_url}")
            else:
                print(f"Warning: Scraped item at index {index} does not have a corresponding URL in parsed_urls.")

        self.all_data = final_copy


    def run(self):
        """runs RedfinBot"""
        print(f'Running bot for {self.user.username}')
        if not self.url:
            print("Search URL is not configured. Exiting.")
            return None # Or handle error appropriately
        
        print(f'Search URL: {self.url}')
        
        self.webdriver()
        self.parse_csv_data()
        
        if not self.parsed_urls and not self.parsed_data: # If CSV parsing failed or yielded nothing new
            print("No new properties to report on after CSV parsing. Exiting bot run.")
            return None

        if self.parsed_urls: # Proceed only if there are new URLs to scrape
            self.scrape()
            if self.scraped_data is not None: # Ensure scraping produced some result (even if empty list)
                 print("Combining data and scraping Zillow URLs...")
                 self.combine()
            else:
                print("Scraping did not complete successfully. Cannot combine.")
                self.all_data = {} # Reset or handle appropriately
        else: 
            print("No new properties from CSV to scrape individual pages for.")
            self.all_data = {} # Ensure all_data is initialized

        return self.all_data