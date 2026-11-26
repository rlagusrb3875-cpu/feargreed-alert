"""CNN Fear & Greed + 미국 3대 지수 + 원/달러 텔레그램 브리핑.

발송물: 이미지 2장(앨범) + 요약 캡션
  1) Fear & Greed 카드 : 게이지 · 비교 타일 · 120거래일 추세 · 20거래일 일간 변동
  2) Markets 카드      : 나스닥 / S&P 500 / 다우 / 원달러 1개월 차트
발송 시각: 스케줄 실행 시 KST 08:00:00 정각까지 대기 후 발송
"""
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyBboxPatch, Wedge
import requests

KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ───────────────────────────────────────────── 설정
SEND_AT = (8, 0)       # 발송 시각 KST (시, 분)
LATE_LIMIT = (9, 0)    # 스케줄 실행이 이 시각 이후 시작되면 발송 안 함(시간 일관성 유지)
PREP_MIN = 3           # 발송 몇 분 전에 데이터 수집·렌더링 시작
TREND_DAYS = 120       # F&G 추세 기간(거래일)
CHANGE_DAYS = 20       # F&G 일간 변동 막대 개수
MARKETS = [            # (Yahoo 심볼, 이미지 표기, 캡션 표기, 소수 자릿수)
    ("^IXIC", "NASDAQ", "나스닥", 2),
    ("^GSPC", "S&P 500", "S&P500", 2),
    ("^DJI", "DOW JONES", "다우", 2),
    ("KRW=X", "USD / KRW", "원/달러", 2),
]

# ───────────────────────────────────────────── 디자인 토큰 (다크)
W, H = 1080, 1350      # 4:5 세로 (모바일 최적)
DPI = 150
BG = "#0f1216"
CARD = "#181d24"
LINE = "#262d37"
TEXT = "#eceef1"
SUB = "#9aa3af"
FAINT = "#69727e"

# F&G 구간 (공포=빨강 ↔ 중립=회색 ↔ 탐욕=파랑, CNN 관례)
ZONES = [
    (0, 25, "EXTREME FEAR", "#e5484d"),
    (25, 45, "FEAR", "#ef8f86"),
    (45, 55, "NEUTRAL", "#7b8494"),
    (55, 75, "GREED", "#7fb0f2"),
    (75, 100, "EXTREME GREED", "#3b82f6"),
]
FEAR_C, GREED_C = ZONES[0][3], ZONES[-1][3]
# 국내 관례: 상승=빨강, 하락=파랑 (지수·환율 카드)
UP_KR, DOWN_KR = "#f2555a", "#4b8bf5"

RATING_KO = {
    "extreme fear": "극단적 공포",
    "fear": "공포",
    "neutral": "중립",
    "greed": "탐욕",
    "extreme greed": "극단적 탐욕",
}
WEEK_KO = "월화수목금토일"


def _pick_font():
    names = {f.name for f in font_manager.fontManager.ttflist}
    for n in ("Inter", "DejaVu Sans"):
        if n in names:
            return n
    return "DejaVu Sans"


FONT = _pick_font()
plt.rcParams.update({"font.family": FONT, "axes.unicode_minus": False})


def zone_of(score):
    for lo, hi, name, color in ZONES:
        if score < hi:
            return name, color
    return ZONES[-1][2], ZONES[-1][3]


# ───────────────────────────────────────────── 발송 여부·시각
def should_skip_today(day_kst: datetime) -> bool:
    """한국 기준 일요일·월요일 = 미국 토·일 → 직전 장 없음."""
    return day_kst.weekday() in (6, 0)


def sleep_until(t: datetime):
    while True:
        left = (t - datetime.now(KST)).total_seconds()
        if left <= 0:
            return
        time.sleep(min(left, 60))


# ───────────────────────────────────────────── 데이터
def _get(url, headers=None):
    last = None
    for i in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA, **(headers or {})}, timeout=30)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"요청 실패 {url}: {last}")


def get_feargreed_data():
    r = _get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
             headers={"Accept": "application/json, text/plain, */*",
                      "Referer": "https://edition.cnn.com/markets/fear-and-greed"})
    return r.json()


def parse_history(data):
    """CNN 히스토리 → [(date, score)] 일자별 마지막 값, 오름차순."""
    by_day = {}
    for p in data.get("fear_and_greed_historical", {}).get("data", []):
        d = datetime.fromtimestamp(p["x"] / 1000, tz=timezone.utc).date()
        by_day[d] = float(p["y"])
    hist = sorted(by_day.items())
    fg = data["fear_and_greed"]
    cur_day = datetime.fromisoformat(fg["timestamp"].replace("Z", "+00:00")).date()
    if not hist or hist[-1][0] < cur_day:
        hist.append((cur_day, float(fg["score"])))
    else:
        hist[-1] = (hist[-1][0], float(fg["score"]))
    return hist


def get_market(symbol):
    """Yahoo 1개월 일봉 → [(datetime, close)]"""
    sym = requests.utils.quote(symbol)
    last_err = None
    for host in ("query1", "query2"):
        try:
            r = _get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}"
                     f"?range=1mo&interval=1d")
            res = r.json()["chart"]["result"][0]
            pts = [(datetime.fromtimestamp(t, tz=timezone.utc), float(c))
                   for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"])
                   if c is not None]
            if len(pts) >= 2:
                return pts
            last_err = "데이터 부족"
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"{symbol} 조회 실패: {last_err}")


def get_markets():
    out = {}
    for sym, *_ in MARKETS:
        try:
            out[sym] = get_market(sym)
        except Exception as e:  # noqa: BLE001
            print("시장 데이터 실패:", sym, e, file=sys.stderr)
    return out


# ───────────────────────────────────────────── 지표 계산
def fg_metrics(data, hist):
    fg = data["fear_and_greed"]
    score = float(fg["score"])
    # '전일'은 히스토리 직전 거래일 값 → 일간 변동 차트와 같은 기준
    # (CNN previous_close 필드는 히스토리와 어긋나는 경우가 있음)
    prev = hist[-2][1] if len(hist) >= 2 else fg.get("previous_close")
    refs = [
        ("PREV DAY", "전일", prev),
        ("1 WEEK", "1주", fg.get("previous_1_week")),
        ("1 MONTH", "1달", fg.get("previous_1_month")),
        ("1 YEAR", "1년", fg.get("previous_1_year")),
    ]
    return score, refs


def mkt_metrics(pts):
    last = pts[-1][1]
    d1 = (last / pts[-2][1] - 1) * 100
    m1 = (last / pts[0][1] - 1) * 100
    return last, d1, m1


# ───────────────────────────────────────────── 그리기 공통
def px(size):
    """픽셀 높이 → 폰트 pt"""
    return size * 72 / DPI


class Canvas:
    """좌상단 원점 픽셀 좌표로 배치하는 카드 캔버스."""

    def __init__(self):
        self.fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
        self.fig.patch.set_facecolor(BG)
        self.bg = self.fig.add_axes([0, 0, 1, 1])
        self.bg.set_xlim(0, W)
        self.bg.set_ylim(H, 0)
        self.bg.axis("off")

    def text(self, x, y, s, size, color=TEXT, weight="normal", ha="left", va="baseline", **kw):
        return self.bg.text(x, y, s, fontsize=px(size), color=color, fontweight=weight,
                            ha=ha, va=va, **kw)

    def card(self, x, y, w, h, r=26):
        self.bg.add_patch(FancyBboxPatch((x, y), w, h,
                                         boxstyle=f"round,pad=0,rounding_size={r}",
                                         linewidth=0, facecolor=CARD, zorder=0))

    def pill(self, x, y, s, size, fg, bg):
        t = self.text(x, y, s, size, color=fg, weight="bold", ha="center", va="center",
                      zorder=7)
        t.set_bbox(dict(boxstyle="round,pad=0.6,rounding_size=0.95", facecolor=bg,
                        edgecolor="none"))

    def axes(self, x, y, w, h):
        ax = self.fig.add_axes([x / W, 1 - (y + h) / H, w / W, h / H])
        ax.set_facecolor("none")
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(colors=SUB, length=0, labelsize=px(22), pad=6)
        return ax

    def png(self):
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=DPI, facecolor=BG)
        plt.close(self.fig)
        buf.seek(0)
        return buf


def fmt_num(v, nd=2):
    return f"{v:,.{nd}f}"


def arrow(v):
    return "▲" if v > 0 else ("▼" if v < 0 else "–")


def header(c, title, sub, right_top, right_sub, M=48):
    c.text(M, 80, title, 36, weight="bold")
    c.text(M, 118, sub, 21, color=SUB)
    c.text(W - M, 80, right_top, 26, weight="bold", ha="right")
    c.text(W - M, 118, right_sub, 21, color=SUB, ha="right")


# ───────────────────────────────────────────── 카드 1: Fear & Greed
def build_fg_card(data, hist, now_kst):
    score, refs = fg_metrics(data, hist)
    zname, zcolor = zone_of(score)
    c = Canvas()
    M = 48
    header(c, "FEAR & GREED INDEX", "CNN Business  ·  US market sentiment",
           f"{hist[-1][0]:%b %d, %Y}", "US close")

    # ── 게이지
    c.card(M, 150, W - 2 * M, 392)
    gx, gy, R, RW = W / 2, 452, 238, 44
    for lo, hi, _, col in ZONES:
        active = lo <= score < hi or (hi == 100 and score >= 100)
        c.bg.add_patch(Wedge((gx, gy), R, -(180 - lo * 1.8 - 0.8), -(180 - hi * 1.8 + 0.8),
                             width=RW, facecolor=col, alpha=1.0 if active else 0.25,
                             lw=0, zorder=2))
    for v in (0, 25, 50, 75, 100):
        a = math.radians(180 - v * 1.8)
        rr = R + 24
        c.text(gx + rr * math.cos(a), gy - rr * math.sin(a) + 2, str(v), 21,
               color=FAINT, ha="center", va="center")
    a = math.radians(180 - score * 1.8)
    rn = R - RW - 12
    c.bg.plot([gx, gx + rn * math.cos(a)], [gy, gy - rn * math.sin(a)], color=TEXT,
              lw=3.4, solid_capstyle="round", zorder=4)
    c.bg.add_patch(Circle((gx, gy), 14, facecolor=TEXT, lw=0, zorder=5))
    c.bg.add_patch(Circle((gx, gy), 5, facecolor=CARD, lw=0, zorder=6))
    c.text(gx, gy - 70, f"{score:.1f}", 96, weight="bold", ha="center", zorder=7)
    c.pill(gx, gy + 52, zname, 22, fg=BG, bg=zcolor)

    # ── 비교 타일
    ty, th, gap = 562, 156, 16
    tw = (W - 2 * M - 3 * gap) / 4
    for i, (code, _, ref) in enumerate(refs):
        x = M + i * (tw + gap)
        c.card(x, ty, tw, th, r=22)
        c.text(x + 24, ty + 44, code, 20, color=SUB, weight="bold")
        if ref is None:
            c.text(x + 24, ty + 104, "–", 42, weight="bold")
            continue
        ref = float(ref)
        diff = score - ref
        c.bg.add_patch(Circle((x + tw - 28, ty + 38), 8, facecolor=zone_of(ref)[1], lw=0))
        c.text(x + 24, ty + 104, f"{ref:.1f}", 42, weight="bold")
        dcol = GREED_C if diff > 0 else (FEAR_C if diff < 0 else SUB)
        c.text(x + 24, ty + 138, f"{arrow(diff)} {abs(diff):.1f} since", 22, color=dcol,
               weight="bold")

    # ── 추세
    cy, chh = 738, 340
    c.card(M, cy, W - 2 * M, chh)
    trend = hist[-TREND_DAYS:]
    c.text(M + 28, cy + 50, "Trend", 24, weight="bold")
    c.text(M + 112, cy + 50, f"last {len(trend)} sessions", 22, color=SUB)
    ax = c.axes(M + 70, cy + 76, W - 2 * M - 104, chh - 128)
    dates = [d for d, _ in trend]
    vals = [v for _, v in trend]
    for lo, hi, _, col in ZONES:
        ax.axhspan(lo, hi, color=col, alpha=0.09, lw=0, zorder=0)
    for yv in (25, 45, 55, 75):
        ax.axhline(yv, color=LINE, lw=0.8, zorder=1)
    ax.plot(dates, vals, color=TEXT, lw=2.0, zorder=3, solid_joinstyle="round",
            solid_capstyle="round")
    ax.scatter([dates[-1]], [vals[-1]], s=80, color=zcolor, edgecolors=CARD,
               linewidths=2.5, zorder=5)
    imax = max(range(len(vals)), key=vals.__getitem__)
    imin = min(range(len(vals)), key=vals.__getitem__)
    for idx, off, va in ((imax, 9, "bottom"), (imin, -9, "top")):
        if idx >= len(vals) - 8:  # 현재값 점과 겹침 방지
            continue
        ax.annotate(f"{vals[idx]:.0f}", (dates[idx], vals[idx]), xytext=(0, off),
                    textcoords="offset points", ha="center", va=va, fontsize=px(21),
                    color=SUB)
    ax.set_ylim(0, 100)
    ax.set_xlim(dates[0], dates[-1] + timedelta(days=2))
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

    # ── 일간 변동
    by, bh = 1094, 216
    c.card(M, by, W - 2 * M, bh)
    tail = hist[-(CHANGE_DAYS + 1):]
    ch_vals = [tail[i + 1][1] - tail[i][1] for i in range(len(tail) - 1)]
    ch_dates = [d for d, _ in tail[1:]]
    if not ch_vals:  # 히스토리 1개뿐인 경우
        ch_vals, ch_dates = [0.0], [hist[-1][0]]
    c.text(M + 28, by + 46, "Daily change", 24, weight="bold")
    c.text(M + 222, by + 46, f"last {len(ch_vals)} sessions", 22, color=SUB)
    c.text(W - M - 28, by + 46, "▼ toward fear", 21, color=FEAR_C, weight="bold", ha="right")
    c.text(W - M - 210, by + 46, "▲ toward greed", 21, color=GREED_C, weight="bold", ha="right")
    ax2 = c.axes(M + 28, by + 70, W - 2 * M - 56, bh - 116)
    ax2.bar(range(len(ch_vals)), ch_vals, width=0.62, zorder=3,
            color=[GREED_C if v >= 0 else FEAR_C for v in ch_vals])
    ax2.axhline(0, color=FAINT, lw=1, zorder=4)
    lim = max(5, max(abs(v) for v in ch_vals) * 1.3)
    ax2.set_ylim(-lim, lim)
    ax2.set_yticks([])
    ax2.set_xlim(-0.6, len(ch_vals) - 0.4)
    ticks = [i for i in range(len(ch_dates)) if (len(ch_dates) - 1 - i) % 4 == 0]
    ax2.set_xticks(ticks)
    ax2.set_xticklabels([ch_dates[i].strftime("%m/%d") for i in ticks])
    v = ch_vals[-1]
    ax2.annotate(f"{v:+.1f}", (len(ch_vals) - 1, v), xytext=(-12, 0),
                 textcoords="offset points", ha="right", va="bottom" if v >= 0 else "top",
                 fontsize=px(19), color=TEXT, fontweight="bold")

    c.text(M, H - 14, f"Source: CNN  ·  {now_kst:%Y-%m-%d %H:%M} KST", 20, color=FAINT)
    return c.png()


# ───────────────────────────────────────────── 카드 2: Markets
def build_market_card(markets, now_kst):
    c = Canvas()
    M = 48
    header(c, "MARKETS", "1 month  ·  daily close", f"{now_kst:%b %d, %Y}", "KST")

    top, bottom, gapy = 150, H - 46, 16
    n = len(MARKETS)
    rh = (bottom - top - gapy * (n - 1)) / n
    for i, (sym, name, _, nd) in enumerate(MARKETS):
        y = top + i * (rh + gapy)
        c.card(M, y, W - 2 * M, rh)
        lx = M + 30
        c.text(lx, y + 52, name, 22, color=SUB, weight="bold")
        pts = markets.get(sym)
        if not pts:
            c.text(lx, y + 120, "No data", 34, color=FAINT, weight="bold")
            continue
        last, d1, m1 = mkt_metrics(pts)
        col_d = UP_KR if d1 > 0 else (DOWN_KR if d1 < 0 else SUB)
        col_m = UP_KR if m1 > 0 else (DOWN_KR if m1 < 0 else SUB)
        c.text(lx, y + 116, fmt_num(last, nd), 44, weight="bold")
        c.text(lx, y + 162, "1D", 20, color=FAINT, weight="bold")
        c.text(lx + 44, y + 162, f"{arrow(d1)} {abs(d1):.2f}%", 23, color=col_d, weight="bold")
        c.text(lx, y + 200, "1M", 20, color=FAINT, weight="bold")
        c.text(lx + 44, y + 200, f"{arrow(m1)} {abs(m1):.2f}%", 23, color=col_m, weight="bold")
        asof = pts[-1][0].astimezone(KST) if sym == "KRW=X" else pts[-1][0].astimezone(
            timezone(timedelta(hours=-5)))
        c.text(lx, y + rh - 22, f"as of {asof:%m/%d}", 20, color=FAINT)

        ax = c.axes(M + 340, y + 28, W - 2 * M - 340 - 100, rh - 66)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        base = ys[0]
        ax.axhline(base, color=FAINT, lw=1, ls=(0, (3, 3)), zorder=1)
        ax.fill_between(xs, ys, base, where=[v >= base for v in ys], interpolate=True,
                        color=UP_KR, alpha=0.14, lw=0, zorder=2)
        ax.fill_between(xs, ys, base, where=[v < base for v in ys], interpolate=True,
                        color=DOWN_KR, alpha=0.14, lw=0, zorder=2)
        ax.plot(xs, ys, color=col_m, lw=2.2, zorder=3, solid_joinstyle="round",
                solid_capstyle="round")
        ax.scatter([xs[-1]], [ys[-1]], s=50, color=col_m, edgecolors=CARD, linewidths=2,
                   zorder=4)
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.14 or abs(hi) * 0.01
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_xlim(xs[0], xs[-1] + (xs[-1] - xs[0]) * 0.025)  # 끝점 잘림 방지
        ax.yaxis.tick_right()
        ax.set_yticks([lo, hi])
        ax.set_yticklabels([fmt_num(lo, 0), fmt_num(hi, 0)])
        for yv in (lo, hi):
            ax.axhline(yv, color=LINE, lw=0.8, zorder=0)
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))

    c.text(M, H - 14, "Source: Yahoo Finance  ·  KR convention: red ▲ up, blue ▼ down  ·  "
           "dashed = 1M-ago close", 19, color=FAINT)
    return c.png()


# ───────────────────────────────────────────── 캡션
def build_caption(data, hist, markets, now_kst):
    score, refs = fg_metrics(data, hist)
    rating = RATING_KO.get(str(data["fear_and_greed"]["rating"]).lower(),
                           data["fear_and_greed"]["rating"])
    emoji = "😱" if score < 25 else "😨" if score < 45 else "😐" if score < 55 else \
        "😏" if score < 75 else "🤑"
    cmp_parts = [f"{ko} {score - float(ref):+.1f}" for _, ko, ref in refs if ref is not None]
    lines = [
        f"📊 <b>마켓 브리핑</b> · {now_kst:%m/%d}({WEEK_KO[now_kst.weekday()]})",
        f"<i>미국 {hist[-1][0]:%m/%d} 종가 기준</i>",
        "",
        f"{emoji} <b>공포탐욕 {score:.1f}</b> · {rating}",
        "    " + " · ".join(cmp_parts),
        "",
    ]
    for sym, _, ko, nd in MARKETS:
        pts = markets.get(sym)
        if not pts:
            lines.append(f"⚪ <b>{ko}</b>  데이터 없음")
            continue
        last, d1, m1 = mkt_metrics(pts)
        dot = "🔺" if d1 > 0 else ("🔹" if d1 < 0 else "▫️")
        lines.append(f"{dot} <b>{ko}</b> {fmt_num(last, nd)}  {d1:+.2f}%  (1M {m1:+.1f}%)")
    return "\n".join(lines)


# ───────────────────────────────────────────── 텔레그램
def _tg(method, **kw):
    token = os.environ["TELEGRAM_TOKEN"]
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", timeout=60, **kw)
    print(f"텔레그램 {method}:", r.status_code, r.text[:160])
    r.raise_for_status()
    return r


def send_album(images, caption):
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    if len(images) == 1:
        return _tg("sendPhoto",
                   data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                   files={"photo": ("card.png", images[0], "image/png")})
    media, files = [], {}
    for i, img in enumerate(images):
        key = f"img{i}"
        item = {"type": "photo", "media": f"attach://{key}"}
        if i == 0:
            item.update(caption=caption, parse_mode="HTML")
        media.append(item)
        files[key] = (f"{key}.png", img, "image/png")
    return _tg("sendMediaGroup", data={"chat_id": chat_id, "media": json.dumps(media)},
               files=files)


def send_text(text):
    _tg("sendMessage", data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
                             "parse_mode": "HTML"})


# ───────────────────────────────────────────── 실행
def build_and_send(now_kst, send_at=None):
    data = get_feargreed_data()
    hist = parse_history(data)
    markets = get_markets()
    caption = build_caption(data, hist, markets, now_kst)

    images = []
    builders = [("F&G", lambda: build_fg_card(data, hist, now_kst))]
    if markets:
        builders.append(("Markets", lambda: build_market_card(markets, now_kst)))
    for name, fn in builders:
        try:
            images.append(fn())
        except Exception as e:  # noqa: BLE001
            print(f"{name} 카드 생성 실패:", repr(e), file=sys.stderr)

    if send_at:
        print(f"{send_at:%H:%M:%S} KST 정각까지 대기")
        sleep_until(send_at)

    try:
        if images:
            send_album(images, caption)
        else:
            send_text(caption)
    except Exception as e:  # noqa: BLE001
        print("이미지 발송 실패, 텍스트 대체:", repr(e), file=sys.stderr)
        send_text(caption)
    print("전송 완료:\n" + caption)


def plan(now):
    """스케줄 실행 시 동작 결정 → (action, send_at). action: send | skip_day | late"""
    send_at = now.replace(hour=SEND_AT[0], minute=SEND_AT[1], second=0, microsecond=0)
    late = now.replace(hour=LATE_LIMIT[0], minute=LATE_LIMIT[1], second=0, microsecond=0)
    if should_skip_today(send_at):
        return "skip_day", send_at
    if now >= late:
        return "late", send_at
    return "send", send_at


def main():
    now = datetime.now(KST)
    scheduled = os.environ.get("SCHEDULED", "").lower() == "true"
    force = os.environ.get("FORCE_SEND", "").lower() == "true"

    if not scheduled:  # 수동 실행: 즉시 발송
        if should_skip_today(now) and not force:
            print(f"{now:%Y-%m-%d %a} KST: 직전 미국장 미개장 → 생략 (force로 강제 가능)")
            return
        build_and_send(now)
        return

    action, send_at = plan(now)
    if action == "skip_day":
        print(f"{send_at:%Y-%m-%d %a} KST: 직전 미국장 미개장 → 생략")
        return
    if action == "late":
        print(f"스케줄 실행 지연({now:%H:%M} KST) → 시간 일관성 위해 생략")
        return

    sleep_until(send_at - timedelta(minutes=PREP_MIN))
    build_and_send(send_at, send_at=send_at)
    marker = os.environ.get("SENT_MARKER")
    if marker:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w").write(send_at.isoformat())


if __name__ == "__main__":
    main()
