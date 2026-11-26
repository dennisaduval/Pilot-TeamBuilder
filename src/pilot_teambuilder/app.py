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
from tkinter import filedialog, font, messagebox, ttk
from uuid import uuid4

from PIL import Image, ImageTk

from .desktop_engine import DesktopEngine, LatestWorker
from .model import PPI, CompositionSettings
from .review import JobStore, ReviewSession, import_manifest, new_job, rows_from_layout

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
        self.worker = LatestWorker(
            lambda: DesktopEngine(self.data_dir / "detections.sqlite3")
        )
        self.token = 0
        self.view = None
        self.selected_id = None
        self.exporting = False
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
        self.title = tk.StringVar(root, "Choose a job to begin")
        self.status = tk.StringVar(
            root, "Open a saved job or choose your incoming team folders."
        )
        self.summary = tk.StringVar(root, "0 teams · 0 exported")
        self.details = tk.StringVar(root, "")
        self.athlete_status = tk.StringVar(
            root, "Select an athlete to review their head and height anchors."
        )
        self.mode = tk.StringVar(root, "Head box")
        self.row_counts = tk.StringVar(root)
        self.target_row = tk.StringVar(root, "1")
        self.values = {}
        self._theme()
        self._build()
        self.root.title("Pilot TeamBuilder · VolumePilot")
        width = max(1240, min(1520, root.winfo_screenwidth() - 40))
        height = max(760, min(960, root.winfo_screenheight() - 80))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(1240, 760)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(80, self._drain)
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
        style.map("Export.TButton", background=[("active", "#FB923C")])
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
            bar, text="  /  TeamBuilder", bg=NAVY, fg="white", font=(self.family, 15)
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
        ttk.Label(actions, text="Job").pack(side="left", padx=(0, 8))
        self.job_box = ttk.Combobox(
            actions, textvariable=self.recent, state="readonly", width=24
        )
        self.job_box.pack(side="left")
        self.job_box.bind("<<ComboboxSelected>>", self._open_recent)
        for text_, command in (
            ("New job", self.new),
            ("Demo", self.demo),
            ("Open…", self.open),
            ("Import team…", self.import_team),
            ("Save", self.save),
            ("Templates…", self.choose_templates),
            ("Output…", self.choose_output),
            ("Row CSV…", self.choose_rows),
        ):
            if text_ == "Save":
                button = ttk.Menubutton(actions, text="Save ▾")
                menu = tk.Menu(button, tearoff=False)
                menu.add_command(label="Save", command=self._guard(self.save))
                menu.add_command(
                    label="Save a copy…", command=self._guard(lambda: self.save(True))
                )
                button.configure(menu=menu)
            else:
                button = ttk.Button(actions, text=text_, command=self._guard(command))
            button.pack(side="left", padx=(7, 0))
        self.analyze_button = ttk.Button(
            actions,
            text="Analyze / recheck",
            style="Primary.TButton",
            command=lambda: self.request_view(True),
        )
        self.analyze_button.pack(side="right")
        main = ttk.Panedwindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=16)
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
        ttk.Label(center, text="ATHLETES", font=(self.family, 10, "bold")).pack(
            anchor="w", pady=(0, 6)
        )
        self.athletes = ttk.Treeview(
            center,
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
        self.athletes.pack(fill="x")
        self.athletes.bind("<<TreeviewSelect>>", self.select_athlete)
        self.tabs = ttk.Notebook(main, width=300)
        main.add(self.tabs, weight=0)
        self._composition_tab()
        self._athlete_tab()
        footer = ttk.Frame(self.root, padding=(16, 10))
        footer.pack(fill="x")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=110)
        self.progress.pack(side="left", padx=(0, 12))
        ttk.Label(footer, textvariable=self.status, wraplength=950).pack(
            side="left", fill="x", expand=True
        )
        self.export_button = ttk.Button(
            footer,
            text="Approve & export JPEG",
            style="Export.TButton",
            command=self.export,
            state="disabled",
        )
        self.export_button.pack(side="right", padx=(12, 0))

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

    def _composition_tab(self):
        page = self._scroll_tab("Composition")
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
            variable = self.values[key] = tk.StringVar(self.root)
            spin = ttk.Spinbox(
                page,
                textvariable=variable,
                from_=-100,
                to=100,
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

    def _guard(self, callback):
        def guarded():
            if self.exporting:
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
        if self.exporting:
            return
        index = self.job_box.current()
        if index >= 0 and self._discard():
            self._guard(
                lambda: self.set_job(self.store.load(Path(self.recent_paths[index])))
            )()

    def new(self):
        if not self._discard():
            return
        path = filedialog.askdirectory(
            title="Choose incoming team folders", parent=self.root
        )
        if path:
            self.store = JobStore()
            self.set_job(new_job(Path(path)))

    def demo(self):
        if self._discard():
            self.store = JobStore()
            self.set_job(make_demo(self.data_dir / "Demos" / uuid4().hex))

    def open(self):
        if not self._discard():
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Open TeamBuilder job",
            filetypes=[("TeamBuilder job", "*.json")],
        )
        if path:
            self.set_job(self.store.load(Path(path)))

    def save(self, copy=False):
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
        self.teams.selection_set(str(job["selected_team"]))
        self._load_team()

    def select_team(self, event=None):
        if self.exporting or not self.session:
            return
        selection = self.teams.selection()
        if selection and int(selection[0]) != self.session.job["selected_team"]:
            self.session.select_team(int(selection[0]))
            self.selected_id = None
            self._clear_view()
            self._load_team()

    def _load_team(self):
        self.title.set(self.session.manifest["team_name"])
        values = asdict(CompositionSettings()) | self.session.manifest.get(
            "settings", {}
        )
        for key, var in self.values.items():
            var.set(f"{values[key] * (100 if key == 'group_scale' else 1):g}")
        self.selected_id = self.session.manifest["athletes"][0]["id"]
        self.request_view()

    def choose_templates(self):
        if not self.session:
            return
        path = filedialog.askdirectory(
            parent=self.root, title="Choose this team's template folder"
        )
        if path:
            self.session.team["templates"] = path
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
            self.request_view()

    def apply_rows(self):
        if self.session:
            self.session.manual_rows(
                tuple(int(v.strip()) for v in self.row_counts.get().split(","))
            )
            self.request_view()

    def auto_rows(self):
        if self.session:
            self.session.manifest.setdefault("corrections", {}).pop("rows", None)
            self.session.touch()
            self.request_view()

    def confirm_scale(self):
        if self.session:
            self.session.confirm_scale()
            self.request_view()

    def confirm_head(self):
        if self.session and self.selected_id:
            record = self._record()
            box = self.session.entry(self.selected_id).get("manual_head") or (
                list(record["head"].values()) if record.get("head") else None
            )
            if box is None:
                raise ValueError("Draw a head box on the athlete preview first.")
            self.session.correct_head(self.selected_id, box)
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
            self.tabs.select(1)
            self.request_view()

    def request_view(self, refresh=False):
        if not self.session or self.exporting:
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
                    self._fill_lists()
                    self._draw_panorama()
                    self._draw_portrait()
                    if (
                        value.analysis.layout
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
                else ("Needs review" if r["issues"] else "Ready")
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
                "Review"
                if report["issues"] or (self.view and not self.view.templates_ready)
                else ("Exported" if self.session.team["exports"] else "Ready"),
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
                0, "Template preview only — choose valid templates before export."
            )
        self.details.set(
            "\n".join(problems[:2])
            + (
                f"\n{len(problems) - 2} more review items; select athletes for details."
                if len(problems) > 2
                else ""
            )
        )
        record = self._record()
        anchors = self.session.team["anchors"].get(self.selected_id, {})
        height = (
            self.session.entry(self.selected_id)["capture"].get("relative_height")
            if self.selected_id
            else None
        )
        state = [
            "Head: "
            + (
                "confirmed"
                if self.selected_id
                and self.session.entry(self.selected_id).get("manual_head")
                else "not confirmed"
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
                text="Team preview\nAnalyze a team, then resolve source review items.",
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
            self._guard(lambda: self.session.correct_head(self.selected_id, box))()
            self.request_view()
        self.drag_start = None

    def _group_press(self, event):
        self.drag_start = None
        if self.exporting or not self.panorama_map:
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
        if (
            self.exporting
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
        if self.exporting:
            self.pending_close = True
            self.status.set("Finishing the current export before closing…")
            return
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
        self.closed = True
        self.worker.close()
        if self.view:
            self.view.close()
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
