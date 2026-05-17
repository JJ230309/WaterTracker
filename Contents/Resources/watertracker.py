#!/usr/bin/env python3
"""💧 Hydration Tracker — macOS water tracking app."""

import tkinter as tk
from tkinter import ttk, messagebox
import json, os, subprocess, time, calendar
from datetime import datetime, date, timedelta

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR    = os.path.expanduser("~/.watertracker")
DATA_FILE   = os.path.join(DATA_DIR, "data.json")
AGENT_LABEL = "com.local.watertracker.reminder"
AGENT_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{AGENT_LABEL}.plist")
# Paths for notification delivery
_HERE           = os.path.dirname(os.path.abspath(__file__))
PYTHON_BIN      = "/usr/local/bin/python3.14"
NOTIFIER_SCRIPT = os.path.join(_HERE, "notifier.py")
# Swift binary: attributes notifications to WaterTracker.app so clicking opens it
NOTIFY_BIN      = os.path.realpath(os.path.join(_HERE, "..", "MacOS", "WaterTrackerNotify"))
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.expanduser("~/Library/LaunchAgents"), exist_ok=True)

GLASS_ML = 250
FONT     = "Avenir Next"

# ── Palette ──────────────────────────────────────────────────────────────────
C = {
    "bg":       "#EDF5FB",
    "card":     "#FFFFFF",
    "card2":    "#F4F9FD",
    "primary":  "#5B9EC9",
    "pri_lt":   "#BDD8EE",
    "pri_dk":   "#3F7EA8",
    "mint":     "#6DC8A0",
    "mint_lt":  "#BDE8D3",
    "peach":    "#F4A07A",
    "teal":     "#5BBCB0",
    "teal_lt":  "#B8ECE4",
    "sip":      "#5A8FC8",
    "sip_lt":   "#C8DCFF",
    "ring_bg":  "#D5E8F5",
    "text_h":   "#2A4A6B",
    "text_b":   "#4A6A8B",
    "text_s":   "#8AABC9",
    "border":   "#D5E8F5",
    "qa0": ("#E5DEFF", "#6952B3"),
    "qa1": ("#C4DFEE", "#2A6890"),
    "qa2": ("#BDE8D3", "#2D8A60"),
    "qa3": ("#FFD9C0", "#B85820"),
    # Calendar heat colours (no-data → goal met)
    "cal_none": "#EBEBEB",
    "cal_low":  "#F4CDBA",
    "cal_25":   "#F4A07A",
    "cal_50":   "#F4D875",
    "cal_75":   "#5B9EC9",
    "cal_100":  "#6DC8A0",
}

QUICK_ADDS = [("100 ml",100,C["qa0"]),("250 ml",250,C["qa1"]),
              ("500 ml",500,C["qa2"]),("750 ml",750,C["qa3"])]
UNITS = ["ml", "L", "glasses"]

# ── Data ──────────────────────────────────────────────────────────────────────
def load_data():
    defaults = dict(
        today=str(date.today()), intake_ml=0, log=[],
        goal_ml=2000, unit="ml",
        reminder_min=30, reminder_enabled=False, reminder_enabled_at=0.0,
        bottle_enabled=False, bottle_ml=500,
        sip_enabled=False,    sip_ml=30,
        history={},
    )
    if os.path.exists(DATA_FILE):
        try:
            saved = json.load(open(DATA_FILE))
            # New day → archive yesterday into history first
            if saved.get("today") != str(date.today()):
                old_date   = saved.get("today", "")
                old_intake = saved.get("intake_ml", 0)
                old_goal   = saved.get("goal_ml", 2000)
                if old_date:
                    hist = saved.get("history", {})
                    hist[old_date] = {"intake_ml": old_intake, "goal_ml": old_goal}
                    saved["history"] = hist
                saved.update(today=str(date.today()), intake_ml=0, log=[])
            return {**defaults, **saved}
        except Exception:
            pass
    return defaults

def save_data(d):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)

def archive_today(d):
    """Write today's snapshot into history dict (call on every add)."""
    hist = d.setdefault("history", {})
    hist[d["today"]] = {"intake_ml": d["intake_ml"], "goal_ml": d["goal_ml"]}

# ── Conversions & helpers ─────────────────────────────────────────────────────
def to_display(ml, unit):
    if unit == "L":       return ml / 1000
    if unit == "glasses": return ml / GLASS_ML
    return float(ml)

def to_ml(val, unit):
    if unit == "L":       return int(val * 1000)
    if unit == "glasses": return int(val * GLASS_ML)
    return int(val)

def fmt(ml, unit):
    v = to_display(ml, unit)
    if unit == "L":       return f"{v:.2f} L"
    if unit == "glasses": return f"{v:.1f} gl"
    return f"{int(v)} ml"

def goal_fmt(goal_ml, unit):
    v = to_display(goal_ml, unit)
    if unit == "L":       return f"{v:.1f} L"
    if unit == "glasses": return f"{int(v)} glasses"
    return f"{int(v)} ml"

def motivation(pct):
    if pct == 0:  return "Let's start — your body is 60% water 🌊"
    if pct < 25:  return "Nice start, keep the momentum going 🌱"
    if pct < 50:  return "Getting there, don't stop now! ✨"
    if pct < 75:  return "Over halfway — you're doing amazing! 🌟"
    if pct < 100: return "So close to your goal — finish strong! 💪"
    return "Daily goal achieved! You're a hydration hero! 🏆"

def fmt_cd(seconds):
    s = max(0, int(seconds))
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}:{s:02d}"

def pct_color(pct):
    if pct is None: return C["cal_none"]
    if pct >= 100:  return C["cal_100"]
    if pct >= 75:   return C["cal_75"]
    if pct >= 50:   return C["cal_50"]
    if pct >= 25:   return C["cal_25"]
    return C["cal_low"]

def pct_text_color(pct):
    return "#FFFFFF" if pct is not None and pct >= 75 else C["text_h"]

# ── LaunchAgent (Swift binary → notification clicks open our app) ─────────────
def _write_plist(interval_min):
    # LaunchAgent calls notifier.py via Python.
    # terminal-notifier sends the banner; clicking it activates WaterTracker.app.
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{PYTHON_BIN}</string>
    <string>{NOTIFIER_SCRIPT}</string>
  </array>
  <key>StartInterval</key><integer>{interval_min * 60}</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardErrorPath</key><string>{DATA_DIR}/notifier.log</string>
</dict>
</plist>"""
    with open(AGENT_PLIST, "w") as f:
        f.write(plist)

def enable_reminders(interval_min):
    _write_plist(interval_min)
    uid = os.getuid()
    subprocess.run(["launchctl","bootout",f"gui/{uid}/{AGENT_LABEL}"], capture_output=True)
    subprocess.run(["launchctl","bootstrap",f"gui/{uid}",AGENT_PLIST], capture_output=True)
    # Fire one notification immediately so the user knows reminders are active
    subprocess.Popen([PYTHON_BIN, NOTIFIER_SCRIPT])

def disable_reminders():
    uid = os.getuid()
    subprocess.run(["launchctl","bootout",f"gui/{uid}/{AGENT_LABEL}"], capture_output=True)

# ── Widgets ───────────────────────────────────────────────────────────────────
class ProgressRing(tk.Canvas):
    def __init__(self, parent, size=190, ring_w=18, **kw):
        super().__init__(parent, width=size, height=size,
                         bg=C["bg"], highlightthickness=0, **kw)
        self.size, self.ring_w = size, ring_w
        self._pct, self._texts = 0.0, ("","","")
        self._draw()

    def set(self, pct, main, unit, goal):
        self._pct   = max(0.0, min(100.0, pct))
        self._texts = (main, unit, goal)
        self._draw()

    def _draw(self):
        self.delete("all")
        s   = self.size; pad = self.ring_w + 8
        x0,y0,x1,y1 = pad,pad,s-pad,s-pad; cx,cy = s//2,s//2
        self.create_arc(x0,y0,x1,y1, start=90, extent=359.9,
                        outline=C["ring_bg"], width=self.ring_w, style="arc")
        if self._pct > 0:
            color = C["mint"] if self._pct >= 100 else C["primary"]
            self.create_arc(x0,y0,x1,y1, start=90,
                            extent=-min(359.9, self._pct/100*360),
                            outline=color, width=self.ring_w, style="arc")
        main,unit,goal = self._texts
        self.create_text(cx,cy-16, text=main, font=(FONT,32,"bold"),
                         fill=C["text_h"], anchor="center")
        self.create_text(cx,cy+12, text=unit, font=(FONT,12),
                         fill=C["text_b"], anchor="center")
        self.create_text(cx,cy+30, text=goal, font=(FONT,10),
                         fill=C["text_s"], anchor="center")


class ScrollableFrame(tk.Frame):
    def __init__(self, parent, bg=C["card"], height=120, **kw):
        super().__init__(parent, bg=bg, **kw)
        self._cv = tk.Canvas(self, bg=bg, highlightthickness=0, height=height)
        self._sb = tk.Scrollbar(self, orient="vertical", command=self._cv.yview)
        self._cv.configure(yscrollcommand=self._sb.set)
        self._cv.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self._cv, bg=bg)
        self._win  = self._cv.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", self._on_inner)
        self._cv.bind("<Configure>", self._on_cv)
        self._cv.bind("<Enter>", lambda _: self._cv.bind_all("<MouseWheel>", self._scroll))
        self._cv.bind("<Leave>", lambda _: self._cv.unbind_all("<MouseWheel>"))

    def _on_inner(self, _):
        self._cv.configure(scrollregion=self._cv.bbox("all"))
        if self.inner.winfo_reqheight() > self._cv.winfo_height():
            self._sb.pack(side="right", fill="y")
        else:
            self._sb.pack_forget()

    def _on_cv(self, e):
        self._cv.itemconfig(self._win, width=e.width)

    def _scroll(self, e):
        self._cv.yview_scroll(int(-1*(e.delta/120)), "units")


# ── Calendar window ───────────────────────────────────────────────────────────
class CalendarWindow(tk.Toplevel):
    VIEWS = ["Day", "Week", "Month", "Year"]

    def __init__(self, parent, data_ref):
        super().__init__(parent)
        self.D       = data_ref
        self._view   = "Month"
        self._nav    = date.today()   # anchor date for navigation
        self._sel    = date.today()   # selected day (day view)
        self.title("📅 History")
        self.resizable(True, True)
        self.configure(bg=C["bg"])
        self.transient(parent)
        W, H = 420, 480
        self.geometry(f"{W}x{H}")
        self.update_idletasks()
        sw = self.winfo_screenwidth(); sh = self.winfo_screenheight()
        self.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        self._build()
        self.update_idletasks()
        # Delay first draw so canvas has real dimensions
        self.after(80, self._draw)

    # ── Layout ───────────────────────────────────────────────────────────────
    def _build(self):
        # Title bar
        hdr = tk.Frame(self, bg=C["bg"])
        hdr.pack(fill="x", padx=20, pady=(18, 0))
        tk.Label(hdr, text="📅  History", font=(FONT,18,"bold"),
                 fg=C["text_h"], bg=C["bg"]).pack(side="left")
        close_btn = tk.Label(hdr, text="✕", font=(FONT,16), fg=C["text_s"],
                             bg=C["bg"], cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _: self.destroy())

        # View selector
        vs = tk.Frame(self, bg=C["bg"])
        vs.pack(pady=(12,0))
        self._view_btns = {}
        for v in self.VIEWS:
            b = tk.Label(vs, text=v, font=(FONT,12,"bold"),
                         padx=14, pady=5, cursor="hand2")
            b.pack(side="left", padx=3)
            b.bind("<Button-1>", lambda e, vv=v: self._set_view(vv))
            self._view_btns[v] = b
        self._style_view_btns()

        # Navigation row
        nav = tk.Frame(self, bg=C["bg"])
        nav.pack(fill="x", padx=20, pady=(10,0))
        back_btn = tk.Label(nav, text="◀", font=(FONT,14), fg=C["primary"],
                            bg=C["bg"], cursor="hand2")
        back_btn.pack(side="left")
        back_btn.bind("<Button-1>", lambda _: self._nav_back())

        self._nav_lbl = tk.Label(nav, font=(FONT,13,"bold"),
                                  fg=C["text_h"], bg=C["bg"])
        self._nav_lbl.pack(side="left", expand=True)

        fwd_btn = tk.Label(nav, text="▶", font=(FONT,14), fg=C["primary"],
                           bg=C["bg"], cursor="hand2")
        fwd_btn.pack(side="right")
        fwd_btn.bind("<Button-1>", lambda _: self._nav_fwd())

        # Canvas for visualisation — Configure binding set once here
        self._canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0)
        self._canvas.pack(fill="both", expand=True, padx=20, pady=(8,8))
        self._canvas.bind("<Configure>", lambda _: self.after(10, self._draw))

        # Legend
        leg = tk.Frame(self, bg=C["bg"])
        leg.pack(pady=(0,12))
        for label, col in [("No data",C["cal_none"]),("< 25%",C["cal_low"]),
                            ("50%",C["cal_50"]),("75%",C["cal_75"]),("100%",C["cal_100"])]:
            tk.Frame(leg, bg=col, width=12, height=12).pack(side="left", padx=(8,2))
            tk.Label(leg, text=label, font=(FONT,9), fg=C["text_s"],
                     bg=C["bg"]).pack(side="left", padx=(0,4))

    def _style_view_btns(self):
        for v, b in self._view_btns.items():
            if v == self._view:
                b.config(bg=C["primary"], fg="white")
            else:
                b.config(bg=C["pri_lt"], fg=C["primary"])

    # ── Navigation ────────────────────────────────────────────────────────────
    def _set_view(self, v):
        self._view = v
        self._style_view_btns()
        self._draw()

    def _nav_back(self):
        if   self._view == "Day":   self._nav -= timedelta(days=1)
        elif self._view == "Week":  self._nav -= timedelta(weeks=1)
        elif self._view == "Month": self._nav = (self._nav.replace(day=1) - timedelta(days=1)).replace(day=1)
        elif self._view == "Year":  self._nav = self._nav.replace(year=self._nav.year-1)
        self._draw()

    def _nav_fwd(self):
        if   self._view == "Day":   self._nav += timedelta(days=1)
        elif self._view == "Week":  self._nav += timedelta(weeks=1)
        elif self._view == "Month":
            last = calendar.monthrange(self._nav.year, self._nav.month)[1]
            self._nav = (self._nav.replace(day=last) + timedelta(days=1))
        elif self._view == "Year":  self._nav = self._nav.replace(year=self._nav.year+1)
        self._draw()

    # ── Data helpers ──────────────────────────────────────────────────────────
    def _pct(self, d: date):
        ds = str(d)
        if ds == self.D["today"]:
            ml, goal = self.D["intake_ml"], self.D["goal_ml"]
        elif ds in self.D.get("history", {}):
            e = self.D["history"][ds]
            ml, goal = e["intake_ml"], e["goal_ml"]
        else:
            return None
        return min(100, int(ml/goal*100)) if goal else 0

    def _ml_for(self, d: date):
        ds = str(d)
        if ds == self.D["today"]:   return self.D["intake_ml"], self.D["goal_ml"]
        e = self.D.get("history",{}).get(ds)
        return (e["intake_ml"], e["goal_ml"]) if e else (None, None)

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _draw(self):
        cv = self._canvas
        cv.delete("all")
        W = cv.winfo_width()
        H = cv.winfo_height()
        # If canvas not yet sized, retry shortly
        if W < 10 or H < 10:
            self.after(50, self._draw)
            return
        if   self._view == "Month": self._draw_month(cv, W, H)
        elif self._view == "Week":  self._draw_week(cv, W, H)
        elif self._view == "Year":  self._draw_year(cv, W, H)
        elif self._view == "Day":   self._draw_day(cv, W, H)

    def _draw_month(self, cv, W, H):
        y, m = self._nav.year, self._nav.month
        self._nav_lbl.config(text=self._nav.strftime("%B %Y"))

        cal_rows = calendar.monthcalendar(y, m)
        cols, rows = 7, len(cal_rows)
        pad_x, pad_y = 10, 4
        cw = (W - pad_x*2) / cols
        ch = (H - pad_y*2 - 24) / rows   # 24 for day-name header

        # Day name headers
        for i, name in enumerate(["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]):
            cx = pad_x + i*cw + cw/2
            cv.create_text(cx, pad_y+10, text=name, font=(FONT,9),
                           fill=C["text_s"], anchor="center")

        today_s = str(date.today())
        for ri, week in enumerate(cal_rows):
            for ci, day in enumerate(week):
                if day == 0: continue
                x1 = pad_x + ci*cw + 2
                y1 = pad_y + 24 + ri*ch + 2
                x2, y2 = x1+cw-4, y1+ch-4
                cx, cy = (x1+x2)/2, (y1+y2)/2
                ds = f"{y}-{m:02d}-{day:02d}"
                pct = self._pct(date(y,m,day))
                fill = pct_color(pct)
                # Rounded-rect via polygon
                r = 6
                cv.create_polygon(
                    x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r,
                    x2,y2, x2-r,y2, x1+r,y2, x1,y2, x1,y2-r,
                    x1,y1+r, x1,y1, smooth=True, fill=fill, outline="")

                # Today ring
                if ds == today_s:
                    cv.create_polygon(
                        x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r,
                        x2,y2, x2-r,y2, x1+r,y2, x1,y2, x1,y2-r,
                        x1,y1+r, x1,y1, smooth=True,
                        fill="", outline=C["primary"], width=2)

                # Day number
                tc = pct_text_color(pct)
                cv.create_text(cx, cy-6, text=str(day),
                               font=(FONT,11,"bold"), fill=tc, anchor="center")
                # Percentage
                if pct is not None:
                    cv.create_text(cx, cy+7, text=f"{pct}%",
                                   font=(FONT,8), fill=tc, anchor="center")

                # Click → switch to day view
                tag = f"d_{ds}"
                cv.create_rectangle(x1,y1,x2,y2, outline="", fill="", tags=tag)
                cv.tag_bind(tag,"<Button-1>", lambda e,d=date(y,m,day): self._goto_day(d))

    def _draw_week(self, cv, W, H):
        # Find Monday of the week containing self._nav
        mon = self._nav - timedelta(days=self._nav.weekday())
        days = [mon + timedelta(days=i) for i in range(7)]
        self._nav_lbl.config(text=f"{mon.strftime('%b %d')} – {days[-1].strftime('%b %d, %Y')}")

        bar_w = (W - 40) / 7
        max_h = H - 80
        pad_x = 20

        for i, d in enumerate(days):
            pct = self._pct(d) or 0
            bar_h = max(4, int(pct / 100 * max_h))
            x1 = pad_x + i*bar_w + bar_w*0.1
            x2 = pad_x + i*bar_w + bar_w*0.9
            y_base = H - 40
            # Background bar
            cv.create_rectangle(x1, 10, x2, y_base,
                                 fill=C["cal_none"], outline="")
            # Progress bar
            raw_pct = self._pct(d)
            fill = pct_color(raw_pct)
            cv.create_rectangle(x1, y_base-bar_h, x2, y_base,
                                 fill=fill, outline="")
            # Percentage label
            if raw_pct is not None:
                cv.create_text((x1+x2)/2, y_base-bar_h-10,
                               text=f"{raw_pct}%", font=(FONT,9,"bold"),
                               fill=C["text_b"], anchor="center")
            # Day name + date
            is_today = (d == date.today())
            name_col = C["primary"] if is_today else C["text_s"]
            cv.create_text((x1+x2)/2, y_base+10, text=d.strftime("%a"),
                           font=(FONT,10,"bold" if is_today else "normal"),
                           fill=name_col, anchor="center")
            cv.create_text((x1+x2)/2, y_base+24, text=d.strftime("%-d"),
                           font=(FONT,9), fill=C["text_s"], anchor="center")

            # Click → day view
            tag = f"w_{d}"
            cv.create_rectangle(x1,10,x2,y_base+30, outline="", fill="", tags=tag)
            cv.tag_bind(tag,"<Button-1>", lambda e,dd=d: self._goto_day(dd))

    def _draw_year(self, cv, W, H):
        y = self._nav.year
        self._nav_lbl.config(text=str(y))

        cols, rows = 4, 3
        pad_x, pad_y = 12, 12
        mw = (W - pad_x*2) / cols
        mh = (H - pad_y*2) / rows

        for i, month in enumerate(range(1, 13)):
            row, col = divmod(i, cols)
            mx = pad_x + col*mw
            my = pad_y + row*mh

            # Month name
            mname = date(y,month,1).strftime("%b")
            cv.create_text(mx+mw/2, my+10, text=mname, font=(FONT,11,"bold"),
                           fill=C["text_h"], anchor="center")

            # Mini heatmap: days in a grid
            days_in_month = calendar.monthrange(y, month)[1]
            first_wd = calendar.monthrange(y, month)[0]  # 0=Mon
            cell = min(mw/7, (mh-22)/6) - 1
            gx = mx + (mw - 7*cell - 6*1)/2
            gy = my + 20

            for d in range(1, days_in_month+1):
                wd = (first_wd + d - 1) % 7
                week_row = (first_wd + d - 1) // 7
                cx1 = gx + wd*(cell+1)
                cy1 = gy + week_row*(cell+1)
                pct = self._pct(date(y, month, d))
                fill = pct_color(pct)
                cv.create_rectangle(cx1, cy1, cx1+cell, cy1+cell,
                                     fill=fill, outline="")

            # Click month → month view
            tag = f"m_{y}_{month}"
            cv.create_rectangle(mx, my, mx+mw, my+mh, outline="", fill="", tags=tag)
            cv.tag_bind(tag,"<Button-1>",
                        lambda e,yy=y,mm=month: self._goto_month(yy,mm))

    def _draw_day(self, cv, W, H):
        d   = self._sel if self._view == "Day" else self._nav
        ds  = str(d)
        self._nav_lbl.config(text=d.strftime("%A, %B %-d %Y"))
        ml, goal = self._ml_for(d)
        pct = self._pct(d)

        cx, cy = W//2, H//2 - 20

        if ml is None:
            cv.create_text(cx, cy, text="No data for this day",
                           font=(FONT,14), fill=C["text_s"], anchor="center")
            return

        # Mini ring
        r_out, r_in = 70, 52
        # Background arc
        cv.create_arc(cx-r_out,cy-r_out,cx+r_out,cy+r_out,
                      start=90, extent=359.9,
                      outline=C["ring_bg"], width=r_out-r_in, style="arc")
        if pct and pct > 0:
            col = C["mint"] if pct >= 100 else C["primary"]
            cv.create_arc(cx-r_out,cy-r_out,cx+r_out,cy+r_out,
                          start=90, extent=-min(359.9, pct/100*360),
                          outline=col, width=r_out-r_in, style="arc")
        cv.create_text(cx, cy-8,  text=f"{pct}%",
                       font=(FONT,22,"bold"), fill=C["text_h"], anchor="center")
        cv.create_text(cx, cy+14, text="of goal",
                       font=(FONT,10), fill=C["text_s"], anchor="center")

        unit = self.D["unit"]
        cv.create_text(cx, cy+55, text=f"{fmt(ml,unit)}  drank",
                       font=(FONT,12), fill=C["text_b"], anchor="center")
        cv.create_text(cx, cy+73, text=f"Goal: {goal_fmt(goal,unit)}",
                       font=(FONT,11), fill=C["text_s"], anchor="center")

        # Colour mood bar at bottom
        fill = pct_color(pct)
        bw = min(240, W-60)
        bh = 22
        bx, by = cx-bw//2, H-50
        cv.create_rectangle(bx, by, bx+bw, by+bh, fill=fill, outline="")
        lbl = "Goal met! 🏆" if pct >= 100 else f"{100-pct}% away from goal"
        tc = pct_text_color(pct)
        cv.create_text(bx+bw//2, by+bh//2, text=lbl,
                       font=(FONT,10,"bold"), fill=tc, anchor="center")

    def _goto_day(self, d):
        self._sel = d; self._nav = d
        self._set_view("Day")

    def _goto_month(self, y, m):
        self._nav = date(y, m, 1)
        self._set_view("Month")


# ── Main App ──────────────────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        self.D    = load_data()

        root.title("Hydration Tracker")
        root.configure(bg=C["bg"])
        root.minsize(360, 620)
        root.withdraw()

        W, H = 390, 740
        root.update_idletasks()
        sw = root.winfo_screenwidth(); sh = root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")

        self._build_ui()
        self._refresh()
        root.update_idletasks()
        root.deiconify()
        root.lift()
        root.focus_force()
        self._tick()

    # ── Layout ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        R = self.root

        # Header
        hdr = tk.Frame(R, bg=C["bg"])
        hdr.pack(fill="x", padx=22, pady=(22,0))
        tk.Label(hdr, text="💧  Hydration Tracker",
                 font=(FONT,20,"bold"), fg=C["text_h"], bg=C["bg"]).pack(side="left")
        # Right side icons
        btn_row = tk.Frame(hdr, bg=C["bg"])
        btn_row.pack(side="right")
        for icon, cmd in [("📅", self._open_calendar), ("⚙", self._open_settings)]:
            lbl = tk.Label(btn_row, text=icon, font=(FONT,17),
                           fg=C["text_s"], bg=C["bg"], cursor="hand2", padx=4)
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, c=cmd: c())

        self._date_lbl = tk.Label(R, font=(FONT,12), fg=C["text_s"], bg=C["bg"])
        self._date_lbl.pack()

        # Ring
        self._ring = ProgressRing(R, size=190, ring_w=18)
        self._ring.pack(pady=(14,0))

        # Stat pills
        sf = tk.Frame(R, bg=C["bg"])
        sf.pack(fill="x", padx=22, pady=(12,0))
        self._s_pct  = self._stat_pill(sf,"0%","complete")
        self._s_gls  = self._stat_pill(sf,"0","glasses")
        self._s_left = self._stat_pill(sf,"—","remaining")
        for p in (self._s_pct, self._s_gls, self._s_left):
            p["f"].pack(side="left", expand=True, fill="x", padx=4)

        # ── Add-water card ───────────────────────────────────────────────────
        add_out = tk.Frame(R, bg=C["bg"])
        add_out.pack(fill="x", padx=22, pady=(14,0))
        add = tk.Frame(add_out, bg=C["card"],
                       highlightbackground=C["border"], highlightthickness=1)
        add.pack(fill="x")
        self._add_card = add

        tk.Label(add, text="Add Water", font=(FONT,13,"bold"),
                 fg=C["text_h"], bg=C["card"]).pack(anchor="w", padx=16, pady=(14,8))

        # Fixed quick-add pills
        self._qa_row = tk.Frame(add, bg=C["card"])
        self._qa_row.pack(fill="x", padx=16, pady=(0,0))
        for i, (label, ml, (bg, fg)) in enumerate(QUICK_ADDS):
            self._qa_row.columnconfigure(i, weight=1)
            pill = tk.Frame(self._qa_row, bg=bg, cursor="hand2")
            pill.grid(row=0, column=i, padx=3, sticky="ew")
            lbl = tk.Label(pill, text=label, font=(FONT,11,"bold"),
                           fg=fg, bg=bg, pady=9, cursor="hand2")
            lbl.pack(fill="x")
            for w in (pill, lbl):
                w.bind("<Button-1>", lambda e, m=ml: self._add(m))

        # Sip pill (dynamic)
        self._sip_row  = tk.Frame(add, bg=C["card"])
        self._sip_pill = tk.Frame(self._sip_row, bg=C["sip_lt"], cursor="hand2")
        self._sip_pill.pack(fill="x", padx=3, pady=(8,0))
        self._sip_lbl  = tk.Label(self._sip_pill, text="",
                                   font=(FONT,12,"bold"), fg=C["sip"],
                                   bg=C["sip_lt"], pady=9, cursor="hand2")
        self._sip_lbl.pack(fill="x")
        for w in (self._sip_pill, self._sip_lbl):
            w.bind("<Button-1>", lambda e: self._add(self.D.get("sip_ml",30), sip=True))

        # Bottle pill (dynamic)
        self._bottle_row  = tk.Frame(add, bg=C["card"])
        self._bottle_pill = tk.Frame(self._bottle_row, bg=C["teal_lt"], cursor="hand2")
        self._bottle_pill.pack(fill="x", padx=3, pady=(8,0))
        self._bottle_lbl  = tk.Label(self._bottle_pill, text="",
                                      font=(FONT,12,"bold"), fg=C["teal"],
                                      bg=C["teal_lt"], pady=9, cursor="hand2")
        self._bottle_lbl.pack(fill="x")
        for w in (self._bottle_pill, self._bottle_lbl):
            w.bind("<Button-1>", lambda e: self._add(self.D.get("bottle_ml",500), bottle=True))

        # Custom amount row
        cr = tk.Frame(add, bg=C["card"])
        cr.pack(fill="x", padx=16, pady=(10,14))

        self._amt_var = tk.StringVar()
        self._amt_e   = tk.Entry(cr, textvariable=self._amt_var,
                                 font=(FONT,13), fg=C["text_s"],
                                 bg=C["card2"], relief="flat", width=8,
                                 insertbackground=C["primary"])
        self._amt_e.pack(side="left", ipady=8, padx=(0,8))
        self._amt_e.insert(0,"Amount"); self._amt_e.config(fg=C["text_s"])
        self._amt_e.bind("<FocusIn>",  self._amt_fi)
        self._amt_e.bind("<FocusOut>", self._amt_fo)
        self._amt_e.bind("<Return>",   lambda _: self._add_custom())

        self._unit_var = tk.StringVar(value=self.D["unit"])
        ucb = ttk.Combobox(cr, textvariable=self._unit_var, values=UNITS,
                           width=8, state="readonly", font=(FONT,12))
        ucb.pack(side="left", padx=(0,8), ipady=5)
        ucb.bind("<<ComboboxSelected>>", lambda _: self._unit_changed())

        self._mk_btn(cr,"Add +",    self._add_custom, C["primary"],"white").pack(side="left",ipady=6)
        self._mk_btn(cr,"Reset day",self._reset_day,  C["border"],C["text_s"],size=11).pack(side="right",ipady=6)

        # ── Reminder card ────────────────────────────────────────────────────
        rem_out = tk.Frame(R, bg=C["bg"])
        rem_out.pack(fill="x", padx=22, pady=(10,0))
        rem = tk.Frame(rem_out, bg=C["card"],
                       highlightbackground=C["border"], highlightthickness=1)
        rem.pack(fill="x")

        ri = tk.Frame(rem, bg=C["card"])
        ri.pack(fill="x", padx=16, pady=(12,6))
        tk.Label(ri, text="🔔", font=(FONT,16), bg=C["card"]).pack(side="left", padx=(0,10))
        tf = tk.Frame(ri, bg=C["card"])
        tf.pack(side="left", fill="x", expand=True)
        self._rem_title  = tk.Label(tf, font=(FONT,13,"bold"), fg=C["text_h"], bg=C["card"], anchor="w")
        self._rem_title.pack(anchor="w")
        self._rem_sub    = tk.Label(tf, font=(FONT,11), fg=C["text_s"], bg=C["card"], anchor="w")
        self._rem_sub.pack(anchor="w")
        self._rem_toggle = tk.Label(ri, font=(FONT,13,"bold"), bg=C["card"], cursor="hand2")
        self._rem_toggle.pack(side="right")
        self._rem_toggle.bind("<Button-1>", lambda _: self._toggle_reminder())

        # Countdown bar
        self._cd_frame = tk.Frame(rem, bg=C["card2"])
        self._cd_lbl   = tk.Label(self._cd_frame, font=(FONT,12,"bold"),
                                   fg=C["primary"], bg=C["card2"], anchor="center")
        self._cd_lbl.pack(pady=8)

        # Motivation
        self._mot = tk.Label(R, font=(FONT,12,"italic"),
                             fg=C["text_s"], bg=C["bg"], wraplength=350)
        self._mot.pack(pady=(10,6))

        # ── Log card ─────────────────────────────────────────────────────────
        lo = tk.Frame(R, bg=C["bg"])
        lo.pack(fill="both", expand=True, padx=22, pady=(0,20))
        lc = tk.Frame(lo, bg=C["card"],
                      highlightbackground=C["border"], highlightthickness=1)
        lc.pack(fill="both", expand=True)
        tk.Label(lc, text="Today's Log", font=(FONT,13,"bold"),
                 fg=C["text_h"], bg=C["card"]).pack(anchor="w", padx=16, pady=(12,6))
        self._log_sf = ScrollableFrame(lc, bg=C["card"], height=110)
        self._log_sf.pack(fill="both", expand=True, padx=16, pady=(0,12))

    # ── Widget helpers ────────────────────────────────────────────────────────
    def _stat_pill(self, parent, value, label):
        f = tk.Frame(parent, bg=C["card"],
                     highlightbackground=C["border"], highlightthickness=1)
        v = tk.Label(f, text=value, font=(FONT,16,"bold"), fg=C["text_h"], bg=C["card"])
        v.pack(pady=(9,0))
        tk.Label(f, text=label, font=(FONT,10), fg=C["text_s"], bg=C["card"]).pack(pady=(0,9))
        return {"f":f,"v":v}

    def _mk_btn(self, parent, text, cmd, bg, fg, size=12):
        f = tk.Frame(parent, bg=bg, cursor="hand2")
        lbl = tk.Label(f, text=text, font=(FONT,size,"bold"),
                       fg=fg, bg=bg, padx=12, cursor="hand2")
        lbl.pack(fill="both", expand=True)
        hover = C["pri_dk"] if bg==C["primary"] else bg
        for w in (f,lbl):
            w.bind("<Button-1>", lambda e: cmd())
            w.bind("<Enter>",    lambda e,h=hover: (f.config(bg=h),lbl.config(bg=h)))
            w.bind("<Leave>",    lambda e,b=bg:    (f.config(bg=b),lbl.config(bg=b)))
        return f

    def _amt_fi(self,_):
        if self._amt_var.get()=="Amount":
            self._amt_e.delete(0,"end"); self._amt_e.config(fg=C["text_b"])
    def _amt_fo(self,_):
        if not self._amt_var.get():
            self._amt_e.insert(0,"Amount"); self._amt_e.config(fg=C["text_s"])

    # ── Countdown ─────────────────────────────────────────────────────────────
    def _tick(self):
        if self.D.get("reminder_enabled"):
            ea  = self.D.get("reminder_enabled_at") or time.time()
            ivl = self.D["reminder_min"] * 60
            now = time.time()
            cycles   = int(max(0, now-ea) // ivl)
            next_f   = ea + (cycles+1)*ivl
            remain   = max(0.0, next_f - now)
            self._cd_lbl.config(text=f"⏱  Next reminder in  {fmt_cd(remain)}")
            self._cd_frame.pack(fill="x")
        else:
            self._cd_frame.pack_forget()
        self.root.after(1000, self._tick)

    # ── Refresh ───────────────────────────────────────────────────────────────
    def _refresh(self):
        D = self.D
        ml, goal, unit = D["intake_ml"], D["goal_ml"], D["unit"]
        pct = min(100.0, ml/goal*100) if goal else 0.0

        self._date_lbl.config(text=datetime.now().strftime("%A, %B %d"))

        iv = to_display(ml, unit)
        mt = f"{iv:.2f}" if unit=="L" else f"{iv:.1f}" if unit=="glasses" else f"{int(iv)}"
        self._ring.set(pct, mt, unit, f"of {goal_fmt(goal,unit)}")

        self._s_pct["v"].config(text=f"{int(pct)}%")
        self._s_gls["v"].config(text=f"{ml/GLASS_ML:.1f}")
        self._s_left["v"].config(text=fmt(max(0,goal-ml), unit))
        self._mot.config(text=motivation(pct))

        # Sip pill
        if D.get("sip_enabled") and D.get("sip_ml",0) > 0:
            self._sip_lbl.config(text=f"💧  1 Sip  ·  {D['sip_ml']} ml")
            self._sip_row.pack(fill="x", padx=16, pady=(8,0), after=self._qa_row)
        else:
            self._sip_row.pack_forget()

        # Bottle pill
        if D.get("bottle_enabled") and D.get("bottle_ml",0) > 0:
            self._bottle_lbl.config(text=f"🍶  1 Bottle  ·  {D['bottle_ml']} ml")
            ref = self._sip_row if D.get("sip_enabled") else self._qa_row
            self._bottle_row.pack(fill="x", padx=16, pady=(8,0), after=ref)
        else:
            self._bottle_row.pack_forget()

        # Reminder
        if D.get("reminder_enabled"):
            self._rem_title.config(text="Reminders active")
            self._rem_sub.config(text=f"Every {D['reminder_min']} min  ·  runs even when closed")
            self._rem_toggle.config(text="Stop", fg=C["peach"])
        else:
            self._rem_title.config(text="Reminders off")
            self._rem_sub.config(text="Tap Start — persists when app is closed")
            self._rem_toggle.config(text="Start", fg=C["mint"])

        # Log
        for w in self._log_sf.inner.winfo_children():
            w.destroy()
        log = D.get("log",[])
        if not log:
            tk.Label(self._log_sf.inner, text="No entries yet today",
                     font=(FONT,11), fg=C["text_s"], bg=C["card"]).pack(pady=8)
        else:
            for i, e in enumerate(reversed(log)):
                row = tk.Frame(self._log_sf.inner, bg=C["card"])
                row.pack(fill="x", pady=2)
                icon = "💧" if e.get("sip") else "🍶" if e.get("bottle") else "·"
                tk.Label(row, text=f"#{len(log)-i}", font=(FONT,10), fg=C["text_s"],
                         bg=C["card"], width=3, anchor="w").pack(side="left")
                tk.Label(row, text=e["time"], font=(FONT,11),
                         fg=C["text_s"], bg=C["card"]).pack(side="left", padx=(4,0))
                tk.Label(row, text=icon, font=(FONT,10), bg=C["card"]).pack(side="left", padx=4)
                tk.Label(row, text=fmt(e["ml"],unit), font=(FONT,11,"bold"),
                         fg=C["primary"], bg=C["card"]).pack(side="right")
            sep = tk.Frame(self._log_sf.inner, bg=C["border"], height=1)
            sep.pack(fill="x", pady=(6,4))
            tr = tk.Frame(self._log_sf.inner, bg=C["card"])
            tr.pack(fill="x", pady=(0,4))
            tk.Label(tr, text="Total today", font=(FONT,11),
                     fg=C["text_s"], bg=C["card"]).pack(side="left")
            tk.Label(tr, text=fmt(ml,unit), font=(FONT,11,"bold"),
                     fg=C["text_h"], bg=C["card"]).pack(side="right")

    # ── Actions ───────────────────────────────────────────────────────────────
    def _add(self, ml, sip=False, bottle=False):
        self.D["intake_ml"] += ml
        entry = {"time": datetime.now().strftime("%H:%M"), "ml": ml}
        if sip:    entry["sip"]    = True
        if bottle: entry["bottle"] = True
        self.D["log"].append(entry)
        archive_today(self.D)
        save_data(self.D)
        self._refresh()

    def _add_custom(self):
        raw = self._amt_var.get().strip()
        if not raw or raw == "Amount": return
        try:
            ml = to_ml(float(raw), self._unit_var.get())
            if ml <= 0: raise ValueError
        except ValueError:
            messagebox.showerror("Invalid","Please enter a positive number.")
            return
        self._amt_var.set("")
        self._add(ml)

    def _unit_changed(self):
        self.D["unit"] = self._unit_var.get()
        save_data(self.D); self._refresh()

    def _reset_day(self):
        if messagebox.askyesno("Reset","Reset today's intake to zero?"):
            self.D["intake_ml"] = 0; self.D["log"] = []
            save_data(self.D); self._refresh()

    def _toggle_reminder(self):
        if self.D.get("reminder_enabled"):
            disable_reminders()
            self.D["reminder_enabled"]    = False
            self.D["reminder_enabled_at"] = 0.0
        else:
            enable_reminders(self.D["reminder_min"])
            self.D["reminder_enabled"]    = True
            self.D["reminder_enabled_at"] = time.time()
        save_data(self.D); self._refresh()

    def _open_calendar(self):
        CalendarWindow(self.root, self.D)

    # ── Settings ──────────────────────────────────────────────────────────────
    def _open_settings(self):
        w = tk.Toplevel(self.root)
        w.title("Settings"); w.resizable(False, False)
        w.configure(bg=C["bg"]); w.transient(self.root); w.grab_set()
        W2, H2 = 340, 620
        w.geometry(f"{W2}x{H2}")
        w.update_idletasks()
        sw = w.winfo_screenwidth(); sh = w.winfo_screenheight()
        w.geometry(f"{W2}x{H2}+{(sw-W2)//2}+{(sh-H2)//2}")

        sf = ScrollableFrame(w, bg=C["bg"], height=H2-80)
        sf.pack(fill="both", expand=True)
        body = sf.inner

        tk.Label(body, text="⚙  Settings", font=(FONT,18,"bold"),
                 fg=C["text_h"], bg=C["bg"]).pack(pady=(22,4))

        def card(title):
            f = tk.Frame(body, bg=C["card"],
                         highlightbackground=C["border"], highlightthickness=1)
            f.pack(fill="x", padx=20, pady=(12,0))
            tk.Label(f, text=title, font=(FONT,13,"bold"),
                     fg=C["text_h"], bg=C["card"]).pack(anchor="w", padx=16, pady=(14,6))
            return f

        def pill_row(parent):
            r = tk.Frame(parent, bg=C["card"])
            r.pack(fill="x", padx=16, pady=(4,14))
            return r

        # ── Goal ─────────────────────────────────────────────────────────────
        gc = card("Daily Water Goal")
        gr = tk.Frame(gc, bg=C["card"]); gr.pack(fill="x", padx=16, pady=(0,8))
        goal_var = tk.StringVar(value=str(self.D["goal_ml"]))
        g_unit   = tk.StringVar(value="ml")
        tk.Entry(gr, textvariable=goal_var, font=(FONT,13), fg=C["text_b"],
                 bg=C["card2"], relief="flat", width=8,
                 insertbackground=C["primary"]).pack(side="left", ipady=7, padx=(0,8))
        ttk.Combobox(gr, textvariable=g_unit, values=UNITS,
                     width=8, state="readonly", font=(FONT,12)).pack(side="left", ipady=5)
        pr = pill_row(gc)
        for lbl, val in [("1.5 L",1500),("2 L",2000),("2.5 L",2500),("3 L",3000)]:
            p = tk.Label(pr, text=lbl, font=(FONT,11,"bold"), fg=C["primary"],
                         bg=C["pri_lt"], padx=8, pady=4, cursor="hand2")
            p.pack(side="left", padx=3)
            p.bind("<Button-1>", lambda e,v=val: (goal_var.set(str(v)), g_unit.set("ml")))

        # ── Reminder ─────────────────────────────────────────────────────────
        ic = card("Reminder Interval")
        ir = tk.Frame(ic, bg=C["card"]); ir.pack(fill="x", padx=16, pady=(0,8))
        tk.Label(ir,text="Every",font=(FONT,12),fg=C["text_b"],bg=C["card"]).pack(side="left",padx=(0,8))
        int_var = tk.IntVar(value=self.D["reminder_min"])
        tk.Spinbox(ir, from_=5, to=240, increment=5, textvariable=int_var,
                   font=(FONT,13), width=5, relief="flat",
                   bg=C["card2"], fg=C["text_b"],
                   buttonbackground=C["card2"]).pack(side="left", ipady=7, padx=(0,8))
        tk.Label(ir,text="minutes",font=(FONT,12),fg=C["text_b"],bg=C["card"]).pack(side="left")
        pp = pill_row(ic)
        for mins in (15,30,45,60,90,120):
            p = tk.Label(pp, text=f"{mins}m", font=(FONT,11,"bold"), fg=C["primary"],
                         bg=C["pri_lt"], padx=7, pady=4, cursor="hand2")
            p.pack(side="left", padx=2)
            p.bind("<Button-1>", lambda e,m=mins: int_var.set(m))

        # ── Sip ──────────────────────────────────────────────────────────────
        sc_card = card("💧  Sip Size")
        sh_row  = tk.Frame(sc_card, bg=C["card"]); sh_row.pack(fill="x", padx=16, pady=(0,8))
        sip_on  = tk.BooleanVar(value=self.D.get("sip_enabled",False))
        tk.Checkbutton(sh_row, text="Enable sip logging", variable=sip_on,
                       font=(FONT,11), fg=C["text_b"], bg=C["card"],
                       activebackground=C["card"], selectcolor=C["card"],
                       cursor="hand2").pack(side="left")
        sr = tk.Frame(sc_card, bg=C["card"]); sr.pack(fill="x", padx=16, pady=(0,8))
        tk.Label(sr,text="One sip =",font=(FONT,12),fg=C["text_b"],bg=C["card"]).pack(side="left",padx=(0,8))
        sip_ml_var = tk.StringVar(value=str(self.D.get("sip_ml",30)))
        tk.Entry(sr, textvariable=sip_ml_var, font=(FONT,13), fg=C["text_b"],
                 bg=C["card2"], relief="flat", width=6,
                 insertbackground=C["primary"]).pack(side="left", ipady=7, padx=(0,6))
        tk.Label(sr,text="ml",font=(FONT,12),fg=C["text_b"],bg=C["card"]).pack(side="left")
        sp = pill_row(sc_card)
        for lbl,val in [("15 ml",15),("20 ml",20),("30 ml",30),("50 ml",50)]:
            p = tk.Label(sp, text=lbl, font=(FONT,11,"bold"), fg=C["sip"],
                         bg=C["sip_lt"], padx=7, pady=4, cursor="hand2")
            p.pack(side="left", padx=2)
            p.bind("<Button-1>", lambda e,v=val: sip_ml_var.set(str(v)))

        # ── Bottle ───────────────────────────────────────────────────────────
        bc_card  = card("🍶  My Bottle")
        bh_row   = tk.Frame(bc_card, bg=C["card"]); bh_row.pack(fill="x", padx=16, pady=(0,8))
        bottle_on = tk.BooleanVar(value=self.D.get("bottle_enabled",False))
        tk.Checkbutton(bh_row, text="Enable bottle logging", variable=bottle_on,
                       font=(FONT,11), fg=C["text_b"], bg=C["card"],
                       activebackground=C["card"], selectcolor=C["card"],
                       cursor="hand2").pack(side="left")
        br = tk.Frame(bc_card, bg=C["card"]); br.pack(fill="x", padx=16, pady=(0,8))
        tk.Label(br,text="Bottle =",font=(FONT,12),fg=C["text_b"],bg=C["card"]).pack(side="left",padx=(0,8))
        bottle_ml_var = tk.StringVar(value=str(self.D.get("bottle_ml",500)))
        tk.Entry(br, textvariable=bottle_ml_var, font=(FONT,13), fg=C["text_b"],
                 bg=C["card2"], relief="flat", width=6,
                 insertbackground=C["primary"]).pack(side="left", ipady=7, padx=(0,6))
        tk.Label(br,text="ml",font=(FONT,12),fg=C["text_b"],bg=C["card"]).pack(side="left")
        bp = pill_row(bc_card)
        for lbl,val in [("350 ml",350),("500 ml",500),("750 ml",750),("1 L",1000)]:
            p = tk.Label(bp, text=lbl, font=(FONT,11,"bold"), fg=C["teal"],
                         bg=C["teal_lt"], padx=7, pady=4, cursor="hand2")
            p.pack(side="left", padx=3)
            p.bind("<Button-1>", lambda e,v=val: bottle_ml_var.set(str(v)))

        # ── Save ─────────────────────────────────────────────────────────────
        def save():
            try:
                self.D["goal_ml"] = to_ml(float(goal_var.get()), g_unit.get())
                if self.D["goal_ml"] <= 0: raise ValueError
            except ValueError:
                messagebox.showerror("Invalid","Enter a positive goal.",parent=w); return
            try:
                sml = int(sip_ml_var.get())
                if sml <= 0: raise ValueError
                self.D["sip_ml"] = sml
            except ValueError:
                messagebox.showerror("Invalid","Enter a valid sip size.",parent=w); return
            try:
                bml = int(bottle_ml_var.get())
                if bml <= 0: raise ValueError
                self.D["bottle_ml"] = bml
            except ValueError:
                messagebox.showerror("Invalid","Enter a valid bottle size.",parent=w); return

            new_iv = int_var.get()
            self.D["reminder_min"]    = new_iv
            self.D["sip_enabled"]     = sip_on.get()
            self.D["bottle_enabled"]  = bottle_on.get()
            save_data(self.D)
            if self.D.get("reminder_enabled"):
                enable_reminders(new_iv)
                self.D["reminder_enabled_at"] = time.time()
                save_data(self.D)
            self._refresh(); w.destroy()

        self._mk_btn(w,"Save Settings",save,C["primary"],"white",14).pack(
            fill="x", padx=20, pady=(14,6), ipady=8)
        cl = tk.Label(w,text="Cancel",font=(FONT,12),fg=C["text_s"],bg=C["bg"],cursor="hand2")
        cl.pack(pady=(0,14)); cl.bind("<Button-1>",lambda _: w.destroy())


# ── Entry ─────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    style = ttk.Style()
    try: style.theme_use("clam")
    except tk.TclError: pass
    style.configure("TCombobox", fieldbackground=C["card2"], background=C["card2"],
                    foreground=C["text_b"], selectbackground=C["pri_lt"],
                    selectforeground=C["text_h"], relief="flat", borderwidth=0)
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
