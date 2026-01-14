import asyncio
import json
import os
import base64
import getpass
from datetime import date, datetime
from collections import defaultdict

from playwright.async_api import async_playwright
from iris.credentials import RsaCredential
from iris.api import IrisHebeCeApi

TOKEN_FILE = "eduvulcan_token.json"
EDUVULCAN_URL = "https://eduvulcan.pl/api/ap"

# ======================================================
# UTILITY
# ======================================================

def decode_jwt_payload(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload)
    return json.loads(decoded)

def ask_date(prompt: str, default: date | None = None) -> date:
    while True:
        raw = input(prompt).strip()
        if not raw and default:
            return default
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            print("Błędny format. Użyj: RRRR-MM-DD (np. 2026-01-15).")

def load_token_from_file():
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    jwt = data.get("jwt")
    tenant = data.get("tenant")
    if not jwt or not tenant:
        raise ValueError("Plik tokena nie zawiera jwt lub tenant.")
    return jwt, tenant

def delete_token_file():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
        print("🗑 Usunięto stary plik tokena.")

def ask_credentials():
    print("\n🔐 Wymagane logowanie do eduVULCAN")
    login = input("Login (e-mail): ").strip()
    password = getpass.getpass("Hasło: ")
    if not login or not password:
        raise RuntimeError("Login i hasło nie mogą być puste.")
    return login, password

# ======================================================
# POBIERANIE JWT Z EDUVULCAN
# ======================================================

async def fetch_new_token(login: str, password: str):
    print("🔐 Pobieram nowy token z eduVULCAN...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # 1️⃣ Wejście na stronę logowania
            await page.goto(EDUVULCAN_URL, wait_until="networkidle")

            # 2️⃣ Usunięcie overlay cookies / privacy
            await page.evaluate("""
                const el = document.getElementById("respect-privacy-wrapper");
                if (el) el.remove();
            """)

            # 3️⃣ Login
            await page.wait_for_selector("#Alias", timeout=30000)
            await page.fill("#Alias", login)
            await page.click("#btNext")

            # 4️⃣ Hasło
            await page.wait_for_selector("#Password", timeout=30000)
            await page.fill("#Password", password)

            # 5️⃣ Captcha (jeśli się pojawi)
            try:
                await page.wait_for_selector("#captcha", state="visible", timeout=5000)
                await page.wait_for_function(
                    "document.querySelector('#captcha-response') && document.querySelector('#captcha-response').value !== ''",
                    timeout=30000
                )
            except:
                pass

            # 6️⃣ Zaloguj
            await page.click("#btLogOn")

            # 7️⃣ Czekaj aż pojawi się input z tokenem
            await page.wait_for_selector("#ap", state="attached", timeout=60000)

            # 8️⃣ Odczyt tokena
            token_json = await page.eval_on_selector("#ap", "el => el.value")
            data = json.loads(token_json)

            tokens = data.get("Tokens") or []
            jwt = tokens[0] if tokens else None
            if not jwt:
                raise RuntimeError("Brak JWT w polu Tokens[]")

            payload = decode_jwt_payload(jwt)
            tenant = payload.get("tenant")
            if not tenant:
                raise RuntimeError("Nie udało się odczytać tenant z JWT")

            # 9️⃣ Zapis do pliku
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "tenant": tenant,
                        "jwt": jwt,
                        "jwt_payload": payload
                    },
                    f,
                    indent=2,
                    ensure_ascii=False
                )

            print(f"✅ Token pobrany poprawnie (tenant: {tenant})")
            return jwt, tenant

        finally:
            await browser.close()

# ======================================================
# PLAN LEKCJI
# ======================================================

async def fetch_schedule(jwt, tenant, start_date, end_date):
    credential = RsaCredential.create_new("Android", "SM-A525F")
    api = IrisHebeCeApi(credential)

    try:
        print("🔗 Rejestruję sesję w API...")
        await api.register_by_jwt(tokens=[jwt], tenant=tenant)

        print("📥 Pobieram listę kont...")
        accounts = await api.get_accounts()
        if not accounts:
            raise RuntimeError("Brak dostępnych kont.")

        account = accounts[0]

        pupil_name = (
            getattr(account.pupil, "name", None)
            or getattr(account.pupil, "displayed_name", None)
            or account.pupil.first_name
        )

        print(f"👩‍🎓 Uczeń: {pupil_name}")
        print(f"🏫 Szkoła: {account.unit.name}\n")

        print("📅 Pobieram plan lekcji...")
        schedule_items = await api.get_schedule(
            rest_url=account.unit.rest_url,
            pupil_id=account.pupil.id,
            date_from=start_date,
            date_to=end_date
        )

        if not schedule_items:
            print("Brak zajęć w podanym zakresie dat.")
            return

        by_day = defaultdict(list)
        for item in schedule_items:
            day_date = getattr(item, "date_", None)
            by_day[day_date].append(item)

        print("\n📘 PLAN LEKCJI:\n")

        for day_date in sorted(by_day):
            print(f"== {day_date} ==")
            for lesson in sorted(by_day[day_date], key=lambda x: x.time_slot.position):
                time_slot = lesson.time_slot.display
                subject = lesson.subject.name if lesson.subject else "?"
                room = lesson.room.code if lesson.room else "?"
                teacher = (
                    lesson.teacher_primary.display_name
                    if lesson.teacher_primary else "?"
                )
                print(f"{time_slot} | {subject} | {teacher} | sala {room}")
            print()

    finally:
        if hasattr(api, "_session"):
            await api._session.close()

# ======================================================
# MAIN LOGIC
# ======================================================

async def main():
    print("==========================================")
    print(" eduVULCAN – pobieranie planu lekcji")
    print("==========================================\n")

    # ====== PYTANIE O DATY ======
    print("Podaj zakres dat (format: RRRR-MM-DD).")
    start_date = ask_date("Data OD (Enter = dziś): ", default=date.today())
    end_date = ask_date("Data DO (Enter = taka sama jak OD): ", default=start_date)

    if end_date < start_date:
        print("❌ Data DO nie może być wcześniejsza niż data OD.")
        return

    print(f"\n➡ Zakres: {start_date} → {end_date}\n")

    # ====== LOGIKA TOKENA ======
    for attempt in (1, 2):
        try:
            if os.path.exists(TOKEN_FILE):
                print(f"📂 Wczytuję token z pliku (próba {attempt})...")
                jwt, tenant = load_token_from_file()
            else:
                raise FileNotFoundError

            await fetch_schedule(jwt, tenant, start_date, end_date)
            print("✅ Zakończono poprawnie.")
            return

        except Exception as e:
            print(f"⚠ Błąd: {e}")

            if attempt == 1:
                print("🔄 Token nieważny lub brak pliku – potrzebne ponowne logowanie.")
                delete_token_file()

                try:
                    login, password = ask_credentials()
                    jwt, tenant = await fetch_new_token(login, password)
                except Exception as token_error:
                    print(f"❌ Nie udało się pobrać nowego tokena: {token_error}")
                    return
            else:
                print("❌ Druga próba nieudana. Kończę działanie.")
                return


if __name__ == "__main__":
    asyncio.run(main())
