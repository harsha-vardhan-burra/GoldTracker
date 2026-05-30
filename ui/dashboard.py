import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.startup_manager import is_startup_enabled, sync_startup_setting
import customtkinter as ctk
import threading
import datetime
from database.db_manager import (
    get_latest_price, get_price_history,
    get_active_alerts, add_alert, trigger_alert,
    get_setting, update_setting
)
from core.analytics import get_buy_label, get_sell_label
from core.scheduler import GoldScheduler

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')

# ─── COLORS ──────────────────────────────────────────────────────────────────
GOLD      = '#FFD700'
GOLD_DARK = '#B8860B'
GREEN     = '#00C853'
ORANGE    = '#FF6D00'
RED       = '#D50000'
BLUE      = '#1E88E5'
BG        = '#141414'
SIDEBAR   = '#1a1a1a'
CARD      = '#242424'
CARD2     = '#2c2c2c'
TEXT      = '#FFFFFF'
SUBTEXT   = '#AAAAAA'
BORDER    = '#333333'


def get_score_color(score):
    if score >= 75: return GREEN
    elif score >= 55: return '#8BC34A'
    elif score >= 35: return ORANGE
    else: return RED


def format_inr(value):
    if value is None: return '---'
    return f"₹{value:,.2f}"


def format_inr_short(value):
    if value is None: return '---'
    return f"₹{value:,.0f}"


# ─── MAIN DASHBOARD ──────────────────────────────────────────────────────────
class Dashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title('GoldTracker — Dashboard')
        self.geometry('1100x680')
        self.minsize(900, 600)
        self.configure(fg_color=BG)

        # Center on screen
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f'1100x680+{(sw-1100)//2}+{(sh-680)//2}')

        self.current_data  = {}
        self.active_tab    = 'dashboard'
        self.scheduler     = None
        self.content_frame = None

        self._build_layout()
        self._load_initial_data()
        self._start_scheduler()

    def _toggle_startup(self):
        enabled = self.startup_var.get() == 'on'
        sync_startup_setting(enabled)
        update_setting('startup_enabled', 'true' if enabled else 'false')

    # ─── LAYOUT ──────────────────────────────────────────────────────────────
    def _build_layout(self):
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, fg_color=SIDEBAR, corner_radius=0, width=180)
        self.sidebar.pack(side='left', fill='y')
        self.sidebar.pack_propagate(False)

        # Main area
        self.main_area = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.main_area.pack(side='left', fill='both', expand=True)

        self._build_sidebar()
        self._show_tab('dashboard')


    # ─── SIDEBAR ─────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        # Logo
        ctk.CTkLabel(
            self.sidebar,
            text='⚡ GOLD\nTRACKER',
            font=ctk.CTkFont(size=18, weight='bold'),
            text_color=GOLD,
            justify='center'
        ).pack(pady=(28, 30))

        self.tab_buttons = {}
        tabs = [
            ('dashboard',      '📊  Dashboard'),
            ('charts',         '📈  Charts'),
            ('alerts',         '🔔  Alerts'),
            ('alert_history',  '📋  Alert History'),
            ('portfolio',      '💰  Portfolio'),
            ('history',        '🕐  History'),
            ('settings',       '⚙️   Settings'),
        ]

        for key, label in tabs:
            btn = ctk.CTkButton(
                self.sidebar,
                text=label,
                anchor='w',
                fg_color='transparent',
                hover_color=CARD2,
                text_color=SUBTEXT,
                font=ctk.CTkFont(size=13),
                height=42,
                corner_radius=8,
                command=lambda k=key: self._show_tab(k)
            )
            btn.pack(fill='x', padx=12, pady=3)
            self.tab_buttons[key] = btn

        # Version at bottom
        ctk.CTkLabel(
            self.sidebar,
            text='v1.0.0',
            font=ctk.CTkFont(size=10),
            text_color='#444444'
        ).pack(side='bottom', pady=16)

        # Refresh button at bottom
        ctk.CTkButton(
            self.sidebar,
            text='↻  Refresh',
            fg_color=GOLD_DARK,
            hover_color=GOLD,
            text_color='black',
            font=ctk.CTkFont(weight='bold'),
            height=38,
            command=self._manual_refresh
        ).pack(side='bottom', fill='x', padx=12, pady=(0, 8))


    def _show_tab(self, tab_key):
    # Highlight active tab
        for key, btn in self.tab_buttons.items():
            if key == tab_key:
                btn.configure(fg_color=CARD2, text_color=GOLD)
            else:
                btn.configure(fg_color='transparent', text_color=SUBTEXT)

        self.active_tab = tab_key

        # Clear content
        if self.content_frame:
            if hasattr(self.content_frame, '_container'):
                self.content_frame._container.destroy()
            else:
                self.content_frame.destroy()

        # Use canvas + scrollbar instead of CTkScrollableFrame
        import tkinter as tk

        container = ctk.CTkFrame(self.main_area, fg_color=BG, corner_radius=0)
        container.pack(fill='both', expand=True)

        canvas = tk.Canvas(container, bg='#141414', highlightthickness=0)
        scrollbar = ctk.CTkScrollbar(container, command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)

        self.content_frame = ctk.CTkFrame(canvas, fg_color=BG, corner_radius=0)
        window_id = canvas.create_window((0, 0), window=self.content_frame, anchor='nw')

        def on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox('all'))

        def on_canvas_configure(e):
            canvas.itemconfig(window_id, width=e.width)

        def on_mousewheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')

        self.content_frame.bind('<Configure>', on_frame_configure)
        canvas.bind('<Configure>', on_canvas_configure)
        canvas.bind_all('<MouseWheel>', on_mousewheel)

        # Store container so we can destroy it next switch
        self.content_frame._container = container

        tabs = {
            'dashboard': self._build_dashboard_tab,
            'charts':    self._build_charts_tab,
            'alerts':    self._build_alerts_tab,
            'alert_history': self._build_alert_history_tab,
            'portfolio': self._build_portfolio_tab,
            'history':   self._build_history_tab,
            'settings':  self._build_settings_tab,
        }
        tabs[tab_key]()

    # =========================================================================
    # TAB 1 — DASHBOARD
    # =========================================================================
    def _build_dashboard_tab(self):
        p = self.content_frame

        # ── Page title ──
        ctk.CTkLabel(
            p, text='Dashboard',
            font=ctk.CTkFont(size=22, weight='bold'),
            text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(20, 4))

        self.dash_updated = ctk.CTkLabel(
            p, text='Loading...',
            font=ctk.CTkFont(size=11),
            text_color=SUBTEXT
        )
        self.dash_updated.pack(anchor='w', padx=24, pady=(0, 16))

        # ── Top cards ──
        cards_row = ctk.CTkFrame(p, fg_color='transparent')
        cards_row.pack(fill='x', padx=24, pady=(0, 12))
        for i in range(4):
            cards_row.columnconfigure(i, weight=1)

        self.card_24k   = self._make_card(cards_row, '24K GOLD',    '---', 'per gram', GOLD,      0)
        self.card_22k   = self._make_card(cards_row, '22K GOLD',    '---', 'per gram', GOLD_DARK, 1)
        city_short = (get_setting('city') or 'city').upper()[:3]
        self.card_retail = self._make_card(cards_row, f'RETAIL ({city_short})', '---', 'per gram', '#FF9800', 2)
        self.card_premium=self._make_card(cards_row, 'MKT PREMIUM', '---', 'over spot',RED,        3)

        # ── Buy / Sell meters ──
        meter_row = ctk.CTkFrame(p, fg_color='transparent')
        meter_row.pack(fill='x', padx=24, pady=(0, 12))
        meter_row.columnconfigure(0, weight=1)
        meter_row.columnconfigure(1, weight=1)

        # Buy meter
        buy_card = ctk.CTkFrame(meter_row, fg_color=CARD, corner_radius=12)
        buy_card.grid(row=0, column=0, padx=(0,6), sticky='ew', ipady=8)

        ctk.CTkLabel(buy_card, text='BUY SIGNAL',
            font=ctk.CTkFont(size=11, weight='bold'), text_color=SUBTEXT
        ).pack(pady=(14, 4))

        self.buy_signal_label = ctk.CTkLabel(buy_card, text='ANALYSING...',
            font=ctk.CTkFont(size=20, weight='bold'), text_color=ORANGE)
        self.buy_signal_label.pack()

        self.buy_score_bar = ctk.CTkProgressBar(buy_card, height=8, corner_radius=4)
        self.buy_score_bar.pack(fill='x', padx=20, pady=(8, 4))
        self.buy_score_bar.set(0.49)

        self.buy_score_text = ctk.CTkLabel(buy_card, text='Score: 49/100',
            font=ctk.CTkFont(size=11), text_color=SUBTEXT)
        self.buy_score_text.pack(pady=(0, 14))

        # Sell meter
        sell_card = ctk.CTkFrame(meter_row, fg_color=CARD, corner_radius=12)
        sell_card.grid(row=0, column=1, padx=(6,0), sticky='ew', ipady=8)

        ctk.CTkLabel(sell_card, text='SELL SIGNAL',
            font=ctk.CTkFont(size=11, weight='bold'), text_color=SUBTEXT
        ).pack(pady=(14, 4))

        self.sell_signal_label = ctk.CTkLabel(sell_card, text='ANALYSING...',
            font=ctk.CTkFont(size=20, weight='bold'), text_color=ORANGE)
        self.sell_signal_label.pack()

        self.sell_score_bar = ctk.CTkProgressBar(sell_card, height=8, corner_radius=4)
        self.sell_score_bar.pack(fill='x', padx=20, pady=(8, 4))
        self.sell_score_bar.set(0.51)

        self.sell_score_text = ctk.CTkLabel(sell_card, text='Score: 51/100',
            font=ctk.CTkFont(size=11), text_color=SUBTEXT)
        self.sell_score_text.pack(pady=(0, 14))

        # ── Explanation ──
        exp_frame = ctk.CTkFrame(p, fg_color=CARD, corner_radius=12)
        exp_frame.pack(fill='x', padx=24, pady=(0, 12))

        ctk.CTkLabel(exp_frame, text='ANALYSIS REASONING',
            font=ctk.CTkFont(size=11, weight='bold'), text_color=SUBTEXT
        ).pack(anchor='w', padx=16, pady=(12, 4))

        self.explanation_label = ctk.CTkLabel(exp_frame,
            text='Waiting for data...',
            font=ctk.CTkFont(size=12),
            text_color=TEXT,
            wraplength=800,
            justify='left'
        )
        self.explanation_label.pack(anchor='w', padx=16, pady=(0, 14))

        # ── Market Insights ──
        insights = ctk.CTkFrame(p, fg_color='transparent')
        insights.pack(fill='x', padx=24, pady=(0, 20))
        for i in range(3):
            insights.columnconfigure(i, weight=1)

        self.ins_spot   = self._make_insight(insights, 'SPOT (USD/oz)', '---', 0)
        self.ins_usd_inr= self._make_insight(insights, 'USD / INR',     '---', 1)
        self.ins_status = self._make_insight(insights, 'POLL STATUS',   'Active', 2)

        # Update display with current data
        if self.current_data:
            self._refresh_dashboard_display(self.current_data)


    def _make_card(self, parent, title, value, subtitle, color, col):
        frame = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12)
        frame.grid(row=0, column=col, padx=(0 if col==0 else 6, 6 if col<3 else 0), sticky='ew', ipady=6)

        ctk.CTkLabel(frame, text=title,
            font=ctk.CTkFont(size=10, weight='bold'), text_color=color
        ).pack(pady=(12, 2))

        val_label = ctk.CTkLabel(frame, text=value,
            font=ctk.CTkFont(size=18, weight='bold'), text_color=TEXT)
        val_label.pack()

        ctk.CTkLabel(frame, text=subtitle,
            font=ctk.CTkFont(size=10), text_color=SUBTEXT
        ).pack(pady=(2, 12))

        return val_label


    def _make_insight(self, parent, title, value, col):
        frame = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=10)
        frame.grid(row=0, column=col, padx=(0 if col==0 else 6, 6 if col<2 else 0), sticky='ew', ipady=4)

        ctk.CTkLabel(frame, text=title,
            font=ctk.CTkFont(size=10, weight='bold'), text_color=SUBTEXT
        ).pack(pady=(10, 2))

        val = ctk.CTkLabel(frame, text=value,
            font=ctk.CTkFont(size=14, weight='bold'), text_color=TEXT)
        val.pack(pady=(0, 10))

        return val


    def _refresh_dashboard_display(self, data):
        if not data: return

        p24     = data.get('price_24k')
        p22     = data.get('price_22k')
        retail  = data.get('retail_price')
        spot    = data.get('spot_usd')
        usd_inr = data.get('usd_inr')

        if hasattr(self, 'card_24k'):
            self.card_24k.configure(text=format_inr(p24))
        if hasattr(self, 'card_22k'):
            self.card_22k.configure(text=format_inr(p22))
        if hasattr(self, 'card_retail'):
            self.card_retail.configure(text=format_inr_short(retail))
        if hasattr(self, 'card_premium') and p24 and retail:
            premium = retail - p24
            self.card_premium.configure(text=f"₹{premium:,.0f}")

        buy_score  = data.get('buy_score') or 49
        sell_score = data.get('sell_score') or 51
        buy_label  = data.get('buy_label')  or get_buy_label(buy_score)
        sell_label = data.get('sell_label') or get_sell_label(sell_score)

        if hasattr(self, 'buy_signal_label'):
            self.buy_signal_label.configure(
                text=buy_label,
                text_color=get_score_color(buy_score)
            )
            self.buy_score_bar.set(buy_score / 100)
            self.buy_score_text.configure(text=f"Score: {buy_score}/100")

        if hasattr(self, 'sell_signal_label'):
            self.sell_signal_label.configure(
                text=sell_label,
                text_color=get_score_color(sell_score)
            )
            self.sell_score_bar.set(sell_score / 100)
            self.sell_score_text.configure(text=f"Score: {sell_score}/100")

        if hasattr(self, 'explanation_label'):
            self.explanation_label.configure(
                text=data.get('explanation') or 'Not enough data yet'
            )

        if hasattr(self, 'ins_spot') and spot:
            self.ins_spot.configure(text=f"${spot:,.2f}")
        if hasattr(self, 'ins_usd_inr') and usd_inr:
            self.ins_usd_inr.configure(text=f"₹{usd_inr:.2f}")

        now = datetime.datetime.now().strftime('%d %b %Y, %I:%M %p')
        if hasattr(self, 'dash_updated'):
            self.dash_updated.configure(text=f'Last updated: {now}')


    # =========================================================================
    # TAB 2 — CHARTS
    # =========================================================================
    def _build_charts_tab(self):
        p = self.content_frame

        ctk.CTkLabel(p, text='Price Charts',
            font=ctk.CTkFont(size=22, weight='bold'), text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(20, 16))

        # Time range toggle
        toggle_frame = ctk.CTkFrame(p, fg_color=CARD, corner_radius=10)
        toggle_frame.pack(padx=24, pady=(0, 16), anchor='w')

        self.chart_range = ctk.StringVar(value='24H')
        for label in ['24H', '7D', '30D']:
            ctk.CTkRadioButton(
                toggle_frame,
                text=label,
                variable=self.chart_range,
                value=label,
                command=self._render_chart,
                text_color=TEXT,
                fg_color=GOLD_DARK
            ).pack(side='left', padx=16, pady=10)

        # Chart canvas area
        self.chart_frame = ctk.CTkFrame(p, fg_color=CARD, corner_radius=12, height=320)
        self.chart_frame.pack(fill='x', padx=24, pady=(0, 20))
        self.chart_frame.pack_propagate(False)

        self._render_chart()


    def _render_chart(self):
        # Clear existing chart
        for widget in self.chart_frame.winfo_children():
            widget.destroy()

        range_map = {'24H': 1, '7D': 7, '30D': 30}
        days = range_map.get(self.chart_range.get(), 1)
        history = get_price_history(days=days)

        if len(history) < 2:
            ctk.CTkLabel(
                self.chart_frame,
                text='Not enough data yet.\nKeep the app running to build up history.',
                font=ctk.CTkFont(size=14),
                text_color=SUBTEXT,
                justify='center'
            ).place(relx=0.5, rely=0.5, anchor='center')
            return

        # Draw simple ASCII-style chart using canvas
        try:
            import tkinter as tk
            canvas = tk.Canvas(
                self.chart_frame,
                bg='#242424',
                highlightthickness=0,
                height=280
            )
            canvas.pack(fill='x', padx=16, pady=16)

            prices = [r['price_24k'] for r in history if r['price_24k']]
            if not prices:
                return

            W = 900
            H = 240
            pad_l, pad_r, pad_t, pad_b = 60, 20, 20, 40

            min_p = min(prices)
            max_p = max(prices)
            price_range = max_p - min_p or 1

            def x_pos(i):
                return pad_l + (i / (len(prices)-1)) * (W - pad_l - pad_r)

            def y_pos(price):
                return pad_t + (1 - (price - min_p) / price_range) * (H - pad_t - pad_b)

            # Grid lines
            for i in range(5):
                y = pad_t + i * (H - pad_t - pad_b) / 4
                price_at = max_p - i * price_range / 4
                canvas.create_line(pad_l, y, W-pad_r, y, fill='#333333', dash=(4,4))
                canvas.create_text(pad_l-4, y, text=f"₹{price_at:,.0f}",
                    anchor='e', fill='#888888', font=('Arial', 9))

            # Price line
            points = []
            for i, price in enumerate(prices):
                points.extend([x_pos(i), y_pos(price)])

            if len(points) >= 4:
                canvas.create_line(points, fill=GOLD, width=2, smooth=True)

            # Dots at first and last
            x0, y0 = x_pos(0), y_pos(prices[0])
            x1, y1 = x_pos(len(prices)-1), y_pos(prices[-1])
            canvas.create_oval(x0-4, y0-4, x0+4, y0+4, fill=GOLD, outline='')
            canvas.create_oval(x1-4, y1-4, x1+4, y1+4, fill=GREEN, outline='')

            # Labels
            canvas.create_text(x0, y0-12,
                text=f"₹{prices[0]:,.0f}", fill=SUBTEXT, font=('Arial', 9))
            canvas.create_text(x1, y1-12,
                text=f"₹{prices[-1]:,.0f}", fill=GREEN, font=('Arial', 9))

        except Exception as e:
            ctk.CTkLabel(self.chart_frame,
                text=f'Chart error: {e}',
                text_color=RED
            ).pack(pady=20)


    # =========================================================================
    # TAB 3 — ALERTS
    # =========================================================================
    def _build_alerts_tab(self):
        p = self.content_frame

        ctk.CTkLabel(p, text='Price Alerts',
            font=ctk.CTkFont(size=22, weight='bold'), text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(20, 16))

        # Add alert form
        form = ctk.CTkFrame(p, fg_color=CARD, corner_radius=12)
        form.pack(fill='x', padx=24, pady=(0, 16))

        ctk.CTkLabel(form, text='SET NEW ALERT',
            font=ctk.CTkFont(size=11, weight='bold'), text_color=SUBTEXT
        ).pack(anchor='w', padx=16, pady=(14, 8))

        row = ctk.CTkFrame(form, fg_color='transparent')
        row.pack(fill='x', padx=16, pady=(0, 14))

        self.alert_type = ctk.StringVar(value='buy')
        ctk.CTkRadioButton(row, text='Buy alert (price drops to)',
            variable=self.alert_type, value='buy',
            text_color=TEXT, fg_color=GREEN
        ).pack(side='left', padx=(0, 20))
        ctk.CTkRadioButton(row, text='Sell alert (price rises to)',
            variable=self.alert_type, value='sell',
            text_color=TEXT, fg_color=RED
        ).pack(side='left')

        input_row = ctk.CTkFrame(form, fg_color='transparent')
        input_row.pack(fill='x', padx=16, pady=(0, 14))

        ctk.CTkLabel(input_row, text='Target Price (₹/gram):',
            font=ctk.CTkFont(size=12), text_color=TEXT
        ).pack(side='left', padx=(0, 12))

        self.alert_price_entry = ctk.CTkEntry(
            input_row, placeholder_text='e.g. 13000',
            width=160, height=36
        )
        self.alert_price_entry.pack(side='left', padx=(0, 12))

        ctk.CTkButton(
            input_row,
            text='Add Alert',
            fg_color=GOLD_DARK,
            hover_color=GOLD,
            text_color='black',
            width=120, height=36,
            font=ctk.CTkFont(weight='bold'),
            command=self._add_alert
        ).pack(side='left')

        self.alert_msg = ctk.CTkLabel(form, text='',
            font=ctk.CTkFont(size=11), text_color=GREEN)
        self.alert_msg.pack(pady=(0, 8))

        # Active alerts list
        ctk.CTkLabel(p, text='Active Alerts',
            font=ctk.CTkFont(size=15, weight='bold'), text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(8, 8))

        self.alerts_list_frame = ctk.CTkFrame(p, fg_color='transparent')
        self.alerts_list_frame.pack(fill='x', padx=24)
        self._render_alerts_list()


    def _add_alert(self):
        try:
            price = float(self.alert_price_entry.get().replace(',', '').replace('₹', ''))
            alert_type = self.alert_type.get()
            add_alert(alert_type, price)
            self.alert_price_entry.delete(0, 'end')
            self.alert_msg.configure(
                text=f"✓ Alert set: notify when price {'drops to' if alert_type=='buy' else 'rises to'} ₹{price:,.0f}",
                text_color=GREEN
            )
            self._render_alerts_list()
        except ValueError:
            self.alert_msg.configure(text='Please enter a valid price', text_color=RED)


    def _render_alerts_list(self):
        for w in self.alerts_list_frame.winfo_children():
            w.destroy()

        alerts = get_active_alerts()

        if not alerts:
            ctk.CTkLabel(
                self.alerts_list_frame,
                text='No active alerts. Add one above.',
                font=ctk.CTkFont(size=12),
                text_color=SUBTEXT
            ).pack(pady=12)
            return

        for alert in alerts:
            row = ctk.CTkFrame(self.alerts_list_frame, fg_color=CARD, corner_radius=10)
            row.pack(fill='x', pady=4, ipady=4)

            icon  = '🟢' if alert['type'] == 'buy' else '🔴'
            label = f"{icon}  {'Buy' if alert['type']=='buy' else 'Sell'} alert — ₹{alert['target_price']:,.0f}/gram"

            ctk.CTkLabel(row, text=label,
                font=ctk.CTkFont(size=13), text_color=TEXT
            ).pack(side='left', padx=16, pady=8)

            ctk.CTkLabel(row,
                text=f"Added: {alert['created_at'][:16]}",
                font=ctk.CTkFont(size=10), text_color=SUBTEXT
            ).pack(side='left', padx=8)

    # =========================================================================
    # TAB 3.5 — PORTFOLIO
    # =========================================================================
    def _build_portfolio_tab(self):
        p = self.content_frame

        ctk.CTkLabel(p, text='Portfolio Tracker',
            font=ctk.CTkFont(size=22, weight='bold'), text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(20, 4))

        ctk.CTkLabel(p, text='Track your gold purchases and monitor real-time P&L',
            font=ctk.CTkFont(size=11), text_color=SUBTEXT
        ).pack(anchor='w', padx=24, pady=(0, 16))

        # ── Summary Cards ──
        summary_row = ctk.CTkFrame(p, fg_color='transparent')
        summary_row.pack(fill='x', padx=24, pady=(0, 12))
        for i in range(4):
            summary_row.columnconfigure(i, weight=1)

        self.port_grams    = self._make_card(summary_row, 'TOTAL GRAMS',    '---', 'grams owned',    GOLD,      0)
        self.port_invested = self._make_card(summary_row, 'TOTAL INVESTED', '---', 'amount spent',   '#FF9800',  1)
        self.port_value    = self._make_card(summary_row, 'CURRENT VALUE',  '---', 'at market price', BLUE,      2)
        self.port_pnl      = self._make_card(summary_row, 'PROFIT / LOSS',  '---', 'unrealised',      GREEN,     3)

        # ── Add Purchase Form ──
        form = ctk.CTkFrame(p, fg_color=CARD, corner_radius=12)
        form.pack(fill='x', padx=24, pady=(0, 12))

        ctk.CTkLabel(form, text='ADD PURCHASE',
            font=ctk.CTkFont(size=11, weight='bold'), text_color=SUBTEXT
        ).pack(anchor='w', padx=16, pady=(14, 10))

        # Row 1 — date, karat, grams
        row1 = ctk.CTkFrame(form, fg_color='transparent')
        row1.pack(fill='x', padx=16, pady=(0, 10))

        ctk.CTkLabel(row1, text='Date:',
            font=ctk.CTkFont(size=12), text_color=TEXT, width=50
        ).pack(side='left', padx=(0, 6))

        self.port_date = ctk.CTkEntry(row1,
            placeholder_text='YYYY-MM-DD',
            width=130, height=36
        )
        # Auto-fill today's date
        import datetime
        self.port_date.insert(0, datetime.date.today().strftime('%Y-%m-%d'))
        self.port_date.pack(side='left', padx=(0, 16))

        ctk.CTkLabel(row1, text='Karat:',
            font=ctk.CTkFont(size=12), text_color=TEXT, width=50
        ).pack(side='left', padx=(0, 6))

        self.port_karat = ctk.CTkOptionMenu(
            row1,
            values=['24K', '22K', '18K'],
            fg_color=CARD2, button_color=GOLD_DARK,
            width=90, height=36
        )
        self.port_karat.pack(side='left', padx=(0, 16))

        ctk.CTkLabel(row1, text='Grams:',
            font=ctk.CTkFont(size=12), text_color=TEXT, width=55
        ).pack(side='left', padx=(0, 6))

        self.port_grams_entry = ctk.CTkEntry(row1,
            placeholder_text='e.g. 10',
            width=100, height=36
        )
        self.port_grams_entry.pack(side='left')

        # Row 2 — price, notes
        row2 = ctk.CTkFrame(form, fg_color='transparent')
        row2.pack(fill='x', padx=16, pady=(0, 10))

        ctk.CTkLabel(row2, text='Price/gram (₹):',
            font=ctk.CTkFont(size=12), text_color=TEXT, width=110
        ).pack(side='left', padx=(0, 6))

        self.port_price_entry = ctk.CTkEntry(row2,
            placeholder_text='e.g. 9250',
            width=130, height=36
        )
        self.port_price_entry.pack(side='left', padx=(0, 16))

        ctk.CTkLabel(row2, text='Notes:',
            font=ctk.CTkFont(size=12), text_color=TEXT, width=50
        ).pack(side='left', padx=(0, 6))

        self.port_notes_entry = ctk.CTkEntry(row2,
            placeholder_text='optional (e.g. wedding purchase)',
            width=220, height=36
        )
        self.port_notes_entry.pack(side='left')

        # Add button + message
        btn_row = ctk.CTkFrame(form, fg_color='transparent')
        btn_row.pack(fill='x', padx=16, pady=(0, 14))

        ctk.CTkButton(
            btn_row,
            text='Add Purchase',
            fg_color=GOLD_DARK, hover_color=GOLD,
            text_color='black',
            font=ctk.CTkFont(weight='bold'),
            width=140, height=36,
            command=self._add_purchase
        ).pack(side='left')

        self.port_msg = ctk.CTkLabel(btn_row, text='',
            font=ctk.CTkFont(size=11), text_color=GREEN)
        self.port_msg.pack(side='left', padx=16)

        # ── Purchase History ──
        ctk.CTkLabel(p, text='Purchase History',
            font=ctk.CTkFont(size=15, weight='bold'), text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(8, 8))

        # Table header
        header = ctk.CTkFrame(p, fg_color=CARD2, corner_radius=8)
        header.pack(fill='x', padx=24, pady=(0, 4))

        cols   = ['Date', 'Karat', 'Grams', 'Buy Price', 'Invested', 'Current Val', 'P&L', '']
        widths = [100,     60,      70,       100,         110,        110,           100,    50]

        for col, w in zip(cols, widths):
            ctk.CTkLabel(header, text=col,
                font=ctk.CTkFont(size=11, weight='bold'),
                text_color=SUBTEXT, width=w, anchor='w'
            ).pack(side='left', padx=6, pady=8)

        # Rows
        self.port_list_frame = ctk.CTkFrame(p, fg_color='transparent')
        self.port_list_frame.pack(fill='x', padx=24)
        self._render_portfolio_rows()

        # Update summary
        self._update_portfolio_summary()

    # =========================================================================
    # TAB — ALERT HISTORY
    # =========================================================================
    def _build_alert_history_tab(self):
        p = self.content_frame

        ctk.CTkLabel(p, text='Alert History',
            font=ctk.CTkFont(size=22, weight='bold'), text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(20, 4))

        ctk.CTkLabel(p,
            text='All active, triggered and cancelled alerts',
            font=ctk.CTkFont(size=11), text_color=SUBTEXT
        ).pack(anchor='w', padx=24, pady=(0, 16))

        # ── Summary cards ──
        summary_row = ctk.CTkFrame(p, fg_color='transparent')
        summary_row.pack(fill='x', padx=24, pady=(0, 16))
        for i in range(3):
            summary_row.columnconfigure(i, weight=1)

        from database.db_manager import get_all_alerts
        all_alerts  = get_all_alerts()
        active      = [a for a in all_alerts if a['status'] == 'active']
        triggered   = [a for a in all_alerts if a['status'] == 'triggered']
        cancelled   = [a for a in all_alerts if a['status'] == 'cancelled']

        self._make_card(summary_row, 'ACTIVE',    str(len(active)),    'monitoring',      GREEN,   0)
        self._make_card(summary_row, 'TRIGGERED', str(len(triggered)), 'alerts fired',    GOLD,    1)
        self._make_card(summary_row, 'CANCELLED', str(len(cancelled)), 'dismissed',       SUBTEXT, 2)

        # ── Filter toggle ──
        filter_frame = ctk.CTkFrame(p, fg_color=CARD, corner_radius=10)
        filter_frame.pack(padx=24, pady=(0, 16), anchor='w')

        self.alert_filter = ctk.StringVar(value='all')
        for label, val in [('All', 'all'), ('Active', 'active'),
                           ('Triggered', 'triggered'), ('Cancelled', 'cancelled')]:
            ctk.CTkRadioButton(
                filter_frame,
                text=label,
                variable=self.alert_filter,
                value=val,
                command=lambda: self._render_alert_history(),
                text_color=TEXT,
                fg_color=GOLD_DARK
            ).pack(side='left', padx=16, pady=10)

        # ── Table header ──
        header = ctk.CTkFrame(p, fg_color=CARD2, corner_radius=8)
        header.pack(fill='x', padx=24, pady=(0, 4))

        cols   = ['Type', 'Target Price', 'Status', 'Created', 'Triggered At', '']
        widths = [80,      120,            100,       160,       160,            60]

        for col, w in zip(cols, widths):
            ctk.CTkLabel(header, text=col,
                font=ctk.CTkFont(size=11, weight='bold'),
                text_color=SUBTEXT, width=w, anchor='w'
            ).pack(side='left', padx=8, pady=8)

        # ── Alert rows ──
        self.alert_history_frame = ctk.CTkFrame(p, fg_color='transparent')
        self.alert_history_frame.pack(fill='x', padx=24)
        self._render_alert_history()


    def _render_alert_history(self):
        for w in self.alert_history_frame.winfo_children():
            w.destroy()

        from database.db_manager import get_all_alerts, cancel_alert

        all_alerts  = get_all_alerts()
        filter_val  = self.alert_filter.get() if hasattr(self, 'alert_filter') else 'all'

        if filter_val != 'all':
            all_alerts = [a for a in all_alerts if a['status'] == filter_val]

        if not all_alerts:
            ctk.CTkLabel(self.alert_history_frame,
                text='No alerts found.',
                font=ctk.CTkFont(size=12), text_color=SUBTEXT
            ).pack(pady=16)
            return

        for alert in all_alerts:
            status      = alert['status']
            alert_type  = alert['type']
            target      = alert['target_price']
            created     = str(alert['created_at'])[:16]
            triggered   = str(alert['triggered_at'])[:16] if alert['triggered_at'] else '---'

            # Colors
            type_color = GREEN if alert_type == 'buy' else RED
            status_color = {
                'active':    GREEN,
                'triggered': GOLD,
                'cancelled': SUBTEXT
            }.get(status, TEXT)

            row = ctk.CTkFrame(
                self.alert_history_frame,
                fg_color=CARD, corner_radius=8
            )
            row.pack(fill='x', pady=3)

            # Type
            ctk.CTkLabel(row,
                text=f"{'🟢' if alert_type=='buy' else '🔴'} {alert_type.upper()}",
                font=ctk.CTkFont(size=11, weight='bold'),
                text_color=type_color, width=80, anchor='w'
            ).pack(side='left', padx=8, pady=10)

            # Target price
            ctk.CTkLabel(row,
                text=f"₹{target:,.0f}/gram",
                font=ctk.CTkFont(size=11),
                text_color=TEXT, width=120, anchor='w'
            ).pack(side='left', padx=8)

            # Status badge
            ctk.CTkLabel(row,
                text=status.upper(),
                font=ctk.CTkFont(size=10, weight='bold'),
                text_color=status_color, width=100, anchor='w'
            ).pack(side='left', padx=8)

            # Created
            ctk.CTkLabel(row,
                text=created,
                font=ctk.CTkFont(size=10),
                text_color=SUBTEXT, width=160, anchor='w'
            ).pack(side='left', padx=8)

            # Triggered at
            ctk.CTkLabel(row,
                text=triggered,
                font=ctk.CTkFont(size=10),
                text_color=GOLD if triggered != '---' else SUBTEXT,
                width=160, anchor='w'
            ).pack(side='left', padx=8)

            # Cancel button (only for active alerts)
            if status == 'active':
                aid = alert['id']
                ctk.CTkButton(row,
                    text='Cancel',
                    width=60, height=28,
                    fg_color='#333333',
                    hover_color='#cc0000',
                    text_color=SUBTEXT,
                    font=ctk.CTkFont(size=10),
                    command=lambda i=aid: self._cancel_alert(i)
                ).pack(side='left', padx=6)

    def _cancel_alert(self, alert_id):
        from database.db_manager import cancel_alert
        cancel_alert(alert_id)
        self._render_alert_history()

    def _add_purchase(self):
        try:
            from database.db_manager import add_purchase
            import datetime

            date_str = self.port_date.get().strip()
            karat    = self.port_karat.get()
            grams    = float(self.port_grams_entry.get().strip())
            price    = float(self.port_price_entry.get().strip().replace(',','').replace('₹',''))
            notes    = self.port_notes_entry.get().strip()

            # Validate date
            datetime.date.fromisoformat(date_str)

            add_purchase(date_str, karat, grams, price, notes)

            # Clear entries
            self.port_grams_entry.delete(0, 'end')
            self.port_price_entry.delete(0, 'end')
            self.port_notes_entry.delete(0, 'end')

            self.port_msg.configure(
                text=f"✓ Added {grams}g of {karat} at ₹{price:,.0f}/gram",
                text_color=GREEN
            )
            self._render_portfolio_rows()
            self._update_portfolio_summary()

        except ValueError as e:
            self.port_msg.configure(
                text=f'Invalid input — check date format and numbers',
                text_color=RED
            )


    def _render_portfolio_rows(self):
        for w in self.port_list_frame.winfo_children():
            w.destroy()

        from database.db_manager import get_portfolio, delete_purchase

        purchases = get_portfolio()

        if not purchases:
            ctk.CTkLabel(self.port_list_frame,
                text='No purchases yet. Add your first purchase above.',
                font=ctk.CTkFont(size=12), text_color=SUBTEXT
            ).pack(pady=16)
            return

        current_price = self.current_data.get('price_24k') or 0

        for p in purchases:
            karat        = p['karat']
            grams        = p['grams']
            buy_price    = p['price_per_gram']
            invested     = p['total_invested']

            # Calculate current value based on karat
            if karat == '24K':
                curr_price = current_price
            elif karat == '22K':
                curr_price = current_price * (22/24)
            else:
                curr_price = current_price * (18/24)

            current_val = round(grams * curr_price, 2) if curr_price else 0
            pnl         = round(current_val - invested, 2) if curr_price else 0
            pnl_pct     = round((pnl / invested) * 100, 2) if invested else 0
            pnl_color   = GREEN if pnl >= 0 else RED

            row = ctk.CTkFrame(self.port_list_frame, fg_color=CARD, corner_radius=8)
            row.pack(fill='x', pady=3)

            values = [
                p['purchase_date'],
                karat,
                f"{grams}g",
                f"₹{buy_price:,.0f}",
                f"₹{invested:,.0f}",
                f"₹{current_val:,.0f}" if curr_price else '---',
                f"{'+'if pnl>=0 else ''}₹{pnl:,.0f} ({pnl_pct:+.1f}%)" if curr_price else '---',
            ]
            colors = [SUBTEXT, GOLD, TEXT, TEXT, TEXT, BLUE, pnl_color]
            widths = [100, 60, 70, 100, 110, 110, 100]

            for val, color, w in zip(values, colors, widths):
                ctk.CTkLabel(row, text=val,
                    font=ctk.CTkFont(size=11),
                    text_color=color, width=w, anchor='w'
                ).pack(side='left', padx=6, pady=8)

            # Delete button
            pid = p['id']
            ctk.CTkButton(row,
                text='✕', width=40, height=28,
                fg_color='transparent',
                hover_color='#cc0000',
                text_color=SUBTEXT,
                command=lambda i=pid: self._delete_purchase(i)
            ).pack(side='left', padx=4)


    def _delete_purchase(self, purchase_id):
        from database.db_manager import delete_purchase
        delete_purchase(purchase_id)
        self._render_portfolio_rows()
        self._update_portfolio_summary()


    def _update_portfolio_summary(self):
        from database.db_manager import get_portfolio_summary, get_portfolio

        summary = get_portfolio_summary()
        if not summary or not summary['total_grams']:
            return

        total_grams    = summary['total_grams'] or 0
        total_invested = summary['total_invested'] or 0
        current_price  = self.current_data.get('price_24k') or 0
        current_value  = round(total_grams * current_price, 2) if current_price else 0
        pnl            = round(current_value - total_invested, 2) if current_price else 0
        pnl_color      = GREEN if pnl >= 0 else RED

        if hasattr(self, 'port_grams'):
            self.port_grams.configure(text=f"{total_grams:.2f}g")
        if hasattr(self, 'port_invested'):
            self.port_invested.configure(text=f"₹{total_invested:,.0f}")
        if hasattr(self, 'port_value'):
            self.port_value.configure(
                text=f"₹{current_value:,.0f}" if current_price else '---'
            )
        if hasattr(self, 'port_pnl'):
            self.port_pnl.configure(
                text=f"{'+'if pnl>=0 else ''}₹{pnl:,.0f}" if current_price else '---',
                text_color=pnl_color
            )

    # =========================================================================
    # TAB 4 — HISTORY
    # =========================================================================
    def _build_history_tab(self):
        p = self.content_frame

        ctk.CTkLabel(p, text='Price History',
            font=ctk.CTkFont(size=22, weight='bold'), text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(20, 16))

        history = get_price_history(days=7)

        if not history:
            ctk.CTkLabel(p,
                text='No history yet. Keep the app running to build up data.',
                font=ctk.CTkFont(size=13), text_color=SUBTEXT
            ).pack(pady=40)
            return

        # Table header
        header = ctk.CTkFrame(p, fg_color=CARD2, corner_radius=8)
        header.pack(fill='x', padx=24, pady=(0, 4))

        cols = ['Timestamp', '24K (calc)', '22K (calc)', 'Retail', 'Buy Score', 'Signal']
        widths = [180, 120, 120, 120, 100, 150]
        for col, w in zip(cols, widths):
            ctk.CTkLabel(header, text=col,
                font=ctk.CTkFont(size=11, weight='bold'),
                text_color=SUBTEXT, width=w, anchor='w'
            ).pack(side='left', padx=8, pady=8)

        # Rows (most recent first)
        for row_data in reversed(history[-50:]):
            row = ctk.CTkFrame(p, fg_color=CARD, corner_radius=8)
            row.pack(fill='x', padx=24, pady=2)

            ts    = str(row_data.get('timestamp', ''))[:16]
            p24   = format_inr(row_data.get('price_24k'))
            p22   = format_inr(row_data.get('price_22k'))
            ret   = format_inr_short(row_data.get('retail_price'))
            score = row_data.get('buy_score') or 0
            sig   = get_buy_label(score)

            values = [ts, p24, p22, ret, f"{score}/100", sig]
            colors = [SUBTEXT, TEXT, TEXT, GOLD, SUBTEXT, get_score_color(score)]

            for val, color, w in zip(values, colors, widths):
                ctk.CTkLabel(row, text=val,
                    font=ctk.CTkFont(size=11),
                    text_color=color, width=w, anchor='w'
                ).pack(side='left', padx=8, pady=7)


    # =========================================================================
    # TAB 5 — SETTINGS
    # =========================================================================
    def _build_settings_tab(self):
        p = self.content_frame

        ctk.CTkLabel(p, text='Settings',
            font=ctk.CTkFont(size=22, weight='bold'), text_color=TEXT
        ).pack(anchor='w', padx=24, pady=(20, 16))

        # ── Data Settings ──
        self._settings_section(p, 'DATA SETTINGS')

        city_row = self._settings_row(p, 'City', 'Gold rate city for retail pricing')
        self.city_var = ctk.StringVar(value=get_setting('city') or 'vijayawada')
        city_menu = ctk.CTkOptionMenu(
            city_row,
            values=['vijayawada', 'hyderabad', 'chennai', 'mumbai',
                    'delhi', 'bangalore', 'kolkata', 'pune'],
            variable=self.city_var,
            fg_color=CARD2, button_color=GOLD_DARK,
            width=180
        )
        city_menu.pack(side='right')

        poll_row = self._settings_row(p, 'Polling Interval', 'How often to fetch new price')
        self.poll_var = ctk.StringVar(value=get_setting('polling_interval') or '5')
        poll_menu = ctk.CTkOptionMenu(
            poll_row,
            values=['1', '2', '5', '10', '15', '30'],
            variable=self.poll_var,
            fg_color=CARD2, button_color=GOLD_DARK,
            width=180
        )
        poll_menu.pack(side='right')

        # ── Appearance ──
        self._settings_section(p, 'APPEARANCE')

        theme_row = self._settings_row(p, 'Theme', 'App color theme')
        self.theme_var = ctk.StringVar(value=get_setting('theme') or 'dark')
        theme_menu = ctk.CTkOptionMenu(
            theme_row,
            values=['dark', 'light'],
            variable=self.theme_var,
            fg_color=CARD2, button_color=GOLD_DARK,
            width=180
        )
        theme_menu.pack(side='right')

        # ── Save button ──
        ctk.CTkButton(
            p,
            text='Save Settings',
            fg_color=GOLD_DARK,
            hover_color=GOLD,
            text_color='black',
            font=ctk.CTkFont(size=13, weight='bold'),
            height=42,
            command=self._save_settings
        ).pack(padx=24, pady=24, anchor='w')

        self.settings_msg = ctk.CTkLabel(p, text='',
            font=ctk.CTkFont(size=12), text_color=GREEN)
        self.settings_msg.pack(anchor='w', padx=24)

        # ── Startup ──
        self._settings_section(p, 'SYSTEM')

        startup_row = self._settings_row(p, 'Launch on Startup', 'Open GoldTracker when Windows starts')
        self.startup_var = ctk.StringVar(
            value='on' if is_startup_enabled() else 'off'
        )
        ctk.CTkSwitch(
            startup_row,
            text='',
            variable=self.startup_var,
            onvalue='on',
            offvalue='off',
            command=self._toggle_startup,
            progress_color=GOLD_DARK
        ).pack(side='right', padx=16)

    def _settings_section(self, parent, title):
        ctk.CTkLabel(parent, text=title,
            font=ctk.CTkFont(size=11, weight='bold'), text_color=GOLD
        ).pack(anchor='w', padx=24, pady=(16, 4))


    def _settings_row(self, parent, title, subtitle):
        row = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=10)
        row.pack(fill='x', padx=24, pady=4, ipady=4)

        ctk.CTkLabel(row, text=title,
            font=ctk.CTkFont(size=13, weight='bold'), text_color=TEXT
        ).pack(side='left', padx=16, pady=12)

        ctk.CTkLabel(row, text=subtitle,
            font=ctk.CTkFont(size=11), text_color=SUBTEXT
        ).pack(side='left', padx=4)

        return row


    def _save_settings(self):
        update_setting('city', self.city_var.get())
        update_setting('polling_interval', self.poll_var.get())
        update_setting('theme', self.theme_var.get())
        ctk.set_appearance_mode(self.theme_var.get())
        self.settings_msg.configure(text='✓ Settings saved successfully')


    # =========================================================================
    # SCHEDULER + DATA FLOW
    # =========================================================================
    def _load_initial_data(self):
        latest = get_latest_price()
        if latest:
            latest['buy_label']  = get_buy_label(latest.get('buy_score') or 49)
            latest['sell_label'] = get_sell_label(latest.get('sell_score') or 51)
            self.current_data = latest
            self._refresh_dashboard_display(latest)


    def _start_scheduler(self):
        self.scheduler = GoldScheduler(on_update=self._on_new_data)
        self.scheduler.start()
        self.after(800, self._fetch_in_background)


    def _fetch_in_background(self):
        t = threading.Thread(target=self._do_fetch, daemon=True)
        t.start()


    def _do_fetch(self):
        result = self.scheduler.run_now()
        if result:
            self.after(0, lambda: self._on_new_data(result))


    def _on_new_data(self, data):
        self.current_data = data
        if self.active_tab == 'dashboard':
            self._refresh_dashboard_display(data)


    def _manual_refresh(self):
        self._fetch_in_background()


    def on_closing(self):
        if self.scheduler:
            self.scheduler.stop()
        self.destroy()


# ─── Run ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = Dashboard()
    app.protocol('WM_DELETE_WINDOW', app.on_closing)
    app.mainloop()