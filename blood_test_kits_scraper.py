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
    
    # Display biomarker count information
    if 'biomarker_count' in product:
        if 'category_count' in product:
            logger.info(f"│ Biomarkers: {product['biomarker_count']} across {product['category_count']} categories")
        else:
            logger.info(f"│ Biomarkers: {product['biomarker_count']}")
    
    if product.get('biomarkers'):
        logger.info("│")
        logger.info("│ Biomarkers:")
        for marker in product['biomarkers']:
            if isinstance(marker, dict):
                logger.info(f"│   {marker['category']} ({len(marker.get('markers', []))} markers):")
                for biomarker in marker.get('markers', []):
                    logger.info(f"│     • {biomarker}")
            else:
                logger.info(f"│     • {marker}")
    logger.info("└" + "─" * 65)

async def get_products(page):
    logger.debug("Getting products...")
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
    logger.debug("Checking for cookie consent dialog...")
    try:
        cookie_button = await page.query_selector('button#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll')
        if cookie_button:
            logger.debug("Cookie consent dialog found, accepting...")
            await cookie_button.click(timeout=5000)
            logger.debug("Cookies accepted successfully")
            return True
        logger.debug("No cookie consent dialog found")
        return False
    except Exception as e:
        logger.warning(f"Error handling cookies: {e}")
        return False

async def has_next_page(page):
    next_button = await page.query_selector('nav.pagination-a li.next a[rel="next"]')
    if next_button:
        next_url = await next_button.get_attribute('href')
        return next_url
    return None

async def scrape_page(page, url):
    logger.info(f"Visiting {url}...")
    await page.goto(url)
    
    # Handle cookies on initial page load
    await handle_cookies(page)
    
    all_products = []
    current_url = url
    page_num = 1
    
    while True:
        # Get products from current page
        products = await get_products(page)
        logger.info(f"Found {len(products)} products on page {page_num}")
        logger.debug(f"Products on page {page_num} at {current_url}:")
        for i, product in enumerate(products, 1):
            logger.debug(f"  {i}. {product['name']} - {product['link']}")
        
        all_products.extend(products)
        
        # Check for next page
        next_url = await has_next_page(page)
        if not next_url:
            logger.debug("No more pages to scrape")
            break
            
        logger.debug(f"Found next page: {next_url}")
        current_url = next_url
        await page.goto(next_url)
        
        # Handle cookies after each page navigation
        await handle_cookies(page)
        
        page_num += 1
    
    logger.info(f"Total products found: {len(all_products)}")
    return all_products

def convert_price_to_number(price_text):
    try:
        # Use price-parser to handle the conversion
        price = Price.fromstring(price_text)
        if price.amount is not None:
            return float(price.amount)
        return None
    except Exception as e:
        logger.warning(f"Error converting price '{price_text}' to number: {e}")
        return None

async def get_product_price(page):
    logger.debug("Getting product price...")
    try:
        price_element = await page.wait_for_selector('div.price-wrapper span.main-price')
        if price_element:
            price_text = await price_element.text_content()
            price_text = price_text.strip()
            price_number = convert_price_to_number(price_text)
            
            # Check if price is zero or "0,-"
            if price_number == 0 or price_text in ["0", "0,-", "€0", "€0,-"]:
                logger.debug("Found zero price, marking as invalid")
                return 0
                
            logger.debug(f"Found price: {price_number}")
            return price_number
                
        logger.warning("Price element not found")
        return None
    except Exception as e:
        logger.warning(f"Error getting price: {e}")
        return None

async def expand_content_via_dom(page):
    """
    Directly manipulate the DOM to expand the content without relying on button clicks.
    """
    logger.info("🔧 Attempting content expansion via DOM manipulation")
    try:
        expanded = await page.evaluate('''
            () => {
                // Find the toggle container
                const container = document.querySelector('article.module-info-update.module-info.toggle.has-anchor');
                if (!container) {
                    console.log('Toggle container not found');
                    return { success: false, message: 'Toggle container not found' };
                }
                
                // Add the expanded class
                container.classList.add('expanded');
                console.log("Added 'expanded' class to container");
                
                // Set the style to display the content
                const content = container.querySelector('.toggle-content');
                if (content) {
                    content.style.display = 'block';
                    console.log("Set content display to 'block'");
                } else {
                    console.log('Toggle content not found');
                }
                
                // Update the button if it exists
                const button = document.querySelector('a.show-more');
                if (button) {
                    button.classList.add('active');
                    button.textContent = button.textContent.replace('Lees meer', 'Lees minder');
                    console.log("Updated button state");
                } else {
                    console.log('Show more button not found');
                }
                
                return { 
                    success: true, 
                    message: 'DOM manipulation completed',
                    containerModified: true,
                    contentModified: !!content,
                    buttonModified: !!button
                };
            }
        ''')
        
        if expanded.get('success', False):
            logger.info("✅ Content expanded successfully via DOM manipulation")
            logger.debug(f"DOM manipulation details: {expanded}")
            # Additional logging for specific DOM changes
            if expanded.get('containerModified'):
                logger.debug("Added 'expanded' class to container")
            if expanded.get('contentModified'):
                logger.debug("Set content display to 'block'")
            if expanded.get('buttonModified'):
                logger.debug("Updated button text and added 'active' class")
            return True
        else:
            logger.warning(f"❌ Failed to expand content via DOM manipulation: {expanded.get('message', 'Unknown error')}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error during DOM manipulation: {e}")
        return False

async def expand_read_more(page):
    """
    Expand the 'Read more' content using direct DOM manipulation.
    """
    logger.debug("Checking for expandable content...")
    try:
        # Check if there's a 'Lees meer' button to indicate expandable content
        show_more_button = await page.query_selector('a.show-more')
        if show_more_button:
            logger.info("🔍 Found expandable content, performing DOM manipulation...")
            
            # Use DOM manipulation to expand the content
            expanded = await page.evaluate('''
                () => {
                    // Find the toggle container
                    const container = document.querySelector('article.module-info-update.module-info.toggle.has-anchor');
                    if (!container) {
                        console.log('Toggle container not found');
                        return { success: false, message: 'Toggle container not found' };
                    }
                    
                    // Add the expanded class
                    container.classList.add('expanded');
                    console.log("Added 'expanded' class to container");
                    
                    // Set the style to display the content
                    const content = container.querySelector('.toggle-content');
                    if (content) {
                        content.style.display = 'block';
                        console.log("Set content display to 'block'");
                    } else {
                        console.log('Toggle content not found');
                    }
                    
                    // Update the button if it exists
                    const button = document.querySelector('a.show-more');
                    if (button) {
                        button.classList.add('active');
                        button.textContent = button.textContent.replace('Lees meer', 'Lees minder');
                        console.log("Updated button state");
                    } else {
                        console.log('Show more button not found');
                    }
                    
                    return { 
                        success: true, 
                        message: 'DOM manipulation completed',
                        containerModified: true,
                        contentModified: !!content,
                        buttonModified: !!button
                    };
                }
            ''')
            
            if expanded.get('success', False):
                logger.info("✅ Content expanded successfully via DOM manipulation")
                return True
            else:
                logger.warning(f"❌ Failed to expand content: {expanded.get('message', 'Unknown error')}")
                return False
        
        logger.debug("No expandable content found")
        return False
    except Exception as e:
        logger.error(f"❌ Error expanding content: {e}")
        return False

async def extract_biomarkers_from_content(page):
    logger.debug("Extracting biomarkers with complex structure...")
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
            logger.debug("Found categorized biomarkers:")
            for category in markers:
                logger.debug(f"  Category: {category['category']}, Markers: {len(category['markers'])}")
            return markers
            
        logger.debug("No categorized biomarkers found")
        return []
        
    except Exception as e:
        logger.warning(f"Error extracting complex biomarkers: {e}")
        return []

async def get_product_biomarkers(page):
    logger.debug("Getting product biomarkers...")
    try:
        # First expand the content if needed
        await expand_read_more(page)
        
        # PRIORITIZE: Check for ordered lists FIRST
        logger.info("🔬 PRIORITY: Checking for biomarkers in ordered lists (<ol> tags)")
        ordered_list_markers = await extract_biomarkers_from_ordered_list(page)
        if ordered_list_markers:
            logger.info(f"✅ SUCCESS: Found {len(ordered_list_markers)} biomarkers in ordered lists")
            return ordered_list_markers
            
        # Only if no ordered lists found, try complex extraction
        logger.debug("No ordered lists found, trying complex extraction...")
        biomarkers = await extract_biomarkers_from_content(page)
        if biomarkers:
            logger.info("Found biomarkers using complex extraction")
            return biomarkers
        
        # Try the unordered list extraction as last resort
        logger.debug("Trying unordered list extraction as last resort...")
        try:
            await page.wait_for_selector('div.desc-wrapper ul', timeout=5000)
            simple_markers = await page.evaluate('''
                () => {
                    const ul = document.querySelector('div.desc-wrapper ul');
                    if (!ul) return [];
                    
                    // Filter out instruction texts
                    const excludeTexts = ['bestel', 'brievenbus', 'prikpunt', 'kortingscode', 'upload', 
                                          'laat je', 'ontvang je', 'plaats je', 'leg je', 'voer je'];
                    
                    return Array.from(ul.querySelectorAll('li'))
                        .map(li => li.textContent.trim())
                        .filter(text => !excludeTexts.some(exclude => 
                            text.toLowerCase().includes(exclude)
                        ));
                }
            ''')
            
            if simple_markers:
                logger.debug(f"Found {len(simple_markers)} simple biomarkers")
                return simple_markers
        except Exception as e:
            logger.debug(f"No unordered lists found or error: {e}")
            
        logger.debug("No biomarkers found with any extraction method")
        return []
        
    except Exception as e:
        logger.warning(f"Error getting biomarkers: {e}")
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

def count_biomarkers(biomarkers):
    """
    Count biomarkers in different formats and return count information.
    Returns a tuple of (total_count, category_count) where category_count may be None.
    """
    # Initialize counts
    total_count = 0
    category_count = None
    
    # Handle empty biomarkers
    if not biomarkers:
        return 0, None
    
    # Check if we have a list
    if isinstance(biomarkers, list):
        # Check if we have categorized biomarkers (list of dicts)
        if biomarkers and isinstance(biomarkers[0], dict):
            # Count all markers across all categories
            total_count = sum(len(category.get('markers', [])) for category in biomarkers)
            category_count = len(biomarkers)
            logger.debug(f"Counted {total_count} biomarkers across {category_count} categories")
        else:
            # Simple list count for flat biomarker list
            total_count = len(biomarkers)
            logger.debug(f"Counted {total_count} biomarkers in flat list")
    else:
        logger.warning("⚠️ Unexpected biomarker format for counting")
    
    return total_count, category_count

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
        
        # Either wait for price wrapper or description wrapper - both should be present on product pages
        try:
            logger.debug("⌛ Waiting for product content")
            await page.wait_for_selector('div.price-wrapper, div.desc-wrapper', timeout=10000)
            logger.debug("✅ Product content found")
        except Exception as e:
            logger.warning(f"⚠️ Product content not found: {e}")
        
        logger.debug("💰 Getting product price")
        price = await get_product_price(page)
        logger.info(f"Found price: {price}")
        
        # Skip products with zero price
        if price == 0:
            logger.info("⏩ Skipping product with zero price")
            product['price'] = 0
            product['biomarkers'] = []
            product['biomarker_count'] = 0
            product['skipped'] = True
            product['reason'] = "Zero price product"
            return product
        
        # Continue with biomarker extraction as before
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
                    logger.info(f"Successfully found biomarkers on attempt {attempt + 1}")
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
        
        # Count biomarkers using the dedicated function
        total_count, category_count = count_biomarkers(biomarkers)
        product['biomarker_count'] = total_count
        
        if category_count:
            product['category_count'] = category_count
            logger.info(f"📊 Found {total_count} biomarkers across {category_count} categories")
        else:
            logger.info(f"📊 Found {total_count} biomarkers")
            
        if total_count == 0:
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
        product['biomarker_count'] = 0
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
        
        logger.info(f"Saved product '{product['name']}' to {filename}")
        return filename
    
    except Exception as e:
        logger.error(f"Error saving product '{product.get('name', 'unknown')}' to JSON: {e}")
        return None

async def extract_biomarkers_from_ordered_list(page):
    """
    Handle edge case where biomarkers are in an ordered list (<ol>).
    Specifically targets <ol> elements and excludes instruction lists.
    """
    logger.debug("🔍 Attempting to extract biomarkers from ordered lists...")
    try:
        markers = await page.evaluate('''
            () => {
                // Helper function to clean text
                const cleanText = (text) => text.replace(/\\s+/g, ' ').trim();
                
                // Specifically target <ol> elements
                const orderedLists = Array.from(document.querySelectorAll('div.desc-wrapper ol'));
                
                if (orderedLists.length === 0) {
                    console.log('No ordered lists found in description');
                    return [];
                }
                
                // Process each ordered list
                let biomarkers = [];
                
                for (const ol of orderedLists) {
                    // Extract markers from list items
                    const items = Array.from(ol.querySelectorAll('li'))
                        .map(li => cleanText(li.textContent))
                        .filter(text => {
                            if (text.length === 0) return false;
                            
                            // Filter out instruction-like texts
                            const excludeTexts = ['bestel', 'brievenbus', 'prikpunt', 'kortingscode', 'upload', 
                                'plaats je bestelling', 'ontvang je', 'maak een dashboard'];
                            const isInstruction = excludeTexts.some(exclude => 
                                text.toLowerCase().includes(exclude)
                            );
                            
                            if (isInstruction) return false;
                            
                            // First check for common biomarker patterns that we're sure about
                            if (/Vitamine|Calcium|Glucose|Cholesterol|Albumine|Ferritine|Kalium|Natrium|Foliumzuur|Transferrine|Testosteron|Globulin|Cortisol|Creatine|Hemoglobine|IJzer/.test(text)) {
                                return true;
                            }
                            
                            // Check if text contains parentheses with abbreviations, common in biomarkers
                            if (/\([A-Z]{2,}[\)\s-]/.test(text)) {
                                return true;
                            }
                            
                            // Check for capitalized words that might be biomarkers (most biomarkers start with capitals)
                            if (/^[A-Z][a-z]+/.test(text)) {
                                return true;
                            }
                            
                            // As a last resort, check if it's likely a biomarker by looking for medical terms
                            return !/^[a-z]/.test(text); // Not starting with lowercase (most instructions do)
                        });
                    
                    if (items.length > 0) {
                        biomarkers = biomarkers.concat(items);
                    }
                }
                
                return biomarkers;
            }
        ''')
        
        if markers and len(markers) > 0:
            logger.info(f"✅ Found {len(markers)} biomarkers in ordered lists")
            for marker in markers:
                logger.debug(f"  • {marker}")
            return markers
            
        logger.debug("No biomarkers found in ordered lists")
        return []
        
    except Exception as e:
        logger.warning(f"❌ Error extracting biomarkers from ordered lists: {e}")
        return []

async def main():
    urls = [
        #'https://www.bloedwaardentest.nl/bloedonderzoek/check-up/',
        #'https://www.bloedwaardentest.nl/bloedonderzoek/bioleeftijd/',
        #'https://www.bloedwaardentest.nl/bloedonderzoek/schildklier/',
        'https://www.bloedwaardentest.nl/bloedonderzoek/insidetracker/',
        #'https://www.bloedwaardentest.nl/bloedonderzoek/hormonen/hormonen-mannen/',
        #'https://www.bloedwaardentest.nl/bloedonderzoek/sport-test/',
        #'https://www.bloedwaardentest.nl/bloedonderzoek/vitamines-mineralen/',
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
                log_section(f"Processing URL: {url}")
                products = await scrape_page(page, url)
                
                if products:
                    logger.info(f"Starting to process {len(products)} product pages...")
                    
                    for i, product in enumerate(products, 1):
                        try:
                            logger.info(f"Processing product {i}/{len(products)}: {product['name']}")
                            
                            # Add source URL to product data
                            product['source_url'] = url
                            
                            # Get product details
                            updated_product = await visit_product_page(page, product)
                            
                            # Only save if not skipped or if you want to save skipped products too
                            if not updated_product.get('skipped', False):
                                await save_product_realtime(updated_product, url)
                            else:
                                logger.info(f"Not saving skipped product: {updated_product['name']}")
                            
                            # Be nice to the server
                            await asyncio.sleep(2)
                            
                        except Exception as e:
                            logger.error(f"Error processing product {product['name']}: {e}")
                            continue
                else:
                    logger.warning(f"No products found for {url}")
                
        except Exception as e:
            logger.error(f"Error in main: {e}", exc_info=True)
        finally:
            await browser.close()

if __name__ == '__main__':
    asyncio.run(main()) 