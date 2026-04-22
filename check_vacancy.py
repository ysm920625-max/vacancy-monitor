#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
フロール川崎中幸町 空室モニタリング
GitHub Actions で10分ごとに実行される
설정값은 GitHub Secrets 에서 환경변수로 읽어옴
"""

import urllib.request
import smtplib
import os
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── 설정값 (GitHub Secrets → 환경변수) ──────────────────────
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
TO_EMAIL           = os.environ["TO_EMAIL"]
MY_PHONE           = os.environ["MY_PHONE"]

SMS_GATEWAY = f"{MY_PHONE}@rakuten.jp"   # 楽天モバイル
URL         = "https://www.kousha-chintai.com/search/list.php?dcd=K120031000"
STATE_FILE  = "vacancy_state.txt"        # 리포지토리에 저장되는 상태 파일


def fetch_vacancy_count():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    req = urllib.request.Request(URL, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as res:
        html = res.read().decode("utf-8", errors="replace")

    m = re.search(
        r"フロール川崎中幸町.*?募集中\s*[:\uff1a]\s*(\d+)\s*戸",
        html, re.DOTALL
    )
    if m:
        return int(m.group(1))

    m2 = re.search(r"募集中[^<]{0,30}?(\d+)\s*戸", html)
    if m2:
        return int(m2.group(1))

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

        # 📧 이메일
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = TO_EMAIL
        msg.attach(MIMEText(email_body, "plain", "utf-8"))
        server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print(f"  📧 이메일 발송 완료 → {TO_EMAIL}")

        # 📱 SMS (楽天モバイル)
        sms = MIMEText(sms_body, "plain", "utf-8")
        sms["Subject"] = subject
        sms["From"]    = GMAIL_ADDRESS
        sms["To"]      = SMS_GATEWAY
        server.sendmail(GMAIL_ADDRESS, SMS_GATEWAY, sms.as_string())
        print(f"  📱 SMS 발송 완료 → {SMS_GATEWAY}")


def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] 체크 시작...")

    try:
        current = fetch_vacancy_count()
    except Exception as e:
        print(f"[{now_str}] ❌ 페이지 조회 실패: {e}")
        return

    if current is None:
        print(f"[{now_str}] ⚠️  戸数を取得できませんでした")
        return

    last = load_last_state()
    print(f"[{now_str}] 이전: {last}戸 → 현재: {current}戸")

    if last == 0 and current >= 1:
        print(f"[{now_str}] 🎉 공실 발생! 알림 발송 중...")
        try:
            send_notifications(current)
            print(f"[{now_str}] ✅ 알림 완료")
        except Exception as e:
            print(f"[{now_str}] ❌ 알림 발송 실패: {e}")
    else:
        print(f"[{now_str}] — 변동 없음")

    save_state(current)


if __name__ == "__main__":
    main()
