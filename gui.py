# Author: Swayam Shakya
"""SwarmForge Advanced Edition — responsive, non-blocking desktop dashboard.

Uses only Python's standard library. All socket/API work runs in worker threads;
Tk widgets are updated only on the main thread through a queue.
"""
from __future__ import annotations

import math
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from config import HOST, TRACKER_PORT, API_BASE_PORT, TORRENT_ID
from utils import send_json_line, recv_json_line

APP_DIR = Path(__file__).resolve().parent
AUTHOR = "Swayam Shakya"

THEMES = {
    "dark": {
        "bg": "#0b1220", "panel": "#111c2e", "panel2": "#17253a", "field": "#0c1728",
        "text": "#e8eef8", "muted": "#91a4bd", "border": "#25364d", "accent": "#58a6ff",
        "accent_hover": "#3287e8", "green": "#35d39a", "yellow": "#f6c85f", "red": "#ff7185",
        "selection": "#214d78", "log": "#a9c1db"
    },
    "light": {
        "bg": "#f2f5fa", "panel": "#ffffff", "panel2": "#eaf0f8", "field": "#ffffff",
        "text": "#17253a", "muted": "#5e7088", "border": "#d4deeb", "accent": "#1769aa",
        "accent_hover": "#0e528a", "green": "#16835f", "yellow": "#946200", "red": "#c6374e",
        "selection": "#cfe5fa", "log": "#334b67"
    }
}


def api_call(peer_port: int, action: str, timeout: float = 2.0, **kwargs):
    """Call a node's local control API. Invoke only from a worker thread."""
    api_port = API_BASE_PORT + int(peer_port)
    with socket.create_connection((HOST, api_port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        send_json_line(sock, {"action": action, **kwargs})
        return recv_json_line(sock)


def fmt_bytes(value: int | float) -> str:
    amount = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{amount:.0f} B"
        amount /= 1024
    return f"{amount:.1f} TB"


class Dashboard:
    REFRESH_MS = 1200

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SwarmForge Advanced | Distributed Systems Lab")
        self.root.geometry("1180x840")
        self.root.minsize(980, 680)
        self.theme_name = "dark"
        self.colors = THEMES[self.theme_name]
        self.executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="swarmforge-ui")
        self.events: queue.Queue = queue.Queue()
        self.processes: dict[int, subprocess.Popen] = {}
        self.tracker_process: subprocess.Popen | None = None
        self.known_ports: set[int] = set()
        self.pending: set[str] = set()
        self.button_registry: list[tuple[tk.Button, str]] = []
        self.last_logs: dict[int, list[str]] = {}
        self.last_stats: dict[int, dict] = {}
        self.last_refresh = 0.0
        self.log_filter = tk.StringVar(value="All")
        self.log_search = tk.StringVar(value="")
        self.selected_port = tk.StringVar(value="5000")
        self.share_dir = tk.StringVar(value=str(APP_DIR))
        self.output_base = tk.StringVar(value=str(APP_DIR / "downloads"))
        self.status_var = tk.StringVar(value="Ready · Start the tracker, then launch peers")
        self.tracker_status_var = tk.StringVar(value="Tracker not started")
        self.node_status_var = tk.StringVar(value="No peer selected")
        self.progress_text_var = tk.StringVar(value="0%")
        self.download_speed_var = tk.StringVar(value="0 KB/s")
        self.uploaded_var = tk.StringVar(value="0 B")
        self.downloaded_var = tk.StringVar(value="0 B")
        self.peer_count_var = tk.StringVar(value="0")
        self.piece_count_var = tk.StringVar(value="0 / 0")
        self.current_piece_var = tk.StringVar(value="—")
        self.output_var = tk.StringVar(value="No output file yet")
        self.auto_refresh_var = tk.BooleanVar(value=True)
        self.show_timestamps_var = tk.BooleanVar(value=True)
        self._configure_style()
        self._build()
        self._apply_theme()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F5>", lambda _e: self.request_refresh())
        self.root.bind("<Control-l>", lambda _e: self.notebook.select(self.activity_tab))
        self.root.bind("<Control-Shift-T>", lambda _e: self.toggle_theme())
        self.root.after(100, self._drain_events)
        self.root.after(400, self._tick)

    # ---------- styling and layout ----------
    def _configure_style(self):
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

    def _build(self):
        c = self.colors
        self.shell = tk.Frame(self.root, bg=c["bg"])
        self.shell.pack(fill="both", expand=True)

        header = tk.Frame(self.shell, bg=c["bg"], padx=22, pady=12)
        header.pack(fill="x")
        title_wrap = tk.Frame(header, bg=c["bg"])
        title_wrap.pack(side="left", fill="x", expand=True)
        self.brand_label = tk.Label(title_wrap, text="SWARMFORGE", bg=c["bg"], fg=c["text"],
                                    font=("Helvetica", 23, "bold"))
        self.brand_label.pack(anchor="w")
        self.subtitle_label = tk.Label(title_wrap,
            text=f"ADVANCED P2P LAB  ·  Author: {AUTHOR}  ·  Torrent: {TORRENT_ID}",
            bg=c["bg"], fg=c["muted"], font=("Helvetica", 9))
        self.subtitle_label.pack(anchor="w", pady=(3, 0))
        right_header = tk.Frame(header, bg=c["bg"])
        right_header.pack(side="right", padx=(12, 0))
        self.connection_dot = tk.Label(right_header, text="●", fg=c["yellow"], bg=c["bg"], font=("Helvetica", 12))
        self.connection_dot.pack(side="left", padx=(0, 5))
        self.connection_label = tk.Label(right_header, text="LOCAL SWARM", fg=c["muted"], bg=c["bg"], font=("Helvetica", 9, "bold"))
        self.connection_label.pack(side="left", padx=(0, 14))
        self.theme_button = self._button(right_header, "☼  Theme", self.toggle_theme, kind="secondary")
        self.theme_button.pack(side="right")

        toolbar = tk.Frame(self.shell, bg=c["panel"], padx=18, pady=9)
        toolbar.pack(fill="x", padx=18)
        self.start_tracker_btn = self._button(toolbar, "▶  Start tracker", self.start_tracker, kind="primary")
        self.start_tracker_btn.pack(side="left", padx=(0, 7))
        tk.Label(toolbar, text="PEER PORT", bg=c["panel"], fg=c["muted"], font=("Helvetica", 8, "bold")).pack(side="left", padx=(10, 6))
        self.port_combo = ttk.Combobox(toolbar, textvariable=self.selected_port, width=9, values=["5000"], state="normal")
        self.port_combo.pack(side="left", padx=(0, 8))
        self.port_combo.bind("<<ComboboxSelected>>", lambda _e: self.request_refresh())
        self.start_node_btn = self._button(toolbar, "+  Add peer", self.start_node, kind="primary")
        self.start_node_btn.pack(side="left", padx=(0, 7))
        self.download_btn = self._button(toolbar, "↓  Download", self.download, kind="success")
        self.download_btn.pack(side="left", padx=(0, 7))
        self.register_btn = self._button(toolbar, "↻  Re-register", self.reregister, kind="secondary")
        self.register_btn.pack(side="left", padx=(0, 7))
        self.stop_node_btn = self._button(toolbar, "■  Stop peer", self.stop_selected_node, kind="danger")
        self.stop_node_btn.pack(side="left", padx=(0, 7))
        self.refresh_btn = self._button(toolbar, "↻ Refresh now", self.request_refresh, kind="secondary")
        self.refresh_btn.pack(side="right", padx=(0, 7))
        self.open_btn = self._button(toolbar, "Open output ↗", self.open_downloads, kind="secondary")
        self.open_btn.pack(side="right")

        self.paths_panel = tk.Frame(self.shell, bg=c["bg"], padx=20, pady=7)
        self.paths_panel.pack(fill="x")
        self._path_row(self.paths_panel, 0, "SHARE DIRECTORY", self.share_dir, self.browse_share)
        self._path_row(self.paths_panel, 1, "DOWNLOAD DIRECTORY", self.output_base, self.browse_output)

        metrics = tk.Frame(self.shell, bg=c["bg"], padx=18, pady=2)
        metrics.pack(fill="x")
        self.metric_cards = {}
        cards = [
            ("progress", "DOWNLOAD PROGRESS", self.progress_text_var, "Piece completion"),
            ("speed", "DOWNLOAD SPEED", self.download_speed_var, "Current session average"),
            ("peers", "PEERS USED", self.peer_count_var, "Successful sources"),
            ("pieces", "PIECES VERIFIED", self.piece_count_var, "Local integrity checked"),
            ("uploaded", "DATA UPLOADED", self.uploaded_var, "Shared with peers"),
        ]
        for col, (key, heading, variable, detail) in enumerate(cards):
            card = tk.Frame(metrics, bg=c["panel"], padx=14, pady=12, highlightthickness=1, highlightbackground=c["border"])
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            tk.Label(card, text=heading, bg=c["panel"], fg=c["muted"], font=("Helvetica", 8, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=variable, bg=c["panel"], fg=c["text"], font=("Helvetica", 18, "bold")).pack(anchor="w", pady=(6, 1))
            tk.Label(card, text=detail, bg=c["panel"], fg=c["muted"], font=("Helvetica", 8)).pack(anchor="w")
            self.metric_cards[key] = card
            metrics.columnconfigure(col, weight=1, uniform="metric")

        progress_row = tk.Frame(self.shell, bg=c["bg"], padx=20)
        progress_row.pack(fill="x", pady=(5, 8))
        self.progress_bar = ttk.Progressbar(progress_row, maximum=100, mode="determinate", style="Swarm.Horizontal.TProgressbar")
        self.progress_bar.pack(fill="x")
        self.current_piece_label = tk.Label(progress_row, textvariable=self.current_piece_var, bg=c["bg"], fg=c["muted"], font=("Helvetica", 9))
        self.current_piece_label.pack(anchor="w", pady=(5, 0))

        self.notebook = ttk.Notebook(self.shell, style="Swarm.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        self.overview_tab = tk.Frame(self.notebook, bg=c["bg"], padx=12, pady=12)
        self.peers_tab = tk.Frame(self.notebook, bg=c["bg"], padx=12, pady=12)
        self.activity_tab = tk.Frame(self.notebook, bg=c["bg"], padx=12, pady=12)
        self.notebook.add(self.overview_tab, text="  Overview  ")
        self.notebook.add(self.peers_tab, text="  Pieces & peers  ")
        self.notebook.add(self.activity_tab, text="  Activity console  ")
        self._build_overview()
        self._build_peers()
        self._build_activity()

        footer = tk.Frame(self.shell, bg=c["panel"], padx=18, pady=8)
        footer.pack(fill="x", side="bottom", before=self.notebook)
        self.footer_dot = tk.Label(footer, text="●", bg=c["panel"], fg=c["green"], font=("Helvetica", 9))
        self.footer_dot.pack(side="left", padx=(0, 6))
        self.footer_label = tk.Label(footer, textvariable=self.status_var, bg=c["panel"], fg=c["muted"], anchor="w", font=("Helvetica", 9))
        self.footer_label.pack(side="left", fill="x", expand=True)
        self.clock_label = tk.Label(footer, text="AUTO-REFRESH ON", bg=c["panel"], fg=c["muted"], font=("Helvetica", 8, "bold"))
        self.clock_label.pack(side="right")

    def _path_row(self, parent, row, label, variable, callback):
        c = self.colors
        tk.Label(parent, text=label, bg=c["bg"], fg=c["muted"], font=("Helvetica", 8, "bold"), width=19, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
        entry = tk.Entry(parent, textvariable=variable, bg=c["field"], fg=c["text"], insertbackground=c["text"],
                         relief="flat", highlightthickness=1, highlightbackground=c["border"], highlightcolor=c["accent"], font=("Helvetica", 9))
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 8), ipady=6)
        btn = self._button(parent, "Browse…", callback, kind="secondary")
        btn.grid(row=row, column=2, sticky="e")
        parent.columnconfigure(1, weight=1)

    def _build_overview(self):
        c = self.colors
        top = tk.Frame(self.overview_tab, bg=c["bg"])
        top.pack(fill="both", expand=True)
        left = tk.Frame(top, bg=c["panel"], padx=16, pady=14, highlightthickness=1, highlightbackground=c["border"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(left, text="SWARM TOPOLOGY", bg=c["panel"], fg=c["text"], font=("Helvetica", 11, "bold")).pack(anchor="w")
        tk.Label(left, text="A live view of the selected peer and peers it has used", bg=c["panel"], fg=c["muted"], font=("Helvetica", 9)).pack(anchor="w", pady=(3, 10))
        self.topology = tk.Canvas(left, height=235, bg=c["field"], highlightthickness=1, highlightbackground=c["border"])
        self.topology.pack(fill="both", expand=True)
        self.topology.bind("<Configure>", lambda _e: self._draw_topology(self.last_stats.get(self._selected_port(), {})))

        right = tk.Frame(top, bg=c["panel"], padx=16, pady=14, width=300, highlightthickness=1, highlightbackground=c["border"])
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        tk.Label(right, text="SESSION DETAILS", bg=c["panel"], fg=c["text"], font=("Helvetica", 11, "bold")).pack(anchor="w")
        self.detail_labels = {}
        for key, label in [("node", "Selected node"), ("tracker", "Tracker endpoint"), ("manifest", "Manifest status"), ("download", "Download state"), ("file", "Output file")]:
            tk.Label(right, text=label.upper(), bg=c["panel"], fg=c["muted"], font=("Helvetica", 8, "bold")).pack(anchor="w", pady=(14, 2))
            val = tk.Label(right, text="—", bg=c["panel"], fg=c["text"], font=("Helvetica", 9), anchor="w", justify="left", wraplength=255)
            val.pack(anchor="w", fill="x")
            self.detail_labels[key] = val
        self.tracker_status_line = tk.Label(right, textvariable=self.tracker_status_var, bg=c["panel"], fg=c["muted"], font=("Helvetica", 9), wraplength=255, justify="left")
        self.tracker_status_line.pack(anchor="w", side="bottom", pady=(12, 0))

    def _build_peers(self):
        c = self.colors
        heading = tk.Frame(self.peers_tab, bg=c["bg"])
        heading.pack(fill="x", pady=(0, 9))
        tk.Label(heading, text="PIECE INVENTORY", bg=c["bg"], fg=c["text"], font=("Helvetica", 11, "bold")).pack(side="left")
        self.piece_filter = tk.StringVar(value="All pieces")
        filter_box = ttk.Combobox(heading, textvariable=self.piece_filter, state="readonly", width=14,
                                  values=["All pieces", "Missing", "Verified"])
        filter_box.pack(side="right")
        filter_box.bind("<<ComboboxSelected>>", lambda _e: self._render_piece_table(self.last_stats.get(self._selected_port(), {})))
        self.piece_tree = ttk.Treeview(self.peers_tab, columns=("index", "piece", "status", "integrity"), show="headings", selectmode="browse")
        for col, text, width in [("index", "#", 55), ("piece", "Piece identifier (SHA-256 prefix)", 310), ("status", "Local state", 150), ("integrity", "Integrity", 170)]:
            self.piece_tree.heading(col, text=text)
            self.piece_tree.column(col, width=width, anchor="w")
        self.piece_tree.pack(fill="both", expand=True)
        self.piece_tree.tag_configure("verified", foreground=c["green"])
        self.piece_tree.tag_configure("missing", foreground=c["muted"])
        note = tk.Label(self.peers_tab, text="Integrity status is based on the node's verified piece inventory. The prototype does not provide cryptographic peer identity.",
                        bg=c["bg"], fg=c["muted"], font=("Helvetica", 9), anchor="w")
        note.pack(fill="x", pady=(9, 0))

    def _build_activity(self):
        c = self.colors
        toolbar = tk.Frame(self.activity_tab, bg=c["bg"])
        toolbar.pack(fill="x", pady=(0, 8))
        tk.Label(toolbar, text="EVENT STREAM", bg=c["bg"], fg=c["text"], font=("Helvetica", 11, "bold")).pack(side="left")
        self.log_filter_combo = ttk.Combobox(toolbar, textvariable=self.log_filter, values=["All", "Errors", "Warnings", "Transfers"], state="readonly", width=12)
        self.log_filter_combo.pack(side="right", padx=(7, 0))
        self.log_filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._render_logs())
        search_entry = tk.Entry(toolbar, textvariable=self.log_search, bg=c["field"], fg=c["text"], insertbackground=c["text"],
                                relief="flat", width=25, highlightthickness=1, highlightbackground=c["border"], font=("Helvetica", 9))
        search_entry.pack(side="right", ipady=5)
        search_entry.insert(0, "")
        self.log_search.trace_add("write", lambda *_: self._render_logs())
        self.log_box = tk.Text(self.activity_tab, bg=c["field"], fg=c["log"], insertbackground=c["text"], relief="flat",
                               wrap="word", font=("Menlo", 9), padx=12, pady=10, state="disabled", undo=False)
        scroll = ttk.Scrollbar(self.activity_tab, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True)
        for tag, color in [("ERROR", c["red"]), ("WARNING", c["yellow"]), ("SUCCESS", c["green"]), ("INFO", c["log"])]:
            self.log_box.tag_configure(tag, foreground=color)
        bottom = tk.Frame(self.activity_tab, bg=c["bg"])
        bottom.pack(fill="x", pady=(8, 0))
        self._button(bottom, "Clear visible logs", self.clear_logs, kind="secondary").pack(side="left")
        self._button(bottom, "Export logs…", self.export_logs, kind="secondary").pack(side="left", padx=8)
        ttk.Checkbutton(bottom, text="Auto-refresh", variable=self.auto_refresh_var, command=self._toggle_refresh).pack(side="right")

    def _button(self, parent, text, command, kind="secondary"):
        c = self.colors
        palette = {"primary": (c["accent"], "#ffffff"), "success": (c["green"], "#071a15" if self.theme_name == "dark" else "#ffffff"),
                   "danger": (c["panel2"], c["red"]), "secondary": (c["panel2"], c["text"])}
        bg, fg = palette.get(kind, palette["secondary"])
        btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=c["accent_hover"],
                        activeforeground="#ffffff", relief="flat", bd=0, padx=11, pady=7, cursor="hand2",
                        font=("Helvetica", 9, "bold"), highlightthickness=1, highlightbackground=c["border"])
        self.button_registry.append((btn, kind))
        btn.bind("<Enter>", lambda _e, b=btn, k=kind: b.configure(bg=self.colors["accent_hover"] if k in ("primary", "success") else self.colors["selection"]))
        btn.bind("<Leave>", lambda _e, b=btn, k=kind: b.configure(bg=self._button_bg(k), fg=self._button_fg(k)))
        return btn

    def _button_bg(self, kind):
        c = self.colors
        return {"primary": c["accent"], "success": c["green"], "danger": c["panel2"], "secondary": c["panel2"]}.get(kind, c["panel2"])

    def _button_fg(self, kind):
        c = self.colors
        if kind == "primary": return "#ffffff"
        if kind == "success": return "#071a15" if self.theme_name == "dark" else "#ffffff"
        if kind == "danger": return c["red"]
        return c["text"]

    def _apply_theme(self):
        c = self.colors
        self.root.configure(bg=c["bg"])
        self.shell.configure(bg=c["bg"])
        # Recolour all Tk widgets by semantic container backgrounds.
        for widget in self.root.winfo_children():
            self._recolor_tree(widget, c)
        self.style.configure("TFrame", background=c["bg"])
        self.style.configure("TLabel", background=c["bg"], foreground=c["text"], font=("Helvetica", 9))
        self.style.configure("TNotebook", background=c["bg"], borderwidth=0)
        self.style.configure("TNotebook.Tab", background=c["panel2"], foreground=c["muted"], padding=(15, 8), font=("Helvetica", 9, "bold"))
        self.style.map("TNotebook.Tab", background=[("selected", c["panel"])], foreground=[("selected", c["text"])])
        self.style.configure("Treeview", background=c["field"], fieldbackground=c["field"], foreground=c["text"], rowheight=30, bordercolor=c["border"])
        self.style.configure("Treeview.Heading", background=c["panel2"], foreground=c["text"], font=("Helvetica", 9, "bold"), relief="flat")
        self.style.map("Treeview", background=[("selected", c["selection"])], foreground=[("selected", c["text"])])
        self.style.configure("TCombobox", fieldbackground=c["field"], background=c["panel2"], foreground=c["text"], arrowcolor=c["text"])
        self.style.configure("TCheckbutton", background=c["bg"], foreground=c["text"])
        self.style.configure("Swarm.Horizontal.TProgressbar", troughcolor=c["panel2"], background=c["green"], thickness=11)
        for key, card in self.metric_cards.items():
            card.configure(bg=c["panel"], highlightbackground=c["border"])
            for child in card.winfo_children():
                child.configure(bg=c["panel"])
                if child.cget("text") in ("DOWNLOAD PROGRESS", "DOWNLOAD SPEED", "PEERS USED", "PIECES VERIFIED", "DATA UPLOADED"):
                    child.configure(fg=c["muted"])
                elif child.cget("text") in ("Piece completion", "Current session average", "Successful sources", "Local integrity checked", "Shared with peers"):
                    child.configure(fg=c["muted"])
                else:
                    child.configure(fg=c["text"])
        for button, kind in self.button_registry:
            try:
                button.configure(bg=self._button_bg(kind), fg=self._button_fg(kind),
                                 activebackground=c["accent_hover"], activeforeground="#ffffff",
                                 highlightbackground=c["border"])
            except tk.TclError:
                pass
        self.piece_tree.tag_configure("verified", foreground=c["green"])
        self.piece_tree.tag_configure("missing", foreground=c["muted"])
        if hasattr(self, "log_box"):
            self.log_box.tag_configure("ERROR", foreground=c["red"])
            self.log_box.tag_configure("WARNING", foreground=c["yellow"])
            self.log_box.tag_configure("SUCCESS", foreground=c["green"])
            self.log_box.tag_configure("INFO", foreground=c["log"])
        self._draw_topology(self.last_stats.get(self._selected_port(), {}))

    def _recolor_tree(self, widget, c):
        try:
            cls = widget.winfo_class()
            if isinstance(widget, tk.Canvas):
                widget.configure(bg=c["field"], highlightbackground=c["border"])
            elif isinstance(widget, tk.Text):
                widget.configure(bg=c["field"], fg=c["log"], insertbackground=c["text"])
            elif isinstance(widget, tk.Entry):
                widget.configure(bg=c["field"], fg=c["text"], insertbackground=c["text"], highlightbackground=c["border"], highlightcolor=c["accent"])
            elif isinstance(widget, tk.Button):
                # Buttons carry their own kind-specific styling; keep existing foreground where possible.
                pass
            elif isinstance(widget, tk.Label):
                old_bg = widget.cget("bg")
                if old_bg in sum(([x[k] for k in ("bg", "panel", "panel2", "field")] for x in THEMES.values()), []):
                    # Keep semantic contrast by matching the previous palette's semantic background.
                    if old_bg in (THEMES["dark"]["bg"], THEMES["light"]["bg"]): bg = c["bg"]
                    elif old_bg in (THEMES["dark"]["panel"], THEMES["light"]["panel"]): bg = c["panel"]
                    elif old_bg in (THEMES["dark"]["panel2"], THEMES["light"]["panel2"]): bg = c["panel2"]
                    else: bg = c["field"]
                    widget.configure(bg=bg)
                    old_fg = widget.cget("fg")
                    if old_fg in (THEMES["dark"]["text"], THEMES["light"]["text"]): widget.configure(fg=c["text"])
                    elif old_fg in (THEMES["dark"]["muted"], THEMES["light"]["muted"]): widget.configure(fg=c["muted"])
            elif isinstance(widget, tk.Frame):
                old_bg = widget.cget("bg")
                if old_bg in (THEMES["dark"]["bg"], THEMES["light"]["bg"]): widget.configure(bg=c["bg"])
                elif old_bg in (THEMES["dark"]["panel"], THEMES["light"]["panel"]): widget.configure(bg=c["panel"])
                elif old_bg in (THEMES["dark"]["panel2"], THEMES["light"]["panel2"]): widget.configure(bg=c["panel2"])
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._recolor_tree(child, c)

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.colors = THEMES[self.theme_name]
        self._apply_theme()
        self.status_var.set(f"Switched to {self.theme_name} theme")

    # ---------- background task/event model ----------
    def _submit(self, key, fn, on_result=None, on_error=None):
        if key in self.pending:
            self._set_status(f"Already working: {key}", "yellow")
            return
        self.pending.add(key)
        self.events.put(("busy", key, True))
        def worker():
            try:
                result = fn()
                self.events.put(("result", key, result, on_result))
            except Exception as exc:
                self.events.put(("error", key, exc, on_error))
        self.executor.submit(worker)

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind, key, *rest = event
                if kind == "busy":
                    continue
                self.pending.discard(key)
                if kind == "result":
                    result, callback = rest
                    if callback:
                        callback(result)
                elif kind == "error":
                    exc, callback = rest
                    if callback:
                        callback(exc)
                    else:
                        self._set_status(f"{key}: {exc}", "red")
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._drain_events)

    def _tick(self):
        if self.auto_refresh_var.get():
            self.request_refresh()
        self.root.after(self.REFRESH_MS, self._tick)

    def _toggle_refresh(self):
        self.clock_label.configure(text="AUTO-REFRESH ON" if self.auto_refresh_var.get() else "AUTO-REFRESH PAUSED")
        if self.auto_refresh_var.get():
            self.request_refresh()

    def request_refresh(self):
        port = self._selected_port()
        if port is None:
            return
        key = f"refresh:{port}"
        def fetch():
            stats = api_call(port, "stats", timeout=1.2)
            logs = api_call(port, "logs", timeout=1.2, limit=250).get("logs", [])
            return stats, logs
        def apply(data):
            stats, logs = data
            self.last_stats[port] = stats
            self.last_logs[port] = logs
            self._render_stats(port, stats)
            self._render_logs()
        def failed(exc):
            self.node_status_var.set(f"Node {port} unreachable")
            self.connection_dot.configure(fg=self.colors["yellow"])
            self._set_status(f"Waiting for node {port} · {exc}", "yellow")
        self._submit(key, fetch, apply, failed)

    def _selected_port(self):
        try:
            p = int(self.selected_port.get().strip())
            if 1 <= p <= 65535 and API_BASE_PORT + p <= 65535:
                return p
        except (ValueError, TypeError):
            pass
        return None

    def _validate_port(self):
        port = self._selected_port()
        if port is None:
            messagebox.showerror("Invalid peer port", f"Enter a port from 1 to {min(65535, 65535 - API_BASE_PORT)}.", parent=self.root)
        return port

    def _remember_port(self, port):
        self.known_ports.add(port)
        values = sorted(self.known_ports | {port})
        self.port_combo.configure(values=[str(v) for v in values])
        self.selected_port.set(str(port))

    def _set_status(self, text, level="green"):
        self.status_var.set(text)
        self.footer_dot.configure(fg=self.colors.get(level, self.colors["green"]))

    # ---------- actions ----------
    def start_tracker(self):
        if self.tracker_process and self.tracker_process.poll() is None:
            self._set_status(f"Tracker already running on {HOST}:{TRACKER_PORT}", "yellow")
            return
        try:
            self.tracker_process = subprocess.Popen([sys.executable, str(APP_DIR / "tracker.py")], cwd=str(APP_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            self.tracker_status_var.set(f"Tracker process launched · {HOST}:{TRACKER_PORT}")
            self._set_status(f"Tracker process started; checking health on {HOST}:{TRACKER_PORT}…", "yellow")
            def check_health():
                last_error = None
                for _ in range(12):
                    try:
                        with socket.create_connection((HOST, TRACKER_PORT), timeout=0.6) as sock:
                            sock.settimeout(0.6)
                            send_json_line(sock, {"action": "health", "torrent_id": TORRENT_ID})
                            response = recv_json_line(sock)
                            if response.get("ok"):
                                return response
                            last_error = response.get("error", "health check rejected")
                    except Exception as exc:
                        last_error = exc
                    time.sleep(0.15)
                raise RuntimeError(f"Tracker did not pass its health check: {last_error}")
            self._submit("tracker-health", check_health,
                lambda result: (self.tracker_status_var.set(f"Healthy · {HOST}:{TRACKER_PORT} · {result.get('torrents', 0)} torrent(s)"), self.connection_dot.configure(fg=self.colors["green"]), self._set_status("Tracker health check passed")),
                lambda exc: (self.tracker_status_var.set(f"Health check failed · {exc}"), self.connection_dot.configure(fg=self.colors["red"]), self._set_status(str(exc), "red")))
        except Exception as exc:
            messagebox.showerror("Could not start tracker", str(exc), parent=self.root)

    def start_node(self):
        port = self._validate_port()
        if port is None:
            return
        process = self.processes.get(port)
        if process and process.poll() is None:
            self._set_status(f"Peer {port} is already running", "yellow")
            return
        share = Path(self.share_dir.get()).expanduser().resolve()
        output = Path(self.output_base.get()).expanduser().resolve() / str(port)
        if not share.is_dir():
            messagebox.showerror("Share folder not found", f"Choose an existing directory:\n{share}", parent=self.root)
            return
        sample_names = ("binfile.part1.bin", "binfile.part2.bin")
        if not any((share / name).is_file() for name in sample_names):
            if not messagebox.askyesno("Start as leecher?", "No sample pieces found in the share directory. Start this peer as a leecher?", parent=self.root):
                return
        try:
            output.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, str(APP_DIR / "node.py"), str(port), "--share-dir", str(share), "--output-dir", str(output)]
            self.processes[port] = subprocess.Popen(cmd, cwd=str(APP_DIR), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            self._remember_port(port)
            self._set_status(f"Peer {port} launched · API port {API_BASE_PORT + port}")
            self.root.after(500, self.request_refresh)
        except Exception as exc:
            messagebox.showerror("Could not start peer", str(exc), parent=self.root)

    def download(self):
        port = self._validate_port()
        if port is None:
            return
        def apply(result):
            if result.get("ok"):
                self._set_status(f"Download requested for peer {port}; telemetry will update automatically")
            else:
                self._set_status(result.get("message", result.get("error", "Download request rejected")), "yellow")
        self._submit(f"download:{port}", lambda: api_call(port, "download"), apply,
                     lambda exc: self._set_status(f"Cannot reach peer {port}: {exc}", "red"))

    def reregister(self):
        port = self._validate_port()
        if port is None:
            return
        self._submit(f"register:{port}", lambda: api_call(port, "register"),
                     lambda result: self._set_status(result.get("message", "Registration refreshed")),
                     lambda exc: self._set_status(f"Registration failed: {exc}", "red"))

    def stop_selected_node(self):
        port = self._validate_port()
        if port is None:
            return
        proc = self.processes.get(port)
        if not proc or proc.poll() is not None:
            self._set_status(f"Peer {port} was not launched by this dashboard", "yellow")
            return
        if not messagebox.askyesno("Stop peer", f"Gracefully stop peer {port}?", parent=self.root):
            return
        def stop():
            try:
                api_call(port, "shutdown", timeout=1.5)
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired: proc.kill()
            return True
        self._submit(f"stop:{port}", stop, lambda _r: self._set_status(f"Peer {port} stopped"),
                     lambda exc: self._set_status(f"Could not stop peer: {exc}", "red"))

    def browse_share(self):
        path = filedialog.askdirectory(parent=self.root, initialdir=self.share_dir.get() or str(APP_DIR))
        if path:
            self.share_dir.set(path)

    def browse_output(self):
        path = filedialog.askdirectory(parent=self.root, initialdir=self.output_base.get() or str(APP_DIR))
        if path:
            self.output_base.set(path)

    def open_downloads(self):
        path = Path(self.output_base.get()).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin": subprocess.Popen(["open", str(path)])
            elif os.name == "nt": os.startfile(str(path))
            else: subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Open folder", str(exc), parent=self.root)

    def clear_logs(self):
        port = self._selected_port()
        if port is not None:
            self.last_logs[port] = []
        self._render_logs()
        self._set_status("Cleared the visible log view; node-side logs are unchanged")

    def export_logs(self):
        port = self._selected_port()
        logs = self.last_logs.get(port, []) if port is not None else []
        if not logs:
            messagebox.showinfo("Export logs", "There are no loaded logs to export yet.", parent=self.root)
            return
        target = filedialog.asksaveasfilename(parent=self.root, title="Export SwarmForge logs", defaultextension=".log",
                                              filetypes=[("Log files", "*.log"), ("Text files", "*.txt")],
                                              initialfile=f"swarmforge-peer-{port}.log")
        if target:
            try:
                Path(target).write_text("\n".join(logs) + "\n", encoding="utf-8")
                self._set_status(f"Exported {len(logs)} log lines to {target}")
            except OSError as exc:
                messagebox.showerror("Export failed", str(exc), parent=self.root)

    # ---------- rendering ----------
    def _render_stats(self, port, stats):
        c = self.colors
        progress = max(0.0, min(100.0, float(stats.get("progress", 0) or 0)))
        self.progress_text_var.set(f"{progress:.1f}%")
        self.progress_bar["value"] = progress
        self.download_speed_var.set(f"{stats.get('speed_kbps', 0):.1f} KB/s")
        self.peer_count_var.set(str(len(stats.get("peers_used", []))))
        self.piece_count_var.set(f"{stats.get('downloaded_pieces', 0)} / {stats.get('total_pieces', 0)}")
        self.uploaded_var.set(fmt_bytes(stats.get("uploaded_bytes", 0)))
        self.downloaded_var.set(fmt_bytes(stats.get("downloaded_bytes", 0)))
        self.current_piece_var.set(f"Current piece: {stats.get('current_piece') or 'idle'}  ·  Downloaded: {fmt_bytes(stats.get('downloaded_bytes', 0))}")
        self.node_status_var.set(f"Peer {port} online · {stats.get('host', HOST)}:{stats.get('port', port)}")
        self.detail_labels["node"].configure(text=f"{stats.get('host', HOST)}:{stats.get('port', port)}  (API {stats.get('api_port', API_BASE_PORT + port)})")
        self.detail_labels["tracker"].configure(text=f"{HOST}:{TRACKER_PORT}")
        self.detail_labels["manifest"].configure(text="Loaded" if stats.get("manifest_loaded") else "Waiting for seed manifest")
        self.detail_labels["download"].configure(text="Downloading" if stats.get("download_running") else ("Complete" if progress >= 100 else "Idle"))
        output = stats.get("output_file", "")
        self.detail_labels["file"].configure(text=output or "—")
        self.output_var.set(output or "No output file yet")
        self._render_piece_table(stats)
        self._draw_topology(stats)
        self.connection_dot.configure(fg=c["green"])
        if stats.get("last_error"):
            self._set_status(f"Peer {port} error: {stats['last_error']}", "red")
        elif progress >= 100:
            self._set_status(f"Download complete · {output}")
        else:
            self._set_status(f"Peer {port} connected · Manifest {'ready' if stats.get('manifest_loaded') else 'pending'}")

    def _render_piece_table(self, stats):
        current = self.piece_filter.get() if hasattr(self, "piece_filter") else "All pieces"
        existing = {self.piece_tree.item(i, "values")[0]: i for i in self.piece_tree.get_children()}
        pieces = stats.get("pieces", [])
        states = stats.get("piece_status", [])
        visible = set()
        for idx, piece in enumerate(pieces):
            done = bool(states[idx]) if idx < len(states) else False
            if current == "Missing" and done: continue
            if current == "Verified" and not done: continue
            row_id = str(idx)
            visible.add(row_id)
            values = (idx + 1, piece + "…", "Verified locally" if done else "Missing", "SHA-256 checked" if done else "Not downloaded")
            tag = "verified" if done else "missing"
            if self.piece_tree.exists(row_id):
                self.piece_tree.item(row_id, values=values, tags=(tag,))
            else:
                self.piece_tree.insert("", "end", iid=row_id, values=values, tags=(tag,))
        for iid in self.piece_tree.get_children():
            if iid not in visible:
                self.piece_tree.delete(iid)

    def _draw_topology(self, stats):
        if not hasattr(self, "topology") or not self.topology.winfo_exists():
            return
        c = self.colors
        canvas = self.topology
        canvas.delete("all")
        w = max(canvas.winfo_width(), 320)
        h = max(canvas.winfo_height(), 220)
        canvas.create_text(14, 12, anchor="nw", fill=c["muted"], text="PEER LINKS · local telemetry", font=("Helvetica", 8, "bold"))
        cx, cy = w / 2, h / 2 + 6
        radius = min(w * .34, h * .34, 125)
        canvas.create_oval(cx-32, cy-32, cx+32, cy+32, fill=c["accent"], outline="")
        canvas.create_text(cx, cy-2, text="NODE", fill="#ffffff", font=("Helvetica", 10, "bold"))
        port = self._selected_port()
        canvas.create_text(cx, cy+14, text=str(port or "—"), fill="#ffffff", font=("Helvetica", 8))
        peers = stats.get("peers_used", []) if stats else []
        if not peers:
            canvas.create_text(cx, h-22, text="No successful peer transfers recorded yet", fill=c["muted"], font=("Helvetica", 9))
            return
        count = min(8, len(peers))
        for idx, peer in enumerate(peers[:count]):
            angle = 2 * math.pi * idx / count - math.pi / 2
            x, y = cx + radius * math.cos(angle), cy + radius * .72 * math.sin(angle)
            canvas.create_line(cx, cy, x, y, fill=c["green"], width=2, dash=(4, 3))
            canvas.create_oval(x-18, y-18, x+18, y+18, fill=c["panel2"], outline=c["green"], width=2)
            canvas.create_text(x, y, text=str(idx + 1), fill=c["text"], font=("Helvetica", 9, "bold"))
            label = peer if len(peer) < 23 else peer[:20] + "…"
            canvas.create_text(x, y+26, text=label, fill=c["muted"], font=("Helvetica", 8))

    def _render_logs(self):
        if not hasattr(self, "log_box"):
            return
        port = self._selected_port()
        logs = self.last_logs.get(port, []) if port is not None else []
        filt = self.log_filter.get()
        query = self.log_search.get().strip().lower()
        filtered = []
        for line in logs:
            upper = line.upper()
            if filt == "Errors" and "ERROR" not in upper: continue
            if filt == "Warnings" and "WARNING" not in upper: continue
            if filt == "Transfers" and not any(x in upper for x in ("UPLOAD", "DOWNLOAD", "PIECE", "TRANSFER")): continue
            if query and query not in line.lower(): continue
            filtered.append(line)
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        for line in filtered[-500:]:
            upper = line.upper()
            tag = "ERROR" if "ERROR" in upper else "WARNING" if "WARNING" in upper else "SUCCESS" if any(x in upper for x in ("COMPLETE", "VERIFIED", "REGISTERED")) else "INFO"
            self.log_box.insert("end", line + "\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ---------- shutdown ----------
    def _shutdown_children(self):
        """Stop owned child processes off the UI thread so closing stays responsive."""
        for port, proc in list(self.processes.items()):
            if proc.poll() is None:
                try:
                    api_call(port, "shutdown", timeout=0.8)
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try: proc.wait(timeout=0.8)
                    except subprocess.TimeoutExpired: proc.kill()
        if self.tracker_process and self.tracker_process.poll() is None:
            self.tracker_process.terminate()
            try: self.tracker_process.wait(timeout=0.8)
            except subprocess.TimeoutExpired: self.tracker_process.kill()

    def close(self):
        if not messagebox.askyesno("Quit SwarmForge", "Stop peers and tracker started by this dashboard?", parent=self.root):
            return
        self.auto_refresh_var.set(False)
        threading.Thread(target=self._shutdown_children, name="swarmforge-shutdown", daemon=True).start()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main():
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
