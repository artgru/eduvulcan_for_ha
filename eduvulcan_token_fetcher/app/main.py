import asyncio
import base64
import json
import os
import sys
import time
from getpass import getpass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

OUTPUT_PATH = Path("/config/eduvulcan_token.json")
LOGIN_URL = "https://eduvulcan.pl/api/ap"


def log(message: str) -> None:
    print(message, flush=True)


def prompt_for_credentials(login: str, password: str) -> Dict[str, str]:
    login_value = login.strip()
    password_value = password

    if login_value and password_value:
        return {"login": login_value, "password": password_value}

    log("Login or password missing; entering interactive mode")
    if not login_value:
        login_value = input("Login: ").strip()
    if not password_value:
        try:
            password_value = getpass("Password: ")
        except Exception:
            password_value = input("Password: ")

    return {"login": login_value, "password": password_value}


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


def is_payload_expired(payload: Dict[str, Any]) -> bool:
    exp_value = payload.get("exp")
    if exp_value is None:
        return False
    try:
        exp_timestamp = float(exp_value)
    except (TypeError, ValueError):
        return False
    return time.time() >= exp_timestamp


def read_existing_token() -> Optional[Dict[str, Any]]:
    if not OUTPUT_PATH.exists():
        return None

    try:
        with OUTPUT_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    jwt_token = data.get("jwt")
    if not is_jwt(jwt_token):
        return None

    payload = data.get("jwt_payload")
    needs_write = False
    if not isinstance(payload, dict):
        payload = decode_jwt_payload(jwt_token)
        needs_write = True

    if is_payload_expired(payload):
        raise RuntimeError("Stored token is expired")

    tenant = data.get("tenant") or payload.get("tenant") or payload.get("Tenant")
    if not tenant:
        raise RuntimeError("Tenant field not found in stored JWT payload")

    return {
        "jwt": jwt_token,
        "tenant": str(tenant),
        "payload": payload,
        "needs_write": needs_write,
    }


def remove_token_file() -> None:
    try:
        OUTPUT_PATH.unlink()
    except FileNotFoundError:
        return
    except Exception as exc:
        log(f"Failed to delete token file: {exc}")


def write_token_file(jwt_token: str, tenant: str, payload: Dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "jwt": jwt_token,
        "tenant": tenant,
        "jwt_payload": payload,
    }
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=True)


def build_selector_list(values: Iterable[str]) -> str:
    return ", ".join(values)


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


async def wait_for_any_selector(page, selectors: Iterable[str], timeout_ms: int) -> str:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except Exception:
                continue
            if count == 0:
                continue
            try:
                if await locator.first.is_visible():
                    return selector
            except Exception:
                continue
        await page.wait_for_timeout(200)

    raise RuntimeError(
        f"Timed out waiting for selectors: {build_selector_list(selectors)}"
    )


async def fill_login(page, login: str) -> None:
    labels = ["Login", "E-mail", "Email", "Nazwa uzytkownika", "Uzytkownik"]
    if await fill_by_labels(page, labels, login):
        return

    selectors = [
        "input#Alias",
        "input[name='Alias']",
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
        "input#Password",
        "input[name='Password']",
        "input[type='password']",
        "input[name='password']",
        "input#password",
    ]
    await fill_by_selectors(page, selectors, password, "password")


async def click_next(page) -> None:
    selectors = [
        "#btNext",
        "button#btNext",
        "button:has-text('Dalej')",
        "button:has-text('Next')",
    ]
    for selector in selectors:
        if await click_if_present(page, selector, timeout_ms=5000):
            return

    raise RuntimeError("Could not find next button")


async def submit_login(page) -> None:
    selectors = [
        "#btLogIn",
        "#btLogin",
        "button#btLogIn",
        "button#btLogin",
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


async def wait_for_user_info(page) -> None:
    try:
        await page.wait_for_response(
            lambda response: "/Account/QueryUserInfo" in response.url
            and response.request.method == "POST",
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        log("User info verification did not finish within timeout")


async def wait_for_captcha(page) -> None:
    captcha_selectors = [
        "#captcha",
        "#captcha-response",
        "[name*='captcha']",
        "[id*='captcha']",
        "iframe[src*='captcha']",
    ]
    needs_attention = False
    for selector in captcha_selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        if count == 0:
            continue
        try:
            if await locator.first.is_visible():
                needs_attention = True
                break
        except Exception:
            continue

    if not needs_attention:
        return

    log("CAPTCHA detected. Please solve it manually in the browser.")

    await page.wait_for_function(
        """(selectors) => {
        const elements = selectors
          .map((selector) => document.querySelector(selector))
          .filter(Boolean);
        if (elements.length === 0) {
          return true;
        }
        for (const el of elements) {
          const style = window.getComputedStyle(el);
          const visible =
            style && style.display !== 'none' && style.visibility !== 'hidden';
          if (visible && el.offsetParent !== null) {
            const value = (el.value || '').trim();
            if (!value) {
              return false;
            }
          }
        }
        return true;
      }""",
        captcha_selectors,
        timeout=600000,
    )


async def retrieve_jwt(login: str, password: str) -> str:
    headful = os.getenv("HEADFUL", "").strip() in {"1", "true", "True"}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=not headful, args=["--no-sandbox"]
        )
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await remove_overlay(page)

        await wait_for_any_selector(
            page, ["#Alias", "input[name='Alias']", "input#login"], timeout_ms=20000
        )
        await fill_login(page, login)
        await click_next(page)
        await wait_for_user_info(page)

        await wait_for_any_selector(
            page,
            ["#Password", "input[name='Password']", "input[type='password']"],
            timeout_ms=20000,
        )
        await fill_password(page, password)
        await wait_for_captcha(page)
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
        await context.close()
        await browser.close()
        return jwt_token


async def fetch_token(login: str, password: str) -> Dict[str, Any]:
    log("Logging in")
    jwt_token = await retrieve_jwt(login, password)
    log("Token extracted")

    payload = decode_jwt_payload(jwt_token)
    tenant = payload.get("tenant") or payload.get("Tenant")
    if not tenant:
        raise RuntimeError("Tenant field not found in JWT payload")

    return {"jwt": jwt_token, "payload": payload, "tenant": str(tenant)}


async def fetch_token_with_retry(login: str, password: str) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            return await fetch_token(login, password)
        except Exception as exc:
            last_error = exc
            remove_token_file()
            if attempt == 0:
                log("Token fetch failed, retrying once")
            else:
                break

    raise RuntimeError("Failed to fetch token") from last_error


async def run() -> None:
    log("Starting add-on")

    try:
        existing = read_existing_token()
    except Exception as exc:
        log(f"Existing token invalid: {exc}")
        remove_token_file()
        existing = None

    if existing:
        if existing.get("needs_write"):
            write_token_file(existing["jwt"], existing["tenant"], existing["payload"])
        log("Using existing token")
        return

    env_login = os.getenv("LOGIN", "")
    env_password = os.getenv("PASSWORD", "")
    credentials = prompt_for_credentials(env_login, env_password)
    login = credentials.get("login", "").strip()
    password = credentials.get("password", "")

    if not login or not password:
        raise RuntimeError("Login and password are required")

    token_data = await fetch_token_with_retry(login, password)
    write_token_file(token_data["jwt"], token_data["tenant"], token_data["payload"])
    log("File written to /config/eduvulcan_token.json")


def main() -> None:
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
