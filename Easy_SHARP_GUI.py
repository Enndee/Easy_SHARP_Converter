import sys
import os

if getattr(sys, "frozen", False):
    _mp = sys._MEIPASS
    os.environ["TCL_LIBRARY"] = os.path.join(_mp, "tcl")
    os.environ["TK_LIBRARY"] = os.path.join(_mp, "tk")

import collections
import concurrent.futures
import json
import math
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from PIL import Image, ImageDraw, ImageOps, ImageTk, ExifTags, TiffImagePlugin
from plyfile import PlyData, PlyElement


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SPLAT_EXTENSIONS = {".ply", ".spx", ".spz", ".sog"}
CONDA_ENV_NAME = "sharp"

BG = "#101418"
BG2 = "#171c22"
BG3 = "#1d242c"
BG4 = "#2a333d"
ACCENT = "#78b7c9"
ACCENT_DIM = "#4f8594"
FG = "#edf3f7"
FG_DIM = "#9aa7b4"
ERR = "#ff7f7f"
WARN = "#d7b36a"
CARD_BG = "#202830"
CARD_BORDER = "#313c47"
CARD_HOVER = "#27323b"
CARD_SELECTED = "#273942"
HEADER_BG = "#0b0f13"
HEADER_FG = "#f5f8fb"
HEADER_MUTED = "#a3b0bb"
INPUT_BORDER = "#3a4753"
LOG_BG = "#11171c"

CARD_WIDTH = 176
CARD_HEIGHT = 236
THUMB_SIZE = (148, 148)
THUMB_CACHE_LIMIT = 200
IMAGE_PREVIEW_BG = "#23342c"
SPLAT_PREVIEW_BG = "#223341"

if getattr(sys, "frozen", False):
    EXE_DIR = os.path.dirname(sys.executable)
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_PATH = os.path.join(EXE_DIR, "easy_sharp_settings.json")

TAGS_BY_NAME = {name: tag for tag, name in ExifTags.TAGS.items()}
TAG_FOCAL = TAGS_BY_NAME.get("FocalLength")
TAG_FOCAL_35 = TAGS_BY_NAME.get("FocalLengthIn35mmFilm")
LANCZOS_FILTER = getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _rational_to_float(value):
    try:
        if isinstance(value, tuple) and len(value) == 2:
            return float(value[0]) / float(value[1]) if value[1] else None
        numerator = getattr(value, "numerator", None)
        denominator = getattr(value, "denominator", None)
        if numerator is not None and denominator is not None:
            return float(numerator) / float(denominator) if denominator else None
        return float(value)
    except Exception:
        return None


def _safe_float(text):
    text = str(text).strip()
    if not text:
        return None
    return float(text)


def _format_mb(file_path):
    try:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        return f"{size_mb:.2f} MB"
    except OSError:
        return ""


def _format_duration(seconds):
    return f"{seconds:.2f}s"


def _default_splat_viewer_path():
    candidates = [
        os.path.join(EXE_DIR, "splatapult", "build", "Release", "splatapult.exe"),
        os.path.join(EXE_DIR, "splatapult", "splatapult.exe"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return os.path.normpath(candidate)
    return ""


def _default_settings():
    return {
        "output_mode": "source",
        "output_folder": "",
        "workers": 2,
        "device": "cuda",
        "fallback_focal": "",
        "format": "ply",
        "quality": 5,
        "sh_degree": 0,
        "trans_x": "",
        "trans_y": "",
        "trans_z": "",
        "rot_x": "",
        "rot_y": "",
        "rot_z": "",
        "scale": "",
        "min_x": "",
        "max_x": "",
        "min_y": "",
        "max_y": "",
        "min_z": "",
        "max_z": "",
        "last_browse_folder": EXE_DIR,
        "gsbox_path": "",
        "splat_viewer_path": _default_splat_viewer_path(),
        "collapsed_sections": {},
    }


def load_settings():
    data = _default_settings()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data.update(loaded)
    except Exception:
        pass
    return data


def save_settings(updates):
    try:
        data = load_settings()
        data.update(updates)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception:
        pass


def _conda_candidates():
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    program_data = os.environ.get("ProgramData", "C:\\ProgramData")
    search_roots = [home, local, appdata, program_data, "C:\\", "D:\\"]
    names = [
        "anaconda3",
        "miniconda3",
        "Anaconda3",
        "Miniconda3",
        "anaconda",
        "miniconda",
        "Anaconda",
        "Miniconda",
    ]

    for root in search_roots:
        for name in names:
            yield os.path.join(root, name)

    for env_name in ("CONDA_PREFIX_1", "CONDA_PREFIX"):
        prefix = os.environ.get(env_name, "")
        if prefix:
            yield prefix
            yield os.path.dirname(prefix)

    try:
        result = subprocess.run(
            ["conda", "info", "--base"],
            capture_output=True,
            text=True,
            creationflags=_creationflags(),
            timeout=10,
        )
        base = result.stdout.strip()
        if base:
            yield base
    except Exception:
        pass


def find_conda_base():
    seen = set()
    for base in _conda_candidates():
        if not base:
            continue
        base = os.path.normpath(base)
        if base in seen or not os.path.isdir(base):
            continue
        seen.add(base)
        if (
            os.path.exists(os.path.join(base, "condabin", "conda.bat"))
            or os.path.exists(os.path.join(base, "Scripts", "conda.exe"))
            or os.path.exists(os.path.join(base, "bin", "conda"))
        ):
            return base
    return None


def find_sharp_exe():
    base = find_conda_base()
    if not base:
        return None
    candidate = os.path.join(base, "envs", CONDA_ENV_NAME, "Scripts", "sharp.exe")
    return candidate if os.path.exists(candidate) else None


def find_gsbox_exe(configured_path=""):
    candidates = []
    if configured_path:
        candidates.append(configured_path)
    candidates.append(os.path.join(EXE_DIR, "gsbox.exe"))
    candidates.append(os.path.join(os.path.dirname(EXE_DIR), "4DGS_SHARP_Pipeline", "gsbox.exe"))
    in_path = shutil.which("gsbox.exe") or shutil.which("gsbox")
    if in_path:
        candidates.append(in_path)
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return os.path.normpath(candidate)
    return None


def clean_ply_file(input_file, output_file):
    plydata = PlyData.read(input_file)
    PlyData([plydata["vertex"]], text=False).write(output_file)


def convert_to_ply(input_file, output_file, gsbox_exe, env=None, cwd=None):
    extension = os.path.splitext(input_file)[1].lower()
    if extension == ".ply":
        shutil.copy2(input_file, output_file)
        return

    command_map = {
        ".spx": "spx2ply",
        ".spz": "spz2ply",
        ".sog": "sog2ply",
    }
    command_name = command_map.get(extension)
    if not command_name:
        raise RuntimeError(f"Unsupported input format: {extension}")

    result = run_process([gsbox_exe, command_name, "-i", input_file, "-o", output_file], env=env, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"gsbox {command_name} failed").strip())


def crop_ply_file(input_file, output_file, bounds):
    min_x, max_x, min_y, max_y, min_z, max_z = bounds
    plydata = PlyData.read(input_file)
    vertex_data = plydata["vertex"].data
    mask = (
        (vertex_data["x"] >= min_x)
        & (vertex_data["x"] <= max_x)
        & (vertex_data["y"] >= min_y)
        & (vertex_data["y"] <= max_y)
        & (vertex_data["z"] >= min_z)
        & (vertex_data["z"] <= max_z)
    )
    filtered = vertex_data[mask]
    PlyData([PlyElement.describe(filtered, "vertex")], text=False).write(output_file)


def extract_focal_length(image_path):
    try:
        with Image.open(image_path) as image:
            exif = image.getexif() or {}
            focal_35 = exif.get(TAG_FOCAL_35)
            if focal_35:
                value = _rational_to_float(focal_35)
                if value:
                    return value
            focal = exif.get(TAG_FOCAL)
            if focal:
                return _rational_to_float(focal)
    except Exception:
        return None
    return None


def copy_image_with_fallback_focal(source_path, output_path, fallback_focal):
    if fallback_focal is None:
        shutil.copy2(source_path, output_path)
        return False

    if extract_focal_length(source_path) is not None:
        shutil.copy2(source_path, output_path)
        return False

    with Image.open(source_path) as image:
        image = ImageOps.exif_transpose(image)
        exif = image.getexif() or Image.Exif()
        if TAG_FOCAL:
            exif[TAG_FOCAL] = TiffImagePlugin.IFDRational(float(fallback_focal))
        if TAG_FOCAL_35:
            exif[TAG_FOCAL_35] = int(round(float(fallback_focal)))
        image.save(output_path, format=image.format, exif=exif)
    return True


def load_thumbnail_payload(image_path, background_color):
    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image)
        pixel_size = image.size
        exif = image.getexif() or {}
        focal = exif.get(TAG_FOCAL_35)
        if focal:
            focal = _rational_to_float(focal)
        else:
            focal = _rational_to_float(exif.get(TAG_FOCAL)) if TAG_FOCAL else None

        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        thumb = image.copy()
        thumb.thumbnail(THUMB_SIZE, LANCZOS_FILTER)
        background = Image.new("RGB", THUMB_SIZE, background_color)
        offset_x = max(0, (THUMB_SIZE[0] - thumb.width) // 2)
        offset_y = max(0, (THUMB_SIZE[1] - thumb.height) // 2)
        background.paste(thumb, (offset_x, offset_y))
        return background, focal, pixel_size


def run_process(command, *, shell=False, env=None, cwd=None):
    return subprocess.run(
        command,
        shell=shell,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        creationflags=_creationflags(),
    )


class App(tk.Tk):
    def __init__(self, initial_target=""):
        super().__init__()
        self.title("Easy SHARP Converter")
        self.configure(bg=BG)
        self.state("zoomed")
        self.minsize(1200, 760)

        self._settings = load_settings()
        self._running = False
        self._current_dir = None
        self._history = []
        self._thumb_cache = collections.OrderedDict()
        self._thumb_futures = {}
        self._focal_cache = {}
        self._preview_image_by_stem = {}
        self._card_records = []
        self._selected_paths = []
        self._selectable_paths = []
        self._anchor_index = None
        self._hovered_path = None
        self._browser_folders = []
        self._browser_images = []
        self._browser_splats = []
        self._browser_columns = 0
        self._browser_resize_after_id = None
        self._section_bodies = {}
        self._section_toggle_buttons = {}
        self._collapsed_sections = dict(self._settings.get("collapsed_sections", {}))
        self._thumbnail_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        self.output_mode_var = tk.StringVar(value=self._settings.get("output_mode", "source"))
        self.output_folder_var = tk.StringVar(value=self._settings.get("output_folder", ""))
        self.device_var = tk.StringVar(value=self._settings.get("device", "cuda"))
        self.workers_var = tk.IntVar(value=max(1, int(self._settings.get("workers", 2))))
        self.fallback_focal_var = tk.StringVar(value=self._settings.get("fallback_focal", ""))
        self.format_var = tk.StringVar(value=self._settings.get("format", "ply"))
        self.quality_var = tk.IntVar(value=min(9, max(1, int(self._settings.get("quality", 5)))))
        self.sh_degree_var = tk.IntVar(value=min(3, max(0, int(self._settings.get("sh_degree", 0)))))
        self.path_var = tk.StringVar(value=self._settings.get("last_browse_folder", EXE_DIR))
        self.gsbox_var = tk.StringVar(value=self._settings.get("gsbox_path", ""))
        self.splat_viewer_var = tk.StringVar(value=self._settings.get("splat_viewer_path", ""))

        self.transform_vars = {
            "trans_x": tk.StringVar(value=self._settings.get("trans_x", "")),
            "trans_y": tk.StringVar(value=self._settings.get("trans_y", "")),
            "trans_z": tk.StringVar(value=self._settings.get("trans_z", "")),
            "rot_x": tk.StringVar(value=self._settings.get("rot_x", "")),
            "rot_y": tk.StringVar(value=self._settings.get("rot_y", "")),
            "rot_z": tk.StringVar(value=self._settings.get("rot_z", "")),
            "scale": tk.StringVar(value=self._settings.get("scale", "")),
        }
        self.crop_vars = {
            "min_x": tk.StringVar(value=self._settings.get("min_x", "")),
            "max_x": tk.StringVar(value=self._settings.get("max_x", "")),
            "min_y": tk.StringVar(value=self._settings.get("min_y", "")),
            "max_y": tk.StringVar(value=self._settings.get("max_y", "")),
            "min_z": tk.StringVar(value=self._settings.get("min_z", "")),
            "max_z": tk.StringVar(value=self._settings.get("max_z", "")),
        }

        self.selection_var = tk.StringVar(value="0 files selected")
        self.focal_var = tk.StringVar(value="Focal length: no image selected")
        self.status_var = tk.StringVar(value="Ready")
        self.progress_text_var = tk.StringVar(value="Idle")
        self.progress_value_var = tk.DoubleVar(value=0)
        self._progress_total = 1
        self._progress_completed = 0
        self._log_file_path = os.path.join(EXE_DIR, "easy_sharp_converter.log")

        self._placeholder_photo = self._build_placeholder_photo()
        self._folder_photo = self._build_folder_photo()
        self._splat_icon_cache = {}

        self._build_styles()
        self._build_ui()
        self._bind_settings()
        self._sync_output_mode()
        self._check_tools()
        self.bind_all("<Delete>", self._on_delete_key, add=True)
        self._log(f"Session started. Log file: {self._log_file_path}", "dim")

        target = initial_target or self.path_var.get()
        self.after(50, lambda: self._open_initial_target(target))

    def destroy(self):
        try:
            self._thumbnail_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        super().destroy()

    def _build_placeholder_photo(self):
        image = Image.new("RGB", THUMB_SIZE, "#182028")
        for x in range(0, THUMB_SIZE[0], 12):
            for y in range(0, THUMB_SIZE[1], 12):
                if (x // 12 + y // 12) % 2 == 0:
                    for px in range(x, min(x + 12, THUMB_SIZE[0])):
                        for py in range(y, min(y + 12, THUMB_SIZE[1])):
                            image.putpixel((px, py), (34, 43, 53))
        return ImageTk.PhotoImage(image)

    def _build_folder_photo(self):
        image = Image.new("RGBA", THUMB_SIZE, (24, 31, 38, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((18, 44, 130, 112), radius=12, fill="#5b6772")
        draw.rounded_rectangle((30, 30, 90, 56), radius=10, fill="#7f8d99")
        draw.rounded_rectangle((16, 52, 132, 122), radius=14, fill="#9aa7b4")
        return ImageTk.PhotoImage(image)

    def _get_splat_icon_photo(self, extension):
        cached = self._splat_icon_cache.get(extension)
        if cached is not None:
            return cached

        image = Image.new("RGB", THUMB_SIZE, SPLAT_PREVIEW_BG)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((22, 18, 126, 130), radius=18, fill="#445563")
        draw.rounded_rectangle((36, 36, 112, 56), radius=8, fill="#78b7c9")
        draw.rounded_rectangle((36, 66, 112, 86), radius=8, fill="#7ea7b8")
        draw.rounded_rectangle((36, 96, 112, 116), radius=8, fill="#b39a6e")
        draw.text((42, 124), extension.lstrip(".").upper(), fill="#e8eef3")
        photo = ImageTk.PhotoImage(image)
        self._splat_icon_cache[extension] = photo
        return photo

    def _build_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Vertical.TScrollbar", background=BG4, troughcolor=BG2, bordercolor=BG2, arrowcolor=FG_DIM)
        style.map("Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT_DIM)])
        style.configure(
            "Easy.Horizontal.TProgressbar",
            troughcolor="#202830",
            background=ACCENT,
            bordercolor="#202830",
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=12,
        )

    def _bind_settings(self):
        tracked = {
            "output_mode": self.output_mode_var,
            "output_folder": self.output_folder_var,
            "device": self.device_var,
            "fallback_focal": self.fallback_focal_var,
            "format": self.format_var,
            "quality": self.quality_var,
            "sh_degree": self.sh_degree_var,
            "gsbox_path": self.gsbox_var,
            "splat_viewer_path": self.splat_viewer_var,
        }
        for key, var in tracked.items():
            var.trace_add("write", lambda *_args, setting_key=key, setting_var=var: save_settings({setting_key: setting_var.get()}))

        self.workers_var.trace_add("write", lambda *_: save_settings({"workers": self.workers_var.get()}))
        self.path_var.trace_add("write", lambda *_: save_settings({"last_browse_folder": self.path_var.get()}))

        for key, var in self.transform_vars.items():
            var.trace_add("write", lambda *_args, setting_key=key, setting_var=var: save_settings({setting_key: setting_var.get()}))
        for key, var in self.crop_vars.items():
            var.trace_add("write", lambda *_args, setting_key=key, setting_var=var: save_settings({setting_key: setting_var.get()}))

        self.output_mode_var.trace_add("write", lambda *_: self._sync_output_mode())

    def _build_ui(self):
        header = tk.Frame(self, bg=HEADER_BG, padx=24, pady=20)
        header.pack(fill=tk.X)
        hero = tk.Frame(header, bg=HEADER_BG)
        hero.pack(fill=tk.X)
        hero_left = tk.Frame(hero, bg=HEADER_BG)
        hero_left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(hero_left, text="Easy SHARP Converter", font=("Segoe UI Semibold", 24), bg=HEADER_BG, fg=HEADER_FG).pack(anchor="w")
        tk.Label(
            hero_left,
            text="Batch splat generation, conversion, preview and cleanup in a single workspace.",
            font=("Segoe UI", 10),
            bg=HEADER_BG,
            fg=HEADER_MUTED,
        ).pack(anchor="w", pady=(6, 0))

        hero_right = tk.Frame(hero, bg=HEADER_BG)
        hero_right.pack(side=tk.RIGHT, anchor="ne")
        for label_text in ("SHARP inference", "gsbox export", "Batch browser"):
            chip = tk.Label(
                hero_right,
                text=label_text,
                bg="#1e2932",
                fg=HEADER_FG,
                font=("Segoe UI", 9, "bold"),
                padx=12,
                pady=7,
            )
            chip.pack(side=tk.LEFT, padx=(10, 0))

        main = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=8, sashrelief=tk.FLAT, bg=BG, bd=0)
        main.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        left_shell = tk.Frame(main, bg=BG)
        right_shell = tk.Frame(main, bg=BG)
        left = tk.Frame(left_shell, bg=BG2, padx=18, pady=18, highlightthickness=1, highlightbackground=CARD_BORDER)
        right = tk.Frame(right_shell, bg=BG2, padx=18, pady=18, highlightthickness=1, highlightbackground=CARD_BORDER)
        left.pack(fill=tk.BOTH, expand=True)
        right.pack(fill=tk.BOTH, expand=True)
        main.add(left_shell, minsize=420)
        main.add(right_shell, minsize=520)

        self._build_left_scrollable_panel(left)
        self._build_right_panel(right)

    def _build_left_scrollable_panel(self, parent):
        outer = tk.Frame(parent, bg=BG2)
        outer.pack(fill=tk.BOTH, expand=True)

        self.left_canvas = tk.Canvas(outer, bg=BG2, bd=0, highlightthickness=0)
        self.left_scroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=self.left_canvas.yview)
        self.left_canvas.configure(yscrollcommand=self.left_scroll.set)

        self.left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.left_inner = tk.Frame(self.left_canvas, bg=BG2)
        self.left_window = self.left_canvas.create_window((0, 0), window=self.left_inner, anchor="nw")

        self.left_inner.bind("<Configure>", lambda _event: self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all")))
        self.left_canvas.bind("<Configure>", self._on_left_resize)
        self.left_canvas.bind_all("<MouseWheel>", self._on_mousewheel, add=True)

        self._build_left_panel(self.left_inner)

    def _build_left_panel(self, parent):
        output_box = self._create_collapsible_card(parent, "output", "Output")

        radio_row = tk.Frame(output_box, bg=BG2)
        radio_row.pack(fill=tk.X)
        self._radio(radio_row, "Source folder", self.output_mode_var, "source").pack(side=tk.LEFT, padx=(0, 16))
        self._radio(radio_row, "Specified folder", self.output_mode_var, "custom").pack(side=tk.LEFT)

        custom_row = tk.Frame(output_box, bg=BG2)
        custom_row.pack(fill=tk.X, pady=(8, 0))
        self.output_entry = self._entry(custom_row, self.output_folder_var)
        self.output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(custom_row, "Browse", self._browse_output_folder).pack(side=tk.LEFT, padx=(8, 0))

        sharp_box = self._create_collapsible_card(parent, "sharp", "SHARP")

        control_row = tk.Frame(sharp_box, bg=BG2)
        control_row.pack(fill=tk.X, pady=(0, 8))
        workers_row = tk.Frame(control_row, bg=BG2)
        workers_row.pack(side=tk.LEFT)
        tk.Label(workers_row, text="SHARP instances", bg=BG2, fg=FG, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        tk.Spinbox(
            workers_row,
            from_=1,
            to=8,
            textvariable=self.workers_var,
            width=6,
            font=("Segoe UI", 10),
            bg=BG3,
            fg=FG,
            insertbackground=FG,
            relief=tk.FLAT,
            buttonbackground=BG4,
        ).pack(side=tk.LEFT, padx=(8, 0))

        device_group = tk.Frame(control_row, bg=BG2)
        device_group.pack(side=tk.LEFT, padx=(14, 0))
        tk.Label(device_group, text="Compute", bg=BG2, fg=FG, font=("Segoe UI", 10)).pack(anchor="w")
        device_buttons = tk.Frame(device_group, bg=BG2)
        device_buttons.pack(anchor="w", pady=(2, 0))
        self._radio(device_buttons, "CUDA", self.device_var, "cuda").pack(side=tk.LEFT, padx=(0, 12))
        self._radio(device_buttons, "CPU", self.device_var, "cpu").pack(side=tk.LEFT)

        focal_row = tk.Frame(sharp_box, bg=BG2)
        focal_row.pack(fill=tk.X)
        tk.Label(focal_row, text="Fallback focal mm (if EXIF missing)", bg=BG2, fg=FG, font=("Segoe UI", 10)).pack(anchor="w")
        focal_input_row = tk.Frame(focal_row, bg=BG2)
        focal_input_row.pack(fill=tk.X, pady=(4, 0))
        self._entry(focal_input_row, self.fallback_focal_var, width=10).pack(side=tk.LEFT)
        tk.Label(focal_input_row, text="Blank keeps EXIF-only behavior", bg=BG2, fg=FG_DIM, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 0))

        process_box = self._create_collapsible_card(parent, "processing", "Processing")

        format_row = tk.Frame(process_box, bg=BG2)
        format_row.pack(fill=tk.X)
        tk.Label(format_row, text="Output format", bg=BG2, fg=FG, font=("Segoe UI", 10)).pack(anchor="w")
        fmt_buttons = tk.Frame(format_row, bg=BG2)
        fmt_buttons.pack(anchor="w", pady=(4, 0))
        self._radio(fmt_buttons, ".ply", self.format_var, "ply").pack(side=tk.LEFT, padx=(0, 14))
        self._radio(fmt_buttons, ".spx", self.format_var, "spx").pack(side=tk.LEFT, padx=(0, 14))
        self._radio(fmt_buttons, ".spz", self.format_var, "spz").pack(side=tk.LEFT, padx=(0, 14))
        self._radio(fmt_buttons, ".sog", self.format_var, "sog").pack(side=tk.LEFT)

        export_options_row = tk.Frame(process_box, bg=BG2)
        export_options_row.pack(fill=tk.X, pady=(10, 0))

        quality_group = tk.Frame(export_options_row, bg=BG2)
        quality_group.pack(side=tk.LEFT, padx=(0, 18))
        tk.Label(quality_group, text="Quality (1-9, default 5)", bg=BG2, fg=FG, font=("Segoe UI", 10)).pack(anchor="w")
        tk.Spinbox(
            quality_group,
            from_=1,
            to=9,
            textvariable=self.quality_var,
            width=6,
            font=("Segoe UI", 10),
            bg=BG3,
            fg=FG,
            insertbackground=FG,
            relief=tk.FLAT,
            buttonbackground=BG4,
        ).pack(anchor="w", pady=(4, 0))

        sh_group = tk.Frame(export_options_row, bg=BG2)
        sh_group.pack(side=tk.LEFT)
        tk.Label(sh_group, text="SH degree (0-3, default 0)", bg=BG2, fg=FG, font=("Segoe UI", 10)).pack(anchor="w")
        tk.Spinbox(
            sh_group,
            from_=0,
            to=3,
            textvariable=self.sh_degree_var,
            width=6,
            font=("Segoe UI", 10),
            bg=BG3,
            fg=FG,
            insertbackground=FG,
            relief=tk.FLAT,
            buttonbackground=BG4,
        ).pack(anchor="w", pady=(4, 0))

        info_box = self._card(parent, fill=BG3)
        info_box.pack(fill=tk.X, pady=(0, 10))
        tk.Label(info_box, text="Selection", bg=BG3, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(info_box, textvariable=self.selection_var, bg=BG3, fg=FG, font=("Segoe UI Semibold", 12)).pack(anchor="w", pady=(2, 0))
        tk.Label(info_box, textvariable=self.focal_var, bg=BG3, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))

        transform_box = self._create_collapsible_card(parent, "transform", "Transform")
        self._triple_row(transform_box, "Translate", ("X", "trans_x"), ("Y", "trans_y"), ("Z", "trans_z"))
        self._triple_row(transform_box, "Rotate", ("X", "rot_x"), ("Y", "rot_y"), ("Z", "rot_z"), pady=(6, 0))

        scale_row = tk.Frame(transform_box, bg=BG2)
        scale_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(scale_row, text="Scale", bg=BG2, fg=FG, width=10, anchor="w", font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._entry(scale_row, self.transform_vars["scale"], width=10).pack(side=tk.LEFT)
        tk.Label(scale_row, text="Empty keeps original scale", bg=BG2, fg=FG_DIM, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 0))

        crop_box = self._create_collapsible_card(parent, "cropbox", "Cropbox")
        self._double_row(crop_box, "X", "min_x", "max_x")
        self._double_row(crop_box, "Y", "min_y", "max_y", pady=(6, 0))
        self._double_row(crop_box, "Z", "min_z", "max_z", pady=(6, 0))
        tk.Label(crop_box, text="Leave any crop field empty to skip cropping.", bg=BG2, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))

        tools_box = self._create_collapsible_card(parent, "tools", "Tools")
        tk.Label(tools_box, text="gsbox.exe path", bg=BG2, fg=FG, font=("Segoe UI", 10)).pack(anchor="w")
        tools_row = tk.Frame(tools_box, bg=BG2)
        tools_row.pack(fill=tk.X, pady=(4, 0))
        self._entry(tools_row, self.gsbox_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(tools_row, "Browse", self._browse_gsbox).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(tools_box, text="Splat viewer program", bg=BG2, fg=FG, font=("Segoe UI", 10)).pack(anchor="w", pady=(10, 0))
        viewer_row = tk.Frame(tools_box, bg=BG2)
        viewer_row.pack(fill=tk.X, pady=(4, 0))
        self._entry(viewer_row, self.splat_viewer_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(viewer_row, "Browse", self._browse_splat_viewer).pack(side=tk.LEFT, padx=(8, 0))

        self.convert_button = self._button(parent, "Convert Selected", self._start_conversion, variant="primary")
        self.convert_button.configure(font=("Segoe UI Semibold", 12), pady=12)
        self.convert_button.pack(fill=tk.X)

        status_box = self._card(parent)
        status_box.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        tk.Label(status_box, textvariable=self.status_var, bg=BG2, fg=FG, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(status_box, textvariable=self.progress_text_var, bg=BG2, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        self.progress = ttk.Progressbar(status_box, mode="determinate", style="Easy.Horizontal.TProgressbar", variable=self.progress_value_var, maximum=1)
        self.progress.pack(fill=tk.X, pady=(8, 10))
        self.log_widget = scrolledtext.ScrolledText(
            status_box,
            bg=LOG_BG,
            fg=FG,
            insertbackground=FG,
            relief=tk.FLAT,
            borderwidth=0,
            height=14,
            font=("Consolas", 9),
        )
        self.log_widget.pack(fill=tk.BOTH, expand=True)
        self.log_widget.configure(state=tk.DISABLED)
        self.log_widget.tag_config("ok", foreground=ACCENT)
        self.log_widget.tag_config("warn", foreground=WARN)
        self.log_widget.tag_config("err", foreground=ERR)
        self.log_widget.tag_config("dim", foreground=FG_DIM)

    def _card(self, parent, fill=BG2):
        return tk.Frame(parent, bg=fill, padx=14, pady=12, highlightthickness=1, highlightbackground=CARD_BORDER)

    def _create_collapsible_card(self, parent, key, title, fill=BG2):
        outer = self._card(parent, fill=fill)
        outer.pack(fill=tk.X, pady=(0, 10))

        header = tk.Frame(outer, bg=fill)
        header.pack(fill=tk.X)
        title_label = tk.Label(header, text=title, bg=fill, fg=FG, font=("Segoe UI Semibold", 11), cursor="hand2")
        title_label.pack(side=tk.LEFT, anchor="w")

        toggle_button = tk.Button(
            header,
            text="+" if self._collapsed_sections.get(key, False) else "-",
            command=lambda section_key=key: self._toggle_section(section_key),
            bg=fill,
            fg=FG_DIM,
            activebackground=fill,
            activeforeground=FG,
            relief=tk.FLAT,
            padx=8,
            pady=0,
            cursor="hand2",
            font=("Segoe UI Semibold", 12),
        )
        toggle_button.pack(side=tk.RIGHT)

        body = tk.Frame(outer, bg=fill)
        if not self._collapsed_sections.get(key, False):
            body.pack(fill=tk.X, pady=(6, 0))

        self._section_bodies[key] = body
        self._section_toggle_buttons[key] = toggle_button
        header.bind("<Button-1>", lambda _event, section_key=key: self._toggle_section(section_key))
        title_label.bind("<Button-1>", lambda _event, section_key=key: self._toggle_section(section_key))
        return body

    def _toggle_section(self, key):
        current = bool(self._collapsed_sections.get(key, False))
        new_state = not current
        self._collapsed_sections[key] = new_state

        body = self._section_bodies[key]
        button = self._section_toggle_buttons[key]
        if new_state:
            body.pack_forget()
            button.configure(text="+")
        else:
            body.pack(fill=tk.X, pady=(6, 0))
            button.configure(text="-")

        save_settings({"collapsed_sections": self._collapsed_sections})

    def _build_right_panel(self, parent):
        nav_row = tk.Frame(parent, bg=BG2)
        nav_row.pack(fill=tk.X)
        self._button(nav_row, "Back", self._go_back, width=8).pack(side=tk.LEFT)
        self._button(nav_row, "Up", self._go_up, width=6).pack(side=tk.LEFT, padx=(8, 8))
        self.path_entry = self._entry(nav_row, self.path_var)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(nav_row, "Open", self._open_from_entry, width=8).pack(side=tk.LEFT, padx=(8, 0))
        self._button(nav_row, "Browse", self._browse_folder, width=8).pack(side=tk.LEFT, padx=(8, 0))

        hint_row = tk.Frame(parent, bg=BG3, padx=14, pady=10, highlightthickness=1, highlightbackground=CARD_BORDER)
        hint_row.pack(fill=tk.X, pady=(10, 10))
        tk.Label(
            hint_row,
            text="Browse folders on the right. Ctrl-click toggles, Shift-click selects a range, Enter opens selected files.",
            bg=BG3,
            fg=FG_DIM,
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        browser_outer = tk.Frame(parent, bg=BG3, bd=0, highlightthickness=1, highlightbackground=CARD_BORDER)
        browser_outer.pack(fill=tk.BOTH, expand=True)
        self.browser_canvas = tk.Canvas(browser_outer, bg=BG3, bd=0, highlightthickness=0)
        self.browser_scroll = ttk.Scrollbar(browser_outer, orient=tk.VERTICAL, command=self.browser_canvas.yview)
        self.browser_canvas.configure(yscrollcommand=self.browser_scroll.set)
        self.browser_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.browser_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.browser_inner = tk.Frame(self.browser_canvas, bg=BG3)
        self.browser_window = self.browser_canvas.create_window((0, 0), window=self.browser_inner, anchor="nw")

        self.browser_inner.bind("<Configure>", lambda _event: self.browser_canvas.configure(scrollregion=self.browser_canvas.bbox("all")))
        self.browser_canvas.bind("<Configure>", self._on_browser_resize)
        self.browser_canvas.bind("<Return>", self._open_selected_files)
        self.browser_canvas.bind("<KP_Enter>", self._open_selected_files)
        self.browser_canvas.bind("<Control-a>", self._select_all_files)
        self.browser_canvas.bind("<Control-A>", self._select_all_files)
        self.browser_canvas.bind_all("<MouseWheel>", self._on_mousewheel, add=True)

    def _section_label(self, parent, text):
        tk.Label(parent, text=text, bg=parent.cget("bg"), fg=FG, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))

    def _button(self, parent, text, command, width=None, variant="secondary"):
        if variant == "primary":
            bg = ACCENT
            fg = HEADER_FG
            active_bg = ACCENT_DIM
            active_fg = HEADER_FG
        else:
            bg = BG4
            fg = FG
            active_bg = "#37434f"
            active_fg = FG
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=active_fg,
            relief=tk.FLAT,
            padx=12,
            pady=7,
            cursor="hand2",
            font=("Segoe UI", 9),
            highlightthickness=0,
        )

    def _radio(self, parent, text, variable, value):
        return tk.Radiobutton(
            parent,
            text=text,
            variable=variable,
            value=value,
            bg=BG3,
            fg=FG,
            selectcolor=ACCENT,
            activebackground=BG4,
            activeforeground=FG,
            font=("Segoe UI", 10),
            indicatoron=False,
            relief=tk.FLAT,
            padx=12,
            pady=6,
            highlightthickness=0,
            borderwidth=0,
        )

    def _checkbutton(self, parent, text, variable):
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            onvalue=True,
            offvalue=False,
            bg=parent.cget("bg"),
            fg=FG,
            selectcolor=BG4,
            activebackground=parent.cget("bg"),
            activeforeground=FG,
            font=("Segoe UI", 10),
            highlightthickness=0,
            bd=0,
            padx=0,
            pady=0,
            anchor="w",
        )

    def _entry(self, parent, variable, width=None):
        return tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bg=BG3,
            fg=FG,
            insertbackground=FG,
            relief=tk.FLAT,
            font=("Segoe UI", 10),
            highlightthickness=1,
            highlightbackground=INPUT_BORDER,
            highlightcolor=ACCENT,
            disabledbackground=BG3,
            disabledforeground=FG_DIM,
            bd=0,
        )

    def _triple_row(self, parent, title, first, second, third, pady=(0, 0)):
        row = tk.Frame(parent, bg=BG2)
        row.pack(fill=tk.X, pady=pady)
        tk.Label(row, text=title, bg=BG2, fg=FG, width=10, anchor="w", font=("Segoe UI", 10)).pack(side=tk.LEFT)
        for label, key in (first, second, third):
            cell = tk.Frame(row, bg=BG2)
            cell.pack(side=tk.LEFT, padx=(0, 10))
            tk.Label(cell, text=label, bg=BG2, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor="w")
            self._entry(cell, self.transform_vars[key], width=8).pack(anchor="w")

    def _double_row(self, parent, axis, min_key, max_key, pady=(0, 0)):
        row = tk.Frame(parent, bg=BG2)
        row.pack(fill=tk.X, pady=pady)
        tk.Label(row, text=axis, bg=BG2, fg=FG, width=10, anchor="w", font=("Segoe UI", 10)).pack(side=tk.LEFT)
        min_cell = tk.Frame(row, bg=BG2)
        min_cell.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(min_cell, text="Min", bg=BG2, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor="w")
        self._entry(min_cell, self.crop_vars[min_key], width=8).pack(anchor="w")
        max_cell = tk.Frame(row, bg=BG2)
        max_cell.pack(side=tk.LEFT)
        tk.Label(max_cell, text="Max", bg=BG2, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor="w")
        self._entry(max_cell, self.crop_vars[max_key], width=8).pack(anchor="w")

    def _sync_output_mode(self):
        state = tk.NORMAL if self.output_mode_var.get() == "custom" else tk.DISABLED
        self.output_entry.configure(state=state)

    def _log(self, message, tag=None):
        def append():
            self.log_widget.configure(state=tk.NORMAL)
            timestamp = time.strftime("%H:%M:%S")
            self.log_widget.insert(tk.END, f"[{timestamp}] {message}\n", tag)
            self.log_widget.see(tk.END)
            self.log_widget.configure(state=tk.DISABLED)
            try:
                with open(self._log_file_path, "a", encoding="utf-8") as handle:
                    handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
            except Exception:
                pass

        self.after(0, append)

    def _configure_progress(self, total, message):
        def apply_state():
            self._progress_total = max(1, total)
            self._progress_completed = 0
            self.progress.configure(maximum=self._progress_total)
            self.progress_value_var.set(0)
            self.progress_text_var.set(message)

        self.after(0, apply_state)

    def _advance_progress(self, step=1, message=None):
        def apply_state():
            self._progress_completed = min(self._progress_total, self._progress_completed + step)
            self.progress_value_var.set(self._progress_completed)
            if message:
                self.progress_text_var.set(message)

        self.after(0, apply_state)

    def _complete_progress(self, message):
        def apply_state():
            self._progress_completed = self._progress_total
            self.progress_value_var.set(self._progress_total)
            self.progress_text_var.set(message)

        self.after(0, apply_state)

    def _set_status(self, text):
        self.after(0, lambda: self.status_var.set(text))

    def _set_running(self, running):
        def apply_state():
            self._running = running
            if running:
                self.convert_button.configure(state=tk.DISABLED)
            else:
                self.convert_button.configure(state=tk.NORMAL)
                if self._progress_completed == 0:
                    self.progress_text_var.set("Idle")

        self.after(0, apply_state)

    def _check_tools(self):
        sharp_exe = find_sharp_exe()
        gsbox_exe = find_gsbox_exe(self.gsbox_var.get())

        if sharp_exe:
            self._log(f"SHARP found: {sharp_exe}", "ok")
        else:
            self._log("SHARP not found. Use Launch_SHARP_GUI.bat so the sharp conda environment is active.", "err")

        if gsbox_exe:
            if not self.gsbox_var.get():
                self.gsbox_var.set(gsbox_exe)
            self._log(f"gsbox found: {gsbox_exe}", "ok")
            self._log("gsbox handles transform plus .spx, .spz, and .sog export.", "dim")
        else:
            self._log("gsbox.exe not found yet. It is required for transforms and .spx/.spz/.sog export.", "warn")

    def _open_initial_target(self, target):
        target = os.path.normpath(target) if target else self.path_var.get()
        if os.path.isfile(target):
            self._open_directory(os.path.dirname(target), add_history=False)
            self.after(150, lambda: self._select_by_path(target))
            return
        if os.path.isdir(target):
            self._open_directory(target, add_history=False)
            return
        self._open_directory(EXE_DIR, add_history=False)

    def _browse_folder(self):
        selected = filedialog.askdirectory(initialdir=self._current_dir or self.path_var.get() or EXE_DIR)
        if selected:
            self._open_directory(selected)

    def _browse_output_folder(self):
        selected = filedialog.askdirectory(initialdir=self.output_folder_var.get() or self._current_dir or EXE_DIR)
        if selected:
            self.output_folder_var.set(selected)
            self.output_mode_var.set("custom")

    def _browse_gsbox(self):
        selected = filedialog.askopenfilename(
            initialdir=os.path.dirname(self.gsbox_var.get()) if self.gsbox_var.get() else EXE_DIR,
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
            title="Locate gsbox.exe",
        )
        if selected:
            self.gsbox_var.set(selected)

    def _browse_splat_viewer(self):
        default_viewer = _default_splat_viewer_path()
        selected = filedialog.askopenfilename(
            initialdir=os.path.dirname(self.splat_viewer_var.get()) if self.splat_viewer_var.get() else os.path.dirname(default_viewer) if default_viewer else EXE_DIR,
            filetypes=[("Programs", "*.exe;*.bat;*.cmd"), ("All files", "*.*")],
            title="Locate splat viewer program",
        )
        if selected:
            self.splat_viewer_var.set(selected)

    def _open_from_entry(self):
        path = self.path_var.get().strip()
        if not path:
            return
        if os.path.isfile(path):
            self._open_directory(os.path.dirname(path))
            self.after(150, lambda: self._select_by_path(path))
        elif os.path.isdir(path):
            self._open_directory(path)
        else:
            self._log(f"Path not found: {path}", "err")

    def _go_up(self):
        if not self._current_dir:
            return
        parent = os.path.dirname(self._current_dir)
        if parent and parent != self._current_dir:
            self._open_directory(parent)

    def _go_back(self):
        if not self._history:
            return
        previous = self._history.pop()
        self._open_directory(previous, add_history=False)

    def _open_directory(self, directory, add_history=True):
        directory = os.path.normpath(directory)
        if not os.path.isdir(directory):
            self._log(f"Folder not found: {directory}", "err")
            return

        if add_history and self._current_dir and self._current_dir != directory:
            self._history.append(self._current_dir)

        self._current_dir = directory
        self.path_var.set(directory)
        self.browser_canvas.yview_moveto(0)
        self._selected_paths = []
        self._anchor_index = None

        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            self._log(f"Cannot open folder: {exc}", "err")
            return

        folders = sorted((entry for entry in entries if entry.is_dir()), key=lambda item: item.name.lower())
        images = sorted(
            (entry for entry in entries if entry.is_file() and os.path.splitext(entry.name)[1].lower() in IMAGE_EXTENSIONS),
            key=lambda item: item.name.lower(),
        )
        splats = sorted(
            (entry for entry in entries if entry.is_file() and os.path.splitext(entry.name)[1].lower() in SPLAT_EXTENSIONS),
            key=lambda item: item.name.lower(),
        )
        self._preview_image_by_stem = {}
        for entry in images:
            self._preview_image_by_stem.setdefault(os.path.splitext(entry.name)[0].lower(), entry.path)
        self._browser_folders = folders
        self._browser_images = images
        self._browser_splats = splats
        self._render_browser(folders, images, splats)
        self._update_selection_ui()

    def _refresh_file_browser(self, preferred_directory=None):
        directory = preferred_directory or self._current_dir
        if directory and os.path.isdir(directory):
            self.after(0, lambda: self._open_directory(directory, add_history=False))

    def _launch_with_viewer(self, file_path):
        viewer_path = os.path.normpath(self.splat_viewer_var.get().strip()) if self.splat_viewer_var.get().strip() else ""
        if not viewer_path or not os.path.exists(viewer_path):
            default_viewer = _default_splat_viewer_path()
            if default_viewer and os.path.exists(default_viewer):
                viewer_path = default_viewer
                self.splat_viewer_var.set(default_viewer)
            else:
                selected = filedialog.askopenfilename(
                    initialdir=os.path.dirname(viewer_path) if viewer_path else EXE_DIR,
                    filetypes=[("Programs", "*.exe;*.bat;*.cmd"), ("All files", "*.*")],
                    title="Locate splat viewer program",
                    parent=self,
                )
                if not selected:
                    return False
                viewer_path = os.path.normpath(selected)
                self.splat_viewer_var.set(viewer_path)

        try:
            if viewer_path.lower().endswith((".bat", ".cmd")):
                subprocess.Popen(
                    f'"{viewer_path}" "{file_path}"',
                    cwd=os.path.dirname(viewer_path) or None,
                    shell=True,
                    creationflags=_creationflags(),
                )
            else:
                subprocess.Popen(
                    [viewer_path, file_path],
                    cwd=os.path.dirname(viewer_path) or None,
                    creationflags=_creationflags(),
                )
            self._log(f"Opened {os.path.basename(file_path)} with {os.path.basename(viewer_path)}.", "ok")
            self._set_status(f"Opened {os.path.basename(file_path)}")
            return True
        except Exception as exc:
            messagebox.showerror("Open failed", f"Could not open file with configured viewer:\n\n{exc}", parent=self)
            self._log(f"Viewer launch failed for {os.path.basename(file_path)}: {exc}", "err")
            return False

    def _open_with_default_viewer(self, file_path):
        try:
            if hasattr(os, "startfile"):
                os.startfile(file_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])
            self._log(f"Opened {os.path.basename(file_path)} with the default viewer.", "ok")
            self._set_status(f"Opened {os.path.basename(file_path)}")
            return True
        except Exception as exc:
            messagebox.showerror("Open failed", f"Could not open file with the default viewer:\n\n{exc}", parent=self)
            self._log(f"Default viewer launch failed for {os.path.basename(file_path)}: {exc}", "err")
            return False

    def _open_selected_files(self, _event=None):
        if not self._selected_paths:
            return "break"

        for file_path in self._selected_paths:
            extension = os.path.splitext(file_path)[1].lower()
            if extension in IMAGE_EXTENSIONS:
                self._open_with_default_viewer(file_path)
            elif extension in SPLAT_EXTENSIONS:
                self._launch_with_viewer(file_path)
        return "break"

    def _on_card_double_click(self, _event, record):
        if record["kind"] not in {"image", "splat"}:
            return None

        if record["path"] not in self._selected_paths:
            self._selected_paths = [record["path"]]
            self._anchor_index = self._selectable_paths.index(record["path"])
            self._refresh_card_selection()
            self._update_selection_ui()

        if record["kind"] == "image":
            self._open_with_default_viewer(record["path"])
        else:
            self._launch_with_viewer(record["path"])
        return "break"

    def _on_browser_resize(self, event):
        self.browser_canvas.itemconfigure(self.browser_window, width=event.width)
        columns = max(1, event.width // CARD_WIDTH)
        if columns == self._browser_columns:
            return
        if self._browser_resize_after_id is not None:
            self.after_cancel(self._browser_resize_after_id)
        self._browser_resize_after_id = self.after(90, self._rerender_cards)

    def _on_left_resize(self, event):
        self.left_canvas.itemconfigure(self.left_window, width=event.width)

    def _set_card_hover(self, path):
        if self._hovered_path == path:
            return
        self._hovered_path = path
        self._refresh_card_selection()

    def _clear_card_hover(self, path):
        if self._hovered_path == path:
            self._hovered_path = None
            self._refresh_card_selection()

    def _on_mousewheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget == self.browser_canvas or widget == self.browser_inner:
                self.browser_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                break
            if hasattr(self, "left_canvas") and (widget == self.left_canvas or widget == self.left_inner):
                self.left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                break
            widget = getattr(widget, "master", None)

    def _on_delete_key(self, _event):
        focus_widget = self.focus_get()
        in_browser = False
        while focus_widget is not None:
            if focus_widget == self.browser_canvas or focus_widget == self.browser_inner:
                in_browser = True
                break
            focus_widget = getattr(focus_widget, "master", None)

        if self._running or not self._selected_paths or not in_browser:
            return

        selected_paths = list(self._selected_paths)
        count = len(selected_paths)
        preview_names = ", ".join(os.path.basename(path) for path in selected_paths[:4])
        if count > 4:
            preview_names += ", ..."

        confirmed = messagebox.askyesno(
            "Delete files",
            f"Delete {count} selected file(s)?\n\n{preview_names}",
            parent=self,
            icon="warning",
        )
        if not confirmed:
            return

        deleted = 0
        failed = []
        for path in selected_paths:
            try:
                os.remove(path)
                deleted += 1
            except OSError as exc:
                failed.append((path, exc))

        self._selected_paths = []
        self._anchor_index = None
        if deleted:
            self._log(f"Deleted {deleted} file(s).", "ok")
        for path, exc in failed[:5]:
            self._log(f"Delete failed for {os.path.basename(path)}: {exc}", "err")
        if failed:
            self._log(f"{len(failed)} file(s) could not be deleted.", "warn")
        self._refresh_file_browser()

    def _select_all_files(self, _event=None):
        if not self._selectable_paths:
            return "break"
        self.browser_canvas.focus_set()
        self._selected_paths = list(self._selectable_paths)
        self._anchor_index = 0
        self._refresh_card_selection()
        self._update_selection_ui()
        return "break"

    def _render_browser(self, folders, images, splats):
        for child in self.browser_inner.winfo_children():
            child.destroy()

        self._card_records = []
        self._selectable_paths = [entry.path for entry in images]
        self._selectable_paths.extend(entry.path for entry in splats)
        items = [("folder", entry.path, entry.name) for entry in folders]
        items.extend(("image", entry.path, entry.name) for entry in images)
        items.extend(("splat", entry.path, entry.name) for entry in splats)

        if not items:
            tk.Label(self.browser_inner, text="Folder is empty.", bg=BG3, fg=FG_DIM, font=("Segoe UI", 10)).pack(anchor="center", pady=30)
            return

        columns = max(1, self.browser_canvas.winfo_width() // CARD_WIDTH)
        self._browser_columns = columns
        self._browser_resize_after_id = None
        for index, (kind, path, name) in enumerate(items):
            row = index // columns
            column = index % columns
            frame = tk.Frame(
                self.browser_inner,
                width=CARD_WIDTH - 10,
                height=CARD_HEIGHT,
                bg=CARD_BG,
                highlightthickness=1,
                highlightbackground=CARD_BORDER,
                cursor="hand2",
            )
            frame.grid(row=row, column=column, padx=6, pady=6, sticky="nsew")
            frame.grid_propagate(False)

            thumb_bg = CARD_BG
            if kind == "image":
                thumb_bg = IMAGE_PREVIEW_BG
            elif kind == "splat":
                thumb_bg = SPLAT_PREVIEW_BG

            thumb_holder = tk.Label(frame, bg=thumb_bg, image=self._placeholder_photo)
            thumb_holder.image = self._placeholder_photo
            thumb_holder.pack(pady=(10, 8))
            name_label = tk.Label(frame, text=name, bg=CARD_BG, fg=FG, wraplength=CARD_WIDTH - 24, justify="center", font=("Segoe UI", 9))
            name_label.pack(fill=tk.X, padx=8)
            meta_text = "Open folder" if kind == "folder" else ""
            meta_label = tk.Label(frame, text=meta_text, bg=CARD_BG, fg=FG_DIM, font=("Segoe UI", 8))
            meta_label.pack(pady=(2, 0))
            size_label = tk.Label(frame, text="", bg=CARD_BG, fg=FG_DIM, font=("Segoe UI", 8))
            size_label.pack(pady=(0, 0))

            record = {
                "kind": kind,
                "path": path,
                "frame": frame,
                "thumb": thumb_holder,
                "thumb_bg": thumb_bg,
                "name": name_label,
                "meta": meta_label,
                "size": size_label,
            }
            self._card_records.append(record)

            for widget in (frame, thumb_holder, name_label, meta_label, size_label):
                widget.bind("<Button-1>", lambda event, rec=record: self._on_card_click(event, rec))
                widget.bind("<Double-Button-1>", lambda event, rec=record: self._on_card_double_click(event, rec))
            frame.bind("<Enter>", lambda _event, item_path=path: self._set_card_hover(item_path))
            frame.bind("<Leave>", lambda _event, item_path=path: self._clear_card_hover(item_path))

            if kind == "folder":
                thumb_holder.configure(image=self._folder_photo)
                thumb_holder.image = self._folder_photo
                size_label.configure(text="")
            elif kind == "image":
                meta_label.pack_forget()
                self._queue_thumbnail(path, path, thumb_holder, IMAGE_PREVIEW_BG)
                size_label.configure(text=_format_mb(path))
            else:
                meta_label.pack_forget()
                preview_path = self._find_matching_image(path)
                if preview_path:
                    self._queue_thumbnail(path, preview_path, thumb_holder, SPLAT_PREVIEW_BG)
                else:
                    icon = self._get_splat_icon_photo(os.path.splitext(path)[1].lower())
                    thumb_holder.configure(image=icon)
                    thumb_holder.image = icon
                size_label.configure(text=_format_mb(path))

        for column in range(columns):
            self.browser_inner.grid_columnconfigure(column, weight=1)

    def _rerender_cards(self):
        if not self._current_dir:
            return
        self._render_browser(self._browser_folders, self._browser_images, self._browser_splats)
        self._refresh_card_selection()

    def _find_matching_image(self, file_path):
        stem = os.path.splitext(os.path.basename(file_path))[0].lower()
        return self._preview_image_by_stem.get(stem)

    def _queue_thumbnail(self, item_path, preview_path, label_widget, background_color):
        cache_key = ("preview", preview_path, background_color)
        cached = self._thumb_cache.get(cache_key)
        if cached is not None:
            label_widget.configure(image=cached)
            label_widget.image = cached
            return

        label_widget._thumb_path = item_path
        label_widget._thumb_cache_key = cache_key

        future = self._thumbnail_executor.submit(load_thumbnail_payload, preview_path, background_color)
        self._thumb_futures[item_path] = future

        def complete(done_future):
            self.after(0, lambda: self._apply_thumbnail_result(item_path, cache_key, label_widget, done_future))

        future.add_done_callback(complete)

    def _apply_thumbnail_result(self, item_path, cache_key, label_widget, future):
        self._thumb_futures.pop(item_path, None)
        try:
            thumb_image, focal, pixel_size = future.result()
        except Exception:
            self._focal_cache.setdefault(item_path, None)
            return

        photo = ImageTk.PhotoImage(thumb_image)
        self._thumb_cache[cache_key] = photo
        self._thumb_cache.move_to_end(cache_key)
        while len(self._thumb_cache) > THUMB_CACHE_LIMIT:
            self._thumb_cache.popitem(last=False)

        self._focal_cache[item_path] = focal
        if getattr(label_widget, "_thumb_path", None) == item_path and label_widget.winfo_exists():
            label_widget.configure(image=photo)
            label_widget.image = photo
            record = next((item for item in self._card_records if item["path"] == item_path), None)
            if record is not None and record["kind"] == "image":
                record["meta"].configure(text="")
                record["size"].configure(text=f"{pixel_size[0]} x {pixel_size[1]} px  |  {_format_mb(item_path)}")
        if item_path in self._selected_paths:
            self._update_focal_summary()

    def _on_card_click(self, event, record):
        if record["kind"] == "folder":
            self._open_directory(record["path"])
            return

        self.browser_canvas.focus_set()

        image_index = self._selectable_paths.index(record["path"])
        ctrl_pressed = bool(event.state & 0x0004)
        shift_pressed = bool(event.state & 0x0001)

        if shift_pressed and self._anchor_index is not None:
            start = min(self._anchor_index, image_index)
            end = max(self._anchor_index, image_index)
            self._selected_paths = self._selectable_paths[start : end + 1]
        elif ctrl_pressed:
            if record["path"] in self._selected_paths:
                self._selected_paths = [path for path in self._selected_paths if path != record["path"]]
            else:
                self._selected_paths.append(record["path"])
            self._anchor_index = image_index
        else:
            self._selected_paths = [record["path"]]
            self._anchor_index = image_index

        self._refresh_card_selection()
        self._update_selection_ui()

    def _select_by_path(self, path):
        if path not in getattr(self, "_selectable_paths", []):
            return
        self._selected_paths = [path]
        self._anchor_index = self._selectable_paths.index(path)
        self._refresh_card_selection()
        self._update_selection_ui()

    def _refresh_card_selection(self):
        selected = set(self._selected_paths)
        for record in self._card_records:
            active = record["path"] in selected and record["kind"] != "folder"
            hovered = record["path"] == self._hovered_path
            bg = CARD_SELECTED if active else CARD_HOVER if hovered else CARD_BG
            fg = FG
            meta_fg = FG_DIM
            border = ACCENT if active else ACCENT_DIM if hovered else CARD_BORDER
            record["frame"].configure(bg=bg, highlightbackground=border)
            record["thumb"].configure(bg=record["thumb_bg"])
            record["name"].configure(bg=bg, fg=fg)
            record["meta"].configure(bg=bg, fg=meta_fg)
            record["size"].configure(bg=bg, fg=meta_fg)

    def _update_selection_ui(self):
        count = len(self._selected_paths)
        if count == 1:
            self.selection_var.set("1 file selected")
        else:
            self.selection_var.set(f"{count} files selected")
        self._update_focal_summary()

    def _update_focal_summary(self):
        if not self._selected_paths:
            self.focal_var.set("Focal length: no image selected")
            return

        values = []
        missing = 0
        for path in self._selected_paths[:12]:
            if path not in self._focal_cache:
                preview_path = path if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS else self._find_matching_image(path)
                self._focal_cache[path] = extract_focal_length(preview_path) if preview_path else None
            value = self._focal_cache.get(path)
            if value is None:
                missing += 1
            else:
                values.append(round(value, 3))

        if not values and missing:
            self.focal_var.set("Focal length: not found in EXIF for selected images")
            return

        unique = sorted(set(values))
        if len(unique) == 1 and missing == 0:
            self.focal_var.set(f"Focal length: {unique[0]:g} mm from EXIF")
        elif len(unique) == 1:
            self.focal_var.set(f"Focal length: {unique[0]:g} mm for some images, missing on others")
        else:
            self.focal_var.set("Focal length: mixed EXIF values across the selection")

    def _start_conversion(self):
        if self._running:
            return

        selected_paths = list(self._selected_paths)
        if not selected_paths:
            self._log("Select at least one file before converting.", "warn")
            return

        missing = [path for path in selected_paths if not os.path.exists(path)]
        if missing:
            self._log("Some selected files no longer exist. Refresh the folder and select again.", "err")
            return

        settings = self._collect_runtime_settings(selected_paths)
        if not settings:
            return

        self._set_running(True)
        self._set_status("Preparing conversion...")
        threading.Thread(target=self._run_conversion_batch, args=(selected_paths, settings), daemon=True).start()

    def _collect_runtime_settings(self, selected_paths):
        try:
            workers = max(1, int(self.workers_var.get()))
        except Exception:
            self._log("Invalid SHARP instance count.", "err")
            return None

        try:
            fallback_focal = _safe_float(self.fallback_focal_var.get())
        except ValueError:
            self._log("Invalid fallback focal length. Use a numeric value in mm.", "err")
            return None
        if fallback_focal is not None and fallback_focal <= 0:
            self._log("Fallback focal length must be greater than zero.", "err")
            return None

        try:
            quality = min(9, max(1, int(self.quality_var.get())))
        except Exception:
            self._log("Invalid output quality. Use a value from 1 to 9.", "err")
            return None

        try:
            sh_degree = min(3, max(0, int(self.sh_degree_var.get())))
        except Exception:
            self._log("Invalid SH degree. Use a value from 0 to 3.", "err")
            return None

        gsbox_exe = find_gsbox_exe(self.gsbox_var.get())
        fmt = self.format_var.get()
        selected_extensions = {os.path.splitext(path)[1].lower() for path in selected_paths}
        has_images = any(extension in IMAGE_EXTENSIONS for extension in selected_extensions)
        has_non_ply_splats = any(extension in {".spx", ".spz", ".sog"} for extension in selected_extensions)

        sharp_exe = None
        if has_images:
            sharp_exe = find_sharp_exe()
            if not sharp_exe:
                self._log("SHARP executable not found.", "err")
                return None

        transform_values = {}
        for key, var in self.transform_vars.items():
            try:
                transform_values[key] = _safe_float(var.get())
            except ValueError:
                self._log(f"Invalid numeric value for {key}.", "err")
                return None

        crop_values = {}
        for key, var in self.crop_vars.items():
            try:
                crop_values[key] = _safe_float(var.get())
            except ValueError:
                self._log(f"Invalid numeric value for {key}.", "err")
                return None

        needs_transform = any(transform_values[key] is not None for key in ("trans_x", "trans_y", "trans_z", "rot_x", "rot_y", "rot_z"))
        scale_value = transform_values["scale"]
        if scale_value is not None and math.isclose(scale_value, 1.0):
            scale_value = None
        transform_values["scale"] = scale_value
        needs_transform = needs_transform or scale_value is not None

        crop_list = [crop_values[key] for key in ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z")]
        crop_enabled = all(value is not None for value in crop_list)
        if any(value is not None for value in crop_list) and not crop_enabled:
            self._log("Cropbox is partially filled. Cropping will be skipped until all six values are set.", "warn")

        if needs_transform and not gsbox_exe:
            self._log("gsbox.exe is required when transform or scale values are set.", "err")
            return None
        if fmt in {"spx", "spz", "sog"} and not gsbox_exe:
            self._log(f"gsbox.exe is required for .{fmt} export.", "err")
            return None
        if has_non_ply_splats and not gsbox_exe:
            self._log("gsbox.exe is required to convert selected .spx/.spz/.sog inputs.", "err")
            return None

        output_mode = self.output_mode_var.get()
        if output_mode == "custom":
            output_folder = self.output_folder_var.get().strip()
            if not output_folder:
                self._log("Choose a custom output folder or switch back to source folder mode.", "err")
                return None
            os.makedirs(output_folder, exist_ok=True)
        else:
            output_folder = None

        return {
            "workers": workers,
            "conversion_workers": max(1, (os.cpu_count() or 4) // 2),
            "device": self.device_var.get(),
            "fallback_focal": fallback_focal,
            "format": fmt,
            "quality": quality,
            "sh_degree": sh_degree,
            "sharp_exe": sharp_exe,
            "gsbox_exe": gsbox_exe,
            "transform": transform_values,
            "crop": tuple(crop_list) if crop_enabled else None,
            "output_mode": output_mode,
            "output_folder": output_folder,
        }

    def _run_conversion_batch(self, selected_paths, settings):
        temp_root = tempfile.mkdtemp(prefix="easy_sharp_")
        batch_started = time.perf_counter()
        sharp_elapsed = 0.0
        post_elapsed = 0.0
        try:
            self._set_status("Preparing temporary workspace...")
            raw_dir = os.path.join(temp_root, "raw_ply")
            os.makedirs(raw_dir, exist_ok=True)

            entries = self._build_processing_entries(selected_paths, settings, temp_root)
            chunk_dirs = self._prepare_chunks(entries)
            self._configure_progress(len(chunk_dirs) + len(entries) + 2, f"Prepared {len(entries)} file(s)")
            self._advance_progress(1, "Input batch ready")
            sharp_started = time.perf_counter()
            self._run_sharp_instances(chunk_dirs, raw_dir, settings)
            sharp_elapsed = time.perf_counter() - sharp_started
            post_started = time.perf_counter()
            self._postprocess_outputs(entries, raw_dir, settings, temp_root)
            post_elapsed = time.perf_counter() - post_started
            total_elapsed = time.perf_counter() - batch_started
            avg_elapsed = total_elapsed / max(1, len(entries))
            self._set_status("Finished")
            self._complete_progress(f"Finished {len(entries)} of {len(entries)} files")
            self._log(f"Finished converting {len(entries)} files.", "ok")
            self._log(
                f"Performance: total={_format_duration(total_elapsed)}, average/file={_format_duration(avg_elapsed)}, "
                f"SHARP={_format_duration(sharp_elapsed)}, post={_format_duration(post_elapsed)}",
                "dim",
            )
            preferred_directory = settings["output_folder"] if settings["output_mode"] == "custom" else self._current_dir
            self._refresh_file_browser(preferred_directory)
        except Exception as exc:
            total_elapsed = time.perf_counter() - batch_started
            self._set_status("Failed")
            self._complete_progress("Batch failed")
            self._log(f"Conversion failed: {exc}", "err")
            self._log(f"Performance until failure: total={_format_duration(total_elapsed)}", "dim")
            self._log(traceback.format_exc().rstrip(), "dim")
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
            self._set_running(False)

    def _build_processing_entries(self, selected_paths, settings, temp_root):
        stem_counts = {}
        entries = []
        for index, source_path in enumerate(selected_paths, start=1):
            original_stem = os.path.splitext(os.path.basename(source_path))[0]
            stem_counts[original_stem] = stem_counts.get(original_stem, 0) + 1
            suffix = stem_counts[original_stem]
            base_output_stem = original_stem if suffix == 1 else f"{original_stem}_{suffix}"
            output_stem = f"{base_output_stem}_q{settings['quality']}_sh{settings['sh_degree']}"
            extension = os.path.splitext(source_path)[1].lower()
            temp_name = f"input_{index:04d}{extension}"
            temp_stem = os.path.splitext(temp_name)[0]

            output_dir = settings["output_folder"] if settings["output_mode"] == "custom" else os.path.dirname(source_path)
            os.makedirs(output_dir, exist_ok=True)

            entry = {
                "index": index,
                "source_path": source_path,
                "source_extension": extension,
                "source_kind": "image" if extension in IMAGE_EXTENSIONS else "splat",
                "fallback_focal": settings["fallback_focal"],
                "output_dir": output_dir,
                "output_stem": output_stem,
                "temp_name": temp_name,
                "temp_stem": temp_stem,
                "chunk_dir": os.path.join(temp_root, f"chunk_{(index - 1) % settings['workers']}"),
            }
            entries.append(entry)
        return entries

    def _prepare_chunks(self, entries):
        chunk_dirs = {}
        fallback_count = 0
        for entry in entries:
            if entry["source_kind"] != "image":
                continue
            chunk_dir = entry["chunk_dir"]
            os.makedirs(chunk_dir, exist_ok=True)
            destination = os.path.join(chunk_dir, entry["temp_name"])
            if copy_image_with_fallback_focal(entry["source_path"], destination, entry["fallback_focal"]):
                fallback_count += 1
            chunk_dirs[chunk_dir] = chunk_dirs.get(chunk_dir, 0) + 1

        image_count = sum(1 for entry in entries if entry["source_kind"] == "image")
        direct_count = len(entries) - image_count
        self._log(f"Prepared {image_count} image input(s) across {len(chunk_dirs)} SHARP chunk folders.", "dim")
        if fallback_count:
            self._log(f"Applied fallback focal length to {fallback_count} image(s) with missing EXIF focal data.", "dim")
        if direct_count:
            self._log(f"Prepared {direct_count} direct splat conversion(s) without SHARP.", "dim")
        for chunk_dir, count in sorted(chunk_dirs.items()):
            self._log(f"  {os.path.basename(chunk_dir)}: {count} image(s)", "dim")
        return [chunk_dir for chunk_dir, count in chunk_dirs.items() if count > 0]

    def _run_sharp_instances(self, chunk_dirs, raw_dir, settings):
        if not chunk_dirs:
            self._log("No image inputs selected. Skipping SHARP stage.", "dim")
            return

        self._set_status("Running SHARP predictions...")
        self._log(f"Running {len(chunk_dirs)} SHARP instance(s) with device={settings['device']}.", "dim")

        def run_chunk(chunk_dir):
            command = [
                settings["sharp_exe"],
                "predict",
                "-i",
                chunk_dir,
                "-o",
                raw_dir,
                "--device",
                settings["device"],
            ]
            result = run_process(command)
            return chunk_dir, result

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunk_dirs)) as executor:
            future_map = {executor.submit(run_chunk, chunk_dir): chunk_dir for chunk_dir in chunk_dirs}
            for future in concurrent.futures.as_completed(future_map):
                chunk_dir, result = future.result()
                name = os.path.basename(chunk_dir)
                if result.returncode != 0:
                    stderr = (result.stderr or result.stdout or "Unknown error").strip()
                    raise RuntimeError(f"SHARP failed for {name}: {stderr}")
                self._advance_progress(1, f"SHARP complete: {name}")
                self._log(f"SHARP finished for {name}.", "ok")

    def _postprocess_outputs(self, entries, raw_dir, settings, temp_root):
        self._set_status("Post-processing outputs...")
        self._log(
            f"Cleaning, transforming, cropping, and exporting results with {settings['conversion_workers']} parallel worker(s)...",
            "dim",
        )

        worker_count = max(1, min(len(entries), settings["conversion_workers"]))

        def process_entry(entry):
            raw_ply = None
            if entry["source_kind"] == "image":
                raw_ply = os.path.join(raw_dir, f"{entry['temp_stem']}.ply")
                if not os.path.exists(raw_ply):
                    raise RuntimeError(f"Missing SHARP output for {os.path.basename(entry['source_path'])}")
            return self._export_single_entry(entry, raw_ply, settings, temp_root)

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(process_entry, entry): entry for entry in entries}
            exported = 0
            for future in concurrent.futures.as_completed(futures):
                entry = futures[future]
                final_path = future.result()
                exported += 1
                self._advance_progress(1, f"Exported {exported}/{len(entries)}")
                self._log(f"Exported {os.path.basename(entry['source_path'])} -> {final_path}", "ok")

    def _export_single_entry(self, entry, raw_ply, settings, temp_root):
        work_dir = os.path.join(temp_root, f"work_{entry['index']:04d}")
        os.makedirs(work_dir, exist_ok=True)
        process_temp_dir = os.path.join(work_dir, "tmp")
        os.makedirs(process_temp_dir, exist_ok=True)
        process_env = os.environ.copy()
        process_env["TMP"] = process_temp_dir
        process_env["TEMP"] = process_temp_dir

        current_ply = os.path.join(work_dir, "source.ply")
        if entry["source_kind"] == "image":
            clean_ply_file(raw_ply, current_ply)
        else:
            convert_to_ply(entry["source_path"], current_ply, settings["gsbox_exe"], env=process_env, cwd=work_dir)

        transform_values = settings["transform"]
        if any(transform_values[key] is not None for key in ("trans_x", "trans_y", "trans_z", "rot_x", "rot_y", "rot_z", "scale")):
            transformed_ply = os.path.join(work_dir, "transformed.ply")
            command = [settings["gsbox_exe"], "ply2ply", "-i", current_ply, "-o", transformed_ply]
            if transform_values["trans_x"] is not None:
                command.extend(["-tx", str(transform_values["trans_x"])])
            if transform_values["trans_y"] is not None:
                command.extend(["-ty", str(transform_values["trans_y"])])
            if transform_values["trans_z"] is not None:
                command.extend(["-tz", str(transform_values["trans_z"])])
            if transform_values["rot_x"] is not None:
                command.extend(["-rx", str(transform_values["rot_x"])])
            if transform_values["rot_y"] is not None:
                command.extend(["-ry", str(transform_values["rot_y"])])
            if transform_values["rot_z"] is not None:
                command.extend(["-rz", str(transform_values["rot_z"])])
            if transform_values["scale"] is not None:
                command.extend(["-s", str(transform_values["scale"])])

            result = run_process(command, env=process_env, cwd=work_dir)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "gsbox transform failed").strip())
            current_ply = transformed_ply

        if settings["crop"] is not None:
            cropped_ply = os.path.join(work_dir, "cropped.ply")
            crop_ply_file(current_ply, cropped_ply, settings["crop"])
            current_ply = cropped_ply

        fmt = settings["format"]
        final_path = os.path.join(entry["output_dir"], f"{entry['output_stem']}.{fmt}")
        if os.path.exists(final_path):
            os.remove(final_path)

        if fmt == "ply":
            if settings["gsbox_exe"]:
                command = [settings["gsbox_exe"], "ply2ply", "-i", current_ply, "-o", final_path, "-sh", str(settings["sh_degree"])]
                result = run_process(command, env=process_env, cwd=work_dir)
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout or "gsbox ply2ply failed").strip())
            else:
                shutil.copy2(current_ply, final_path)
            return final_path

        if fmt == "spx":
            command = [
                settings["gsbox_exe"],
                "ply2spx",
                "-i",
                current_ply,
                "-o",
                final_path,
                "-q",
                str(settings["quality"]),
                "-sh",
                str(settings["sh_degree"]),
            ]
            result = run_process(command, env=process_env, cwd=work_dir)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "gsbox ply2spx failed").strip())
            return final_path

        if fmt == "spz":
            command = [
                settings["gsbox_exe"],
                "ply2spz",
                "-i",
                current_ply,
                "-o",
                final_path,
                "-q",
                str(settings["quality"]),
                "-sh",
                str(settings["sh_degree"]),
            ]
            result = run_process(command, env=process_env, cwd=work_dir)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "gsbox ply2spz failed").strip())
            return final_path

        command = [
            settings["gsbox_exe"],
            "ply2sog",
            "-i",
            current_ply,
            "-o",
            final_path,
            "-q",
            str(settings["quality"]),
            "-sh",
            str(settings["sh_degree"]),
        ]
        result = run_process(command, env=process_env, cwd=work_dir)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "gsbox ply2sog failed").strip())
        return final_path


def main():
    initial_target = sys.argv[1] if len(sys.argv) > 1 else ""
    app = App(initial_target)
    app.mainloop()


if __name__ == "__main__":
    main()