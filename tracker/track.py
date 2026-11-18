#!/usr/bin/env python3
"""winningcircle.io/canlicuzdan takipçisi.

Sitenin kullandığı JSON API'yi okur, bir önceki durumla karşılaştırır ve
işlem açıldığında / kapandığında / büyütüldüğünde Telegram'a mesaj atar.
Sadece Python standart kütüphanesi kullanır.

Ortam değişkenleri:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> yoksa mesajlar sadece ekrana yazılır
  STATE_FILE                            -> durum dosyası (varsayılan: state.json)
  API_URL                               -> test için farklı kaynak (dosya yolu da olur)
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_URL = os.environ.get("API_URL", "https://www.winningcircle.io/api/efe-yolda/okx")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

SIZE_CHANGE_THRESHOLD = 0.05   # pozisyon %5'ten fazla büyürse/küçülürse bildir
FAIL_ALERT_AFTER = 12          # üst üste bu kadar hata olursa (~1 saat) uyar
TR = timezone(timedelta(hours=3))
ACCOUNT_NAMES = {"manual": "Manuel", "bot": "Bot"}


# ---------------------------------------------------------------- yardımcılar
def fetch():
    if not API_URL.startswith("http"):
        with open(API_URL, encoding="utf-8") as f:
            return json.load(f)
    req = urllib.request.Request(API_URL, headers={
        "User-Agent": "Mozilla/5.0 (compatible; canlicuzdan-tracker)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def send(text):
    print("----\n" + text)
    if not (BOT_TOKEN and CHAT_ID):
        return
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        urllib.request.urlopen(url, data=data, timeout=30).read()
    except Exception as e:  # mesaj gitmese de takip devam etsin
        print(f"Telegram hatası: {e}", file=sys.stderr)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def num(x, digits=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "-"
    if abs(x) >= 1000:
        digits = min(digits, 2)
    elif abs(x) < 10:
        digits = max(digits, 4)
    s = f"{x:,.{digits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def coin(inst):
    return (inst or "?").replace("-USDT-SWAP", "").replace("-SWAP", "")


def acct(a):
    return ACCOUNT_NAMES.get(a, a or "?")


def side_tr(s):
    return {"long": "🟢 LONG", "short": "🔴 SHORT"}.get(s, s or "?")


def pos_key(p):
    return f"{p.get('account')}|{p.get('instId')}|{p.get('side')}"


def fmt_time(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, TR).strftime("%d.%m %H:%M")
    except Exception:
        return "-"


# ---------------------------------------------------------------- mesajlar
def msg_open(p):
    return (f"🚀 <b>İŞLEM AÇILDI</b> — {coin(p['instId'])} {side_tr(p.get('side'))}\n"
            f"Hesap: {acct(p.get('account'))} · Kaldıraç: {p.get('lever', '-')}x\n"
            f"Giriş: {num(p.get('avgPx'))} · Büyüklük: ${num(p.get('notionalUsd'), 0)}")


def msg_resize(old, new):
    o, n = float(old.get("pos") or 0), float(new.get("pos") or 0)
    word = "BÜYÜTÜLDÜ ➕" if n > o else "KÜÇÜLTÜLDÜ ➖"
    return (f"🔁 <b>POZİSYON {word}</b> — {coin(new['instId'])} {side_tr(new.get('side'))}\n"
            f"Hesap: {acct(new.get('account'))} · Kaldıraç: {new.get('lever', '-')}x\n"
            f"Ort. giriş: {num(old.get('avgPx'))} → {num(new.get('avgPx'))}\n"
            f"Büyüklük: ${num(old.get('notionalUsd'), 0)} → ${num(new.get('notionalUsd'), 0)}\n"
            f"Anlık K/Z: ${num(new.get('upl'))}")


def msg_close(h):
    pnl = float(h.get("pnl") or 0)
    icon = "✅" if pnl >= 0 else "❌"
    title = "KISMİ KAPANIŞ" if h.get("partial") else "İŞLEM KAPANDI"
    ratio = h.get("pnlRatio")
    ratio_txt = f" ({num(ratio)}%)" if ratio not in (None, 0) else ""
    lev = h.get("lever")
    lev_txt = f" · Kaldıraç: {lev}x" if lev else ""
    return (f"{icon} <b>{title}</b> — {coin(h.get('instId'))} {side_tr(h.get('side'))}\n"
            f"Hesap: {acct(h.get('account'))}{lev_txt}\n"
            f"Giriş: {num(h.get('avgPx'))} → Çıkış: {num(h.get('closePx'))}\n"
            f"K/Z: <b>${num(pnl)}</b>{ratio_txt} · {fmt_time(h.get('closedAt'))}")


def msg_close_unknown(p):
    return (f"🏁 <b>İŞLEM KAPANDI</b> — {coin(p['instId'])} {side_tr(p.get('side'))}\n"
            f"Hesap: {acct(p.get('account'))} · Kaldıraç: {p.get('lever', '-')}x\n"
            f"Giriş: {num(p.get('avgPx'))} · Son görülen K/Z: ${num(p.get('upl'))}\n"
            f"(Kesin kapanış fiyatı sitede henüz görünmüyor)")


def msg_summary(d, title):
    lines = [f"📊 <b>{title}</b>",
             f"Bakiye: ${num(d.get('totalEq'))} ({num(d.get('changePct'))}%)"]
    ps = d.get("positions") or []
    if ps:
        lines.append("Açık pozisyonlar:")
        for p in ps:
            lines.append(f"• {coin(p['instId'])} {side_tr(p.get('side'))} {p.get('lever')}x "
                         f"[{acct(p.get('account'))}] giriş {num(p.get('avgPx'))}, "
                         f"K/Z ${num(p.get('upl'))}")
    else:
        lines.append("Açık pozisyon yok.")
    return "\n".join(lines)


# ---------------------------------------------------------------- ana akış
def main():
    state = load_state()

    try:
        data = fetch()
        if not data.get("ok"):
            raise ValueError("API ok=false döndü")
    except Exception as e:
        print(f"Veri alınamadı: {e}", file=sys.stderr)
        if state is not None:
            state["fails"] = state.get("fails", 0) + 1
            if state["fails"] == FAIL_ALERT_AFTER:
                send(f"⚠️ Canlı cüzdan ~1 saattir okunamıyor.\nSon hata: {e}")
            save_state(state)
        return 0  # Actions'ı kırmızıya boyamayalım; bir sonraki turda tekrar dener

    positions = {pos_key(p): p for p in data.get("positions") or []}
    history = data.get("history") or []
    hist_ids = [h["id"] for h in history if h.get("id")]

    if state is None:
        send(msg_summary(data, "Takip başladı"))
        save_state({"positions": positions, "seen": hist_ids, "fails": 0})
        return 0

    if state.get("fails", 0) >= FAIL_ALERT_AFTER:
        send("✅ Canlı cüzdan yeniden okunabiliyor.")

    old = state.get("positions", {})
    seen = set(state.get("seen", []))
    new_closes = [h for h in history if h.get("id") and h["id"] not in seen]
    new_closes.sort(key=lambda h: h.get("closedAt") or 0)
    closed_keys = {pos_key(h) for h in new_closes}

    msgs = []
    for k, p in positions.items():
        if k not in old:
            msgs.append(msg_open(p))
        else:
            o = float(old[k].get("pos") or 0)
            n = float(p.get("pos") or 0)
            if o and abs(n - o) / o > SIZE_CHANGE_THRESHOLD and \
                    not (n < o and k in closed_keys):  # kısmi kapanış zaten bildirilecek
                msgs.append(msg_resize(old[k], p))
    for h in new_closes:
        msgs.append(msg_close(h))
    for k, p in old.items():
        if k not in positions and k not in closed_keys:
            msgs.append(msg_close_unknown(p))

    for m in msgs:
        send(m)
    if not msgs:
        print("Değişiklik yok.")

    # Geçmiş listesi kısaltılırsa eski id'ler tekrar "yeni" görünmesin diye birleştir
    keep = hist_ids + [i for i in state.get("seen", []) if i not in set(hist_ids)]
    save_state({"positions": positions, "seen": keep[:500], "fails": 0})
    return 0


if __name__ == "__main__":
    sys.exit(main())
