from playwright.async_api import async_playwright
import asyncio
from price_parser import Price


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

async def main():
    # URLs to scrape
    urls = [
        'https://www.bloedwaardentest.nl/bloedonderzoek/check-up/',
        # 'https://www.bloedwaardentest.nl/bloedonderzoek/schildklier/'
        # 'https://www.bloedwaardentest.nl/bloedonderzoek/hormonen/'
        # 'https://www.bloedwaardentest.nl/bloedonderzoek/sport-test/'
        # 'https://www.bloedwaardentest.nl/bloedonderzoek/vitamines-mineralen/'
    ]
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        page = await browser.new_page()
        
        # First, collect all products from all pages
        all_products = []
        for url in urls:
            products = await scrape_page(page, url)
            all_products.extend(products)
        
        print(f"\nTotal products found across all pages: {len(all_products)}")
        
        # Then, visit each product page separately
        print("\nStarting to visit individual product pages...")
        products_with_details = []
        for product in all_products:
            updated_product = await visit_product_page(page, product)
            products_with_details.append(updated_product)
            # Add a small delay between requests to be nice to the server
            await asyncio.sleep(2)
        
        # Print all products with their details
        print("\nAll products with details:")
        for product in products_with_details:
            print(f"Name: {product['name']}")
            print(f"Price: {product['price']}")
            print(f"URL: {product['link']}")
            if product['biomarkers']:
                print("Biomarkers:")
                for marker in product['biomarkers']:
                    print(f"  - {marker}")
            print()
        
        print("\nFinished visiting all product pages")
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main()) 