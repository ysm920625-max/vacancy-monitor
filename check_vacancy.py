#!/usr/bin/env python3
"""
Vacancy monitor for three Japanese rental sites.
Sends email to RECIPIENT when a new vacancy appears (state transitions from 0 → positive).
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
# Page fetcher
# ---------------------------------------------------------------------------

async def fetch_text(browser, url: str) -> str:
    ctx = await browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
    page = await ctx.new_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        # Extra wait for JavaScript-rendered content
        await page.wait_for_timeout(3_000)
        html = await page.content()
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    finally:
        await ctx.close()


# ---------------------------------------------------------------------------
# Site-specific checkers
# ---------------------------------------------------------------------------

async def check_citymobile(browser) -> int | None:
    """
    Return number of properties with vacancies on the 川崎駅 station page.
    Alert condition: result > 0 (was 0 before).
    """
    tag = "[citymobile]"
    try:
        text = await fetch_text(browser, URLS["citymobile"])
        print(f"{tag} page chars: {len(text)}")

        # Strategy 1: explicit "空室あり" badge count
        count = text.count("空室あり")
        if count:
            print(f"{tag} '空室あり' ×{count}")
            return count

        # Strategy 2: numeric vacancy summary
        for pat in [
            r'空室\s*(\d+)\s*件',
            r'空室数[:\s：]+(\d+)',
            r'(\d+)\s*室\s*空',
            r'空き\s*(\d+)\s*件',
        ]:
            m = re.search(pat, text)
            if m:
                count = int(m.group(1))
                print(f"{tag} pattern '{pat}' → {count}")
                return count

        print(f"{tag} no vacancy pattern found. sample:\n  {text[:400]}")
        return 0

    except Exception as exc:
        print(f"{tag} ERROR: {exc}")
        return None


async def check_ur_net(browser) -> int | None:
    """
    Return 該当空室数 (vacant rooms) on the UR Housing Kanagawa area page.
    Alert condition: result >= 1 (was 0 before).
    """
    tag = "[ur_net]"
    try:
        text = await fetch_text(browser, URLS["ur_net"])
        print(f"{tag} page chars: {len(text)}")

        for pat in [
            r'該当空室数\s*(\d+)\s*部屋',
            r'空室数[:\s：]+(\d+)',
            r'(\d+)\s*部屋.*?空',
        ]:
            m = re.search(pat, text)
            if m:
                count = int(m.group(1))
                print(f"{tag} pattern '{pat}' → {count}")
                return count

        print(f"{tag} no vacancy pattern found. sample:\n  {text[:400]}")
        return None

    except Exception as exc:
        print(f"{tag} ERROR: {exc}")
        return None


async def check_kousha(browser) -> int | None:
    """
    Return number of 戸 (units) available on kousha-chintai.com.
    Alert condition: result >= 1 (was 0 before).
    """
    tag = "[kousha]"
    try:
        text = await fetch_text(browser, URLS["kousha"])
        print(f"{tag} page chars: {len(text)}")

        for pat in [
            r'募集中\s*\d+\s*物件\s*(\d+)\s*戸',
            r'(\d+)\s*戸.*?募集',
            r'募集.*?(\d+)\s*戸',
        ]:
            m = re.search(pat, text, re.DOTALL)
            if m:
                units = int(m.group(1))
                print(f"{tag} pattern '{pat}' → {units}")
                return units

        print(f"{tag} no unit pattern found. sample:\n  {text[:400]}")
        return None

    except Exception as exc:
        print(f"{tag} ERROR: {exc}")
        return None


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

        # Run all three checks (sequential to avoid getting rate-limited)
        checks = [
            ("citymobile", check_citymobile),
            ("ur_net",     check_ur_net),
            ("kousha",     check_kousha),
        ]
        labels = {
            "citymobile": ("City Mobile 川崎駅周辺",    "空室が {n} 件発生しました！"),
            "ur_net":     ("UR都市機構 神奈川エリア",   "該当空室数が {n} 部屋になりました！"),
            "kousha":     ("公社賃貸",                  "募集物件が {n} 戸になりました！"),
        }

        for key, checker in checks:
            result = await checker(browser)
            if result is not None:
                prev = state.get(key, 0)
                if result > 0 and prev == 0:
                    title, tmpl = labels[key]
                    msg = tmpl.format(n=result)
                    alerts.append(
                        f"■ {title}\n"
                        f"  {msg}\n"
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
        print(f"[{now}] No new vacancies detected. State: {new_state}")


if __name__ == "__main__":
    asyncio.run(main())
