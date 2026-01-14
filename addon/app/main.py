import asyncio
import base64
import json
import sys
from getpass import getpass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

CONFIG_PATH = Path("/data/options.json")
OUTPUT_PATH = Path("/config/eduvulcan_token.json")
LOGIN_URL = "https://eduvulcan.pl/api/ap"


def log(message: str) -> None:
    print(message, flush=True)


def read_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
        raise RuntimeError("Config file format is invalid")
    except Exception as exc:
        raise RuntimeError("Failed to read add-on configuration") from exc


def prompt_for_credentials(config: Dict[str, Any]) -> Dict[str, str]:
    login = (config.get("login") or "").strip()
    password = config.get("password") or ""

    if login and password:
        return {"login": login, "password": password}

    log("Prompting for credentials")
    if not login:
        login = input("Login: ").strip()
    if not password:
        try:
            password = getpass("Password: ")
        except Exception:
            password = input("Password: ")

    return {"login": login, "password": password}


def is_jwt(value: Any) -> bool:
    return isinstance(value, str) and value.count(".") == 2


def extract_jwt(ap_data: Dict[str, Any]) -> str:
    tokens: Optional[Iterable[Any]] = ap_data.get("Tokens") or ap_data.get("tokens")
    if tokens is None:
        raise RuntimeError("Tokens not found in login response")

    if isinstance(tokens, dict):
        tokens_iter: Iterable[Any] = [tokens]
    else:
        tokens_iter = tokens

    for item in tokens_iter:
        if is_jwt(item):
            return item
        if isinstance(item, dict):
            for key in (
                "Token",
                "token",
                "Value",
                "value",
                "AccessToken",
                "access_token",
                "Jwt",
                "jwt",
            ):
                token_value = item.get(key)
                if is_jwt(token_value):
                    return token_value

    raise RuntimeError("JWT token not found in Tokens")


def decode_jwt_payload(jwt_token: str) -> Dict[str, Any]:
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise RuntimeError("Invalid JWT format")

    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)

    try:
        payload_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("Failed to decode JWT payload") from exc

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("JWT payload is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("JWT payload is not a JSON object")

    return payload


async def click_if_present(page, selector: str, timeout_ms: int = 1000) -> bool:
    locator = page.locator(selector)
    try:
        count = await locator.count()
    except Exception:
        return False

    if count == 0:
        return False

    for index in range(count):
        candidate = locator.nth(index)
        try:
            if await candidate.is_visible():
                await candidate.click(timeout=timeout_ms)
                return True
        except Exception:
            continue

    return False


async def remove_overlay(page) -> None:
    selectors = [
        "#onetrust-accept-btn-handler",
        "button:has-text('Akceptuj')",
        "button:has-text('Akceptuje')",
        "button:has-text('Zgadzam')",
        "button:has-text('Rozumiem')",
        "button:has-text('Accept')",
        "button:has-text('OK')",
    ]
    for selector in selectors:
        if await click_if_present(page, selector):
            return

    await page.evaluate(
        """() => {
        const ids = [
          'onetrust-banner-sdk',
          'cookie',
          'cookies',
          'cookie-policy',
          'rodo',
        ];
        for (const id of ids) {
          const el = document.getElementById(id);
          if (el) {
            el.remove();
          }
        }
        const nodes = document.querySelectorAll(
          '[class*="cookie"], [class*="rodo"], [id*="cookie"], [id*="rodo"]'
        );
        for (const node of nodes) {
          if (node && node.style) {
            node.style.display = 'none';
          }
        }
      }"""
    )


async def fill_by_labels(page, labels: Iterable[str], value: str) -> bool:
    for label in labels:
        locator = page.get_by_label(label, exact=False)
        try:
            count = await locator.count()
        except Exception:
            continue
        if count == 0:
            continue
        try:
            await locator.first.fill(value)
            return True
        except Exception:
            continue
    return False


async def fill_by_selectors(
    page, selectors: Iterable[str], value: str, field_name: str
) -> None:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        if count == 0:
            continue
        for index in range(count):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible():
                    await candidate.fill(value)
                    return
            except Exception:
                continue

    raise RuntimeError(f"Could not find {field_name} field")


async def fill_login(page, login: str) -> None:
    labels = ["Login", "E-mail", "Email", "Nazwa uzytkownika", "Uzytkownik"]
    if await fill_by_labels(page, labels, login):
        return

    selectors = [
        "input[name='login']",
        "input#login",
        "input[name='email']",
        "input[type='email']",
        "input[name='username']",
        "input[type='text']",
    ]
    await fill_by_selectors(page, selectors, login, "login")


async def fill_password(page, password: str) -> None:
    labels = ["Haslo", "Password"]
    if await fill_by_labels(page, labels, password):
        return

    selectors = [
        "input[type='password']",
        "input[name='password']",
        "input#password",
    ]
    await fill_by_selectors(page, selectors, password, "password")


async def submit_login(page) -> None:
    selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Zaloguj')",
        "button:has-text('Zaloguj sie')",
        "button:has-text('Login')",
        "button:has-text('Sign in')",
    ]
    for selector in selectors:
        if await click_if_present(page, selector, timeout_ms=5000):
            return

    raise RuntimeError("Could not find submit button")


async def retrieve_jwt(login: str, password: str) -> str:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        await remove_overlay(page)
        await fill_login(page, login)
        await fill_password(page, password)
        await submit_login(page)

        try:
            await page.wait_for_selector("#ap", timeout=30000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("Timed out waiting for token payload") from exc

        ap_value = await page.locator("#ap").get_attribute("value")
        if not ap_value:
            raise RuntimeError("Token payload is empty")

        try:
            ap_data = json.loads(ap_value)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Token payload is not valid JSON") from exc

        jwt_token = extract_jwt(ap_data)
        await browser.close()
        return jwt_token


def write_token_file(jwt_token: str, tenant: str, payload: Dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "jwt": jwt_token,
        "tenant": tenant,
        "jwt_payload": payload,
    }
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=True)


async def run() -> None:
    log("Starting add-on")
    log("Reading configuration")
    config = read_config()

    credentials = prompt_for_credentials(config)
    login = credentials.get("login", "").strip()
    password = credentials.get("password", "")

    if not login or not password:
        raise RuntimeError("Login and password are required")

    log("Logging in")
    jwt_token = await retrieve_jwt(login, password)
    log("Token extracted")

    payload = decode_jwt_payload(jwt_token)
    tenant = payload.get("tenant") or payload.get("Tenant")
    if not tenant:
        raise RuntimeError("Tenant field not found in JWT payload")

    write_token_file(jwt_token, str(tenant), payload)
    log("File written to /config/eduvulcan_token.json")


def main() -> None:
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
