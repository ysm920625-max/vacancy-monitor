#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
공실 모니터링 스크립트
1. フロール川崎中幸町 - 募集中 0戸 → 1戸 이상
2. シティモバイル 川崎駅 - 「詳細を見る」버튼 수 증가 시 알림
3. UR賃貸 川崎市幸区 - 該当空室数 0 → 1 이상
"""

import smtplib
import os
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from playwright.sync_api import sync_playwright

GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
TO_EMAIL           = os.environ["TO_EMAIL"]
MY_PHONE           = os.environ["MY_PHONE"]

SITES = {
    "kousha": {
        "name": "フロール川崎中幸町",
        "url": "https://www.kousha-chintai.com/search/list.php?dcd=K120031000",
        "state_file": "state_kousha.txt",
        "mode": "zero_to_one",   # 0 → 1 이상일 때만
    },
    "citymobile": {
        "name": "シティモバイル (川崎駅)",
        "url": "https://www.citymobile.co.jp/line/station/1133231?name=%E5%B7%9D%E5%B4%8E%E9%A7%85",
        "state_file": "state_citymobile.txt",
        "mode": "increase",      # 이전보다 증가할 때
    },
    "ur": {
        "name": "UR賃貸 川崎市幸区",
        "url": "https://www.ur-net.go.jp/chintai/kanto/kanagawa/area/132.html",
        "state_file": "state_ur.txt",
        "mode": "zero_to_one",   # 0 → 1 이상일 때만
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
    page.goto(SITES["kousha"]["url"], wait_until="domcontentloaded", timeout=60000)
    html = page.content()
    section = re.search(r"募集中.*?<span>\d+</span>物件.*?<span>(\d+)</span>戸", html, re.DOTALL)
    if section:
        count = int(section.group(1))
        print(f"  [kousha] 募集中: {count}戸")
        return count
    print(f"  [kousha] 파싱 실패")
    return None


def fetch_citymobile(page):
    """シティモバイル 川崎駅 - 「詳細を見る」버튼 수"""
    page.goto(SITES["citymobile"]["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    html = page.content()
    count = len(re.findall(r"詳細を見る", html))
    print(f"  [citymobile] 詳細を見るボタン数: {count}")
    return count


def fetch_ur(page):
    """UR賃貸 川崎市幸区 該当空室数"""
    page.goto(SITES["ur"]["url"], wait_until="domcontentloaded", timeout=60000)
    # JS 렌더링 충분히 대기
    page.wait_for_timeout(8000)
    html = page.content()

    # 패턴1: 「該当空室数X部屋」
    m = re.search(r"該当空室数\s*(\d+)\s*部屋", html)
    if m:
        count = int(m.group(1))
        print(f"  [ur] 該当空室数: {count}部屋")
        return count

    # 패턴2: 공실 건수를 나타내는 다른 패턴
    m2 = re.search(r"(\d+)\s*部屋", html)
    if m2:
        count = int(m2.group(1))
        print(f"  [ur] 部屋数 (fallback): {count}")
        return count

    # 패턴3: HTML 전체 출력해서 디버그
    idx = html.find("該当")
    if idx >= 0:
        print(f"  [ur] 該当 주변: {html[idx:idx+100]}")
    else:
        print(f"  [ur] 「該当」문자 없음 - JS 미로딩 가능성")

    return None


def load_state(state_file):
    if os.path.exists(state_file):
        with open(state_file, "r") as f:
            val = f.read().strip()
            return int(val) if val.lstrip('-').isdigit() else -1
    return -1  # 파일 없으면 -1 (첫 실행)


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

    # 첫 실행(last=-1)은 현재값 저장만 하고 알림 없음
    if last == -1:
        print(f"  첫 실행 - 현재값 {current} 저장 (알림 없음)")
        save_state(site["state_file"], current)
        return

    print(f"  이전: {last} → 현재: {current}")

    mode = site["mode"]
    if mode == "zero_to_one":
        should_notify = (last == 0 and current >= 1)
    else:  # increase
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
