import io
import os
import sys
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests

KST = timezone(timedelta(hours=9))

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://edition.cnn.com/markets/fear-and-greed",
}

# 차트 기간(거래일 수)
TREND_DAYS = 120   # 상단: 추세 라인
CHANGE_DAYS = 20   # 하단: 일간 변동 막대

# 구간 경계 (CNN 기준)
ZONES = [
    (0, 25, "Extreme Fear", "#f6c9c9"),
    (25, 45, "Fear", "#fbe4e4"),
    (45, 55, "Neutral", "#efefef"),
    (55, 75, "Greed", "#dcebfc"),
    (75, 100, "Extreme Greed", "#bcd9f8"),
]
RATING_KO = {
    "extreme fear": "극단적 공포 😱",
    "fear": "공포 😨",
    "neutral": "중립 😐",
    "greed": "탐욕 😏",
    "extreme greed": "극단적 탐욕 🤑",
}
UP, DOWN, INK, MUTED = "#2370cd", "#dc3c3d", "#222222", "#6b6b6b"


# ---------------------------------------------------------------- 발송 여부
def should_skip_today(now_kst: datetime) -> bool:
    """한국 기준 일요일·월요일 = 미국 토·일 → 직전 장 없음."""
    return now_kst.weekday() in (6, 0)  # 6=일, 0=월


# ---------------------------------------------------------------- 데이터
def get_feargreed_data():
    r = requests.get(CNN_URL, headers=HEADERS, timeout=30)
    print(f"상태 코드: {r.status_code}")
    r.raise_for_status()
    return r.json()


def parse_history(data):
    """CNN 히스토리 → [(date, score)] (일자별 마지막 값, 오름차순)"""
    points = data.get("fear_and_greed_historical", {}).get("data", [])
    by_day = {}
    for p in points:
        d = datetime.fromtimestamp(p["x"] / 1000, tz=timezone.utc).date()
        by_day[d] = float(p["y"])
    hist = sorted(by_day.items())

    # 현재 값을 마지막 포인트로 보정 (히스토리가 하루 늦게 갱신되는 경우 대비)
    fg = data["fear_and_greed"]
    cur_day = datetime.fromisoformat(fg["timestamp"].replace("Z", "+00:00")).date()
    if not hist or hist[-1][0] < cur_day:
        hist.append((cur_day, float(fg["score"])))
    else:
        hist[-1] = (hist[-1][0], float(fg["score"]))
    return hist


# ---------------------------------------------------------------- 차트
def build_chart(hist, fg):
    trend = hist[-TREND_DAYS:]
    dates = [d for d, _ in trend]
    scores = [s for _, s in trend]

    tail = hist[-(CHANGE_DAYS + 1):]
    c_dates = [d for d, _ in tail[1:]]
    c_vals = [tail[i + 1][1] - tail[i][1] for i in range(len(tail) - 1)]

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(9, 7.2), dpi=150,
        gridspec_kw={"height_ratios": [2.3, 1], "hspace": 0.38},
    )
    fig.patch.set_facecolor("white")

    # ── 상단: 추세 + 구간 밴드
    for lo, hi, name, color in ZONES:
        ax1.axhspan(lo, hi, color=color, zorder=0, lw=0)
        ax1.text(dates[0], (lo + hi) / 2, f" {name}", va="center", ha="left",
                 fontsize=7.5, color=MUTED, zorder=1)
    ax1.plot(dates, scores, color=INK, lw=2, zorder=3, solid_capstyle="round")

    # 비교 기준점: 1주/1개월 전
    refs = [("1W", fg.get("previous_1_week")), ("1M", fg.get("previous_1_month"))]
    for label, val in refs:
        if val is None:
            continue
        ax1.axhline(val, color=MUTED, lw=0.8, ls=(0, (3, 3)), zorder=2)
        ax1.text(dates[-1], val, f" {label} {val:.0f}", va="center", ha="left",
                 fontsize=7.5, color=MUTED)

    last_d, last_s = dates[-1], scores[-1]
    ax1.scatter([last_d], [last_s], s=60, color=INK, zorder=4,
                edgecolors="white", linewidths=2)
    ax1.annotate(f"{last_s:.1f}", (last_d, last_s), xytext=(-8, 10),
                 textcoords="offset points", ha="right", fontsize=11,
                 fontweight="bold", color=INK)

    ax1.set_ylim(0, 100)
    ax1.set_xlim(dates[0], dates[-1] + timedelta(days=3))
    ax1.set_yticks([0, 25, 45, 55, 75, 100])
    ax1.set_title(f"CNN Fear & Greed Index - last {len(dates)} sessions",
                  loc="left", fontsize=12, fontweight="bold", color=INK)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%y.%m"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator())

    # ── 하단: 일간 변동
    colors = [UP if v >= 0 else DOWN for v in c_vals]
    x = range(len(c_vals))
    ax2.bar(x, c_vals, color=colors, width=0.72, zorder=3)
    ax2.axhline(0, color=INK, lw=0.8, zorder=4)
    lim = max(5, max(abs(v) for v in c_vals) * 1.25) if c_vals else 5
    ax2.set_ylim(-lim, lim)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels([d.strftime("%m/%d") for d in c_dates], rotation=90, fontsize=7.5)
    if c_vals:
        v = c_vals[-1]
        ax2.annotate(f"{v:+.1f}", (len(c_vals) - 1, v),
                     xytext=(0, 4 if v >= 0 else -12), textcoords="offset points",
                     ha="center", fontsize=9, fontweight="bold", color=INK)
    ax2.set_title(f"Daily change (pts) - last {len(c_vals)} sessions",
                  loc="left", fontsize=11, fontweight="bold", color=INK)

    for ax in (ax1, ax2):
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color("#cccccc")
        ax.spines["bottom"].set_color("#cccccc")
        ax.tick_params(colors=MUTED, length=0)
    ax2.grid(axis="y", color="#e6e6e6", lw=0.8, zorder=0)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- 메시지
def arrow(cur, prev):
    if prev is None:
        return "-"
    diff = cur - prev
    mark = "🔺" if diff > 0 else ("🔻" if diff < 0 else "➖")
    return f"{prev:.1f} ({mark}{diff:+.1f})"


def build_caption(data, hist, now_kst):
    fg = data["fear_and_greed"]
    score = float(fg["score"])
    rating = RATING_KO.get(str(fg["rating"]).lower(), fg["rating"])
    sps = data.get("stock_price_strength", {}).get("score")
    us_date = hist[-1][0].strftime("%Y-%m-%d")

    lines = [
        "📊 <b>CNN Fear &amp; Greed Index</b>",
        f"🗓 {now_kst:%Y-%m-%d (%a)} KST · 미국 {us_date} 장 기준",
        "",
        f"<b>종합: {score:.1f}</b> — {rating}",
        "",
        f"전일   {arrow(score, fg.get('previous_close'))}",
        f"1주전  {arrow(score, fg.get('previous_1_week'))}",
        f"1달전  {arrow(score, fg.get('previous_1_month'))}",
        f"1년전  {arrow(score, fg.get('previous_1_year'))}",
    ]
    if sps is not None:
        lines += ["", f"Stock Price Strength: {float(sps):.1f}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 텔레그램
def send_telegram_photo(png, caption):
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("feargreed.png", png, "image/png")},
        timeout=60,
    )
    print("텔레그램 응답:", r.status_code, r.text[:200])
    r.raise_for_status()


def send_telegram_text(text):
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )
    print("텔레그램 응답:", r.status_code, r.text[:200])
    r.raise_for_status()


# ---------------------------------------------------------------- main
def main():
    now_kst = datetime.now(KST)
    force = os.environ.get("FORCE_SEND", "").lower() == "true"
    if should_skip_today(now_kst) and not force:
        print(f"{now_kst:%Y-%m-%d %a} KST: 직전 미국장 미개장 → 알림 생략")
        return

    data = get_feargreed_data()
    hist = parse_history(data)
    caption = build_caption(data, hist, now_kst)

    try:
        png = build_chart(hist, data["fear_and_greed"])
        send_telegram_photo(png, caption)
    except Exception as e:  # 차트 실패 시 텍스트라도 발송
        print("차트 발송 실패, 텍스트로 대체:", repr(e), file=sys.stderr)
        send_telegram_text(caption)

    print("전송 완료:\n" + caption)


if __name__ == "__main__":
    main()
