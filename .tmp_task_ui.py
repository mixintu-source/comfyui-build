import time
from playwright.sync_api import sync_playwright
errors=[]
with sync_playwright() as p:
    b=p.chromium.launch(channel="chrome", headless=True)
    page=b.new_page(viewport={"width":1440,"height":1200})
    page.on("console", lambda m: errors.append(m.text) if m.type=="error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:8766/#models", wait_until="networkidle")
    page.wait_for_function("document.querySelector('#sumSource').textContent.includes('9')", timeout=15000)
    page.locator('button.migrate-model-btn[data-rel="models/diffusion_models/progress-test.safetensors"]').click()
    page.wait_for_selector(".overlay", timeout=10000)
    page.wait_for_function("!document.querySelector('#spaceInfo').textContent.includes('检查中')", timeout=10000)
    print("space:", page.text_content("#spaceInfo"))
    page.click('[data-action="confirm"]')
    samples=[]
    for _ in range(30):
        if page.locator("#taskMeta").count()==0: break
        samples.append(page.text_content("#taskMeta"))
        time.sleep(0.15)
    print("progress samples:", samples[:5], "...", samples[-2:])
    page.wait_for_function("document.querySelector('#sumTarget').textContent.includes('1')", timeout=60000)
    print("target after copy", page.text_content("#sumTarget"))

    page.locator('button.migrate-model-btn[data-rel="models/background_removal/birefnet.safetensors"]').click()
    page.wait_for_selector(".overlay", timeout=10000)
    page.check('input[name="dest"][value="comfyui"]')
    page.check("#cutSource")
    page.wait_for_timeout(800)
    print("same-location info:", page.text_content("#spaceInfo"))
    print("confirm disabled:", page.locator('[data-action="confirm"]').is_disabled())
    page.click('[data-action="cancel"]')

    page.locator('button.migrate-model-btn[data-rel="models/background_removal/birefnet.safetensors"]').click()
    page.wait_for_selector(".overlay", timeout=10000)
    page.check("#cutSource")
    page.wait_for_timeout(800)
    print("cut-to-target info:", page.text_content("#spaceInfo"))
    page.click('[data-action="confirm"]')
    page.wait_for_timeout(1500)
    print("target after cut", page.text_content("#sumTarget"))
    print("errors", errors)
    b.close()
if errors: raise SystemExit(1)
