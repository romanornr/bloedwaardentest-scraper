from playwright.async_api import async_playwright
import asyncio
from price_parser import Price
import json
from pathlib import Path
from datetime import datetime
import logging
from colorlog import ColoredFormatter

def setup_logger(name='scraper', log_file='data/scraper.log'):
    """
    Set up a logger with colored output for console and detailed logging for file
    """
    # Create data directory if it doesn't exist
    log_dir = Path(log_file).parent
    log_dir.mkdir(exist_ok=True)
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers = []
    
    # Console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Color scheme for different log levels
    console_colors = {
        'DEBUG':    'cyan',
        'INFO':     'green',
        'WARNING':  'yellow',
        'ERROR':    'red',
        'CRITICAL': 'red,bg_white',
    }
    
    # Console formatter with colors
    console_formatter = ColoredFormatter(
        "%(log_color)s%(asctime)s │ %(message)s",
        datefmt="%H:%M:%S",
        log_colors=console_colors,
        reset=True,
        style='%'
    )
    console_handler.setFormatter(console_formatter)
    
    # File handler with detailed formatting
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s │ %(levelname)-8s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    
    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

# Create global logger instance
logger = setup_logger()

def log_section(title, char='─'):
    """
    Create a visually distinct section in the logs
    """
    width = 80
    padding = (width - len(title) - 2) // 2
    logger.info(char * width)
    logger.info(f"{char * padding} {title} {char * padding}")
    logger.info(char * width)

def log_product_info(product):
    """
    Log product information in a structured format
    """
    logger.info("┌─ Product Details " + "─" * 50)
    logger.info(f"│ Name: {product['name']}")
    logger.info(f"│ Price: {product['price']}")
    logger.info(f"│ URL: {product['link']}")
    
    if product.get('biomarkers'):
        logger.info("│")
        logger.info("│ Biomarkers:")
        for marker in product['biomarkers']:
            if isinstance(marker, dict):
                logger.info(f"│   {marker['category']}:")
                for biomarker in marker['markers']:
                    logger.info(f"│     • {biomarker}")
            else:
                logger.info(f"│     • {marker}")
    logger.info("└" + "─" * 65)

async def get_products(page):
    print("Getting products...")
    await page.wait_for_selector('ul.list-collection li.data-product')
    products = await page.evaluate('''
        () => {
            const products = document.querySelectorAll('ul.list-collection li.data-product');
            return Array.from(products).map(product => {
                const nameElement = product.querySelector('h3 a');
                return {
                    name: nameElement ? nameElement.textContent.trim() : '',
                    link: nameElement ? nameElement.href : ''
                };
            });
        }
    ''')
    return products

async def handle_cookies(page):
    print("Checking for cookie consent dialog...")
    try:
        cookie_button = await page.query_selector('button#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll')
        if cookie_button:
            print("Cookie consent dialog found, accepting...")
            await cookie_button.click(timeout=5000)
            print("Cookies accepted successfully")
            return True
        print("No cookie consent dialog found")
        return False
    except Exception as e:
        print(f"Error handling cookies: {e}")
        return False

async def has_next_page(page):
    next_button = await page.query_selector('nav.pagination-a li.next a[rel="next"]')
    if next_button:
        next_url = await next_button.get_attribute('href')
        return next_url
    return None

async def scrape_page(page, url):
    print(f"\nVisiting {url}...")
    await page.goto(url)
    
    # Handle cookies on initial page load
    await handle_cookies(page)
    
    all_products = []
    current_url = url
    page_num = 1
    
    while True:
        # Get products from current page
        products = await get_products(page)
        print(f"\nFound products on page {page_num} at {current_url}:")
        for i, product in enumerate(products, 1):
            print(f"{i}. {product['name']}")
            print(f"   Link: {product['link']}\n")
        
        all_products.extend(products)
        
        # Check for next page
        next_url = await has_next_page(page)
        if not next_url:
            print("No more pages to scrape")
            break
            
        print(f"Found next page: {next_url}")
        current_url = next_url
        await page.goto(next_url)
        
        # Handle cookies after each page navigation
        await handle_cookies(page)
        
        page_num += 1
    
    print(f"Total products found: {len(all_products)}")
    return all_products

def convert_price_to_number(price_text):
    try:
        # Use price-parser to handle the conversion
        price = Price.fromstring(price_text)
        if price.amount is not None:
            return float(price.amount)
        return None
    except Exception as e:
        print(f"Error converting price '{price_text}' to number: {e}")
        return None

async def get_product_price(page):
    print("Getting product price...")
    try:
        price_element = await page.wait_for_selector('div.price-wrapper span.main-price')
        if price_element:
            price_text = await price_element.text_content()
            price_text = price_text.strip()
            price_number = convert_price_to_number(price_text)
            print(f"Found price: {price_number}")
            return price_number
                
        print("Price element not found")
        return None
    except Exception as e:
        print(f"Error getting price: {e}")
        return None

async def expand_read_more(page):
    print("Checking for 'Lees meer' button...")
    try:
        show_more_button = await page.query_selector('a.show-more')
        if show_more_button:
            print("Found 'Lees meer' button, expanding content...")
            await show_more_button.click()
            # Wait for the expanded state
            await page.wait_for_selector('article.module-info-update.module-info.toggle.has-anchor.expanded', 
                                      timeout=5000)
            print("Content expanded successfully")
            return True
        print("No 'Lees meer' button found")
        return False
    except Exception as e:
        print(f"Error expanding content: {e}")
        return False

async def extract_biomarkers_from_content(page):
    print("Extracting biomarkers with complex structure...")
    try:
        # First look for the biomarker header text to ensure we're in the right section
        markers = await page.evaluate('''
            () => {
                // Helper function to clean text
                const cleanText = (text) => text.replace(/\\s+/g, ' ').trim();
                
                // Find all strong elements that might be categories
                const categories = Array.from(document.querySelectorAll('div.desc-wrapper li > strong'));
                let biomarkers = [];
                
                for (const category of categories) {
                    // Get the category name
                    const categoryName = cleanText(category.textContent);
                    
                    // Find the closest ul that contains the actual markers
                    const markerList = category.closest('li').querySelector('ul');
                    if (markerList) {
                        // Get all markers in this category
                        const markers = Array.from(markerList.querySelectorAll('li'))
                            .map(li => cleanText(li.textContent))
                            .filter(text => text.length > 0);  // Filter out empty items
                            
                        if (markers.length > 0) {
                            biomarkers.push({
                                category: categoryName,
                                markers: markers
                            });
                        }
                    }
                }
                
                return biomarkers;
            }
        ''')
        
        if markers:
            print("\nFound categorized biomarkers:")
            for category in markers:
                print(f"\n{category['category']}:")
                for marker in category['markers']:
                    print(f"  - {marker}")
            return markers
            
        print("No categorized biomarkers found")
        return []
        
    except Exception as e:
        print(f"Error extracting complex biomarkers: {e}")
        return []

async def get_product_biomarkers(page):
    print("Getting product biomarkers...")
    try:
        # First expand the content if needed
        await expand_read_more(page)
        
        # Look for the biomarker indicator text
        content_text = await page.evaluate('''
            () => {
                const wrapper = document.querySelector('div.desc-wrapper');
                return wrapper ? wrapper.textContent : '';
            }
        ''')
        
        # If we find text about biomarkers, use the complex extraction
        if 'biomarkers' in content_text.lower() or 'gemeten worden' in content_text.lower():
            return await extract_biomarkers_from_content(page)
        
        # Fallback to simple list extraction if no complex structure found
        await page.wait_for_selector('div.desc-wrapper ul')
        simple_markers = await page.evaluate('''
            () => {
                const ul = document.querySelector('div.desc-wrapper ul');
                if (!ul) return [];
                
                // Filter out instruction texts
                const excludeTexts = ['bestel', 'brievenbus', 'prikpunt', 'kortingscode', 'upload'];
                
                return Array.from(ul.querySelectorAll('li'))
                    .map(li => li.textContent.trim())
                    .filter(text => !excludeTexts.some(exclude => 
                        text.toLowerCase().includes(exclude)
                    ));
            }
        ''')
        
        if simple_markers:
            print(f"\nFound simple biomarkers:")
            for marker in simple_markers:
                print(f"- {marker}")
            return simple_markers
            
        print("No biomarkers found")
        return []
        
    except Exception as e:
        print(f"Error getting biomarkers: {e}")
        return []

async def try_load_page(page, url, max_retries=3):
    """
    Attempt to load a page with multiple retry strategies
    """
    logger.debug(f"Attempting to load page: {url}")
    
    strategies = [
        {'wait_until': 'domcontentloaded', 'timeout': 30000},
        {'wait_until': 'load', 'timeout': 60000},
        {'wait_until': 'networkidle', 'timeout': 90000}
    ]
    
    for attempt in range(max_retries):
        for strategy in strategies:
            try:
                logger.debug(f"Attempt {attempt + 1}/{max_retries} using {strategy['wait_until']} strategy")
                await page.goto(
                    url,
                    timeout=strategy['timeout'],
                    wait_until=strategy['wait_until']
                )
                logger.info(f"✅ Successfully loaded page using {strategy['wait_until']} strategy")
                return True
            except Exception as e:
                logger.warning(f"⚠️ Failed with {strategy['wait_until']} strategy: {str(e)}")
                continue
        
        if attempt < max_retries - 1:
            wait_time = (attempt + 1) * 5  # Increasing wait time between attempts
            logger.info(f"⏳ Waiting {wait_time} seconds before next attempt...")
            await asyncio.sleep(wait_time)
    
    logger.error("❌ Failed to load page with all strategies")
    return False

async def visit_product_page(page, product):
    """
    Visit a product page and extract its details.
    """
    log_section(f"Processing Product: {product['name']}")
    logger.debug(f"Product URL: {product['link']}")
    
    try:
        # Try to load the page with our robust loading strategy
        page_loaded = await try_load_page(page, product['link'])
        if not page_loaded:
            raise Exception("Failed to load page after multiple attempts")
        
        logger.debug("🍪 Handling cookies")
        await handle_cookies(page)
        
        # Wait for main content to be available
        try:
            logger.debug("⌛ Waiting for main content")
            await page.wait_for_selector('div.page-content', timeout=10000)
        except Exception as e:
            logger.warning(f"⚠️ Main content not found: {e}")
        
        logger.debug("💰 Getting product price")
        price = await get_product_price(page)
        logger.info(f"Found price: {price}")
        
        logger.debug("🔬 Getting product biomarkers")
        # Try multiple times to get biomarkers
        max_attempts = 3
        biomarkers = []
        
        for attempt in range(max_attempts):
            try:
                logger.debug(f"Attempt {attempt + 1}/{max_attempts} to get biomarkers")
                
                # First try expanding content
                expanded = await expand_read_more(page)
                if expanded:
                    logger.debug("Content expanded successfully")
                    await asyncio.sleep(2)  # Wait for content to settle
                
                biomarkers = await get_product_biomarkers(page)
                if biomarkers:
                    logger.info(f"Successfully found {len(biomarkers)} biomarkers on attempt {attempt + 1}")
                    break
                
                logger.warning(f"No biomarkers found on attempt {attempt + 1}")
                if attempt < max_attempts - 1:
                    logger.debug("Waiting before next attempt...")
                    await asyncio.sleep(3)
            except Exception as e:
                logger.warning(f"Error during biomarker extraction attempt {attempt + 1}: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(3)
        
        # Update product data
        product['price'] = price
        product['biomarkers'] = biomarkers
        product['extraction_attempts'] = attempt + 1
        
        if not biomarkers:
            logger.warning("⚠️ No biomarkers found after all attempts")
            product['error'] = "No biomarkers found after multiple attempts"
        else:
            logger.info("✅ Successfully processed product page")
        
        log_product_info(product)
        return product
        
    except Exception as e:
        logger.error(f"❌ Error processing product page: {e}", exc_info=True)
        product['price'] = None
        product['biomarkers'] = []
        product['error'] = str(e)
        return product

async def save_product_realtime(product, base_url, filename='data/products.json'):
    """
    Save a single product to a single JSON file in real-time, creating or updating the file as needed.
    """
    try:
        # Create data directory if it doesn't exist
        data_dir = Path(filename).parent
        data_dir.mkdir(exist_ok=True)
        
        # Initialize or load existing data
        if Path(filename).exists():
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {
                'scrape_timestamp': datetime.now().isoformat(),
                'sources': {},
                'total_products': 0,
                'products': []
            }
        
        # Update or add source URL info
        if base_url not in data['sources']:
            data['sources'][base_url] = {
                'last_updated': datetime.now().isoformat(),
                'product_count': 0
            }
        
        # Add or update product
        product_exists = False
        for i, existing_product in enumerate(data['products']):
            if existing_product['link'] == product['link']:
                data['products'][i] = product
                product_exists = True
                break
        
        if not product_exists:
            data['products'].append(product)
            data['sources'][base_url]['product_count'] += 1
        
        # Update total count and timestamp
        data['total_products'] = len(data['products'])
        data['sources'][base_url]['last_updated'] = datetime.now().isoformat()
        
        # Save updated data
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"Saved/updated product '{product['name']}' to {filename}")
        return filename
    
    except Exception as e:
        print(f"Error saving product '{product.get('name', 'unknown')}' to JSON: {e}")
        return None

async def main():
    urls = [
        'https://www.bloedwaardentest.nl/bloedonderzoek/check-up/',
        # Add other URLs as needed
    ]
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            args=['--disable-dev-shm-usage']
        )
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            bypass_csp=True,
            ignore_https_errors=True
        )
        
        page = await context.new_page()
        page.set_default_timeout(60000)
        page.set_default_navigation_timeout(60000)
        
        try:
            for url in urls:
                print(f"\nProcessing URL: {url}")
                products = await scrape_page(page, url)
                
                if products:
                    print(f"\nFound {len(products)} products, starting to visit individual pages...")
                    
                    for i, product in enumerate(products, 1):
                        try:
                            print(f"\nProcessing product {i}/{len(products)}: {product['name']}")
                            
                            # Add source URL to product data
                            product['source_url'] = url
                            
                            # Get product details
                            updated_product = await visit_product_page(page, product)
                            
                            # Save immediately after processing each product
                            await save_product_realtime(updated_product, url)
                            
                            # Print summary of current product
                            print(f"\nProduct details:")
                            print(f"Name: {updated_product['name']}")
                            print(f"Price: {updated_product['price']}")
                            print(f"URL: {updated_product['link']}")
                            if updated_product['biomarkers']:
                                print("Biomarkers:")
                                for marker in updated_product['biomarkers']:
                                    if isinstance(marker, dict):
                                        print(f"\n{marker['category']}:")
                                        for biomarker in marker['markers']:
                                            print(f"  - {biomarker}")
                                    else:
                                        print(f"  - {marker}")
                            
                            # Be nice to the server
                            await asyncio.sleep(2)
                            
                        except Exception as e:
                            print(f"Error processing product {product['name']}: {e}")
                            continue
                else:
                    print(f"No products found for {url}")
                
        except Exception as e:
            print(f"Error in main: {e}")
        finally:
            await browser.close()

if __name__ == '__main__':
    asyncio.run(main()) 