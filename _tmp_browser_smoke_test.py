from core.intent_executor import IntentExecutor

ex = IntentExecutor()

tests = [
    ("BROWSER_OPEN", {"url": "https://example.com"}),
    ("BROWSER_PAGE_INFO", {}),
    ("BROWSER_NEW_TAB", {"url": "https://www.wikipedia.org"}),
    ("BROWSER_LIST_TABS", {}),
    ("BROWSER_SWITCH_TAB", {"index": 1}),
    ("BROWSER_SEARCH", {"query": "python list comprehension"}),
    ("BROWSER_OPEN_RESULT", {"rank": 1}),
    ("BROWSER_READ", {}),
    ("BROWSER_EXTRACT_LINKS", {}),
    ("BROWSER_SUMMARIZE", {}),
    ("BROWSER_BACK", {}),
    ("BROWSER_FORWARD", {}),
    ("BROWSER_RELOAD", {}),
    ("BROWSER_CONTEXT", {}),
    ("BROWSER_CLOSE_TAB", {}),
    ("BROWSER_CLOSE", {}),
]

print("--- BROWSER SMOKE TEST START ---")
for name, params in tests:
    try:
        r = ex.execute(name, params)
    except Exception as e:
        print(f"{name}: EXCEPTION {e!r}")
        continue

    ok = isinstance(r, dict) and bool(r.get("success"))
    msg = (r.get("message") if isinstance(r, dict) else str(r))
    print(f"{name}: {'PASS' if ok else 'FAIL'} - {str(msg)[:220].replace(chr(10), ' | ')}")

print("--- BROWSER SMOKE TEST END ---")
