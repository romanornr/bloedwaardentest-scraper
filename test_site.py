from playwright.async_api import async_playwright
import asyncio


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
    
    # Handle cookies using the new function
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
        page_num += 1
    
    print(f"Total products found: {len(all_products)}")
    return all_products

async def main():
    # URLs to scrape
    urls = [
        'https://www.bloedwaardentest.nl/bloedonderzoek/check-up/',
        'https://www.bloedwaardentest.nl/bloedonderzoek/schildklier/'
        'https://www.bloedwaardentest.nl/bloedonderzoek/hormonen/'
        'https://www.bloedwaardentest.nl/bloedonderzoek/sport-test/'
        'https://www.bloedwaardentest.nl/bloedonderzoek/vitamines-mineralen/'
    ]
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        page = await browser.new_page()
        
        all_products = []
        # Scrape each URL
        for url in urls:
            products = await scrape_page(page, url)
            all_products.extend(products)
        
        print(f"\nTotal products found across all pages: {len(all_products)}")
        print("\nWaiting for 10 seconds...")
        await asyncio.sleep(10)
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main()) 