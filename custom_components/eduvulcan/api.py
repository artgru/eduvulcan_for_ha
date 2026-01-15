import os
import json
import base64
from datetime import date
from collections import defaultdict

from playwright.async_api import async_playwright
from iris.credentials import RsaCredential
from iris.api import IrisHebeCeApi

from .const import TOKEN_FILE, STORAGE_FILE, EDUVULCAN_URL


def decode_jwt_payload(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload)
    return json.loads(decoded)


def school_year_start(today: date) -> date:
    """Zwraca 1 września bieżącego roku szkolnego."""
    if today.month < 9:
        return date(today.year - 1, 9, 1)
    return date(today.year, 9, 1)


class EduVulcanAPI:
    def __init__(self, hass, login: str, password: str):
        self.hass = hass
        self.login = login
        self.password = password
        self.config_dir = hass.config.path()

        self.token_path = os.path.join(self.config_dir, TOKEN_FILE)
        self.storage_path = os.path.join(self.config_dir, STORAGE_FILE)

    async def _fetch_new_token(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )

            if os.path.exists(self.storage_path):
                context = await browser.new_context(storage_state=self.storage_path)
            else:
                context = await browser.new_context()

            page = await context.new_page()

            try:
                await page.goto(EDUVULCAN_URL, wait_until="networkidle")

                await page.evaluate("""
                    const el = document.getElementById("respect-privacy-wrapper");
                    if (el) el.remove();
                """)

                try:
                    await page.wait_for_selector("#ap", timeout=5000)
                except:
                    await page.wait_for_selector("#Alias", timeout=30000)
                    await page.fill("#Alias", self.login)
                    await page.click("#btNext")

                    await page.wait_for_selector("#Password", timeout=30000)
                    await page.fill("#Password", self.password)
                    await page.click("#btLogOn")

                    await page.wait_for_selector("#ap", state="attached", timeout=60000)

                token_json = await page.eval_on_selector("#ap", "el => el.value")
                data = json.loads(token_json)

                tokens = data.get("Tokens") or []
                jwt = tokens[0]
                payload = decode_jwt_payload(jwt)
                tenant = payload.get("tenant")

                with open(self.token_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {"tenant": tenant, "jwt": jwt},
                        f,
                        indent=2,
                        ensure_ascii=False
                    )

                await context.storage_state(path=self.storage_path)
                return jwt, tenant

            finally:
                await browser.close()

    def _load_token(self):
        with open(self.token_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["jwt"], data["tenant"]

    async def get_schedule(self):
        today = date.today()
        start_date = school_year_start(today)
        end_date = today

        try:
            jwt, tenant = self._load_token()
        except Exception:
            jwt, tenant = await self._fetch_new_token()

        credential = RsaCredential.create_new("Android", "SM-A525F")
        api = IrisHebeCeApi(credential)

        try:
            await api.register_by_jwt(tokens=[jwt], tenant=tenant)
            accounts = await api.get_accounts()
            account = accounts[0]

            schedule_items = await api.get_schedule(
                rest_url=account.unit.rest_url,
                pupil_id=account.pupil.id,
                date_from=start_date,
                date_to=end_date
            )

            return schedule_items

        finally:
            if hasattr(api, "_session"):
                await api._session.close()
