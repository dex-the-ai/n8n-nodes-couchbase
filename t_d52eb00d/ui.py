#!/usr/bin/env python3
"""Playwright UI evidence: open each imported fixture and run the core fixture in the n8n editor."""
import json, os, sys, time
from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
N8N = "http://127.0.0.1:25678"
ids = json.load(open("/home/ubuntu/tmp/t_d52eb00d/fixtures-result.json"))["workflow_ids"]
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 950}, record_video_dir=OUT,
                        record_video_size={"width": 1600, "height": 950})
    pg = ctx.new_page()
    pg.goto(f"{N8N}/signin")
    pg.fill('input[type="email"], input[name="emailOrLdapLoginId"], input[name="email"]', "e2e@example.com")
    pg.fill('input[type="password"]', "Testpassw0rd!")
    pg.keyboard.press("Enter")
    pg.wait_for_url(lambda u: "signin" not in u, timeout=30000)
    time.sleep(2)

    def dismiss():
        for sel in ['button:has-text("Skip")', 'button:has-text("Got it")', '[aria-label="Close"]',
                    'button:has-text("Dismiss")']:
            try:
                el = pg.locator(sel).first
                if el.is_visible(timeout=500):
                    el.click()
            except Exception:
                pass

    pg.goto(f"{N8N}/home/workflows"); time.sleep(3); dismiss()
    pg.screenshot(path=f"{OUT}/00-workflows-list.png")
    pg.goto(f"{N8N}/settings/community-nodes"); time.sleep(3); dismiss()
    pg.screenshot(path=f"{OUT}/01-community-node-installed.png")

    for i, (k, wid) in enumerate((("query", ids["query"]), ("search", ids["search"])), start=2):
        pg.goto(f"{N8N}/workflow/{wid}"); time.sleep(5); dismiss()
        try:
            pg.keyboard.press("Shift+1")  # zoom to fit
        except Exception:
            pass
        time.sleep(1)
        pg.screenshot(path=f"{OUT}/0{i}-{k}-vector-store-fixture.png")

    pg.goto(f"{N8N}/workflow/{ids['core']}"); time.sleep(5); dismiss()
    pg.keyboard.press("Shift+1"); time.sleep(1)
    pg.screenshot(path=f"{OUT}/04-core-fixture-before-run.png")
    btn = pg.locator('[data-test-id="execute-workflow-button"]').first
    btn.click()
    # Wait for the run to finish: the button stops showing the running state.
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(3)
        if pg.locator('[data-test-id="stop-execution-button"]').count() == 0 and \
           pg.locator('text=/Workflow executed successfully|executed successfully/i').count() + 1:
            if pg.locator('[data-test-id="execute-workflow-button"]').first.is_enabled():
                break
    time.sleep(3)
    pg.screenshot(path=f"{OUT}/05-core-fixture-after-ui-run.png")
    # Open the advanced-search node output and the memory load output.
    for idx, name in ((6, "Search Advanced Raw JSON"), (7, "Memory Manager Load")):
        try:
            pg.locator(f'[data-test-id="canvas-node"][data-node-name="{name}"]').first.dblclick()
            time.sleep(3)
            pg.screenshot(path=f"{OUT}/0{idx}-core-{name.lower().replace(' ', '-').replace('+', '')}-output.png")
            pg.keyboard.press("Escape"); time.sleep(1)
        except Exception as e:
            print("could not open", name, e)
    pg.goto(f"{N8N}/workflow/{ids['core']}/executions"); time.sleep(4); dismiss()
    pg.screenshot(path=f"{OUT}/08-core-executions.png")
    video = pg.video.path()
    ctx.close(); b.close()
    os.rename(video, f"{OUT}/core-fixture-ui-run.webm")
print("done", sorted(os.listdir(OUT)))
