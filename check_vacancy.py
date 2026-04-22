#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
공실 모니터링 스크립트
1. フロール川崎中幸町 (kousha-chintai) - 募集中 0→1 이상
2. シティモバイル (citymobile) - 川崎駅 검색결과 건수 변화
3. UR賃貸 (ur-net) - 川崎市幸区 공실 건수 변화
"""

import smtplib
import os
import re
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from playwright.sync_api import sync_playwright

GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
TO_EMAIL           = os.environ["TO_EMAIL"]
MY_PHONE           = os.environ["MY_PHONE"]

SMS_GATEWAY = f"{MY_PHONE}@rakuten.jp"

SITES = {
    "kousha": {
        "name": "フロール川崎中幸町",
        "url": "https://www.kousha-chintai.com/search/list.php?dcd=K120031000",
        "state_file": "state_kousha.txt",
    },
    "citymobile": {
        "name": "シティモバイル (川崎駅)",
        "url": "https://www.citymobile.co.jp/keyword?keyword=%E5%B7%9D%E5%B4%8E%E9%A7%85&page=1",
        "state_file": "state_citymobile.txt",
    },
    "ur": {
        "name": "UR賃貸 川崎市幸区",
        "url": "https://www.ur-net.go.jp/chintai/kanto/kanagawa/area/132.html",
        "state_file": "state_ur.txt",
    },
}


def get_browser_page(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="ja-JP",
    )
    return browser, context.new_page()


def fetch_kousha(page):
    """フロール川崎中幸町 募集中 戸数"""
    page.goto(SITES["kousha"]["url"], wait_until="domcontentloaded", timeout=30000)
    html = page.content()
    section = re.search(r"募集中.*?(<span>\d+</span>物件.*?<span>(\d+)</span>戸)", html, re.DOTALL)
    if section:
        return int(section.group(2))
    return None


def fetch_citymobile(page):
    """シティモバイル 川崎駅 검색결과 건수"""
    page.goto(SITES["citymobile"]["url"], wait_until="domcontentloaded", timeout=60000)
    html = page.content()

    # 물건 카드 수를 세기 (각 사이트 구조에 맞게)
    # 물건 링크 수로 카운트
    count = len(re.findall(r'href="[^"]*/detail/[^"]*"', html))
    if count == 0:
        # 다른 패턴 시도
        count = len(re.findall(r'class="[^"]*property[^"]*"', html, re.IGNORECASE))
    print(f"  [citymobile] 감지된 물건 수: {count}")
    return count


def fetch_ur(page):
    """UR賃貸 川崎市幸区 공실 건수"""
    page.goto(SITES["ur"]["url"], wait_until="domcontentloaded", timeout=60000)
    # JS 로딩 대기
    page.wait_for_timeout(5000)
    html = page.content()

    # 「該当空室数 X部屋」패턴
    m = re.search(r"該当空室数[^\d]*(\d+)\s*部屋", html)
    if m:
        return int(m.group(1))

    # 물건 건수 대안
    m2 = re.search(r"(\d+)\s*物件中", html)
    if m2:
        return int(m2.group(1))

    # 물건 카드 수
    count = len(re.findall(r'class="[^"]*bukken[^"]*"', html, re.IGNORECASE))
    print(f"  [ur] 감지된 물건 수: {count}")
    return count if count > 0 else None


def load_state(state_file):
    if os.path.exists(state_file):
        with open(state_file, "r") as f:
            val = f.read().strip()
            return int(val) if val.lstrip('-').isdigit() else 0
    return 0


def save_state(state_file, count):
    with open(state_file, "w") as f:
        f.write(str(count))


def send_email(site_name, url, current, last):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"[空き情報] {site_name} に新着物件！"
    body = f"""\
{site_name} に新着物件が出ました！

  以前: {last}件 → 現在: {current}件
  確認時刻: {now_str}

▶ 物件ページを確認する
{url}

---
このメールは自動監視スクリプトが送信しました。
"""
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = TO_EMAIL
        msg.attach(MIMEText(body, "plain", "utf-8"))
        server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
    print(f"  이메일 발송 완료 -> {TO_EMAIL}")


def check_site(key, fetch_fn, page):
    site = SITES[key]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str}] {site['name']} 체크 중...")

    try:
        current = fetch_fn(page)
    except Exception as e:
        print(f"  ❌ 조회 실패: {e}")
        return

    if current is None:
        print(f"  ⚠️  건수를 가져오지 못했습니다")
        return

    last = load_state(site["state_file"])
    print(f"  이전: {last} → 현재: {current}")

    # kousha는 0→1 이상일 때만 / 나머지는 증가하면 알림
    should_notify = False
    if key == "kousha":
        should_notify = (last == 0 and current >= 1)
    else:
        should_notify = (current > last)

    if should_notify:
        print(f"  🎉 새 물건 감지! 이메일 발송 중...")
        try:
            send_email(site["name"], site["url"], current, last)
        except Exception as e:
            print(f"  ❌ 이메일 발송 실패: {e}")
    else:
        print(f"  — 변동 없음")

    save_state(site["state_file"], current)


def main():
    with sync_playwright() as p:
        browser, page = get_browser_page(p)
        try:
            check_site("kousha",     fetch_kousha,     page)
            check_site("citymobile", fetch_citymobile, page)
            check_site("ur",         fetch_ur,         page)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
