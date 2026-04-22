#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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

SMS_GATEWAY = f"{MY_PHONE}@rakuten.jp"
URL         = "https://www.kousha-chintai.com/search/list.php?dcd=K120031000"
STATE_FILE  = "vacancy_state.txt"


def fetch_vacancy_count():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        html = page.content()
        browser.close()

    # 募集中 이후 섹션에서 <span>숫자</span>戸 패턴으로 찾기
    # 예: <span>0</span>戸
    section = re.search(r"募集中.*?(<span>\d+</span>物件.*?<span>(\d+)</span>戸)", html, re.DOTALL)
    if section:
        count = int(section.group(2))
        print(f"=== 파싱 성공: {count}戸")
        return count

    print("=== 파싱 실패")
    return None


def load_last_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            val = f.read().strip()
            return int(val) if val.isdigit() else 0
    return 0


def save_state(count):
    with open(STATE_FILE, "w") as f:
        f.write(str(count))


def send_notifications(vacancy_count):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"[空き情報] フロール川崎中幸町 募集中 {vacancy_count}戸！"
    email_body = f"""\
フロール川崎中幸町 に空き部屋が出ました！

  募集中 : {vacancy_count}戸
  確認時刻: {now_str}

▶ 物件ページを確認する
{URL}

---
このメールは自動監視スクリプトが送信しました。
"""
    sms_body = f"【空室】フロール川崎中幸町 {vacancy_count}戸！\n{URL}"

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = TO_EMAIL
        msg.attach(MIMEText(email_body, "plain", "utf-8"))
        server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print(f"  이메일 발송 완료 -> {TO_EMAIL}")

        sms = MIMEText(sms_body, "plain", "utf-8")
        sms["Subject"] = subject
        sms["From"]    = GMAIL_ADDRESS
        sms["To"]      = SMS_GATEWAY
        server.sendmail(GMAIL_ADDRESS, SMS_GATEWAY, sms.as_string())
        print(f"  SMS 발송 완료 -> {SMS_GATEWAY}")


def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 체크 시작...")

    try:
        current = fetch_vacancy_count()
    except Exception as e:
        print(f"[{now_str}] 페이지 조회 실패: {e}")
        return

    if current is None:
        print(f"[{now_str}] 戸数を取得できませんでした")
        return

    last = load_last_state()
    print(f"[{now_str}] 이전: {last}戸 -> 현재: {current}戸")

    if last == 0 and current >= 1:
        print(f"[{now_str}] 공실 발생! 알림 발송 중...")
        try:
            send_notifications(current)
            print(f"[{now_str}] 알림 완료")
        except Exception as e:
            print(f"[{now_str}] 알림 발송 실패: {e}")
    else:
        print(f"[{now_str}] 변동 없음")

    save_state(current)


if __name__ == "__main__":
   main()
