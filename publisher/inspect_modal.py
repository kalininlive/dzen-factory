import asyncio
import sys
import os
sys.path.append('/opt/dzen-publisher/publisher')

from patchright.async_api import async_playwright
from cookies import load_cookies

async def main():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox"]
    )
    cookies = load_cookies('/opt/dzen-publisher/publisher/cookies/dzen_cookies.json')
    ctx = await browser.new_context()
    await ctx.add_cookies(cookies)
    page = await ctx.new_page()
    
    print("Переходим на страницу создания...")
    await page.goto('https://dzen.ru/profile/editor/create', wait_until='domcontentloaded')
    await asyncio.sleep(5)
    
    print("Кликаем Создать...")
    await page.evaluate("document.querySelector('[data-testid=\"add-publication-button\"]').click()")
    await asyncio.sleep(2)
    
    print("Выбираем пост...")
    await page.evaluate("""() => {
        const all = document.querySelectorAll('span, button, li, [role="menuitem"]');
        for (const el of all) {
            const txt = el.innerText ? el.innerText.trim() : "";
            if (txt === 'Написать пост' || txt === 'Создать пост' || txt === 'Пост') {
                el.click();
                return;
            }
        }
    }""")
    await asyncio.sleep(5)
    
    print("Ищем элементы в модальном окне...")
    res = await page.evaluate('''() => {
        const modal = document.querySelector('[role="dialog"], .ReactModal__Content, [class*="modal"]');
        if (!modal) return { 'error': 'Modal not found', 'body_html': document.body.innerHTML.substring(0, 1000) };
        
        const inputs = Array.from(modal.querySelectorAll('input, textarea, [contenteditable], [role="textbox"], [class*="editor"], [class*="Editor"]'));
        const info = inputs.map(el => ({
            tag: el.tagName,
            id: el.id,
            className: el.className,
            placeholder: el.getAttribute('placeholder') || el.innerText || '',
            role: el.getAttribute('role'),
            contenteditable: el.getAttribute('contenteditable'),
            aria_label: el.getAttribute('aria-label')
        }));
        return { 'inputs': info, 'modal_html': modal.innerHTML.substring(0, 2000) };
    }''')
    
    import pprint
    pprint.pprint(res)
    
    await browser.close()
    await pw.stop()

if __name__ == '__main__':
    asyncio.run(main())
