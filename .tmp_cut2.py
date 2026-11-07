import time
from playwright.sync_api import sync_playwright
errors=[]
with sync_playwright() as p:
    b=p.chromium.launch(channel="chrome", headless=True)
    page=b.new_page(viewport={"width":1440,"height":1100})
    page.on("console", lambda m: errors.append(m.text) if m.type=="error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:8766/#models", wait_until="networkidle")
    page.wait_for_function("document.querySelector('#sumSource').textContent.includes('8')", timeout=15000)
    page.locator('button.migrate-model-btn[data-rel="models/background_removal/birefnet.safetensors"]').click()
    page.wait_for_selector(".overlay", timeout=10000)
    page.check("#cutSource")
    page.wait_for_timeout(800)
    page.click('[data-action="confirm"]')
    for _ in range(100):
        if page.locator("#taskMeta").count()==0: break
        time.sleep(0.1)
    page.wait_for_timeout(800)
    print("target", page.text_content("#sumTarget"), "errors", errors)
    b.close()
if errors: raise SystemExit(1)
