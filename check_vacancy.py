#!/usr/bin/env python3
"""
Vacancy monitor for three Japanese rental sites.
Sends email to RECIPIENT when a new vacancy appears (state transitions 0 → positive).
"""

import asyncio
import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

RECIPIENT = "ysm920625@gmail.com"
STATE_FILE = "state.json"

URLS = {
    "citymobile": (
        "https://www.citymobile.co.jp/line/station/1133231"
        "?name=%E5%B7%9D%E5%B4%8E%E9%A7%85"
    ),
    "ur_net": "https://www.ur-net.go.jp/chintai/kanto/kanagawa/area/132.html",
    "kousha": "https://www.kousha-chintai.com/search/list.php?dcd=K120031000",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"citymobile": 0, "ur_net": 0, "kousha": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helper: extract Next.js __NEXT_DATA__
# ---------------------------------------------------------------------------

def _search_json_for_vacancy(obj, depth: int = 0) -> int | None:
    """Recursively search a parsed JSON object for vacancy-related counts."""
    if depth > 8:
        return None
    vacancy_keys = {"kūshitsu", "空室", "akitsu", "count", "total", "rooms", "units"}
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(vk in kl for vk in ["vacant", "空室", "kushitsu", "room", "unit", "count"]):
                if isinstance(v, int) and v >= 0:
                    return v
            result = _search_json_for_vacancy(v, depth + 1)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _search_json_for_vacancy(item, depth + 1)
            if result is not None:
                return result
    return None


def extract_next_data(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Site checkers
# ---------------------------------------------------------------------------

async def check_citymobile(browser) -> int | None:
    """
    citymobile is a Next.js SPA. Property data is fetched via internal API calls.
    Strategy:
      1. Intercept JSON API responses and look for property/room lists.
      2. Fallback: parse __NEXT_DATA__ embedded in HTML.
      3. Fallback: text pattern matching after full JS render.
    """
    tag = "[citymobile]"
    ctx = await browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
    page = await ctx.new_page()

    captured_json: list[tuple[str, object]] = []

    async def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            if "json" in ct and response.status == 200:
                data = await response.json()
                captured_json.append((response.url, data))
        except Exception:
            pass

    page.on("response", on_response)

    try:
        await page.goto(URLS["citymobile"], wait_until="domcontentloaded", timeout=60_000)
        # Wait for SPA to fetch and render property listings
        await page.wait_for_timeout(10_000)
        html = await page.content()
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        print(f"{tag} page chars: {len(text)}, API responses captured: {len(captured_json)}")

        # --- Strategy 1: scan intercepted API responses ---
        for url, data in captured_json:
            print(f"{tag}   API: {url[:80]}")
            # Look for property list arrays
            if isinstance(data, dict):
                for key in ("data", "items", "properties", "rooms", "results", "list"):
                    if isinstance(data.get(key), list):
                        items = data[key]
                        # Count items where vacancy indicator is positive
                        vacancy = sum(
                            1 for item in items
                            if isinstance(item, dict) and (
                                item.get("vacancyCount", 0) > 0
                                or item.get("空室数", 0) > 0
                                or item.get("roomCount", 0) > 0
                                or item.get("available", False)
                            )
                        )
                        if vacancy > 0:
                            print(f"{tag} API key='{key}', {vacancy} properties with vacancy")
                            return vacancy
                        # Even if 0, return 0 rather than None so we track state
                        if items:
                            print(f"{tag} API key='{key}', {len(items)} properties, 0 vacancies")
                            return 0
            # Try generic recursive search
            found = _search_json_for_vacancy(data)
            if found is not None:
                print(f"{tag} API recursive search → {found}")
                return found

        # --- Strategy 2: __NEXT_DATA__ ---
        next_data = extract_next_data(html)
        if next_data:
            found = _search_json_for_vacancy(next_data)
            if found is not None:
                print(f"{tag} __NEXT_DATA__ → {found}")
                return found

        # --- Strategy 3: text patterns ---
        count = text.count("空室あり")
        if count:
            print(f"{tag} text '空室あり' ×{count}")
            return count

        for pat in [
            r'空室\s*(\d+)\s*件',
            r'空室数[:\s：]+(\d+)',
            r'(\d+)\s*室\s*空',
            r'空き\s*(\d+)\s*件',
            r'公開中\s*(\d+)',
        ]:
            m = re.search(pat, text)
            if m:
                count = int(m.group(1))
                print(f"{tag} text pattern '{pat}' → {count}")
                return count

        print(f"{tag} no vacancy data found. text sample:\n  {text[:500]}")
        return None

    except Exception as exc:
        print(f"{tag} ERROR: {exc}")
        return None
    finally:
        await ctx.close()


async def check_ur_net(browser) -> int | None:
    """
    UR Housing page — server-rendered but has background XHR keeping network busy.
    Use domcontentloaded + fixed wait to avoid networkidle timeout.
    """
    tag = "[ur_net]"
    ctx = await browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
    page = await ctx.new_page()
    try:
        await page.goto(URLS["ur_net"], wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(5_000)
        html = await page.content()
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        print(f"{tag} page chars: {len(text)}")

        for pat in [
            r'該当空室数\s*(\d+)\s*部屋',
            r'空室数[:\s：]+(\d+)',
            r'(\d+)\s*部屋.*?空',
            r'空室\s*(\d+)',
        ]:
            m = re.search(pat, text)
            if m:
                count = int(m.group(1))
                print(f"{tag} pattern '{pat}' → {count}")
                return count

        print(f"{tag} no vacancy pattern found. sample:\n  {text[:500]}")
        return None

    except Exception as exc:
        print(f"{tag} ERROR: {exc}")
        return None
    finally:
        await ctx.close()


async def check_kousha(browser) -> int | None:
    """
    kousha-chintai.com — PHP server-rendered, use domcontentloaded to avoid timeout.
    """
    tag = "[kousha]"
    ctx = await browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
    page = await ctx.new_page()
    try:
        await page.goto(URLS["kousha"], wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(3_000)
        html = await page.content()
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        print(f"{tag} page chars: {len(text)}")

        for pat in [
            r'募集中\s*\d+\s*物件\s*(\d+)\s*戸',
            r'(\d+)\s*戸.*?募集',
            r'募集.*?(\d+)\s*戸',
            r'(\d+)\s*戸\s*あり',
        ]:
            m = re.search(pat, text, re.DOTALL)
            if m:
                units = int(m.group(1))
                print(f"{tag} pattern '{pat}' → {units}")
                return units

        print(f"{tag} no unit pattern found. sample:\n  {text[:500]}")
        return None

    except Exception as exc:
        print(f"{tag} ERROR: {exc}")
        return None
    finally:
        await ctx.close()


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str) -> None:
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    smtp_host = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))

    if not smtp_user or not smtp_pass:
        print("SMTP_USER / SMTP_PASSWORD not configured — skipping email")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = RECIPIENT

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as srv:
        srv.login(smtp_user, smtp_pass)
        srv.sendmail(smtp_user, [RECIPIENT], msg.as_bytes())
    print(f"Email sent → {RECIPIENT}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state = load_state()
    new_state = dict(state)
    alerts: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        checks = [
            ("citymobile", check_citymobile, "City Mobile 川崎駅周辺",  "空室が {n} 件発生しました！"),
            ("ur_net",     check_ur_net,     "UR都市機構 神奈川エリア", "該当空室数が {n} 部屋になりました！"),
            ("kousha",     check_kousha,     "公社賃貸",                "募集物件が {n} 戸になりました！"),
        ]

        for key, checker, label, tmpl in checks:
            result = await checker(browser)
            if result is not None:
                prev = state.get(key, 0)
                if result > 0 and prev == 0:
                    alerts.append(
                        f"■ {label}\n"
                        f"  {tmpl.format(n=result)}\n"
                        f"  {URLS[key]}"
                    )
                new_state[key] = result

        await browser.close()

    save_state(new_state)

    if alerts:
        subject = f"【空室速報】{len(alerts)} 件の空室情報 ({now})"
        body = (
            "空室情報が見つかりました。お早めにご確認ください。\n\n"
            + "\n\n".join(alerts)
            + f"\n\n確認日時: {now}"
        )
        send_email(subject, body)
    else:
        print(f"[{now}] No new vacancies. State: {new_state}")


if __name__ == "__main__":
    asyncio.run(main())
