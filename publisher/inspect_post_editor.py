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
    
    print("Ждем 10 секунд для полной загрузки редактора и появления всех модалок...")
    await asyncio.sleep(10)
    
    print("Делаем скриншот до закрытия модалок...")
    await page.screenshot(path="post_before_close.png")
    
    print("Закрываем модалки...")
    await page.evaluate("""() => {
        // Нажимаем Escape несколько раз
        const escEvent = new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true });
        document.dispatchEvent(escEvent);
        
        // Кликаем по кнопкам закрытия
        const closeSelectors = [
            '[data-testid*="close"]', '[data-testid*="cross"]',
            '[aria-label*="Закрыть"]',
            '[class*="close"]', '[class*="Close"]', '[class*="cross"]',
            '.editor--donations-promo-banner-popup__closeButton-1o',
            '[class*="onboarding-banner__close"]',
            '[data-testid="promo-editor-onboarding-button"]'
        ];
        closeSelectors.forEach(s => {
            document.querySelectorAll(s).forEach(b => {
                try { b.click(); } catch(e) {}
            });
        });
        
        // Кликаем ок/понятно
        const confirmTexts = ["Понятно", "Ок", "Хорошо", "Продолжить", "Закрыть"];
        document.querySelectorAll('button').forEach(b => {
            try {
                if (confirmTexts.some(t => b.innerText && b.innerText.includes(t))) {
                    b.click();
                }
            } catch(e) {}
        });
    }""")
    
    await asyncio.sleep(3)
    print("Делаем скриншот после закрытия модалок...")
    await page.screenshot(path="post_after_close.png")
    
    print("Ищем текстовые поля на странице...")
    res = await page.evaluate('''() => {
        const elements = Array.from(document.querySelectorAll('input, textarea, [contenteditable], [role="textbox"], [class*="editor"], [class*="Editor"]'));
        return elements.map(el => ({
            tag: el.tagName,
            id: el.id,
            className: el.className,
            placeholder: el.getAttribute('placeholder') || el.innerText || '',
            role: el.getAttribute('role'),
            contenteditable: el.getAttribute('contenteditable'),
            aria_label: el.getAttribute('aria-label'),
            visible: el.offsetWidth > 0 && el.offsetHeight > 0
        }));
    }''')
    
    import pprint
    pprint.pprint([r for r in res if r['visible']])
    
    await browser.close()
    await pw.stop()

if __name__ == '__main__':
    asyncio.run(main())
