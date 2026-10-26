"""Real-browser setup checks against isolated consumer servers; no live APIs."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import queue
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def run(source: Path, package: str, browser_executable: str | None = None, *, installed=False, screenshots=None, launch_fixture=False):
    import sys

    from playwright.sync_api import sync_playwright

    if not installed:
        sys.path.insert(0, str(source.resolve() / "src"))
    web = importlib.import_module(package + ".web")
    config_module = importlib.import_module(package + ".config")
    codex = package == "codex_adapter"
    urls, servers, launches, failures = queue.Queue(), [], [], []
    with tempfile.TemporaryDirectory(prefix="protoverta-browser-") as temporary, ExitStack() as stack:
        root = Path(temporary)
        from test_environment import sanitized_environment

        safe = sanitized_environment()
        # Resolve the test browser cache before replacing LOCALAPPDATA; never use
        # the user's browser profile. Cache binaries are read-only to this run.
        safe["PLAYWRIGHT_BROWSERS_PATH"] = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(Path(os.environ.get("LOCALAPPDATA", temporary)) / "ms-playwright"))
        for name in ("roaming", "local", "profile"):
            (root / name).mkdir()
        safe.update(APPDATA=str(root / "roaming"), LOCALAPPDATA=str(root / "local"), USERPROFILE=str(root / "profile"), HOME=str(root / "profile"))
        safe.update({("CODEX_CA_ENV_FILE" if codex else "CLAUDE_CA_ENV_FILE"): str(root / ".env")})
        stack.enter_context(patch.dict(os.environ, safe, clear=True))
        config = config_module.Config(path=root / "config.toml")

        setup_server = web.ThreadingHTTPServer

        def server(address, handler):
            result = setup_server(address, handler)
            servers.append(result)
            return result

        stack.enter_context(patch.object(web, "ThreadingHTTPServer", side_effect=server))
        stack.enter_context(patch.object(web.webbrowser, "open", side_effect=urls.put))
        stack.enter_context(patch.object(web, "fetch_models", return_value=["model-" + str(i) for i in range(20)] if codex else [{"id": "model-" + str(i), "display_name": "Model " + str(i)} for i in range(20)]))
        stack.enter_context(patch.object(web, "preflight"))
        foreground = None
        if launch_fixture:
            from setup_launch_fixture import SetupLaunchFixture

            foreground = SetupLaunchFixture(root, web, importlib.import_module(package + ".launcher"))
            stack.enter_context(patch.object(web, "launch", side_effect=foreground.launch))
        else:
            stack.enter_context(patch.object(web, "launch", side_effect=lambda *args: launches.append(args) or 0))

        def serve():
            try:
                result = web.serve(config)
                if foreground:
                    assert result == 7, "Foreground CLI exit code was lost"
            except (OSError, ValueError, RuntimeError, AssertionError) as exc:
                failures.append(type(exc).__name__)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            url = urls.get(timeout=5)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, **({"executable_path": browser_executable} if browser_executable else {}))
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    errors = []
                    page.on("pageerror", lambda exc: errors.append(str(exc)))
                    # Local setup/assets only; never contact a real provider.
                    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(url.split("/?")[0] + "/") else route.abort())
                    page.goto(url)
                    select = "#providerSelect" if codex else "#provider"
                    page.locator(select).wait_for()
                    assert page.locator(select).is_disabled()
                    page.locator("#addBtn" if codex else "#add").click()
                    page.locator("#providerId" if codex else "#id").fill("fixture")
                    page.locator("#displayName").fill("Fixture <b>Provider</b>")
                    page.locator("#baseUrl").fill("https://example.invalid/v1")
                    if codex:
                        page.locator("#providerProtocol").select_option("chat_completions")
                        page.locator("#useGateway").check()
                    else:
                        page.locator("#protocol").select_option("chat")
                        assert page.locator("#executionMode").input_value() == "gateway"
                    page.locator("#apiKey").fill("synthetic-browser-key")
                    page.locator("#gatewayRuntimeFields").evaluate("(el) => el.closest('details').open = true")
                    page.locator("#runtime_retry_attempts").fill("2")
                    with page.expect_response(lambda response: "/api/provider" in response.url and response.request.method == "POST") as saved:
                        page.locator("#saveProvider" if codex else "#providerForm button[type=submit]").click()
                    assert saved.value.ok
                    page.wait_for_function("selector => document.querySelector(selector).options.length === 1", arg=select)
                    assert page.locator(select + " option").inner_text() == "Fixture <b>Provider</b>"
                    assert page.locator(select + " b").count() == 0
                    model = "#modelInput" if codex else "#model"
                    page.locator(model).fill("")
                    page.wait_for_function("document.querySelectorAll('.model-option').length === 20")
                    page.locator(model).fill("model-19")
                    page.wait_for_function("document.querySelectorAll('.model-option').length === 1")
                    page.locator("#editBtn" if codex else "#edit").click()
                    page.locator("#displayName").focus()
                    page.keyboard.press("Escape")
                    assert page.locator("#providerForm").evaluate("el => el.classList.contains('hidden')")
                    assert page.evaluate("document.activeElement.id") == ("editBtn" if codex else "edit")
                    page.locator("#editBtn" if codex else "#edit").click()
                    assert page.locator("#baseUrl").input_value() == "https://example.invalid/v1"
                    assert page.locator("#authMode").input_value() == "bearer"
                    assert page.locator("#runtime_retry_attempts").input_value() == "2"
                    page.locator("#authMode").select_option("api_key")
                    with page.expect_response(lambda response: "/api/provider" in response.url and response.request.method == "POST") as saved:
                        # Same-task assertions cannot race the HTTP response: both
                        # model entry and Run must stay disabled until save settles.
                        assert page.evaluate("""codex => {
                            if (codex) document.querySelector('#saveProvider').click();
                            else document.querySelector('#providerForm').requestSubmit();
                            return document.querySelector(codex ? '#modelInput' : '#model').disabled
                                && document.querySelector(codex ? '#runBtn' : '#run').disabled;
                        }""", codex)
                    assert saved.value.ok
                    page.locator(model).fill("manual-model")
                    page.route("**/api/models**", lambda route: route.abort())
                    page.locator("#refreshBtn" if codex else "#refresh").click()
                    page.wait_for_function("id => !document.getElementById(id).disabled", arg="refreshBtn" if codex else "refresh")
                    assert page.locator(model).is_enabled()
                    assert page.locator(model).input_value() == "manual-model"
                    page.unroute("**/api/models**")
                    page.keyboard.press("Tab")
                    assert page.evaluate("document.activeElement.tagName") != "BODY"
                    before = page.locator("html").get_attribute("data-theme")
                    page.locator("#themeToggle").click()
                    assert page.locator("html").get_attribute("data-theme") != before
                    page.wait_for_timeout(300)  # Let the real CSS theme transition settle.
                    assert "_icon.png" in page.locator("#favicon").get_attribute("href")
                    assert "_icon" not in page.locator("#brandLogo").get_attribute("src")
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    if screenshots:
                        screenshots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(screenshots / (package + "-desktop.png")), full_page=True)
                        page.locator("#themeToggle").click()
                        page.wait_for_timeout(300)
                        page.screenshot(path=str(screenshots / (package + "-alternate-theme.png")), full_page=True)
                        page.locator("#themeToggle").click()
                        page.wait_for_timeout(300)
                        page.set_viewport_size({"width": 390, "height": 844})
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        page.screenshot(path=str(screenshots / (package + "-mobile.png")), full_page=True)
                    with page.expect_response(lambda response: "/api/run" in response.url) as started:
                        page.locator("#runBtn" if codex else "#run").click()
                    assert started.value.ok
                    if foreground:
                        foreground.release_after_ready(url, worker)
                    worker.join(timeout=5)
                    assert not worker.is_alive() and (foreground.calls if foreground else launches) and not failures
                    if foreground:
                        assert foreground.exit_codes == [7]
                        assert (root / "child-finished").is_file()
                    restored = config_module.load(config.path).providers["fixture"]
                    assert restored.auth_mode == "api_key"
                    assert restored.gateway_runtime["retry_attempts"] == 2
                    assert not errors, errors
                    return {"package": package, "passed": True, "launch_stubbed": not launch_fixture, "synthetic_foreground_cli": launch_fixture}
                finally:
                    browser.close()
        finally:
            if foreground:
                foreground.release()
            if worker.is_alive():
                for running in servers:
                    running.shutdown()
                worker.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--package", choices=["codex_adapter", "claudapter"], required=True)
    parser.add_argument("--browser-executable")
    parser.add_argument("--installed", action="store_true", help="Test the installed wheel, not checkout imports")
    parser.add_argument("--screenshots", type=Path, help="Optional synthetic-fixture screenshots directory")
    parser.add_argument("--launch-fixture", action="store_true", help="Exercise real foreground launcher/gateway with a synthetic CLI child")
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.package, args.browser_executable, installed=args.installed, screenshots=args.screenshots, launch_fixture=args.launch_fixture)))


if __name__ == "__main__":
    main()
