"""Pilot TeamBuilder desktop review. Tk stays on the main thread."""

import argparse
import json
import os
import re
import sys
import tkinter as tk
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from queue import Empty
from threading import Event
from tkinter import filedialog, font, messagebox, ttk
from uuid import uuid4

from PIL import Image, ImageTk

from . import __version__
from .desktop_engine import DesktopEngine, LatestWorker
from .model import PPI, CompositionSettings
from .production import ProductionEngine
from .review import (
    JobStore,
    ReviewSession,
    fingerprint,
    import_manifest,
    new_job,
    requested_distribution,
    rows_from_layout,
    validate_job,
)

NAVY, ROYAL, ORANGE, SKY, CLOUD, INSTRUMENT = (
    "#0B1F3A",
    "#175CD3",
    "#F97316",
    "#38BDF8",
    "#F5F7FA",
    "#111827",
)


def support_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/VolumePilot/TeamBuilder"
    if sys.platform == "win32":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home()))
            / "VolumePilot/TeamBuilder"
        )
    return Path.home() / ".local/share/volumepilot/teambuilder"


def make_demo(folder: Path):
    from .synthetic import athletes, write_sources

    incoming = folder / "Incoming"
    for name, count in (("001_Demo Team", 17), ("002_Small Team", 5)):
        write_sources(incoming / name, athletes(count))
    templates = folder / "Templates/Team"
    templates.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (900, 300), NAVY) as image:
        image.save(templates / "Team Background_default.jpg")
    with Image.new("RGBA", (900, 300)) as image:
        image.save(templates / "Team Foreground_default.png")
    job = new_job(incoming, "Synthetic demonstration")
    job["is_demo"] = True
    for team in job["teams"]:
        for entry, person in zip(
            team["manifest"]["athletes"], athletes(len(team["manifest"]["athletes"]))
        ):
            entry["manual_head"] = list(vars(person.head).values())
            entry["capture"].update(
                source_scale=1.0,
                relative_height=person.relative_height,
                geometry_id="synthetic",
                provenance="Synthetic geometry fixture",
            )
    return job


class TeamBuilderApp:
    def __init__(self, root, data_dir: Path | None = None, job: dict | None = None):
        self.root = root
        self.data_dir = data_dir or support_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore()
        self.session = None
        cache_path = self.data_dir / "detections.sqlite3"
        self.worker = LatestWorker(lambda path=cache_path: DesktopEngine(path))
        self.token = 0
        self.view = None
        self.selected_id = None
        self.exporting = False
        self.monitoring = False
        self.production_busy = False
        self.production_worker = None
        self.production_cancel = Event()
        self.saved_fingerprint = None
        self.closed = False
        self.export_destination = None
        self.pending_close = False
        self.team_status = {}
        self.photos = {}
        self.portrait_map = None
        self.panorama_map = None
        self.drag_start = None
        self.recent_paths = []
        self.recent = tk.StringVar(root)
        self.title = tk.StringVar(root, "Choose a team folder to begin")
        self.status = tk.StringVar(
            root, "Choose a folder of athlete PNGs, or a parent folder containing teams."
        )
        self.summary = tk.StringVar(root, "0 teams · 0 exported")
        self.details = tk.StringVar(root, "")
        self.athlete_status = tk.StringVar(
            root, "Select an athlete to review their head and height anchors."
        )
        self.mode = tk.StringVar(root, "Head box")
        self.row_counts = tk.StringVar(root)
        self.target_row = tk.StringVar(root, "1")
        self.values = {
            key: tk.StringVar(root, f"{value * (100 if key == 'group_scale' else 1):g}")
            for key, value in asdict(CompositionSettings()).items()
        }
        self.loading_form = False
        self.live_timer = None
        self.live_readouts = {}
        self.athlete_count = tk.StringVar(root, "0")
        self.requested_rows = tk.StringVar(root, "Auto")
        self.distribution = tk.StringVar(root, "—")
        self.setup_summary = tk.StringVar(root, "1  Choose hotfolder   →   2  Job setup   →   3  Start automatic processing")
        self.monitor_caption = tk.StringVar(root, "Start automatic processing")
        self._theme()
        self._build()
        self.root.title(f"Pilot TeamBuilder {__version__} · VolumePilot")
        width = max(1240, min(1520, root.winfo_screenwidth() - 40))
        height = max(760, min(960, root.winfo_screenheight() - 80))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(1240, 760)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self._drain)
        self.root.after(200, self._production_poll)
        self._load_recent()
        if job:
            self.set_job(job)

    def _theme(self):
        available = font.families(self.root)
        family = next(
            (
                f
                for f in ("Inter", "Segoe UI", "Helvetica", "DejaVu Sans")
                if f in available
            ),
            "TkDefaultFont",
        )
        self.root.option_add("*Font", (family, 10))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=CLOUD)
        style.configure(
            "TLabel", background=CLOUD, foreground=INSTRUMENT, font=(family, 10)
        )
        style.configure("Title.TLabel", font=(family, 18, "bold"))
        style.configure("Sub.TLabel", foreground="#475467", font=(family, 9))
        style.configure("TButton", padding=(10, 6), font=(family, 10))
        style.configure("Primary.TButton", background=ROYAL, foreground="white")
        style.map("Primary.TButton", background=[("active", "#134AAB")])
        style.configure("Export.TButton", background=ORANGE, foreground=NAVY)
        style.map("Export.TButton",
                  background=[("disabled", "#FED7AA"), ("active", "#FB923C")],
                  foreground=[("disabled", NAVY), ("active", NAVY), ("!disabled", NAVY)])
        style.configure(
            "Treeview",
            rowheight=29,
            font=(family, 10),
            background="white",
            fieldbackground="white",
        )
        style.configure("Treeview.Heading", font=(family, 9, "bold"))
        style.map(
            "Treeview",
            background=[("selected", ROYAL)],
            foreground=[("selected", "white")],
        )
        self.family = family

    def _build(self):
        bar = tk.Frame(self.root, bg=NAVY, padx=20, pady=12)
        bar.pack(fill="x")
        tk.Label(
            bar, text="VOLUME", bg=NAVY, fg="white", font=(self.family, 18, "bold")
        ).pack(side="left")
        tk.Label(
            bar, text="PILOT", bg=NAVY, fg=ORANGE, font=(self.family, 18, "bold")
        ).pack(side="left")
        tk.Label(
            bar, text=f"  /  TeamBuilder  {__version__}", bg=NAVY, fg="white", font=(self.family, 15)
        ).pack(side="left", padx=12)
        tk.Label(
            bar,
            text="MORE PHOTOS. LESS CHAOS.",
            bg=NAVY,
            fg=SKY,
            font=(self.family, 10),
        ).pack(side="right")
        actions = ttk.Frame(self.root, padding=(16, 10))
        actions.pack(fill="x")
        self.job_box = ttk.Combobox(actions, textvariable=self.recent, state="readonly", width=20)
        self.job_box.pack(side="left", padx=(0, 8))
        self.job_box.bind("<<ComboboxSelected>>", self._open_recent)
        ttk.Button(actions, text="Choose hotfolder…", style="Primary.TButton",
                   command=self._guard(self.choose_team_folder)).pack(side="left", padx=4)
        ttk.Button(actions, text="Job setup…", command=self._guard(self.job_setup)).pack(side="left", padx=4)
        self.monitor_button = ttk.Button(actions, textvariable=self.monitor_caption,
                                        style="Export.TButton", command=self.toggle_monitor)
        self.monitor_button.pack(side="right", padx=(8, 0))
        self.analyze_button = ttk.Button(actions, text="Refresh preview", command=lambda: self.request_view(True))
        self.analyze_button.pack(side="right", padx=4)
        menu_button = ttk.Menubutton(actions, text="Job ▾")
        menu = tk.Menu(menu_button, tearoff=False)
        for label, callback in (("New job…", self.new), ("Open saved job…", self.open),
                                ("Save job", self.save), ("Save a copy…", lambda: self.save(True)),
                                ("Demo", self.demo), ("Rebuild selected team", self.rebuild_selected)):
            menu.add_command(label=label, command=self._guard(callback))
        menu_button.configure(menu=menu)
        menu_button.pack(side="right", padx=4)
        ttk.Label(self.root, textvariable=self.setup_summary, style="Sub.TLabel",
                  wraplength=1200).pack(fill="x", padx=20, pady=(0, 8))
        main = ttk.Panedwindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=16)
        self.main = main
        queue = ttk.Frame(main, padding=(0, 8, 10, 0), width=210)
        main.add(queue, weight=0)
        ttk.Label(queue, text="TEAM QUEUE", font=(self.family, 10, "bold")).pack(
            anchor="w", pady=(0, 8)
        )
        self.teams = ttk.Treeview(
            queue,
            columns=("count", "state"),
            show="tree headings",
            selectmode="browse",
            height=20,
        )
        self.teams.heading("#0", text="Team")
        self.teams.heading("count", text="Qty")
        self.teams.heading("state", text="Status")
        self.teams.column("#0", width=128, stretch=True)
        self.teams.column("count", width=32, stretch=False, anchor="center")
        self.teams.column("state", width=75, stretch=False)
        self.teams.pack(fill="both", expand=True)
        self.teams.bind("<<TreeviewSelect>>", self.select_team)
        ttk.Label(
            queue, textvariable=self.summary, style="Sub.TLabel", wraplength=185
        ).pack(anchor="w", pady=10)
        center = ttk.Frame(main, padding=(4, 8, 12, 0))
        main.add(center, weight=4)
        ttk.Label(center, textvariable=self.title, style="Title.TLabel").pack(
            anchor="w"
        )
        self.canvas = tk.Canvas(
            center, background=INSTRUMENT, highlightthickness=0, height=340
        )
        self.canvas.pack(fill="both", expand=True, pady=(10, 6))
        self.canvas.bind("<Configure>", lambda e: self._draw_panorama())
        self.canvas.bind("<ButtonPress-1>", self._group_press)
        self.canvas.bind("<ButtonRelease-1>", self._group_release)
        ttk.Label(
            center,
            text="26 × 8 in  ·  Artwork 24 × 8 in  ·  Drag a selected athlete in ⅛-inch steps",
            style="Sub.TLabel",
        ).pack(anchor="w")
        self.issue_label = ttk.Label(
            center,
            textvariable=self.details,
            foreground="#92400E",
            wraplength=750,
            justify="left",
        )
        self.issue_label.pack(fill="x", pady=(8, 10))
        self.preview_tabs = ttk.Notebook(center, height=238)
        self.preview_tabs.pack(fill="x", pady=(4, 0))
        layout_page = ttk.Frame(self.preview_tabs, padding=(10, 6))
        athlete_page = ttk.Frame(self.preview_tabs)
        self.preview_tabs.add(layout_page, text="Layout")
        self.preview_tabs.add(athlete_page, text="Sources & issues")
        self._layout_controls(layout_page)
        self.athletes = ttk.Treeview(
            athlete_page,
            columns=("file", "row", "status"),
            show="headings",
            height=7,
            selectmode="browse",
        )
        for key, label, width in (
            ("file", "Source image", 340),
            ("row", "Row", 45),
            ("status", "Review", 160),
        ):
            self.athletes.heading(key, text=label)
            self.athletes.column(key, width=width, stretch=key == "file")
        self.athletes.pack(fill="both", expand=True)
        self.athletes.bind("<<TreeviewSelect>>", self.select_athlete)
        self.tabs = ttk.Notebook(main, width=300)
        self._composition_tab()
        self._athlete_tab()
        footer = ttk.Frame(self.root, padding=(16, 10))
        footer.pack(fill="x")
        self.export_button = ttk.Button(
            footer,
            text="Export selected team",
            style="Export.TButton",
            command=self.export,
            state="disabled",
        )
        self.export_button.pack(side="right", padx=(12, 0))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=110)
        self.progress.pack(side="left", padx=(0, 12))
        status_label = ttk.Label(footer, textvariable=self.status, wraplength=700)
        status_label.pack(side="left", fill="x", expand=True)
        footer.bind(
            "<Configure>",
            lambda e: status_label.configure(
                wraplength=max(
                    200,
                    e.width
                    - self.export_button.winfo_reqwidth()
                    - self.progress.winfo_reqwidth()
                    - 80,
                )
            ),
        )

    def _scroll_tab(self, name):
        host = ttk.Frame(self.tabs)
        self.tabs.add(host, text=name)
        surface = tk.Canvas(host, bg=CLOUD, highlightthickness=0, width=300)
        scroll = ttk.Scrollbar(host, command=surface.yview)
        scroll.pack(side="right", fill="y")
        surface.pack(side="left", fill="both", expand=True)
        surface.configure(yscrollcommand=scroll.set)
        page = ttk.Frame(surface, padding=10)
        item = surface.create_window(0, 0, anchor="nw", window=page)
        page.bind(
            "<Configure>", lambda e: surface.configure(scrollregion=surface.bbox("all"))
        )
        surface.bind(
            "<Configure>", lambda e: surface.itemconfigure(item, width=e.width)
        )
        return page

    def _layout_controls(self, page):
        counts = ttk.Frame(page)
        counts.pack(fill="x", pady=(0, 5))
        ttk.Label(counts, text="Athletes").pack(side="left")
        ttk.Label(counts, textvariable=self.athlete_count, width=5).pack(side="left", padx=8)
        ttk.Label(counts, text="Requested rows").pack(side="left")
        self.rows_picker = ttk.Combobox(
            counts, textvariable=self.requested_rows, values=("Auto",),
            state="readonly", width=5,
        )
        self.rows_picker.pack(side="left", padx=8)
        self.rows_picker.bind("<<ComboboxSelected>>", lambda event: self._guard(self.apply_requested_rows)())
        ttk.Label(counts, textvariable=self.distribution).pack(side="right")
        ttk.Label(page, text="Row distribution: back to front · Auto follows the row CSV",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 4))
        for key, label, low, high, step, suffix in (
            ("row_separation_heads", "Vertical row spacing", 0.5, 3.0, 0.125, " heads"),
            ("v_step_in", "Inverted-V step", 0.0, 0.25, 0.025, " in"),
            ("head_spacing_in", "Horizontal head spacing", 0.25, 2.0, 0.125, " in"),
            ("group_scale", "Job athlete size", 25, 300, 1, "%"),
        ):
            row = ttk.Frame(page)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=24).pack(side="left")
            readout = tk.StringVar(self.root)
            self.live_readouts[key] = (readout, suffix)
            ttk.Label(row, textvariable=readout, width=11, anchor="e").pack(side="right")
            slider = ttk.Scale(
                row, from_=low, to=high, variable=self.values[key],
                command=lambda value, k=key, s=step: self._live_change(k, value, s),
            )
            slider.pack(side="left", fill="x", expand=True, padx=10)
        tools = ttk.Frame(page)
        tools.pack(fill="x", pady=(8, 0))
        ttk.Label(tools, text="Job-wide settings", style="Sub.TLabel").pack(side="left")
        ttk.Button(tools, text="Fit largest team", command=self._guard(self.fit_largest)).pack(side="right", padx=5)
        ttk.Button(tools, text="Fix a detection / placement…", command=self.show_review).pack(side="right")
        self._refresh_readouts()

    def _refresh_readouts(self):
        for key, (readout, suffix) in self.live_readouts.items():
            try:
                readout.set(f"{float(self.values[key].get()):g}{suffix}")
            except ValueError:
                readout.set("—")

    def _live_change(self, key, value, step):
        if self.loading_form or self.exporting or self.production_busy or self.monitoring:
            return
        self.values[key].set(f"{round(float(value) / step) * step:.3f}")
        self._refresh_readouts()
        if not self.session:
            return
        if self.live_timer is not None:
            self.root.after_cancel(self.live_timer)
        self.export_button.state(["disabled"])
        self.live_timer = self.root.after(180, self._guard(self._flush_live))

    def _flush_live(self):
        if self.live_timer is None:
            return False
        self.root.after_cancel(self.live_timer)
        self.live_timer = None
        self.apply_settings()
        return True

    def _sync_rows(self, counts=None):
        if not self.session or not self.session.job["teams"]:
            return
        active = sum(not e.get("excluded", False) for e in self.session.manifest["athletes"])
        self.athlete_count.set(str(active))
        minimum = max(1, (active + 15) // 16)
        self.rows_picker.configure(values=("Auto", *map(str, range(minimum, active + 1))))
        self.requested_rows.set(str(self.session.team.get("requested_rows", "Auto")))
        if counts is None and active:
            manual = self.session.manifest.get("corrections", {}).get("rows")
            if manual:
                counts = tuple(map(len, manual))
            else:
                from .rows import RowTable
                table = RowTable.load(
                    Path(self.session.job["row_csv"]) if self.session.job["row_csv"]
                    else Path(__file__).parent / "data/row_counts.csv"
                )
                counts = requested_distribution(
                    active, self.session.team.get("requested_rows"), table.for_count(active)
                )
        self.distribution.set(" / ".join(map(str, counts)) if counts else "—")

    def apply_requested_rows(self, event=None):
        if not self.session or not self.session.job["teams"] or self.exporting or self.production_busy:
            return
        self._flush_live()
        value = self.requested_rows.get()
        try:
            self.session.request_rows(None if value == "Auto" else int(value))
        finally:
            self._sync_rows()
        self.request_view()

    def _composition_tab(self):
        page = self._scroll_tab("Advanced")
        fields = (
            ("head_spacing_in", "Head spacing (in)", 0.125),
            ("row_separation_heads", "Row separation (heads)", 0.125),
            ("v_step_in", "V step (in)", 0.025),
            ("v_max_drop_in", "V edge limit (in)", 0.125),
            ("group_scale", "Group size (%)", 5),
            ("x_offset_in", "Group X (in)", 0.125),
            ("y_offset_in", "Group Y (in)", 0.125),
            ("left_in", "Area left (in)", 0.125),
            ("top_in", "Area top (in)", 0.125),
            ("width_in", "Area width (in)", 0.125),
            ("height_in", "Area height (in)", 0.125),
        )
        for row, (key, label, step) in enumerate(fields):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=4)
            variable = self.values[key]
            spin = ttk.Spinbox(
                page,
                textvariable=variable,
                from_=-100,
                to=300 if key == "group_scale" else 100,
                increment=step,
                width=8,
                command=self._guard(self.apply_settings),
            )
            spin.grid(row=row, column=1, sticky="e", padx=(8, 0))
            spin.bind("<Return>", lambda e: self._guard(self.apply_settings)())
        ttk.Button(
            page, text="Apply composition", command=self._guard(self.apply_settings)
        ).grid(row=11, column=0, columnspan=2, sticky="ew", pady=(10, 18))
        ttk.Label(page, text="Rows · back to front").grid(
            row=12, column=0, columnspan=2, sticky="w"
        )
        ttk.Entry(page, textvariable=self.row_counts, width=15).grid(
            row=13, column=0, sticky="ew", pady=6
        )
        ttk.Button(page, text="Apply rows", command=self._guard(self.apply_rows)).grid(
            row=13, column=1, padx=(6, 0)
        )
        ttk.Button(
            page, text="Use automatic rows", command=self._guard(self.auto_rows)
        ).grid(row=14, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            page,
            text="Confirm original canvas & scale",
            command=self._guard(self.confirm_scale),
        ).grid(row=15, column=0, columnspan=2, sticky="ew", pady=(22, 6))
        ttk.Label(
            page,
            text="Use only when the team shares one fixed capture setup and extraction preserved the original canvas and scale.",
            style="Sub.TLabel",
            wraplength=265,
        ).grid(row=16, column=0, columnspan=2, sticky="w")
        page.columnconfigure(0, weight=1)

    def _athlete_tab(self):
        page = self._scroll_tab("Athlete")
        self.portrait = tk.Canvas(page, bg="#D8DDE4", height=340, highlightthickness=0)
        self.portrait.pack(fill="x")
        self.portrait.bind("<Configure>", lambda e: self._draw_portrait())
        self.portrait.bind("<ButtonPress-1>", self._portrait_press)
        self.portrait.bind("<B1-Motion>", self._portrait_motion)
        self.portrait.bind("<ButtonRelease-1>", self._portrait_release)
        modes = ttk.Frame(page)
        modes.pack(fill="x", pady=6)
        for mode in ("Head box", "Height top", "Feet"):
            ttk.Radiobutton(modes, text=mode, value=mode, variable=self.mode).pack(
                side="left", padx=(0, 7)
            )
        ttk.Label(
            page,
            text="Drag a head box including hair/bow. Mark anatomical head top and feet above the reflection for height order.",
            style="Sub.TLabel",
            wraplength=275,
        ).pack(anchor="w", pady=(0, 7))
        ttk.Button(
            page, text="Confirm shown head box", command=self._guard(self.confirm_head)
        ).pack(fill="x")
        controls = ttk.Frame(page)
        controls.pack(fill="x", pady=6)
        for text_, action in (
            ("←", lambda: self.move_slot(-1)),
            ("→", lambda: self.move_slot(1)),
            ("Taller", lambda: self.rank(True)),
            ("Shorter", lambda: self.rank(False)),
        ):
            ttk.Button(controls, text=text_, command=self._guard(action), width=6).pack(
                side="left", padx=(0, 3)
            )
        rows = ttk.Frame(page)
        rows.pack(fill="x", pady=(0, 6))
        ttk.Label(rows, text="Move to row").pack(side="left")
        ttk.Spinbox(rows, textvariable=self.target_row, from_=1, to=100, width=4).pack(
            side="left", padx=6
        )
        ttk.Button(rows, text="Move", command=self._guard(self.move_row)).pack(
            side="left"
        )
        ttk.Button(
            page, text="Exclude / restore athlete", command=self._guard(self.exclude)
        ).pack(fill="x")
        ttk.Label(
            page, textvariable=self.athlete_status, wraplength=275, justify="left"
        ).pack(anchor="w", pady=10)

    def fit_largest(self):
        if not self.session or not self.session.job["teams"]:
            return
        self.stop_monitor()
        job = self.session.job
        job["base_scale"] = None
        self.session.settings({"group_scale": 1.0})
        self.session.select_team(max(range(len(job["teams"])), key=lambda i: len(job["teams"][i]["manifest"]["athletes"])))
        self.teams.selection_set(str(job["selected_team"]))
        self._load_team()
        self._autosave()

    def show_review(self):
        if str(self.tabs) not in self.main.panes():
            self.main.add(self.tabs, weight=0)
        self.tabs.select(1)
        self.preview_tabs.select(1)

    def _update_setup_summary(self):
        if not self.session:
            return
        job = self.session.job
        templates = self.view.template_names if self.view else ""
        rows = Path(job["row_csv"]).name if job.get("row_csv") else "Built-in team row CSV"
        self.setup_summary.set(f"Incoming: {job['incoming']}  ·  {rows}\nTemplates: {templates or job['templates']}  ·  Settings apply to the whole job")

    def _autosave(self):
        if not self.session:
            return
        digest = fingerprint(self.session.job)
        if digest == self.saved_fingerprint:
            self.session.dirty = False
            return
        if self.store.path is None:
            path = self.data_dir / "Jobs" / (uuid4().hex + ".json")
            self.store.save(self.session.job, path)
        else:
            self.store.save(self.session.job)
        self.saved_fingerprint = digest
        self.session.dirty = False
        self._remember()

    def job_setup(self):
        if not self.session:
            self.choose_team_folder()
        if not self.session:
            return
        self.stop_monitor()
        self._flush_live()
        job = self.session.job
        dialog = tk.Toplevel(self.root)
        dialog.title(f"TeamBuilder {__version__} · Job setup")
        dialog.transient(self.root)
        page = ttk.Frame(dialog, padding=20)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="Set up once. Process every team automatically.", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
        variables = {}
        fields = (
            ("incoming", "Incoming hotfolder", False),
            ("templates", "Templates folder", False),
            ("background", "Default background JPG", True),
            ("foreground", "Default foreground PNG", True),
            ("row_csv", "Team row CSV (blank = built-in)", True),
            ("output", "Completed panoramas", False),
            ("upload", "Upload folder (optional)", False),
            ("samples", "Sample-print folder (optional)", False),
        )
        routes = job.get("routes", [])
        for index, (key, label, is_file) in enumerate(fields, 1):
            default = (routes[0] if routes else "") if key == "upload" else ((routes[1] if len(routes) > 1 else "") if key == "samples" else job.get(key, ""))
            variable = variables[key] = tk.StringVar(dialog, default)
            ttk.Label(page, text=label).grid(row=index, column=0, sticky="w", pady=5)
            ttk.Entry(page, textvariable=variable, width=56).grid(row=index, column=1, sticky="ew", padx=10)
            def browse(k=key, file=is_file):
                if file:
                    types = [("CSV", "*.csv")] if k == "row_csv" else ([("JPEG", "*.jpg *.jpeg")] if k == "background" else [("PNG", "*.png")])
                    chosen = filedialog.askopenfilename(parent=dialog, title="Select " + k.replace("_", " "), filetypes=types)
                else:
                    chosen = filedialog.askdirectory(parent=dialog, title="Select " + k.replace("_", " "))
                if chosen:
                    variables[k].set(chosen)
                    if k == "templates":
                        variables["background"].set("")
                        variables["foreground"].set("")
                        try:
                            from .templates import catalog
                            pair = catalog(Path(chosen)).get("default", {})
                            for role in ("background", "foreground"):
                                if role in pair:
                                    variables[role].set(str(pair[role]))
                        except (OSError, ValueError) as error:
                            feedback.set(str(error))
            ttk.Button(page, text="Choose…", command=browse).grid(row=index, column=2)
        feedback = tk.StringVar(dialog)
        ttk.Label(page, text="Automatic naming: Team Background_default.jpg + Team Foreground_default.png\nColor pairs: Team Background_Orange.jpg + Team Foreground_Orange.png (case-insensitive).\nColor comes from player filenames (_Orange, _Aqua, _Green…), not the team folder.\nYou can select default files directly above, regardless of their names.", style="Sub.TLabel", wraplength=760).grid(row=9, column=0, columnspan=3, sticky="w", pady=12)
        retained = tk.BooleanVar(dialog, job.get("retained_capture", True))
        marker = tk.BooleanVar(dialog, job.get("require_ready", False))
        quiet = tk.StringVar(dialog, str(job.get("settle_seconds", 20)))
        ttk.Checkbutton(page, text="Original canvas/scale retained; all athletes use the same fixed camera setup", variable=retained).grid(row=10, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(page, text="Require team.ready file after copying (strongest transfer protection)", variable=marker).grid(row=11, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Label(page, text="Wait after last folder change (seconds)").grid(row=12, column=0, sticky="w")
        ttk.Spinbox(page, textvariable=quiet, from_=2, to=3600, increment=1, width=8).grid(row=12, column=1, sticky="w", padx=10)
        ttk.Label(page, text="Athlete size, spacing and placement are shared across this job. A team that cannot fit is held for attention; it is never silently resized.", style="Sub.TLabel", wraplength=750).grid(row=13, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Label(page, textvariable=feedback, foreground="#92400E", wraplength=750).grid(row=14, column=0, columnspan=3, sticky="w")
        def save_setup():
            try:
                candidate = deepcopy(job)
                for key in ("incoming", "templates", "background", "foreground", "row_csv", "output"):
                    candidate[key] = variables[key].get().strip()
                if Path(candidate["incoming"]).resolve() != Path(job["incoming"]).resolve():
                    raise ValueError("Choose a different hotfolder from the main screen to start another job.")
                candidate["routes"] = list(dict.fromkeys(variables[k].get().strip() for k in ("upload", "samples") if variables[k].get().strip()))
                candidate["retained_capture"] = retained.get()
                candidate["require_ready"] = marker.get()
                candidate["settle_seconds"] = float(quiet.get())
                from .rows import RowTable
                if candidate["row_csv"]:
                    RowTable.load(Path(candidate["row_csv"]))
                from .templates import catalog
                if bool(candidate["background"]) != bool(candidate["foreground"]):
                    raise ValueError("Select both default files, or leave both blank to use automatic discovery.")
                if candidate["background"]:
                    from .rendering import TemplateCache
                    cache = TemplateCache()
                    try:
                        cache.load(Path(candidate["background"]), False, (900, 300))
                        cache.load(Path(candidate["foreground"]), True, (900, 300))
                    finally:
                        cache.close()
                else:
                    pairs = catalog(Path(candidate["templates"]))
                    if not any(set(pair) == {"background", "foreground"} for pair in pairs.values()):
                        raise ValueError("No complete template pairs found. Select the default files above or use the naming examples.")
                for team in candidate["teams"]:
                    team["templates"] = ""
                validate_job(candidate, Path.cwd())
                self.session.job = candidate
                self.session.touch()
                self._autosave()
                self._update_setup_summary()
                dialog.destroy()
                self.request_view()
            except (ValueError, OSError) as error:
                feedback.set(str(error))
        buttons = ttk.Frame(page)
        buttons.grid(row=15, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=8)
        ttk.Button(buttons, text="Save job setup", style="Primary.TButton", command=save_setup).pack(side="left")
        page.columnconfigure(1, weight=1)
        dialog.grab_set()

    def stop_monitor(self):
        self.monitoring = False
        self.production_cancel.set()
        self.monitor_caption.set("Start automatic processing")

    def toggle_monitor(self):
        if self.monitoring:
            self.stop_monitor()
            self.status.set("Paused. Finishing the current team." if self.production_busy else "Automatic processing paused.")
            return
        if not self.session:
            self.status.set("Choose an incoming hotfolder, then open Job setup.")
            return
        if self.production_busy:
            self.status.set("Finishing the current team before restarting.")
            return
        try:
            validate_job(self.session.job, Path.cwd())
            self._flush_live()
            self._autosave()
            # A worker is scoped to one incoming job and owns its durable ledger.
            identity = fingerprint(str(Path(self.session.job["incoming"]).resolve()))[:24]
            if self.production_worker:
                self.production_worker.close()
            state_dir = self.data_dir / "Production" / identity
            self.production_worker = LatestWorker(lambda path=state_dir: ProductionEngine(path))
            self.production_cancel = Event()
            self.monitoring = True
            self.monitor_caption.set("Pause automatic processing")
            self.status.set("Watching hotfolder. Stable teams will process and export automatically.")
        except (OSError, ValueError) as error:
            self.status.set(str(error))

    def rebuild_selected(self):
        if not self.session or not self.session.job["teams"]:
            return
        self.session.team["rebuild_nonce"] = self.session.team.get("rebuild_nonce", 0) + 1
        self.session.team.pop("production", None)
        self.session.touch()
        self._autosave()
        self.request_view(True)
        self.status.set("Team queued for a deliberate new version. Start automatic processing to render and deliver it.")

    def _production_poll(self):
        if self.closed:
            return
        if self.production_worker:
            try:
                while True:
                    _token, result, error = self.production_worker.results.get_nowait()
                    self.production_busy = False
                    if error:
                        self.stop_monitor()
                        self.status.set("Automatic processing paused: " + error)
                    elif result and self.session:
                        current = self.session.job
                        selected_source = (self.session.manifest["source_folder"] if current["teams"] else None)
                        old_selection = (deepcopy(self.session.manifest) if selected_source else None,
                                         current.get("base_scale"), current.get("capture_canvas"))
                        updated = result["job"]
                        for i, team in enumerate(updated["teams"]):
                            source = team["manifest"]["source_folder"]
                            if source == selected_source:
                                updated["selected_team"] = i
                            team["production"] = result["statuses"].get(str(Path(source).resolve()), {})
                            state = team["production"].get("state", "Queued")
                            values = (len(team["manifest"]["athletes"]), state)
                            if self.teams.exists(str(i)):
                                self.teams.item(str(i), text=team["manifest"]["team_name"], values=values)
                            else:
                                self.teams.insert("", "end", iid=str(i), text=team["manifest"]["team_name"], values=values)
                        self.session.job = updated
                        self.session.touch()
                        self._autosave()
                        self._update_setup_summary()
                        done = sum(t.get("production", {}).get("state") == "Completed" for t in updated["teams"])
                        held = sum(t.get("production", {}).get("state") == "Needs attention" for t in updated["teams"])
                        self.summary.set(f"{len(updated['teams'])} teams · {done} complete · {held} need attention")
                        if updated["teams"]:
                            self.teams.selection_set(str(updated["selected_team"]))
                            selection = (self.session.manifest, updated.get("base_scale"), updated.get("capture_canvas"))
                            if old_selection != selection or not self.view:
                                self._load_team()
                        if self.monitoring:
                            self.status.set(f"Watching hotfolder · {done} complete · {held} need attention")
                    if self.pending_close:
                        self.pending_close = False
                        self.close()
                        return
            except Empty:
                pass
            except (OSError, ValueError) as error:
                self.stop_monitor()
                self.status.set("Job could not be saved; automatic processing paused: " + str(error))
        if self.monitoring and not self.production_busy and not self.exporting:
            self.production_busy = True
            snapshot = self.session.snapshot()
            cancel = self.production_cancel
            self.production_worker.submit(1, lambda engine: engine.tick(snapshot, cancel))
        self.root.after(1000, self._production_poll)

    def _guard(self, callback):
        def guarded():
            if self.exporting or self.production_busy:
                self.status.set("Finishing the current team. Pause automatic processing before changing job settings.")
                return
            try:
                callback()
            except (ValueError, OSError, KeyError, StopIteration) as error:
                self.status.set(str(error))

        return guarded

    def _load_recent(self):
        try:
            self.recent_paths = json.loads((self.data_dir / "recent.json").read_text())[
                -12:
            ]
        except (OSError, ValueError):
            self.recent_paths = []
        self.job_box["values"] = [Path(p).stem for p in self.recent_paths]

    def _remember(self):
        if self.store.path:
            p = str(self.store.path)
            self.recent_paths = ([x for x in self.recent_paths if x != p] + [p])[-12:]
            (self.data_dir / "recent.json").write_text(json.dumps(self.recent_paths))
            self.job_box["values"] = [Path(x).stem for x in self.recent_paths]
            self.recent.set(self.store.path.stem)

    def _discard(self):
        self._flush_live()
        return (
            not self.session
            or not self.session.dirty
            or messagebox.askyesno(
                "Unsaved review changes",
                "Discard unsaved changes and continue?",
                parent=self.root,
            )
        )

    def _open_recent(self, event=None):
        if self.exporting or self.production_busy:
            return
        index = self.job_box.current()
        if index >= 0 and self._discard():
            self._guard(
                lambda: self.set_job(self.store.load(Path(self.recent_paths[index])))
            )()

    def choose_team_folder(self):
        path = filedialog.askdirectory(title="Choose incoming hotfolder (team folders go inside)", parent=self.root)
        if not path:
            return
        self._flush_live()
        selected = Path(path).resolve()
        same = self.session and not self.session.job.get("is_demo") and selected == Path(self.session.job["incoming"]).resolve()
        if same:
            self.request_view(True)
            return
        if self.session and self.session.dirty and not self._discard():
            return
        self.stop_monitor()
        self.store = JobStore()
        job = new_job(selected, allow_empty=True)
        if job["teams"]:
            job["selected_team"] = max(range(len(job["teams"])), key=lambda i: len(job["teams"][i]["manifest"]["athletes"]))
        self.set_job(job)
        self.session.dirty = True
        self._autosave()

    def new(self):
        if not self._discard():
            return
        path = filedialog.askdirectory(
            title="Choose incoming team folders", parent=self.root
        )
        if path:
            self.store = JobStore()
            self.set_job(new_job(Path(path), allow_empty=True))

    def demo(self):
        if self._discard():
            self.store = JobStore()
            self.set_job(make_demo(self.data_dir / "Demos" / uuid4().hex))

    def open(self):
        if not self._discard():
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Open saved TeamBuilder job",
            filetypes=[("TeamBuilder job", "*.json")],
        )
        if path:
            self.set_job(self.store.load(Path(path)))

    def save(self, copy=False):
        self._flush_live()
        if not self.session:
            return
        path = None if copy else self.store.path
        if path is None:
            chosen = filedialog.asksaveasfilename(
                parent=self.root,
                title="Save TeamBuilder job",
                defaultextension=".json",
                initialfile=self.session.job["name"]
                + (" copy" if copy else "")
                + ".json",
            )
            if not chosen:
                return
            path = Path(chosen)
        self.store.save(self.session.job, path)
        self.session.dirty = False
        self._remember()
        self.status.set("Job and all manual corrections saved.")

    def import_team(self):
        self._flush_live()
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Import verified team manifest",
            filetypes=[("Team manifest", "*.json")],
        )
        if path:
            job = import_manifest(
                self.session.job if self.session else None, Path(path)
            )
            self.set_job(job)
            self.session.dirty = True

    def set_job(self, job):
        if self.production_busy:
            raise ValueError("Pause and finish the current team before changing jobs.")
        self.saved_fingerprint = None
        self.stop_monitor()
        self._flush_live()
        self.token += 1
        self.session = ReviewSession(job)
        self.team_status = {}
        self.selected_id = None
        self._clear_view()
        self.teams.delete(*self.teams.get_children())
        for i, team in enumerate(job["teams"]):
            m = team["manifest"]
            self.teams.insert(
                "",
                "end",
                iid=str(i),
                text=m["team_name"],
                values=(
                    len(m["athletes"]),
                    "Exported" if team["exports"] else "Not checked",
                ),
            )
        self._update_setup_summary()
        if job["teams"]:
            self.teams.selection_set(str(job["selected_team"]))
            self._load_team()
        else:
            self.title.set("Waiting for team folders")
            self.athletes.delete(*self.athletes.get_children())
            self.athlete_count.set("0")
            self.distribution.set("—")

    def select_team(self, event=None):
        if self.exporting or not self.session:
            return
        selection = self.teams.selection()
        if selection and int(selection[0]) != self.session.job["selected_team"]:
            self._flush_live()
            self.session.select_team(int(selection[0]))
            self.selected_id = None
            self._clear_view()
            self._load_team()

    def _load_team(self):
        if not self.session.job["teams"]:
            return
        self.title.set(self.session.manifest["team_name"])
        values = asdict(CompositionSettings()) | self.session.manifest.get("settings", {}) | self.session.job.get("job_settings", {})
        self.loading_form = True
        try:
            for key, var in self.values.items():
                var.set(f"{values[key] * (100 if key == 'group_scale' else 1):g}")
            self._refresh_readouts()
            self._sync_rows()
        finally:
            self.loading_form = False
        self.selected_id = self.session.manifest["athletes"][0]["id"]
        self.athletes.delete(*self.athletes.get_children())
        for entry in self.session.manifest["athletes"]:
            self.athletes.insert("", "end", iid=entry["id"],
                                 values=(entry["file"], "—", "Waiting for analysis"))
        self.request_view()

    def choose_templates(self):
        self._flush_live()
        if not self.session:
            return
        path = filedialog.askdirectory(
            parent=self.root, title="Choose job template folder"
        )
        if path:
            self.session.job["templates"] = path
            self.session.job.pop("background", None)
            self.session.job.pop("foreground", None)
            for team in self.session.job["teams"]:
                team["templates"] = ""
            preset = self.session.job["template_presets"].get(path)
            if preset:
                self.session.manifest["settings"] = deepcopy(preset)
            self.session.touch()
            self._load_team()

    def choose_output(self):
        if self.session:
            path = filedialog.askdirectory(
                parent=self.root, title="Choose completed-image destination"
            )
            if path:
                from .review import validate_job

                candidate = self.session.snapshot()
                candidate["output"] = path
                validate_job(candidate, Path.cwd())
                self.session.job["output"] = path
                self.session.touch()
                self.status.set(
                    "Completed-image destination updated; save the job to retain it."
                )

    def choose_rows(self):
        if self.session:
            path = filedialog.askopenfilename(
                parent=self.root,
                title="Choose row-count CSV",
                filetypes=[("CSV", "*.csv")],
            )
            if path:
                from .rows import RowTable

                RowTable.load(Path(path))
                self.session.job["row_csv"] = path
                self.session.touch()
                self.request_view()

    def apply_settings(self):
        if self.session:
            values = {
                k: float(v.get()) / (100 if k == "group_scale" else 1)
                for k, v in self.values.items()
            }
            self.session.settings(values)
            self._autosave()
            self._refresh_readouts()
            self.request_view()

    def apply_rows(self):
        if self.session:
            self._adopt_ranks()
            self.session.manual_rows(
                tuple(int(v.strip()) for v in self.row_counts.get().split(","))
            )
            self.request_view()

    def auto_rows(self):
        if self.session:
            self.session.request_rows(None)
            self._sync_rows()
            self.request_view()

    def confirm_scale(self):
        if self.session:
            self.session.confirm_scale()
            self.request_view()

    def _adopt_ranks(self):
        if not self.session or not self.view:
            return
        versions = self.session.team.setdefault("review_hashes", {})
        for record in self.view.analysis.report["athletes"]:
            capture = record.get("capture", {})
            if capture.get("relative_height") is not None:
                entry = self.session.entry(record["id"])
                if entry["capture"].get("relative_height") is None:
                    entry["capture"].update(capture)
                if record.get("sha256"):
                    versions[record["id"]] = record["sha256"]

    def _correct_head(self, identity, box):
        self._adopt_ranks()
        self.session.correct_head(identity, box)

    def confirm_head(self):
        if self.session and self.selected_id:
            record = self._record()
            box = self.session.entry(self.selected_id).get("manual_head") or (
                list(record["head"].values()) if record.get("head") else None
            )
            if box is None:
                raise ValueError("Draw a head box on the athlete preview first.")
            self._correct_head(self.selected_id, box)
            self.request_view()

    def _rows(self):
        if not self.view or not self.view.analysis.layout:
            raise ValueError(
                "Resolve head and height review items before rearranging rows."
            )
        return rows_from_layout(self.view.analysis.layout)

    def move_slot(self, direction):
        if not self.session or not self.selected_id:
            return
        self.session.move(self.selected_id, self._rows(), direction=direction)
        self.request_view()

    def move_row(self):
        if not self.session or not self.selected_id:
            return
        self.session.move(
            self.selected_id, self._rows(), target_row=int(self.target_row.get()) - 1
        )
        self.request_view()

    def rank(self, taller):
        self._adopt_ranks()
        if not self.session or not self.selected_id:
            return
        self.session.rank(self.selected_id, taller)
        self.request_view()

    def exclude(self):
        if self.session and self.selected_id:
            self.session.exclude(self.selected_id)
            self.request_view()

    def select_athlete(self, event=None):
        selected = self.athletes.selection()
        if not self.exporting and selected and selected[0] != self.selected_id:
            self.selected_id = selected[0]
            self.show_review()
            self.tabs.select(1)
            self.request_view()

    def request_view(self, refresh=False):
        if not self.session or not self.session.job["teams"] or self.exporting:
            return
        self.token += 1
        token = self.token
        job, selected = self.session.snapshot(), self.selected_id
        self.export_button.state(["disabled"])
        self.progress.start(12)
        self.status.set(
            "Analyzing sources…"
            if refresh or self.view is None
            else "Updating preview…"
        )
        self.worker.submit(
            (token, "view"), lambda engine: engine.view(job, selected, refresh)
        )

    def _clear_view(self):
        if self.view:
            self.view.close()
        self.view = None
        self.export_button.state(["disabled"])
        self._draw_panorama()
        self._draw_portrait()

    def _record(self):
        if self.view:
            return next(
                (
                    r
                    for r in self.view.analysis.report["athletes"]
                    if r["id"] == self.selected_id
                ),
                {},
            )
        return {}

    def _drain(self):
        if self.closed:
            return
        try:
            while True:
                token, value, error = self.worker.results.get_nowait()
                if token == -1:
                    self.status.set(error)
                    self.progress.stop()
                    continue
                if token[0] != self.token:
                    if hasattr(value, "close"):
                        value.close()
                    continue
                self.progress.stop()
                if token[1] == "export":
                    self.exporting = False
                    self.analyze_button.state(["!disabled"])
                    if error:
                        self.status.set("Export failed: " + error)
                        self.export_button.state(["disabled"])
                    else:
                        self.session.team["exports"].append(
                            {"file": str(self.export_destination), "result": value}
                        )
                        self.session.touch()
                        self.status.set("Exported " + str(self.export_destination))
                        self.export_button.state(["!disabled"])
                        self._fill_lists()
                    if self.pending_close:
                        self.pending_close = False
                        self.close()
                elif error:
                    self.status.set(error)
                    self._clear_view()
                else:
                    if self.view:
                        self.view.close()
                    self.view = value
                    self._update_setup_summary()
                    if value.analysis.layout and self.session.job.get("base_scale") is None and not self.session.job.get("is_demo") and not self.production_busy and not self.monitoring:
                        factor = self.session.job.get("job_settings", {}).get("group_scale", 1.0)
                        self.session.job["base_scale"] = value.analysis.layout.global_scale / factor * 0.85
                        self.session.job["capture_canvas"] = value.analysis.report.get("capture_canvas")
                        self.session.touch()
                        self._autosave()
                        self.request_view()
                        continue
                    self._fill_lists()
                    self._draw_panorama()
                    self._draw_portrait()
                    if (
                        value.analysis.layout
                        and self.live_timer is None
                        and value.templates_ready
                        and not any(
                            "outside athlete placement area" in i
                            for i in value.analysis.layout.issues
                        )
                    ):
                        self.export_button.state(["!disabled"])
                    issues = value.analysis.report["issues"]
                    self.status.set(
                        "Review required" if issues else "Preview ready for review"
                    )
        except Empty:
            pass
        if not self.closed:
            self.root.after(80, self._drain)

    def _fill_lists(self):
        report = (
            self.view.analysis.report if self.view else {"athletes": [], "issues": []}
        )
        row_map = (
            {p.athlete_id: p.row + 1 for p in self.view.analysis.layout.placements}
            if self.view and self.view.analysis.layout
            else {}
        )
        self.athletes.delete(*self.athletes.get_children())
        for r in report["athletes"]:
            label = (
                "Excluded"
                if r["status"] == "excluded"
                else (r["issues"][0] if r["issues"] else "Automatic detection OK")
            )
            self.athletes.insert(
                "",
                "end",
                iid=r["id"],
                values=(r["file"], row_map.get(r["id"], "—"), label),
            )
        if self.selected_id and self.athletes.exists(self.selected_id):
            self.athletes.selection_set(self.selected_id)
        active = sum(
            not e.get("excluded", False) for e in self.session.manifest["athletes"]
        )
        self.title.set(f"{self.session.manifest['team_name']}  ·  {active} athletes")
        self.teams.item(
            str(self.session.job["selected_team"]),
            values=(
                active,
                self.session.team.get("production", {}).get("state") or (
                    "Needs attention" if report["issues"] or (self.view and not self.view.templates_ready)
                    else ("Exported" if self.session.team["exports"] else "Ready")),
            ),
        )
        exported = sum(bool(t["exports"]) for t in self.session.job["teams"])
        self.summary.set(
            f"{len(self.session.job['teams'])} teams · {exported} exported"
        )
        layout = self.view.analysis.layout if self.view else None
        if layout:
            self.row_counts.set(", ".join(map(str, layout.row_counts)))
        else:
            from .rows import RowTable

            if active:
                self.row_counts.set(
                    ", ".join(
                        map(
                            str,
                            RowTable.load(
                                Path(self.session.job["row_csv"])
                                if self.session.job["row_csv"]
                                else Path(__file__).parent / "data/row_counts.csv"
                            ).for_count(active),
                        )
                    )
                )
        problems = [i for r in report["athletes"] for i in r["issues"]] + report[
            "issues"
        ]
        if self.view and not self.view.templates_ready:
            problems.insert(
                0, self.view.template_issue + " Open Job setup to fix templates."
            )
        production = self.session.team.get("production", {})
        if production.get("message"):
            problems.insert(0, production["message"])
        if self.view and self.view.draft:
            problems.insert(0, "Showing all source images while detection issues are resolved. Use Sources & issues for the specific error.")
        self.details.set(
            "\n".join(problems[:2])
            + (
                f"\n{len(problems) - 2} more review items; select athletes for details."
                if len(problems) > 2
                else ""
            )
        )
        self._sync_rows(layout.row_counts if layout else None)
        record = self._record()
        anchors = self.session.team["anchors"].get(self.selected_id, {})
        height = (
            self.session.entry(self.selected_id)["capture"].get("relative_height")
            if self.selected_id
            else None
        )
        height = height or record.get("capture", {}).get("relative_height")
        state = [
            "Head: "
            + (
                "confirmed"
                if self.selected_id
                and self.session.entry(self.selected_id).get("manual_head")
                else ("detected automatically" if record.get("head") else "needs attention")
            ),
            "Height top: " + ("set" if "height_top" in anchors else "not set"),
            "Feet: " + ("set" if "floor_y" in anchors else "not set"),
            "Height order: " + ("available" if height else "needs measurement"),
        ]
        self.athlete_status.set("\n".join(state + record.get("issues", [])))

    @staticmethod
    def _fit(image, canvas):
        w, h = max(1, canvas.winfo_width() - 20), max(1, canvas.winfo_height() - 20)
        ratio = min(w / image.width, h / image.height)
        size = max(1, round(image.width * ratio)), max(1, round(image.height * ratio))
        return (
            ratio,
            size,
            (canvas.winfo_width() - size[0]) / 2,
            (canvas.winfo_height() - size[1]) / 2,
        )

    def _draw_panorama(self):
        self.canvas.delete("all")
        self.panorama_map = None
        if not self.view or self.view.panorama is None:
            self.canvas.create_text(
                max(10, self.canvas.winfo_width() / 2),
                max(10, self.canvas.winfo_height() / 2),
                text=("Team preview\nAnalyze the selected team, then resolve source review items."
                      if self.session else "Choose team folder…\nSelect athlete PNGs or a folder containing teams."),
                fill="white",
                justify="center",
                width=500,
            )
            return
        image = self.view.panorama
        ratio, size, x, y = self._fit(image, self.canvas)
        with image.resize(size, Image.Resampling.LANCZOS) as resized:
            self.photos["panorama"] = ImageTk.PhotoImage(resized, master=self.root)
        self.canvas.create_image(x, y, anchor="nw", image=self.photos["panorama"])
        if self.view.analysis.layout is None:
            return
        scale = ratio * 0.2
        self.panorama_map = (x, y, scale)
        area = CompositionSettings(**self.session.manifest.get("settings", {})).area
        self.canvas.create_rectangle(
            x + area.left * scale,
            y + area.top * scale,
            x + area.right * scale,
            y + area.bottom * scale,
            outline=SKY,
            dash=(5, 4),
        )
        for p in self.view.analysis.layout.placements:
            if p.athlete_id == self.selected_id:
                b = p.head
                self.canvas.create_rectangle(
                    x + b.left * scale,
                    y + b.top * scale,
                    x + b.right * scale,
                    y + b.bottom * scale,
                    outline=ORANGE,
                    width=2,
                )

    def _draw_portrait(self):
        self.portrait.delete("all")
        self.portrait_map = None
        if not self.view or self.view.portrait is None:
            return
        ratio, size, x, y = self._fit(self.view.portrait, self.portrait)
        with self.view.portrait.resize(size, Image.Resampling.LANCZOS) as resized:
            self.photos["portrait"] = ImageTk.PhotoImage(resized, master=self.root)
        self.portrait.create_image(x, y, anchor="nw", image=self.photos["portrait"])
        sx, sy = (
            size[0] / self.view.portrait_size[0],
            size[1] / self.view.portrait_size[1],
        )
        self.portrait_map = (x, y, sx, sy)
        entry = self.session.entry(self.selected_id)
        record = self._record()
        box = entry.get("manual_head") or (
            list(record["head"].values()) if record.get("head") else None
        )
        if box:
            l, t, r, b = box
            self.portrait.create_rectangle(
                x + l * sx, y + t * sy, x + r * sx, y + b * sy, outline=ORANGE, width=2
            )
        for kind, anchor in (
            self.session.team["anchors"].get(self.selected_id, {}).items()
        ):
            py = y + anchor * sy
            self.portrait.create_line(
                x,
                py,
                x + size[0],
                py,
                fill=ROYAL if kind == "floor_y" else "#047857",
                width=2,
            )

    def _source_point(self, event):
        if not self.portrait_map or not self.view:
            return None
        x, y, sx, sy = self.portrait_map
        px, py = (event.x - x) / sx, (event.y - y) / sy
        if (
            0 <= px <= self.view.portrait_size[0]
            and 0 <= py <= self.view.portrait_size[1]
        ):
            return px, py
        return None

    def _portrait_press(self, event):
        if self.exporting:
            return
        point = self._source_point(event)
        if point:
            if self.mode.get() == "Head box":
                self.drag_start = point
            else:
                kind = "floor_y" if self.mode.get() == "Feet" else "height_top"
                self._guard(
                    lambda: self.session.anchor(self.selected_id, kind, point[1])
                )()
                self.request_view()

    def _portrait_motion(self, event):
        if self.drag_start and self.portrait_map and self.mode.get() == "Head box":
            self.portrait.delete("drag")
            x, y, sx, sy = self.portrait_map
            self.portrait.create_rectangle(
                x + self.drag_start[0] * sx,
                y + self.drag_start[1] * sy,
                event.x,
                event.y,
                outline=ORANGE,
                width=2,
                tags="drag",
            )

    def _portrait_release(self, event):
        end = self._source_point(event)
        if (
            self.drag_start
            and end
            and self.mode.get() == "Head box"
            and not self.exporting
        ):
            a, b = self.drag_start, end
            box = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
            self._guard(lambda: self._correct_head(self.selected_id, box))()
            self.request_view()
        self.drag_start = None

    def _group_press(self, event):
        self.drag_start = None
        if self.exporting or self.production_busy or self.monitoring or not self.panorama_map:
            return
        x, y, scale = self.panorama_map
        px, py = (event.x - x) / scale, (event.y - y) / scale
        for p in reversed(self.view.analysis.layout.placements):
            if p.head.left <= px <= p.head.right and p.head.top <= py <= p.head.bottom:
                self.selected_id = p.athlete_id
                self.athletes.selection_set(p.athlete_id)
                self.drag_start = (event.x, event.y)
                self._draw_panorama()
                break

    def _group_release(self, event):
        if self.drag_start and self.panorama_map and not self.exporting:
            scale = self.panorama_map[2]
            previous = (
                self.session.manifest.get("corrections", {})
                .get("offsets_in", {})
                .get(self.selected_id, [0, 0])
            )
            dx, dy = (
                (event.x - self.drag_start[0]) / (scale * PPI),
                (event.y - self.drag_start[1]) / (scale * PPI),
            )
            self.session.offset(
                self.selected_id,
                round((previous[0] + dx) * 8) / 8,
                round((previous[1] + dy) * 8) / 8,
            )
            self.request_view()
        self.drag_start = None

    def export(self):
        if self._flush_live():
            return
        if (
            self.exporting
            or self.production_busy
            or self.monitoring
            or not self.view
            or not self.view.analysis.layout
            or not self.view.templates_ready
        ):
            return
        if self.view.analysis.report["issues"] and not messagebox.askyesno(
            "Review layout warnings",
            "This layout has visibility or placement warnings. Export the reviewed image anyway?",
            parent=self.root,
        ):
            return
        if not self.session:
            return
        name = (
            re.sub(r"[^\w .-]+", "_", self.session.manifest["team_name"]).strip(" .")
            or "Team"
        )
        folder = Path(self.session.job["output"]) / name
        number = 1
        while (folder / f"{name}_team_{number:04}.jpg").exists():
            number += 1
        self.export_destination = folder / f"{name}_team_{number:04}.jpg"
        snapshot = self.session.snapshot()
        signature = self.view.review_signature
        self.token += 1
        self.exporting = True
        self.export_button.state(["disabled"])
        self.analyze_button.state(["disabled"])
        self.progress.start(12)
        self.status.set("Rendering and verifying the full 26 × 8-inch JPEG…")
        destination = self.export_destination
        self.worker.submit(
            (self.token, "export"),
            lambda engine: engine.export(snapshot, signature, destination),
        )

    def close(self):
        if self.exporting or self.production_busy:
            self.stop_monitor()
            self.pending_close = True
            self.status.set("Finishing the current export before closing…")
            return
        self._flush_live()
        if self.session and self.session.dirty:
            answer = messagebox.askyesnocancel(
                "Save review changes", "Save this job before closing?", parent=self.root
            )
            if answer is None:
                return
            if answer:
                self._guard(self.save)()
                if self.session.dirty:
                    return
        self.stop_monitor()
        self.closed = True
        if self.production_worker:
            self.production_worker.close()
        self.worker.close()
        if self.view:
            self.view.close()
        # Destroy UI objects only on the main thread and remove pending callbacks.
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(callback)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--job", type=Path)
    mode.add_argument("--demo", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = tk.Tk()
    demo = make_demo(support_dir() / "Demos" / uuid4().hex) if args.demo else None
    app = TeamBuilderApp(root, job=demo)
    if args.smoke_test:
        from .detection import VisionDetector
        from .rows import RowTable

        RowTable.load(Path(__file__).parent / "data/row_counts.csv")
        if getattr(sys, "frozen", False) and not VisionDetector().helper.is_file():
            raise RuntimeError("Packaged native detector is missing.")
        root.withdraw()
        root.after(500, app.close)
    if args.job:
        try:
            app.set_job(app.store.load(args.job))
            app._remember()
        except (OSError, ValueError, KeyError) as error:
            app.status.set(str(error))
    root.mainloop()


if __name__ == "__main__":
    main()
