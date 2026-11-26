"""CNN Fear & Greed + 미국 3대 지수 + 원/달러 텔레그램 브리핑.

발송물: 이미지 2장(앨범) + 요약 캡션  — 디자인: B-2a 딥 인사이트(라이트, 한글)
  1) 공포·탐욕 카드 : 오늘의 한 줄 · 지수/구간/1년 백분위 · 비교 칩 · 구성 지표 7개 · 6개월 흐름 · 60거래일 분포
  2) 해외 증시 카드 : 3대 지수 비교(1개월 전=100) · 지수 행(미니차트) · 원/달러 1개월
발송 시각: 스케줄 실행 시 KST 08:00:00 정각까지 대기 후 발송
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle
import numpy as np
import requests

KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ───────────────────────────────────────────── 설정
SEND_AT = (8, 0)       # 발송 시각 KST (시, 분)
LATE_LIMIT = (9, 0)    # 스케줄 실행이 이 시각 이후 시작되면 발송 안 함(시간 일관성 유지)
PREP_MIN = 3           # 발송 몇 분 전에 데이터 수집·렌더링 시작
TREND_DAYS = 120       # F&G 추세 기간(거래일)
STRIP_DAYS = 60        # 심리 분포 띠 기간(거래일)
MARKETS = [            # (Yahoo 심볼, 이미지 표기, 캡션 표기, 소수 자릿수)
    ("^IXIC", "NASDAQ", "나스닥", 2),
    ("^GSPC", "S&P 500", "S&P500", 2),
    ("^DJI", "DOW JONES", "다우", 2),
    ("KRW=X", "USD / KRW", "원/달러", 2),
]

# ───────────────────────────────────────────── 디자인 토큰 (B-2a 딥 인사이트 · 라이트)
W = 1080
DPI = 150
P = dict(bg="#f4f2ed", card="#ffffff", text="#17181a", sub="#5c6370", faint="#9aa0a8",
         line="#ebe8e1", up="#e0383e", down="#2f6fe0", track="#efede8", ink="#2b2d31")
IDX_COLOR = {"^IXIC": "#7b5cff", "^GSPC": "#17181a", "^DJI": "#1aa39a"}
NAMES = {"^IXIC": "나스닥", "^GSPC": "S&P 500", "^DJI": "다우존스", "KRW=X": "원/달러"}
# 공포(빨강) ↔ 중립(회색) ↔ 탐욕(파랑), CNN 구간 기준
ZONES = [
    (0, 25, "극단적 공포", "#d9363e"),
    (25, 45, "공포", "#f08c6c"),
    (45, 55, "중립", "#a3a8b0"),
    (55, 75, "탐욕", "#6fa5ef"),
    (75, 100, "극단적 탐욕", "#2f6fe0"),
]
COMPONENTS = [  # (CNN 키, 이름, 설명)
    ("market_momentum_sp500", "시장 모멘텀", "S&P500 vs 125일선"),
    ("stock_price_strength", "주가 강도", "52주 신고가·신저가"),
    ("stock_price_breadth", "주가 폭", "상승·하락 거래량"),
    ("put_call_options", "풋/콜 옵션", "풋·콜 비율"),
    ("market_volatility_vix", "변동성", "VIX vs 50일 평균"),
    ("junk_bond_demand", "정크본드 수요", "투기등급 스프레드"),
    ("safe_haven_demand", "안전자산 수요", "주식 vs 국채 수익률"),
]
WEEK_KO = "월화수목금토일"


def _pick_font():
    names = {f.name for f in font_manager.fontManager.ttflist}
    for n in ("Noto Sans CJK KR", "Noto Sans CJK JP", "NanumGothic"):
        if n in names:
            return n
    return None


FONT = _pick_font()  # 한글 폰트 없으면 None → 이미지 대신 텍스트 발송
plt.rcParams.update({"font.family": FONT or "DejaVu Sans", "axes.unicode_minus": False})


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
    # '전일'은 히스토리 직전 거래일 값 (CNN previous_close 필드는 히스토리와 어긋나는 경우가 있음)
    prev = hist[-2][1] if len(hist) >= 2 else fg.get("previous_close")
    refs = [
        ("PREV DAY", "전일", prev),
        ("1 WEEK", "1주", fg.get("previous_1_week")),
        ("1 MONTH", "1달", fg.get("previous_1_month")),
        ("1 YEAR", "1년", fg.get("previous_1_year")),
    ]
    return score, [r for r in refs if r[2] is not None]


def mkt_metrics(pts):
    last = pts[-1][1]
    d1 = (last / pts[-2][1] - 1) * 100
    m1 = (last / pts[0][1] - 1) * 100
    return last, d1, m1


def streak(hist):
    """현재와 같은 큰 구간(공포<45 / 중립 / 탐욕≥55)이 이어진 거래일 수"""
    def side(v):
        return 0 if v < 45 else (2 if v >= 55 else 1)
    s, cur = 0, side(hist[-1][1])
    for _, v in reversed(hist):
        if side(v) != cur:
            break
        s += 1
    return s


def percentile_1y(hist, score):
    yr = [v for d, v in hist if d >= hist[-1][0] - timedelta(days=365)]
    return sum(1 for v in yr if v <= score) / len(yr) * 100, len(yr)


def headline(hist, markets, score):
    zn, _ = zone_of(score)
    parts = [f"{zn} {streak(hist)}거래일째"]
    idx = [mkt_metrics(markets[s])[2] for s in ("^IXIC", "^GSPC", "^DJI") if s in markets]
    if idx:
        if all(m < 0 for m in idx):
            parts.append("3대 지수 모두 1개월 하락")
        elif all(m > 0 for m in idx):
            parts.append("3대 지수 모두 1개월 상승")
        else:
            parts.append("3대 지수 혼조")
    if "KRW=X" in markets:
        m = mkt_metrics(markets["KRW=X"])[2]
        parts.append(f"원화 {'약세' if m > 0 else '강세'}({m:+.1f}%)")
    return " · ".join(parts)


def fmt_num(v, nd=2):
    return f"{v:,.{nd}f}"


# ───────────────────────────────────────────── 그리기 공통
def px(size):
    """픽셀 높이 → 폰트 pt"""
    return size * 72 / DPI


class Canvas:
    """좌상단 원점 픽셀 좌표로 배치하는 카드 캔버스."""

    def __init__(self, h):
        self.H = h
        self.fig = plt.figure(figsize=(W / DPI, h / DPI), dpi=DPI)
        self.fig.patch.set_facecolor(P["bg"])
        self.bg = self.fig.add_axes([0, 0, 1, 1])
        self.bg.set_xlim(0, W)
        self.bg.set_ylim(h, 0)
        self.bg.axis("off")

    def t(self, x, y, s, size, color, weight="normal", ha="left", va="baseline", **kw):
        return self.bg.text(x, y, s, fontsize=px(size), color=color, fontweight=weight,
                            ha=ha, va=va, **kw)

    def box(self, x, y, w, h, color, r=28, z=0):
        self.bg.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                         facecolor=color, edgecolor="none", zorder=z))

    def pill(self, x, y, s, size, fg, bg, ha="left"):
        tb = self.t(x, y, s, size, fg, "bold", ha=ha, va="center")
        tb.set_bbox(dict(boxstyle="round,pad=0.45,rounding_size=0.85", facecolor=bg,
                         edgecolor="none"))

    def ax(self, x, y, w, h, tick, size=19):
        a = self.fig.add_axes([x / W, 1 - (y + h) / self.H, w / W, h / self.H])
        a.set_facecolor("none")
        for s in a.spines.values():
            s.set_visible(False)
        a.tick_params(colors=tick, length=0, labelsize=px(size), pad=5)
        return a

    def png(self):
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=DPI, facecolor=P["bg"])
        plt.close(self.fig)
        buf.seek(0)
        return buf


def hbar(c, x, y, w, h, score):
    """가로 그라데이션 게이지(공포→탐욕) + 현재값 마커"""
    cmap = LinearSegmentedColormap.from_list("z", [z[3] for z in ZONES])
    c.bg.imshow(np.linspace(0, 1, 256).reshape(1, -1), extent=[x, x + w, y + h, y], cmap=cmap,
                aspect="auto", zorder=2)
    mx = x + w * score / 100
    c.bg.add_patch(Polygon([[mx - 13, y - 22], [mx + 13, y - 22], [mx, y - 3]], closed=True,
                           facecolor=P["text"], zorder=4))
    for v, lab, ha in ((0, "공포", "left"), (50, "중립", "center"), (100, "탐욕", "right")):
        c.t(x + w * v / 100, y + h + 30, lab, 19, P["sub"], ha=ha)


# ───────────────────────────────────────────── 카드 1: 공포·탐욕
def build_fg_card(data, hist, markets, now_kst):
    score, refs = fg_metrics(data, hist)
    zn, zc = zone_of(score)
    pct, n = percentile_1y(hist, score)
    H = 1560
    c = Canvas(H)

    c.t(56, 88, "공포·탐욕 지수", 42, P["text"], "bold")
    c.t(W - 56, 88, f"{now_kst:%m.%d}({WEEK_KO[now_kst.weekday()]})", 26, P["text"], "bold",
        ha="right")
    c.box(40, 116, W - 80, 92, P["ink"], r=24)
    c.t(72, 172, "오늘의 한 줄", 20, "#c9ccd2", "bold")
    c.t(220, 172, headline(hist, markets, score), 23, "#ffffff", "bold")

    # 히어로
    c.box(40, 228, W - 80, 330, P["card"])
    c.t(80, 372, f"{score:.1f}", 120, P["text"], "bold")
    c.pill(400, 300, f" {zn} ", 26, "white", zc)
    c.t(400, 360, f"구간 {streak(hist)}거래일째", 24, P["text"], "bold")
    c.t(400, 400, f"최근 1년 중 하위 {pct:.0f}%", 24, P["text"], "bold")
    c.t(640, 400, f"({n}거래일 기준)", 19, P["faint"])
    hbar(c, 80, 466, W - 160, 22, score)

    # 비교 칩
    cy = 578
    cw = (W - 80 - 3 * 14) / 4
    for i, (_, ko, ref) in enumerate(refs):
        x = 40 + i * (cw + 14)
        ref = float(ref)
        d = score - ref
        c.box(x, cy, cw, 112, P["card"], r=22)
        c.t(x + 22, cy + 38, f"{ko} 전", 20, P["sub"])
        c.t(x + 22, cy + 88, f"{ref:.1f}", 34, P["text"], "bold")
        c.t(x + cw - 20, cy + 88, f"{d:+.1f}", 21, P["down"] if d > 0 else P["up"], "bold",
            ha="right")

    # 구성 지표 7개
    ky = 710
    c.box(40, ky, W - 80, 430, P["card"])
    c.t(80, ky + 56, "구성 지표 7개", 25, P["text"], "bold")
    comps = [(k, nm, desc, float(data[k]["score"])) for k, nm, desc in COMPONENTS
             if isinstance(data.get(k), dict) and data[k].get("score") is not None]
    if comps:
        weakest = min(comps, key=lambda r: r[3])
        c.t(W - 80, ky + 56, f"가장 약한 지표: {weakest[1]}", 20, P["sub"], ha="right")
    else:
        c.t(80, ky + 120, "세부 지표 데이터 없음", 22, P["faint"])
    bx, bw = 420, W - 80 - 420 - 90
    for i, (_, nm, desc, v) in enumerate(comps):
        y = ky + 100 + i * 46
        c.t(80, y + 9, nm, 22, P["text"], "bold")
        c.t(80 + len(nm) * 23 + 14, y + 9, desc, 17, P["faint"])
        c.box(bx, y - 9, bw, 22, P["track"], r=11)
        c.box(bx, y - 9, max(bw * v / 100, 22), 22, zone_of(v)[1], r=11, z=1)
        c.bg.plot([bx + bw / 2, bx + bw / 2], [y - 15, y + 19], color=P["faint"], lw=1, zorder=2)
        c.t(W - 80, y + 9, f"{v:.0f}", 23, P["text"], "bold", ha="right")

    # 6개월 흐름 + 60거래일 분포
    ty = 1160
    c.box(40, ty, W - 80, 370, P["card"])
    c.t(80, ty + 54, "최근 6개월 흐름", 25, P["text"], "bold")
    ax = c.ax(110, ty + 76, W - 190, 170, P["sub"], 17)
    tr = hist[-TREND_DAYS:]
    ds, vs = [d for d, _ in tr], [v for _, v in tr]
    for lo, hi, _, col in ZONES:
        ax.axhspan(lo, hi, color=col, alpha=0.08, lw=0)
    ax.plot(ds, vs, color=P["text"], lw=2)
    ax.scatter([ds[-1]], [vs[-1]], s=70, color=zc, edgecolors="white", linewidths=2.2, zorder=5)
    ax.set_ylim(0, 100)
    ax.set_xlim(ds[0], ds[-1] + timedelta(days=1))
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-m월"))
    imax = max(range(len(vs)), key=vs.__getitem__)
    if imax < len(vs) - 8:
        ax.annotate(f"고점 {vs[imax]:.0f}", (ds[imax], vs[imax]), xytext=(0, 8),
                    textcoords="offset points", ha="center", va="bottom", fontsize=px(17),
                    color=P["sub"])

    last = hist[-STRIP_DAYS:]
    c.t(80, ty + 300, f"{len(last)}거래일", 18, P["sub"], "bold")
    sx, sw = 180, (W - 260) / len(last)
    cnt = {}
    for i, (_, v) in enumerate(last):
        nm, col = zone_of(v)
        cnt[nm] = cnt.get(nm, 0) + 1
        c.box(sx + i * sw + 1.5, ty + 280, sw - 3, 30, col, r=4)
    summ = " · ".join(f"{nm} {cnt[nm] / len(last) * 100:.0f}%" for _, _, nm, _ in ZONES if nm in cnt)
    c.t(180, ty + 345, summ, 18, P["sub"])

    c.t(56, H - 14, "출처 CNN · 구성 지표 막대 중앙선 = 50(중립)", 18, P["faint"])
    return c.png()


# ───────────────────────────────────────────── 카드 2: 해외 증시 · 환율
def build_market_card(markets, now_kst):
    H = 1350
    c = Canvas(H)
    c.t(56, 88, "해외 증시 · 환율", 42, P["text"], "bold")
    c.t(W - 56, 88, "최근 1개월", 24, P["sub"], ha="right")

    # 3대 지수 비교 (1개월 전 = 100)
    c.box(40, 120, W - 80, 470, P["card"])
    c.t(80, 176, "3대 지수 비교", 25, P["text"], "bold")
    c.t(W - 80, 176, "1개월 전 = 100", 20, P["faint"], ha="right")
    ax = c.ax(110, 200, W - 400, 350, P["sub"])
    ends = []
    for sym in ("^IXIC", "^GSPC", "^DJI"):
        pts = markets.get(sym)
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] / pts[0][1] * 100 for p in pts]
        ax.plot(xs, ys, color=IDX_COLOR[sym], lw=2.4)
        ends.append((ys[-1], sym, xs[-1]))
    ax.axhline(100, color=P["faint"], lw=1, ls=(0, (3, 3)))
    ax.grid(axis="y", color=P["line"], lw=1)
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-m/%-d"))
    ends.sort(reverse=True)
    lo, hi = ax.get_ylim()
    gap, prev = (hi - lo) * 0.11, None
    for v, sym, xe in ends:  # 끝 라벨 겹침 방지
        yl = v if prev is None or prev - v >= gap else prev - gap
        prev = yl
        ax.annotate(f"{NAMES[sym]} {v - 100:+.1f}%", (xe, v), xytext=(xe + timedelta(days=1.2), yl),
                    textcoords="data", color=IDX_COLOR[sym], fontsize=px(20), fontweight="bold",
                    va="center", annotation_clip=False)

    # 지수 행
    ry = 610
    c.box(40, ry, W - 80, 300, P["card"])
    for i, sym in enumerate(("^IXIC", "^GSPC", "^DJI")):
        y = ry + 60 + i * 92
        c.bg.add_patch(Rectangle((80, y - 26), 8, 34, facecolor=IDX_COLOR[sym]))
        c.t(104, y, NAMES[sym], 25, P["text"], "bold")
        if i < 2:
            c.bg.plot([80, W - 80], [y + 40, y + 40], color=P["line"], lw=1)
        pts = markets.get(sym)
        if not pts:
            c.t(W - 80, y, "데이터 없음", 24, P["faint"], ha="right")
            continue
        last, d1, m1 = mkt_metrics(pts)
        c.t(520, y, fmt_num(last, 2), 28, P["text"], "bold", ha="right")
        sp = c.ax(550, y - 38, 150, 50, P["sub"])
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        col = P["up"] if m1 > 0 else P["down"]
        sp.plot(xs, ys, color=col, lw=1.8)
        sp.axhline(ys[0], color=P["faint"], lw=0.8, ls=(0, (2, 2)))
        sp.scatter([xs[-1]], [ys[-1]], s=16, color=col, zorder=3)
        sp.axis("off")
        c.pill(W - 230, y - 9, f"{'▲' if d1 > 0 else '▼'} {abs(d1):.2f}%", 20, "white",
               P["up"] if d1 > 0 else P["down"], ha="right")
        c.t(W - 80, y, f"1M {m1:+.1f}%", 21, P["sub"], ha="right")

    # 원/달러
    x, y, w, h = 40, 930, W - 80, 380
    c.box(x, y, w, h, P["card"])
    c.t(x + 40, y + 58, "원/달러 환율", 25, P["text"], "bold")
    pts = markets.get("KRW=X")
    if pts:
        last, d1, m1 = mkt_metrics(pts)
        c.t(x + w - 40, y + 62, f"{last:,.2f}원", 34, P["text"], "bold", ha="right")
        c.t(x + 40, y + 102, f"전일 {d1:+.2f}% · 1개월 {m1:+.1f}% → 원화 {'약세' if m1 > 0 else '강세'}",
            21, P["sub"])
        ax = c.ax(x + 40, y + 130, w - 170, h - 178, P["faint"], 17)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        col = P["up"] if m1 > 0 else P["down"]
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.12 or abs(hi) * 0.005
        ax.plot(xs, ys, color=col, lw=2.4)
        ax.fill_between(xs, ys, lo - pad, color=col, alpha=0.10, lw=0)
        ax.axhline(ys[0], color=P["faint"], lw=1, ls=(0, (3, 3)))
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_xlim(xs[0], xs[-1])
        ax.yaxis.tick_right()
        ax.set_yticks([lo, hi])
        ax.set_yticklabels([f"{lo:,.0f}", f"{hi:,.0f}"])
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%-m/%-d"))
    else:
        c.t(x + 40, y + 120, "데이터 없음", 26, P["faint"])

    c.t(56, H - 14, "출처 Yahoo Finance · 빨강 상승 / 파랑 하락 · 미니차트 점선 = 1개월 전", 18,
        P["faint"])
    return c.png()


# ───────────────────────────────────────────── 캡션
def build_caption(data, hist, markets, now_kst):
    score, refs = fg_metrics(data, hist)
    zn, _ = zone_of(score)
    pct, _ = percentile_1y(hist, score)
    emoji = "😱" if score < 25 else "😨" if score < 45 else "😐" if score < 55 else \
        "😏" if score < 75 else "🤑"
    cmp_parts = [f"{ko} {score - float(ref):+.1f}" for _, ko, ref in refs]
    lines = [
        f"📊 <b>마켓 브리핑</b> · {now_kst:%m/%d}({WEEK_KO[now_kst.weekday()]})",
        f"<i>미국 {hist[-1][0]:%m/%d} 종가 기준</i>",
        "",
        f"💬 {headline(hist, markets, score)}",
        "",
        f"{emoji} <b>공포탐욕 {score:.1f}</b> · {zn} · 1년 하위 {pct:.0f}%",
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
    builders = [("F&G", lambda: build_fg_card(data, hist, markets, now_kst))]
    if not FONT:  # 한글 폰트 없음 → 깨진 이미지 대신 텍스트만
        print("한글 폰트 없음 → 텍스트 발송", file=sys.stderr)
        builders = []
    elif markets:
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
