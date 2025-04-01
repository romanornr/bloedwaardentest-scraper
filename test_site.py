from playwright.async_api import async_playwright
import asyncio
from price_parser import Price
import json
from pathlib import Path
from datetime import datetime


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

async def visit_product_page(page, product):
    print(f"\nVisiting product: {product['name']}")
    print(f"URL: {product['link']}")
    
    try:
        # Navigate to the page
        await page.goto(product['link'])
        
        # Try to wait for network idle, but don't fail if it times out
        try:
            await page.wait_for_load_state('networkidle', timeout=10000)  # 10 seconds timeout
        except Exception as e:
            print(f"Note: Page didn't reach network idle state: {e}")
            # Wait a bit to let the page settle
            await asyncio.sleep(2)
        
        # Handle cookies on product page
        await handle_cookies(page)
        
        # Get the price using the new function
        price = await get_product_price(page)
        
        # Get biomarkers using the new function
        biomarkers = await get_product_biomarkers(page)
        
        # Add data to product
        product['price'] = price
        product['biomarkers'] = biomarkers
        
        print("Successfully loaded product page")
        return product
    except Exception as e:
        print(f"Error processing product page: {e}")
        # Return product with error information
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