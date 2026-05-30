import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import customtkinter as ctk
import datetime
import threading
from database.db_manager import get_latest_price, get_setting
from core.scheduler import GoldScheduler
from core.analytics import get_buy_label, get_sell_label

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')

GOLD      = '#FFD700'
GOLD_DARK = '#B8860B'
GREEN     = '#00C853'
ORANGE    = '#FF6D00'
RED       = '#D50000'
BG        = '#1e1e1e'
CARD      = '#2a2a2a'
TEXT      = '#FFFFFF'
SUBTEXT   = '#AAAAAA'

def get_score_color(score):
    if score >= 75: return GREEN
    elif score >= 55: return '#8BC34A'
    elif score >= 35: return ORANGE
    else: return RED


class StartupPopup(ctk.CTk):
    def __init__(self):
        super().__init__()

        # ── Borderless window ──
        self.overrideredirect(True)       # removes title bar completely
        self.attributes('-topmost', True) # stays on top
        self.attributes('-alpha', 0.0)    # start transparent for fade-in

        self.configure(fg_color=BG)

        # ── Size and position — bottom right corner ──
        width, height = 360, 480
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        taskbar_offset = 60              # space for taskbar
        x = sw - width - 16
        y = sh - height - taskbar_offset
        self.geometry(f'{width}x{height}+{x}+{y}')

        self.scheduler = None
        self._build_ui()
        self._start_scheduler()

        # Fade in after UI is built
        self.after(100, self._fade_in)


    def _fade_in(self, alpha=0.0):
        alpha += 0.08
        self.attributes('-alpha', min(alpha, 1.0))
        if alpha < 1.0:
            self.after(20, lambda: self._fade_in(alpha))


    def _fade_out(self, alpha=1.0, callback=None):
        alpha -= 0.08
        self.attributes('-alpha', max(alpha, 0.0))
        if alpha > 0:
            self.after(20, lambda: self._fade_out(alpha, callback))
        else:
            if callback:
                callback()


    def _build_ui(self):
        # ── Drag support (since no title bar) ──
        self.bind('<Button-1>',   self._start_drag)
        self.bind('<B1-Motion>',  self._do_drag)

        # ── Top bar ──
        topbar = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=42)
        topbar.pack(fill='x')
        topbar.pack_propagate(False)

        ctk.CTkLabel(
            topbar,
            text='⚡ GOLD TRACKER',
            font=ctk.CTkFont(size=12, weight='bold'),
            text_color=GOLD
        ).pack(side='left', padx=14, pady=10)

        # Close button
        ctk.CTkButton(
            topbar,
            text='✕',
            width=32, height=28,
            fg_color='transparent',
            hover_color='#cc0000',
            text_color=SUBTEXT,
            font=ctk.CTkFont(size=13),
            command=self.on_closing
        ).pack(side='right', padx=8, pady=6)

        # Minimize to tray button
        ctk.CTkButton(
            topbar,
            text='—',
            width=32, height=28,
            fg_color='transparent',
            hover_color='#333333',
            text_color=SUBTEXT,
            font=ctk.CTkFont(size=13),
            command=self.on_closing
        ).pack(side='right', padx=0, pady=6)

        # ── Last updated ──
        self.updated_label = ctk.CTkLabel(
            self,
            text='Fetching latest prices...',
            font=ctk.CTkFont(size=10),
            text_color=SUBTEXT
        )
        self.updated_label.pack(pady=(10, 4))

        # ── Price cards ──
        price_row = ctk.CTkFrame(self, fg_color='transparent')
        price_row.pack(fill='x', padx=14, pady=4)
        price_row.columnconfigure(0, weight=1)
        price_row.columnconfigure(1, weight=1)

        card_24 = ctk.CTkFrame(price_row, fg_color=CARD, corner_radius=10)
        card_24.grid(row=0, column=0, padx=(0,5), sticky='ew')
        ctk.CTkLabel(card_24, text='24K',
            font=ctk.CTkFont(size=10, weight='bold'), text_color=GOLD
        ).pack(pady=(10,2))
        self.price_24k = ctk.CTkLabel(card_24, text='---',
            font=ctk.CTkFont(size=19, weight='bold'), text_color=TEXT)
        self.price_24k.pack()
        ctk.CTkLabel(card_24, text='per gram',
            font=ctk.CTkFont(size=9), text_color=SUBTEXT
        ).pack(pady=(0,10))

        card_22 = ctk.CTkFrame(price_row, fg_color=CARD, corner_radius=10)
        card_22.grid(row=0, column=1, padx=(5,0), sticky='ew')
        ctk.CTkLabel(card_22, text='22K',
            font=ctk.CTkFont(size=10, weight='bold'), text_color=GOLD_DARK
        ).pack(pady=(10,2))
        self.price_22k = ctk.CTkLabel(card_22, text='---',
            font=ctk.CTkFont(size=19, weight='bold'), text_color=TEXT)
        self.price_22k.pack()
        ctk.CTkLabel(card_22, text='per gram',
            font=ctk.CTkFont(size=9), text_color=SUBTEXT
        ).pack(pady=(0,10))

        # ── Retail price ──
        retail_card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
        retail_card.pack(fill='x', padx=14, pady=4)

        city = (get_setting('city') or 'city').upper()
        ctk.CTkLabel(retail_card, text=f'{city} RETAIL (24K)',
            font=ctk.CTkFont(size=10, weight='bold'), text_color=SUBTEXT
        ).pack(pady=(10,2))
        self.retail_price = ctk.CTkLabel(retail_card, text='---',
            font=ctk.CTkFont(size=24, weight='bold'), text_color=GOLD)
        self.retail_price.pack()
        self.premium_label = ctk.CTkLabel(retail_card, text='',
            font=ctk.CTkFont(size=9), text_color=SUBTEXT)
        self.premium_label.pack(pady=(0,10))

        # ── Buy signal ──
        buy_card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
        buy_card.pack(fill='x', padx=14, pady=4)

        ctk.CTkLabel(buy_card, text='BUY SIGNAL',
            font=ctk.CTkFont(size=10, weight='bold'), text_color=SUBTEXT
        ).pack(pady=(10,4))
        self.buy_label = ctk.CTkLabel(buy_card, text='ANALYSING...',
            font=ctk.CTkFont(size=16, weight='bold'), text_color=ORANGE)
        self.buy_label.pack()
        self.buy_score = ctk.CTkLabel(buy_card, text='',
            font=ctk.CTkFont(size=9), text_color=SUBTEXT)
        self.buy_score.pack(pady=(2,0))
        self.explanation = ctk.CTkLabel(buy_card, text='',
            font=ctk.CTkFont(size=9), text_color=SUBTEXT,
            wraplength=300)
        self.explanation.pack(pady=(2,10))

        # ── Buttons ──
        btn_row = ctk.CTkFrame(self, fg_color='transparent')
        btn_row.pack(fill='x', padx=14, pady=(6,14))
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        ctk.CTkButton(
            btn_row,
            text='Dashboard',
            fg_color=GOLD_DARK, hover_color=GOLD,
            text_color='black',
            font=ctk.CTkFont(size=11, weight='bold'),
            height=34,
            command=self._open_dashboard
        ).grid(row=0, column=0, padx=(0,5), sticky='ew')

        ctk.CTkButton(
            btn_row,
            text='Dismiss',
            fg_color='#333333', hover_color='#444444',
            text_color=TEXT,
            height=34,
            command=self.on_closing
        ).grid(row=0, column=1, padx=(5,0), sticky='ew')


    # ── Drag support ──
    def _start_drag(self, e):
        self._drag_x = e.x
        self._drag_y = e.y

    def _do_drag(self, e):
        x = self.winfo_x() + (e.x - self._drag_x)
        y = self.winfo_y() + (e.y - self._drag_y)
        self.geometry(f'+{x}+{y}')


    # ── Data display ──
    def _update_display(self, data):
        if not data: return

        p24    = data.get('price_24k')
        p22    = data.get('price_22k')
        retail = data.get('retail_price')

        if p24:
            self.price_24k.configure(text=f"₹{p24:,.0f}")
        if p22:
            self.price_22k.configure(text=f"₹{p22:,.0f}")
        if retail:
            self.retail_price.configure(text=f"₹{retail:,.0f}")
            if p24:
                self.premium_label.configure(
                    text=f"+₹{retail-p24:,.0f}/gram over spot"
                )

        score = data.get('buy_score') or 49
        label = data.get('buy_label') or get_buy_label(score)
        self.buy_label.configure(text=label, text_color=get_score_color(score))
        self.buy_score.configure(text=f"Score: {score}/100")
        self.explanation.configure(text=data.get('explanation') or '')

        now = datetime.datetime.now().strftime('%d %b, %I:%M %p')
        self.updated_label.configure(text=f'Updated: {now}')


    # ── Scheduler ──
    def _start_scheduler(self):
        latest = get_latest_price()
        if latest:
            latest['buy_label']  = get_buy_label(latest.get('buy_score') or 49)
            latest['sell_label'] = get_sell_label(latest.get('sell_score') or 51)
            self._update_display(latest)

        self.scheduler = GoldScheduler(on_update=self._on_new_data)
        self.scheduler.start()
        self.after(500, self._fetch_now)

    def _fetch_now(self):
        threading.Thread(target=self._do_fetch, daemon=True).start()

    def _do_fetch(self):
        result = self.scheduler.run_now()
        if result:
            self.after(0, lambda: self._update_display(result))

    def _on_new_data(self, data):
        self.after(0, lambda: self._update_display(data))

    def _open_dashboard(self):
        self.on_closing()
        threading.Thread(target=self._launch_dashboard, daemon=True).start()

    def _launch_dashboard(self):
        from ui.dashboard import Dashboard
        app = Dashboard()
        app.protocol('WM_DELETE_WINDOW', app.on_closing)
        app.mainloop()

    def on_closing(self):
        if self.scheduler:
            self.scheduler.stop()
        self._fade_out(callback=self.destroy)


if __name__ == '__main__':
    app = StartupPopup()
    app.mainloop()