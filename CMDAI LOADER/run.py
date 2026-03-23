import os
import sys
import re
import json
import time
import ast
import glob
import shutil
import socket
import hashlib
import importlib
import subprocess
import threading
import contextlib
import http.server
import socketserver
import tempfile
import ctypes
import shlex
import sqlite3

try:
    import winreg
except Exception:
    winreg = None
from http import HTTPStatus
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from datetime import datetime
from typing import Dict, List, Optional, Union, Any, Tuple, Callable

term_width = shutil.get_terminal_size().columns
LEFT_WIDTH = int(term_width * 0.55)

AUTO_UPDATE_LLAMA = True
HTTP_PORT = 8080
_AUTO_UPDATE_FLAG = "_AUTO_UPDATE_IN_PROGRESS..."
PLAN_FILENAME = "CMDAIPLAN.md"
TODO_FILENAME = "CMDAI_TODO.md"
ANALYST_TEST_FILENAME = "ANALYST_TEST.md"
SETTINGS_FILENAME = ".CMDAISETTINGS.json"
DEBUG_HISTORY_FILENAME = ".CMDAIDEBUG.json"
MD_CONTEXT_MAX_FILES = 14
MD_CONTEXT_MAX_CHARS = 12000
LAUNCHER_DIR_NAME = "CMDAI"
SLASH_COMMAND_HINTS: List[Tuple[str, str]] = [
    ("/help",    "Show commands"),
    ("/load",    "Load model"),
    ("/ide",     "Pick IDE"),
    ("/files",   "Show edited files"),
    ("/run",     "Run terminal command"),
    ("/visualtest", "Preview UI"),
    ("/unload",  "Unload model"),
    ("/exit",    "Exit app"),
    ("/go",      "Execute current plan"),
    ("/swap",    "Swap model"),
    ("/pause",   "Pause chat"),
    ("/status",  "Show status"),
    ("/version", "Show version"),
    ("/update",  "Update AI engine"),
    ("/settings","App settings"),
]
ALLOWED_USER_COMMANDS = {
    "help",
    "load",
    "ide",
    "visualtest",
    "unload",
    "exit",
    "go",
    "swap",
    "pause",
    "status",
    "version",
    "update",
    "settings",
    "files",
    "run",
    "tests",
    "debug",
    "dhelp",
    "trace",
    "stack",
    "quickfix",
    "patterns",
    "autofix",
    "ahelp",
    "analyst",
    "todo",
    "deps",
    "perf",
    "refactor",
    "docs",
    "complexity",
    "security",
    "coverage",
    "architecture",
    "style",
    "graph",
    "deadcode",
    "benchmark",
}


class AppMode:
    CHAT = "chat"
    PLAN = "plan"
    CODE = "code"
    DEBUG = "debug"
    ANALYST = "analyst"

    @staticmethod
    def list():
        return [
            AppMode.CHAT,
            AppMode.PLAN,
            AppMode.CODE,
            AppMode.DEBUG,
            AppMode.ANALYST,
        ]

    @staticmethod
    def description(mode):
        descriptions = {
            AppMode.CHAT: "Standardowa rozmowa z AI",
            AppMode.PLAN: "Planowanie i architektura projektu",
            AppMode.CODE: "Programowanie - AI tworzy pliki",
            AppMode.DEBUG: "Debugowanie - analiza błędów + tworzenie/edycja plików",
            AppMode.ANALYST: "Analiza kodu - zaleznosci, wydajnosc, bezpieczenstwo i architektura",
        }
        return descriptions.get(mode, "Unknown")


CURRENT_MODE = AppMode.CHAT
MODE_INDICATOR = ""


def _visible_debug_command_hints() -> List[Tuple[str, str]]:
    return [
        ("/dhelp", "Show debug commands"),
        ("/debug", "Analyze and debug"),
        ("/trace", "Analyze traceback"),
        ("/stack", "Show call stack"),
        ("/quickfix", "Suggest fixes"),
        ("/patterns", "Show error patterns"),
        ("/autofix", "Attempt auto-fix"),
        ("/tests", "Generate tests"),
    ]


def _visible_analyst_command_hints() -> List[Tuple[str, str]]:
    return [
        ("/ahelp", "Show analyst commands"),
        ("/analyst", "Run full analysis"),
        ("/todo", "Write analyst TODO file"),
        ("/deps", "Analyze dependencies"),
        ("/perf", "Analyze performance"),
        ("/refactor", "Suggest refactors"),
        ("/docs", "Generate docs preview"),
        ("/complexity", "Analyze complexity"),
        ("/security", "Run security audit"),
        ("/coverage", "Analyze test coverage"),
        ("/architecture", "Analyze architecture"),
        ("/style", "Check code style"),
        ("/graph", "Build dependency graph"),
        ("/deadcode", "Detect dead code"),
        ("/benchmark", "Benchmark suggestions"),
    ]


def _visible_command_hints() -> List[Tuple[str, str]]:
    hints = list(SLASH_COMMAND_HINTS)
    if CURRENT_MODE == AppMode.DEBUG:
        hints.append(("/dhelp", "Show debug commands"))
    if CURRENT_MODE == AppMode.ANALYST:
        hints.append(("/ahelp", "Show analyst commands"))
    return hints


def _is_debug_mode_enabled() -> bool:
    return _bool_from_any(APP_SETTINGS.get("debug_mode_enabled"), False)


def _is_analyst_mode_enabled() -> bool:
    return _bool_from_any(APP_SETTINGS.get("analyst_mode_enabled"), False)


def _should_auto_accept_debug() -> bool:
    if "auto_accept_debug" in APP_SETTINGS:
        return _bool_from_any(APP_SETTINGS.get("auto_accept_debug"), False)
    return _bool_from_any(APP_SETTINGS.get("auto_accept_code"), False)


def _request_needs_frontend_assets(
    user_text: str, project_root: Optional[str] = None
) -> bool:
    text = str(user_text or "").lower()
    frontend_markers = (
        "frontend",
        "front-end",
        "ui",
        "ux",
        "html",
        "css",
        "javascript",
        "typescript",
        "react",
        "vue",
        "svelte",
        "vite",
        "next",
        "tailwind",
        "landing page",
        "web page",
        "strona",
        "interfejs",
    )
    if any(marker in text for marker in frontend_markers):
        return True

    root = os.path.abspath(project_root or _current_project_root_path(False) or os.getcwd())
    frontend_paths = ("index.html", "package.json", "frontend", "client", "public", "src")
    try:
        for rel in frontend_paths:
            if os.path.exists(os.path.join(root, rel)):
                return True
    except Exception:
        return False
    return False


def _request_needs_backend_assets(
    user_text: str, project_root: Optional[str] = None
) -> bool:
    text = str(user_text or "").lower()
    backend_markers = (
        "backend",
        "api",
        "server",
        "endpoint",
        "express",
        "fastapi",
        "flask",
        "django",
        "node",
        "python",
        "database",
        "db",
        "sql",
        "auth",
        "controller",
        "route",
        "middleware",
    )
    if any(marker in text for marker in backend_markers):
        return True
    root = os.path.abspath(project_root or _current_project_root_path(False) or os.getcwd())
    backend_paths = ("server.js", "app.py", "manage.py", "backend", "api", "src", "package.json", "pyproject.toml")
    try:
        for rel in backend_paths:
            if os.path.exists(os.path.join(root, rel)):
                return True
    except Exception:
        return False
    return False


def _available_modes() -> List[str]:
    modes = [AppMode.CHAT]
    if _bool_from_any(APP_SETTINGS.get("plan_mode_enabled"), False):
        modes.append(AppMode.PLAN)
    if _bool_from_any(APP_SETTINGS.get("code_mode_enabled"), False):
        modes.append(AppMode.CODE)
    if _is_debug_mode_enabled():
        modes.append(AppMode.DEBUG)
    if _is_analyst_mode_enabled():
        modes.append(AppMode.ANALYST)
    return modes


def get_mode_prompt() -> str:
    use_uni = _supports_unicode_ui()
    diamond = "◆" if use_uni else "*"
    arrow   = "›" if use_uni else ">"
    _mode_colors = {
        AppMode.CHAT:    Colors.MODE_CHAT,
        AppMode.PLAN:    Colors.MODE_PLAN,
        AppMode.CODE:    Colors.MODE_CODE,
        AppMode.DEBUG:   Colors.MODE_DEBUG,
        AppMode.ANALYST: Colors.MODE_ANALYST,
    }
    color = _mode_colors.get(CURRENT_MODE, Colors.CMDAI_GRAY)
    label = CURRENT_MODE.upper()
    return (
        f"{color}{diamond}{Colors.ENDC} "
        f"{Colors.BOLD}{color}{label}{Colors.ENDC} "
        f"{Colors.CMDAI_GRAY}{arrow}{Colors.ENDC} "
    )


def _strip_mode_prompt_prefix(text: str) -> str:
    raw = _strip_ansi(str(text or ""))
    stripped = raw.lstrip()
    match = re.match(
        r"^(?:(?:◆|\*|\?)\s*)?(?:CHAT|PLAN|CODE|DEBUG|ANALYST)\s*(?:›|>|\?)\s*",
        stripped,
        re.IGNORECASE,
    )
    if not match:
        return str(text or "")
    return stripped[match.end() :].lstrip()


class Colors:
    ENDC        = "\033[0m"
    BOLD        = "\033[1m"
    DIM         = "\033[2m"
    UNDERLINE   = "\033[4m"


    WHITE       = "\033[97m"
    GRAY        = "\033[37m"
    DARK_GRAY   = "\033[90m"


    MODE_CHAT    = "\033[38;5;78m"
    MODE_PLAN    = "\033[38;5;214m"
    MODE_CODE    = "\033[38;5;39m"
    MODE_DEBUG   = "\033[38;5;203m"
    MODE_ANALYST = "\033[38;5;141m"


    CMDAI_ACCENT = "\033[38;5;78m"
    CMDAI_GRAY   = "\033[38;5;244m"
    CMDAI_DIM    = "\033[38;5;240m"
    CMDAI_BORDER = "\033[38;5;238m"
    CMDAI_TEXT   = "\033[38;5;252m"
    CMDAI_GREEN  = "\033[38;5;78m"
    CMDAI_BLUE   = "\033[38;5;75m"


    SUCCESS      = "\033[38;5;78m"
    WARNING      = "\033[93m"
    FAIL         = "\033[91m"
    INFO         = "\033[38;5;75m"
    ACTION_STATUS= "\033[90m"


    BOLD_GREEN   = "\033[1;38;5;78m"
    BOLD_BLUE    = "\033[1;38;5;75m"
    BOLD_YELLOW  = "\033[1;93m"
    BOLD_RED     = "\033[1;91m"
    BOLD_CYAN    = "\033[1;96m"


    HEADER       = "\033[95m"
    OKBLUE       = "\033[94m"
    OKCYAN       = "\033[96m"
    OKGREEN      = "\033[92m"
    BG_GREEN     = "\033[42m"
    BG_BLUE      = "\033[44m"
    BG_YELLOW    = "\033[43m"


    SYNTAX_KEYWORD  = "\033[38;5;204m"
    SYNTAX_STRING   = "\033[38;5;114m"
    SYNTAX_COMMENT  = "\033[38;5;240m"
    SYNTAX_NUMBER   = "\033[38;5;180m"
    SYNTAX_FUNCTION = "\033[38;5;81m"


HAS_AI_ENGINE = False
LAST_UPDATE_STATUS = None
http_server = None
loader = None
_LLAMA_LOG_CONFIGURED = False
_LLAMA_LOG_CALLBACK = None
TERMINAL_CHAT_HISTORY: List[Dict[str, str]] = []
INPUT_AREA_START_ROW = 1
INPUT_AREA_CLEAR_LINES = 4
LOG_SCROLL_TOP_ROW = 1
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STATUS_CALL_RE = re.compile(
    r"\[\[CALL:STATUS\]\](.*?)\[\[/CALL\]\]", re.IGNORECASE | re.DOTALL
)
ACTION_RE = re.compile(r"\[\[ACTION\]\](.*?)\[\[/ACTION\]\]", re.IGNORECASE | re.DOTALL)
CMD_CALL_RE = re.compile(
    r"\[\[CALL:CMD\]\](.*?)\[\[/CALL\]\]", re.IGNORECASE | re.DOTALL
)
ALLOWED_STATUS_LABELS = {
    "Reading files",
    "Analyzing code",
    "Creating plan",
    "Writing steps",
    "Reading code",
    "Writing files",
    "Adding features",
    "Applying changes",
    "Validating output",
    "Running checks",
    "Finalizing response",
    "Working",
}

_UI_NOTE_LOCK = threading.Lock()
_UI_NOTE_TEXT = ""
_UI_NOTE_EXPIRES_AT = 0.0


def _ui_set_note(text: str, ttl_sec: float = 2.5) -> None:
    global _UI_NOTE_TEXT, _UI_NOTE_EXPIRES_AT
    msg = str(text or "").strip()
    if not msg:
        return
    now = time.time()
    try:
        ttl = float(ttl_sec)
    except Exception:
        ttl = 2.5
    ttl = max(0.3, min(ttl, 10.0))
    with _UI_NOTE_LOCK:
        _UI_NOTE_TEXT = msg
        _UI_NOTE_EXPIRES_AT = now + ttl


def _ui_get_note() -> str:
    global _UI_NOTE_TEXT, _UI_NOTE_EXPIRES_AT
    now = time.time()
    with _UI_NOTE_LOCK:
        if not _UI_NOTE_TEXT:
            return ""
        if now >= float(_UI_NOTE_EXPIRES_AT or 0.0):
            _UI_NOTE_TEXT = ""
            _UI_NOTE_EXPIRES_AT = 0.0
            return ""
        return str(_UI_NOTE_TEXT or "").strip()

DEFAULT_APP_SETTINGS: Dict[str, Any] = {
    "plan_mode_enabled": False,
    "code_mode_enabled": False,
    "debug_mode_enabled": False,
    "analyst_mode_enabled": False,
    "auto_accept_plan": False,
    "auto_accept_code": True,
    "auto_accept_debug": True,
    "quality_gate_enabled": True,
    "confirm_exit": True,
    "allow_ai_commands": True,
    "confirm_ai_commands": True,
    "pin_input_top": True,
    "hide_top_input_border": False,
    "restrict_writes_to_open_file": True,
    "code_mode_without_plan": False,
    "prefer_fragment_edits": True,
    "require_fragment_edits": True,


    "ide_require_host": False,
    "ide_open_target_on_approval": False,
    "ide_auto_open_written_files": False,
    "ai_command_timeout_sec": 25,
}
APP_SETTINGS: Dict[str, Any] = dict(DEFAULT_APP_SETTINGS)


def _should_auto_open_written_files() -> bool:
    return _bool_from_any(APP_SETTINGS.get("ide_auto_open_written_files"), False)


FILE_INDEX: Dict[str, List[str]] = {}


def _build_file_index(project_root: str) -> None:
    root = os.path.abspath(project_root or os.getcwd())
    for root_dir, _, files in os.walk(root):
        for fname in files:
            FILE_INDEX.setdefault(fname, []).append(os.path.join(root_dir, fname))


def _find_file_in_index_or_project(
    filename: str, project_root: str, max_depth: int = 5
) -> str:
    name = (filename or "").strip()
    if not name:
        return ""
    root = os.path.abspath(project_root or os.getcwd())

    if not FILE_INDEX:
        _build_file_index(root)
    paths = FILE_INDEX.get(name, [])
    if paths:

        for p in paths:
            try:
                if os.path.commonpath([root, os.path.abspath(p)]) == root:
                    return os.path.abspath(p)
            except Exception:
                continue
        return os.path.abspath(paths[0])


    for dirpath, dirs, files in os.walk(root):
        depth = dirpath[len(root) :].count(os.sep)
        if depth > max_depth:
            dirs[:] = []
            continue
        if name in files:
            return os.path.abspath(os.path.join(dirpath, name))
    return ""


def _get_active_window_title_direct() -> str:
    if os.name != "nt":
        return ""
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return ""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buff = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buff, length + 1)
    return buff.value or ""


def _parse_simple_filename_from_title(title: str) -> str:
    raw = (title or "").strip()
    if not raw:
        return ""
    parts = re.split(r"\s[-–—]\s", raw)
    if not parts:
        return ""
    candidate = (parts[0] or "").strip()
    if "." not in candidate:
        return ""
    return candidate


def _settings_path() -> str:
    return os.path.join(os.getcwd(), SETTINGS_FILENAME)


def _ensure_parent_dir(path: str) -> None:
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    except Exception:
        pass


def _ensure_json_file(path: str, payload: Any) -> bool:
    if not path:
        return False
    try:
        if os.path.exists(path):
            return False
    except Exception:

        return False
    try:
        _ensure_parent_dir(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return True
    except Exception:
        return False


def _bool_from_any(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def load_app_settings() -> None:
    global APP_SETTINGS
    APP_SETTINGS = dict(DEFAULT_APP_SETTINGS)
    path = _settings_path()
    if not os.path.exists(path):

        _ensure_json_file(path, dict(DEFAULT_APP_SETTINGS))
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return
    except Exception:
        return

    merged = dict(DEFAULT_APP_SETTINGS)
    merged.update(raw)
    for key in (
        "plan_mode_enabled",
        "code_mode_enabled",
        "debug_mode_enabled",
        "analyst_mode_enabled",
        "auto_accept_plan",
        "auto_accept_code",
        "auto_accept_debug",
        "quality_gate_enabled",
        "confirm_exit",
        "allow_ai_commands",
        "confirm_ai_commands",
        "pin_input_top",
        "hide_top_input_border",
        "restrict_writes_to_open_file",
        "code_mode_without_plan",
        "prefer_fragment_edits",
        "require_fragment_edits",
        "ide_require_host",
        "ide_open_target_on_approval",
        "ide_auto_open_written_files",
    ):
        merged[key] = _bool_from_any(merged.get(key), DEFAULT_APP_SETTINGS[key])
    try:
        merged["ai_command_timeout_sec"] = max(
            3,
            min(
                120,
                int(
                    merged.get(
                        "ai_command_timeout_sec",
                        DEFAULT_APP_SETTINGS["ai_command_timeout_sec"],
                    )
                ),
            ),
        )
    except Exception:
        merged["ai_command_timeout_sec"] = DEFAULT_APP_SETTINGS[
            "ai_command_timeout_sec"
        ]
    APP_SETTINGS = merged


def save_app_settings() -> bool:
    try:
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(APP_SETTINGS, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


class DebugEngine:
    FRAME_RE = re.compile(
        r'File "(?P<file>.+?)", line (?P<line>\d+)(?:, in (?P<func>.+))?'
    )
    ERROR_RE = re.compile(r"^(?P<etype>[A-Za-z_][A-Za-z0-9_\.]*)(?::\s*(?P<msg>.*))?$")

    def __init__(self):
        self.history: List[Dict[str, Any]] = []
        self.last_traceback_text = ""
        self.last_analysis: Dict[str, Any] = {}
        self._load_history()

    def _path(self) -> str:

        try:
            import tempfile
            debug_dir = os.path.join(tempfile.gettempdir(), "cmdai_debug")
            os.makedirs(debug_dir, exist_ok=True)
            return os.path.join(debug_dir, DEBUG_HISTORY_FILENAME)
        except Exception:

            root = os.getcwd()
            try:
                if code_file_manager and hasattr(code_file_manager, "_effective_project_root"):
                    root = str(code_file_manager._effective_project_root() or root)
            except Exception:
                root = os.getcwd()
            try:
                root = os.path.abspath(root)
            except Exception:
                pass
            return os.path.join(root, DEBUG_HISTORY_FILENAME)

    def _load_history(self) -> None:
        path = self._path()
        if not os.path.exists(path):
            self.history = []

            _ensure_json_file(path, [])
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.history = data if isinstance(data, list) else []
        except Exception:
            self.history = []

    def _save_history(self) -> None:
        try:
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(self.history[-80:], f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def parse_traceback(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        lines = [line.rstrip() for line in raw.splitlines() if line.strip()]
        frames = []
        for idx, line in enumerate(lines):
            match = self.FRAME_RE.search(line)
            if not match:
                continue
            code_line = ""
            if idx + 1 < len(lines):
                next_line = lines[idx + 1].strip()
                if next_line and not self.FRAME_RE.search(next_line):
                    code_line = next_line
            frames.append(
                {
                    "file": match.group("file"),
                    "line": int(match.group("line")),
                    "function": (match.group("func") or "").strip() or "<module>",
                    "code": code_line,
                }
            )

        error_type = ""
        error_message = ""
        for line in reversed(lines):
            match = self.ERROR_RE.match(line.strip())
            if match:
                error_type = match.group("etype") or ""
                error_message = match.group("msg") or ""
                break

        primary = frames[-1] if frames else {}
        return {
            "raw": raw,
            "frames": frames,
            "error_type": error_type or "UnknownError",
            "error_message": error_message,
            "primary_file": primary.get("file", ""),
            "primary_line": primary.get("line", 0),
            "primary_function": primary.get("function", ""),
            "primary_code": primary.get("code", ""),
        }

    def identify_root_cause(self, analysis: Dict[str, Any]) -> str:
        error_type = str(analysis.get("error_type", "")).lower()
        code = str(analysis.get("primary_code", "")).strip()
        if "attributeerror" in error_type:
            return "Object is None or wrong type before attribute access."
        if "keyerror" in error_type:
            return "Dictionary key is used without existence check."
        if "indexerror" in error_type:
            return "List or sequence index is outside valid range."
        if "typeerror" in error_type:
            return "Function received unexpected type or invalid None value."
        if "valueerror" in error_type:
            return "Input value is invalid and needs validation before use."
        if "zerodivisionerror" in error_type:
            return "Division happens without zero guard."
        if "filenotfounderror" in error_type:
            return "Path is missing or file existence is not checked."
        if "syntaxerror" in error_type:
            return "Source code has invalid syntax and cannot be parsed."
        if code:
            return f"Failure likely starts at: {code}"
        return "Root cause needs manual inspection of the last failing frame."

    def _solution_options(self, analysis: Dict[str, Any]) -> List[Dict[str, str]]:
        error_type = str(analysis.get("error_type", "")).lower()
        generic = [
            {
                "title": "Add validation",
                "pros": "Fast and safe guard before failure point.",
                "cons": "May hide deeper data-flow problem.",
            },
            {
                "title": "Normalize inputs",
                "pros": "Prevents repeated edge-case failures.",
                "cons": "Needs clear default behavior.",
            },
            {
                "title": "Refactor call path",
                "pros": "Best long-term fix when bug is structural.",
                "cons": "More code changes and retesting needed.",
            },
        ]
        if "attributeerror" in error_type:
            generic[0]["title"] = "Check for None before access"
        elif "keyerror" in error_type:
            generic[0]["title"] = "Use dict.get or key guard"
        elif "indexerror" in error_type:
            generic[0]["title"] = "Guard index bounds"
        elif "zerodivisionerror" in error_type:
            generic[0]["title"] = "Add zero division guard"
        return generic

    def analyze_traceback(self, text: str) -> Dict[str, Any]:
        analysis = self.parse_traceback(text)
        analysis["root_cause"] = self.identify_root_cause(analysis)
        analysis["solutions"] = self._solution_options(analysis)
        return analysis

    def record_error(
        self, source: str, text: str, analysis: Optional[Dict[str, Any]] = None
    ) -> None:
        payload = analysis or self.analyze_traceback(text)
        item = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "error_type": payload.get("error_type", "UnknownError"),
            "error_message": payload.get("error_message", ""),
            "primary_file": payload.get("primary_file", ""),
            "primary_line": payload.get("primary_line", 0),
            "root_cause": payload.get("root_cause", ""),
        }
        self.last_traceback_text = str(text or "")
        self.last_analysis = dict(payload)
        self.history.append(item)
        self._save_history()

    def stack_summary(self, analysis: Dict[str, Any]) -> List[str]:
        rows = []
        for idx, frame in enumerate(analysis.get("frames", []), 1):
            file_name = os.path.basename(frame.get("file", ""))
            rows.append(
                f"{idx}. {file_name}:{frame.get('line', 0)} in {frame.get('function', '<module>')}"
            )
        return rows or ["No stack frames detected."]

    def quick_fixes(self, analysis: Dict[str, Any]) -> List[str]:
        error_type = str(analysis.get("error_type", "")).lower()
        fixes = []
        if "keyerror" in error_type:
            fixes.extend(["Use dict.get(key)", "Check key in dict before read"])
        elif "attributeerror" in error_type:
            fixes.extend(["Add None guard", "Validate object type before access"])
        elif "typeerror" in error_type:
            fixes.extend(["Validate function args", "Add safe defaults for None"])
        elif "indexerror" in error_type:
            fixes.extend(["Check list length", "Return early when collection is empty"])
        elif "zerodivisionerror" in error_type:
            fixes.extend(["Guard denominator == 0", "Return fallback value"])
        elif "filenotfounderror" in error_type:
            fixes.extend(["Check os.path.exists", "Create file/path before read"])
        else:
            fixes.extend(["Add input validation", "Add defensive error handling"])
        fixes.append("Write regression test for failing scenario")
        return fixes[:3]

    def recurring_patterns(self) -> List[str]:
        counts: Dict[str, int] = {}
        for item in self.history:
            key = str(item.get("error_type", "UnknownError"))
            counts[key] = counts.get(key, 0) + 1
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
        return [f"{name}: {count}" for name, count in ordered[:8]] or [
            "No patterns yet."
        ]


class AnalystEngine:
    def __init__(self):
        self.last_report: Dict[str, List[str]] = {}

    def _project_root(self) -> str:
        if code_file_manager:
            return code_file_manager._effective_project_root()
        return os.getcwd()

    def _selected_file(self) -> str:
        if ide_integration:
            selected = getattr(ide_integration, "selected_file", None)
            if selected:
                return os.path.abspath(selected)
        return ""

    def _python_files(self, limit: int = 120) -> List[str]:
        root = self._project_root()
        out: List[str] = []
        for base, dirs, files in os.walk(root):
            dirs[:] = [
                d
                for d in dirs
                if d
                not in {
                    ".git",
                    "__pycache__",
                    ".idea",
                    "node_modules",
                    ".venv",
                    "venv",
                    "env",
                }
                and not self._should_skip_relpath(
                    os.path.relpath(os.path.join(base, d), root).replace("\\", "/")
                )
            ]
            for name in files:
                if name.endswith(".py"):
                    rel = os.path.relpath(os.path.join(base, name), root).replace(
                        "\\", "/"
                    )
                    if self._should_skip_relpath(rel):
                        continue
                    out.append(os.path.join(base, name))
                    if len(out) >= limit:
                        return out
        return out

    def _read_text(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def _should_skip_relpath(self, rel_path: str) -> bool:
        rel = str(rel_path or "").replace("\\", "/").strip("./")
        rel_lower = rel.lower()
        return (
            rel_lower.startswith("tests/_tmp")
            or rel_lower.startswith("cmdai_test_")
            or rel_lower.startswith(".venv/")
            or rel_lower.startswith("venv/")
            or rel_lower.startswith("env/")
            or "/site-packages/" in rel_lower
        )

    def _dedupe_rows(self, rows: List[str], limit: int = 24) -> List[str]:
        seen = set()
        cleaned: List[str] = []
        for row in rows:
            value = str(row or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
            if len(cleaned) >= limit:
                break
        return cleaned

    def _is_informational_row(self, row: str) -> bool:
        value = str(row or "").strip().lower()
        if not value:
            return True
        prefixes = (
            "no ",
            "project root:",
            "generated:",
            "benchmark baseline",
            "measure before/after",
            "exact percentage requires",
            "outdated/vulnerability check requires",
        )
        return value.startswith(prefixes)

    def _sanitize_report(self, report: Dict[str, List[str]]) -> Dict[str, List[str]]:
        cleaned: Dict[str, List[str]] = {}
        for key, rows in (report or {}).items():
            normalized = self._dedupe_rows(list(rows or []))
            if key not in {"docs", "deps", "coverage", "benchmark"}:
                actionable = [
                    row for row in normalized if not self._is_informational_row(row)
                ]
                normalized = actionable or normalized[:1]
            cleaned[key] = normalized
        return cleaned

    def _project_name(self) -> str:
        return os.path.basename(self._project_root()) or "Project"

    def _entrypoint_candidates(self, limit: int = 12) -> List[str]:
        entrypoints: List[str] = []
        for _, rel, tree in self._iter_python_modules(limit=limit):
            for node in tree.body:
                if not isinstance(node, ast.If):
                    continue
                test = node.test
                if not isinstance(test, ast.Compare):
                    continue
                if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
                    continue
                if not test.comparators:
                    continue
                comp = test.comparators[0]
                comp_value = comp.value if isinstance(comp, ast.Constant) else None
                if comp_value == "__main__":
                    entrypoints.append(rel)
                    break
        return entrypoints[:limit]

    def _guess_run_command(self, summary: Dict[str, Any]) -> str:
        entrypoints = list(summary.get("entrypoints") or [])
        if entrypoints:
            return f"py -3 {entrypoints[0]}"
        module_rels = [
            str(item.get("rel", "")).strip()
            for item in list(summary.get("modules") or [])
        ]
        if "run.py" in module_rels:
            return "py -3 run.py"
        if module_rels:
            return f"py -3 {module_rels[0]}"
        if summary.get("has_package_json"):
            return "npm run start"
        return "py -3 <entrypoint>.py"

    def _render_overview_text(self, summary: Dict[str, Any]) -> str:
        modules = list(summary.get("modules") or [])
        entrypoints = list(summary.get("entrypoints") or [])
        commands = list(summary.get("commands") or [])
        if commands:
            return f"{summary['name']} is a local AI coding assistant workspace with command-driven workflows."
        if entrypoints:
            if len(entrypoints) == 1:
                return f"{summary['name']} is a small Python project with entrypoint `{entrypoints[0]}`."
            return f"{summary['name']} is a Python project with {len(entrypoints)} runnable entrypoints."
        if modules:
            return f"{summary['name']} is a Python project with {len(modules)} source file(s)."
        return f"{summary['name']} is a local project workspace."

    def _python_module_summaries(self, limit: int = 8) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path, rel, tree in self._iter_python_modules(limit=limit):
            functions = [
                node.name
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
            ]
            classes = [
                node.name
                for node in tree.body
                if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
            ]
            rows.append(
                {
                    "path": path,
                    "rel": rel,
                    "functions": functions[:6],
                    "classes": classes[:6],
                }
            )
        return rows

    def _project_summary(self) -> Dict[str, Any]:
        root = self._project_root()
        modules = self._python_module_summaries(limit=12)
        entrypoints = self._entrypoint_candidates(limit=12)
        model_files = []
        models_dir = os.path.join(root, "models")
        if os.path.isdir(models_dir):
            try:
                model_files = sorted(
                    [
                        name
                        for name in os.listdir(models_dir)
                        if os.path.isfile(os.path.join(models_dir, name))
                    ]
                )[:8]
            except Exception:
                model_files = []
        commands = []
        run_py_path = os.path.join(root, "run.py")
        if os.path.isfile(run_py_path):
            for row in self._read_text(run_py_path).splitlines():
                match = re.search(r"""\(\s*["'](/[^"']+)["']\s*,""", row)
                if match:
                    commands.append(match.group(1))
        return {
            "name": self._project_name(),
            "root": root,
            "modules": modules,
            "entrypoints": entrypoints,
            "models": model_files,
            "commands": commands[:12],
            "has_requirements": os.path.exists(os.path.join(root, "requirements.txt")),
            "has_package_json": os.path.exists(os.path.join(root, "package.json")),
            "has_tests": os.path.isdir(os.path.join(root, "tests")),
        }

    def _render_readme(self, summary: Dict[str, Any]) -> str:
        run_command = self._guess_run_command(summary)
        lines = [
            f"# {summary['name']}",
            "",
            "Generated by CMDAI Analyst Mode.",
            "",
            "## Overview",
            "",
            self._render_overview_text(summary),
        ]
        if summary["models"]:
            lines.extend(
                [
                    "",
                    "## Local Models",
                    "",
                ]
            )
            for name in summary["models"][:5]:
                lines.append(f"- `{name}`")
        if summary["commands"]:
            lines.extend(["", "## Main Commands", ""])
            for cmd in summary["commands"][:8]:
                lines.append(f"- `{cmd}`")
        elif summary["entrypoints"]:
            lines.extend(["", "## Entrypoints", ""])
            for rel in summary["entrypoints"][:5]:
                lines.append(f"- `{rel}`")
        lines.extend(
            [
                "",
                "## Project Files",
                "",
            ]
        )
        for module in summary["modules"][:6]:
            lines.append(f"- `{module['rel']}`")
        lines.extend(
            [
                "",
                "## Run",
                "",
                "```powershell",
                run_command,
                "```",
                "",
            ]
        )
        return "\n".join(lines)

    def _render_architecture(self, summary: Dict[str, Any]) -> str:
        lines = [
            "# Architecture",
            "",
            f"Project root: `{summary['root']}`",
            "",
            "## Main Modules",
            "",
        ]
        for module in summary["modules"][:8]:
            details = []
            if module["classes"]:
                details.append(f"classes: {', '.join(module['classes'])}")
            if module["functions"]:
                details.append(f"functions: {', '.join(module['functions'])}")
            suffix = f" ({'; '.join(details)})" if details else ""
            lines.append(f"- `{module['rel']}`{suffix}")
        lines.extend(
            [
                "",
                "## Notes",
                "",
                f"- Project root: `{summary['root']}`.",
                "- `tests/` stores regression and behavior checks when present.",
                "",
            ]
        )
        if summary.get("entrypoints"):
            lines.insert(-1, f"- Main runnable file: `{summary['entrypoints'][0]}`.")
        if summary.get("models"):
            lines.insert(-1, "- `models/` stores local GGUF model files when present.")
        return "\n".join(lines)

    def _render_getting_started(self, summary: Dict[str, Any]) -> str:
        deps = []
        if summary["has_requirements"]:
            deps.append("Install Python dependencies from `requirements.txt`.")
        if summary["has_package_json"]:
            deps.append(
                "Install Node dependencies from `package.json` if frontend tooling is used."
            )
        if not deps:
            deps.append(
                "No dependency manifest was detected; verify local runtime requirements manually."
            )
        lines = [
            "# Getting Started",
            "",
            "## Prerequisites",
            "",
        ]
        for item in deps:
            lines.append(f"- {item}")
        lines.extend(
            [
                "",
                "## Launch",
                "",
                "```powershell",
                self._guess_run_command(summary),
                "```",
                "",
                "## Recommended Flow",
                "",
                "- Open the intended source file in your IDE before editing.",
                "- Run the entrypoint once to confirm the current behavior.",
                "- Add or update tests for changed behavior when possible.",
                "",
            ]
        )
        return "\n".join(lines)

    def _render_troubleshooting(self, summary: Dict[str, Any]) -> str:
        lines = [
            "# Troubleshooting",
            "",
            "## Common Issues",
            "",
            "- If the project does not start, verify the selected entrypoint and Python environment.",
            "- If imports fail, verify the active interpreter and installed dependencies.",
            "- If IDE integration points to the wrong file, set the intended source file again.",
            "- If analysis output is noisy, clean virtualenv or generated files and run `/analyst` again.",
            "",
            "## Diagnostics",
            "",
            "- Run `/status` to inspect current mode, model and IDE integration.",
            f"- Run `{self._guess_run_command(summary)}` to confirm the current entrypoint works.",
            "- Run `/analyst` to generate or refresh project docs and analysis output.",
            "",
        ]
        return "\n".join(lines)

    def _render_faq(self, summary: Dict[str, Any]) -> str:
        return "\n".join(
            [
                "# FAQ",
                "",
                f"Generated for `{summary['name']}`.",
                "",
                "## Why is IDE file shown as None?",
                "",
                "The IDE is detected, but no active project file is bound yet.",
                "Use `/ide file <path>` or open a file via `/ide open <path>`.",
                "",
                "## Why does Analyst mode show static findings?",
                "",
                "Some checks are static heuristics and do not execute code.",
                "",
                "## How to run the app?",
                "",
                "```powershell",
                self._guess_run_command(summary),
                "```",
                "",
            ]
        )

    def _render_contributing(self, summary: Dict[str, Any]) -> str:
        return "\n".join(
            [
                "# Contributing",
                "",
                f"Thanks for contributing to `{summary['name']}`.",
                "",
                "## Workflow",
                "",
                "1. Create a focused change.",
                "2. Run local checks (`py -3 -m unittest discover -s tests -v` if tests exist).",
                "3. Keep changes small and describe behavior impact.",
                "",
                "## Quality Bar",
                "",
                "- No syntax errors.",
                "- No regressions in command routing.",
                "- Keep output concise and actionable.",
                "",
            ]
        )

    def _render_security_policy(self, summary: Dict[str, Any]) -> str:
        return "\n".join(
            [
                "# Security Policy",
                "",
                f"Security policy for `{summary['name']}`.",
                "",
                "## Reporting a Vulnerability",
                "",
                "Open a private report with reproduction steps and affected files.",
                "",
                "## Scope",
                "",
                "- Hardcoded secrets",
                "- Command execution safety",
                "- Unsafe deserialization and eval/exec usage",
                "",
            ]
        )

    def _render_code_of_conduct(self, summary: Dict[str, Any]) -> str:
        return "\n".join(
            [
                "# Code of Conduct",
                "",
                "Be respectful, factual, and collaborative.",
                "",
                "## Expected Behavior",
                "",
                "- Constructive feedback",
                "- Clear technical communication",
                "- Respectful disagreement",
                "",
            ]
        )

    def _iter_python_modules(self, limit: int = 120) -> List[Tuple[str, str, ast.AST]]:
        modules: List[Tuple[str, str, ast.AST]] = []
        for path in self._python_files(limit=limit):
            text = self._read_text(path)
            rel = os.path.relpath(path, self._project_root()).replace("\\", "/")
            try:
                tree = ast.parse(text or "", filename=rel)
            except Exception:
                continue
            modules.append((path, rel, tree))
        return modules

    def _module_name(self, path: str) -> str:
        rel = os.path.relpath(path, self._project_root()).replace("\\", "/")
        if rel.lower().endswith(".py"):
            rel = rel[:-3]
        if rel.endswith("/__init__"):
            rel = rel[:-9]
        return rel.replace("/", ".").strip(".")

    def _local_import_edges(self, tree: ast.AST, path: str) -> List[str]:
        local_modules = {
            self._module_name(item) for item in self._python_files(limit=120)
        }
        edges: List[str] = []
        current_module = self._module_name(path)
        current_parts = current_module.split(".") if current_module else []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.name or "").strip()
                    if name in local_modules:
                        edges.append(name)
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").strip()
                if node.level and current_parts:
                    base_parts = current_parts[: -node.level]
                    if module:
                        module = ".".join(base_parts + module.split("."))
                    else:
                        module = ".".join(base_parts)
                if module in local_modules:
                    edges.append(module)
        return sorted(set(edge for edge in edges if edge and edge != current_module))

    def _function_complexity(self, node: ast.FunctionDef) -> int:
        complexity = 1
        for inner in ast.walk(node):
            if isinstance(
                inner,
                (
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.Try,
                    ast.BoolOp,
                    ast.With,
                    ast.AsyncWith,
                    ast.IfExp,
                    ast.ExceptHandler,
                    ast.Match,
                    ast.comprehension,
                ),
            ):
                complexity += 1
        return complexity

    def analyze_dependencies(self) -> List[str]:
        root = self._project_root()
        rows = []
        req = os.path.join(root, "requirements.txt")
        pkg = os.path.join(root, "package.json")
        if os.path.exists(req):
            deps = [
                line.strip()
                for line in self._read_text(req).splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            rows.append(f"requirements.txt: {len(deps)} entries")
            pinned = sum(1 for dep in deps if "==" in dep)
            unpinned = len(deps) - pinned
            rows.append(f"requirements.txt: pinned={pinned}, flexible={unpinned}")
        if os.path.exists(pkg):
            try:
                data = json.loads(self._read_text(pkg))
                deps_map = data.get("dependencies") or {}
                dev_map = data.get("devDependencies") or {}
                dep_count = len(deps_map) + len(dev_map)
                rows.append(f"package.json: {dep_count} dependencies")
                wildcard = sum(
                    1
                    for version in list(deps_map.values()) + list(dev_map.values())
                    if str(version).strip() in {"*", "latest", ""}
                )
                if wildcard:
                    rows.append(
                        f"package.json: {wildcard} dependencies use wildcard/latest versions"
                    )
            except Exception:
                rows.append("package.json: invalid JSON")
        if not rows:
            rows.append("No requirements.txt or package.json found.")
        rows.append("Outdated/vulnerability check requires online package metadata.")
        return rows

    def analyze_performance(self) -> List[str]:
        rows = []
        target = self._selected_file()
        files = [target] if target else self._python_files(limit=20)
        for path in files:
            text = self._read_text(path)
            rel = os.path.relpath(path, self._project_root()).replace("\\", "/")
            try:
                tree = ast.parse(text or "", filename=rel)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    loop_nodes = [
                        inner
                        for inner in ast.walk(node)
                        if isinstance(inner, (ast.For, ast.AsyncFor, ast.While))
                    ]
                    if len(loop_nodes) >= 2:
                        rows.append(
                            f"{rel}:{node.lineno} {node.name} has nested/repeated loops"
                        )
                    if len(loop_nodes) >= 3:
                        rows.append(
                            f"{rel}:{node.lineno} {node.name} may benefit from caching or precomputation"
                        )
                    calls = [
                        inner for inner in ast.walk(node) if isinstance(inner, ast.Call)
                    ]
                    query_calls = 0
                    for inner in calls:
                        func = inner.func
                        if isinstance(func, ast.Attribute) and func.attr.lower() in {
                            "execute",
                            "query",
                            "fetchone",
                            "fetchall",
                        }:
                            query_calls += 1
                    if query_calls >= 2 and loop_nodes:
                        rows.append(
                            f"{rel}:{node.lineno} {node.name} possible N+1 query pattern"
                        )
            if "while True" in text:
                rows.append(f"{rel}: infinite loop candidate")
        return rows or ["No obvious performance bottlenecks found by static scan."]

    def analyze_refactor(self) -> List[str]:
        rows = []
        for path in self._python_files(limit=20):
            text = self._read_text(path)
            rel = os.path.relpath(path, self._project_root()).replace("\\", "/")
            try:
                tree = ast.parse(text or "", filename=rel)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    length = max(
                        0, getattr(node, "end_lineno", node.lineno) - node.lineno + 1
                    )
                    if length > 40:
                        rows.append(
                            f"{rel}:{node.lineno} {node.name} is long ({length} lines)"
                        )
                    if len(node.args.args) > 5:
                        rows.append(
                            f"{rel}:{node.lineno} {node.name} has many parameters ({len(node.args.args)})"
                        )
                    if self._function_complexity(node) > 10:
                        rows.append(
                            f"{rel}:{node.lineno} {node.name} should be split into smaller units"
                        )
        return rows or ["No strong refactoring hotspots detected."]

    def generate_docs_preview(self) -> List[str]:
        root = self._project_root()
        rows = [f"Project root: {root}"]
        selected = self._selected_file()
        if selected:
            rel = os.path.relpath(selected, root).replace("\\", "/")
            rows.append(f"README.md: overview + setup for {rel}")
            rows.append(f"FAQ.md: usage notes for {rel}")
            rows.append(f"CONTRIBUTING.md: change workflow for {rel}")
            rows.append(f"SECURITY.md: reporting and scope for {rel}")
            rows.append(f"CODE_OF_CONDUCT.md: collaboration rules for {rel}")
        else:
            rows.append("README.md: overview + setup")
            rows.append("FAQ.md: usage notes and common answers")
            rows.append("CONTRIBUTING.md: contribution flow")
            rows.append("SECURITY.md: reporting and scope")
            rows.append("CODE_OF_CONDUCT.md: collaboration rules")
        return rows

    def ensure_project_basics(self) -> List[str]:
        root = self._project_root()
        written: List[str] = []
        summary = self._project_summary()
        doc_templates = {
            "README.md": self._render_readme(summary) + "\n",
            "FAQ.md": self._render_faq(summary) + "\n",
            "CONTRIBUTING.md": self._render_contributing(summary) + "\n",
            "SECURITY.md": self._render_security_policy(summary) + "\n",
            "CODE_OF_CONDUCT.md": self._render_code_of_conduct(summary) + "\n",
        }
        for filename, content in doc_templates.items():
            path = os.path.join(root, filename)
            try:
                if os.path.exists(path):
                    continue
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                written.append(filename)
            except Exception:
                pass
        return written

    def write_analyst_test_report(self, report: Dict[str, List[str]]) -> str:
        root = self._project_root()
        out_path = os.path.join(root, ANALYST_TEST_FILENAME)
        cleaned_report = self._sanitize_report(report)
        lines: List[str] = [
            "# ANALYST TEST REPORT",
            "",
            f"Project root: `{root}`",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            "",
            "## Summary",
            "",
        ]
        order = (
            "deps",
            "perf",
            "refactor",
            "docs",
            "complexity",
            "security",
            "coverage",
            "architecture",
            "style",
            "graph",
            "deadcode",
            "benchmark",
        )
        for key in order:
            rows = cleaned_report.get(key, [])
            lines.append(f"- `{key}`: {len(rows)} item(s)")
        lines.extend(["", "## Top Findings", ""])
        for key in order:
            rows = cleaned_report.get(key, [])
            if not rows:
                continue
            lines.append(f"### {key.upper()}")
            for row in rows[:3]:
                lines.append(f"- {row}")
            lines.append("")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines).rstrip() + "\n")
            return out_path
        except Exception:
            return ""

    def analyze_complexity(self) -> List[str]:
        rows = []
        target = self._selected_file()
        files = [target] if target else self._python_files(limit=20)
        for path in files:
            text = self._read_text(path)
            rel = os.path.relpath(path, self._project_root()).replace("\\", "/")
            try:
                tree = ast.parse(text or "", filename=rel)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    complexity = self._function_complexity(node)
                    if complexity > 10:
                        rows.append(
                            f"{rel}:{node.lineno} {node.name} complexity={complexity}"
                        )
        return rows or ["No functions above complexity threshold."]

    def analyze_security(self) -> List[str]:
        rows = []
        for path in self._python_files(limit=20):
            text = self._read_text(path)
            rel = os.path.relpath(path, self._project_root()).replace("\\", "/")
            for line_no, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if "eval(" in low or "exec(" in low:
                    rows.append(f"{rel}:{line_no} eval/exec usage")
                if "pickle.loads(" in low or "yaml.load(" in low:
                    rows.append(f"{rel}:{line_no} unsafe deserialization")
                if "shell=true" in low:
                    rows.append(f"{rel}:{line_no} subprocess shell=True")
                if "password" in low and "=" in line:
                    rows.append(f"{rel}:{line_no} possible hardcoded secret")
                if "hashlib.md5(" in low or "hashlib.sha1(" in low:
                    rows.append(f"{rel}:{line_no} weak hash usage")
                if re.search(r"api[_-]?key\s*=", low):
                    rows.append(f"{rel}:{line_no} possible hardcoded API key")
        return rows or ["No obvious security issues found by static scan."]

    def analyze_coverage(self) -> List[str]:
        root = self._project_root()
        tests_dir = os.path.join(root, "tests")
        if not os.path.isdir(tests_dir):
            return ["No tests directory found."]
        test_files = [name for name in os.listdir(tests_dir) if name.endswith(".py")]
        rows = [f"tests/: {len(test_files)} test files"]
        source_files = []
        for path in self._python_files(limit=80):
            rel = os.path.relpath(path, root).replace("\\", "/")
            if rel.startswith("tests/"):
                continue
            source_files.append(path)
        covered = 0
        for source_path in source_files:
            stem = os.path.splitext(os.path.basename(source_path))[0]
            expected = f"test_{stem}.py"
            if expected in test_files:
                covered += 1
        if source_files:
            rows.append(
                f"Approx coverage by file presence: {covered}/{len(source_files)} modules"
            )
        selected = self._selected_file()
        if selected:
            stem = os.path.splitext(os.path.basename(selected))[0]
            expected = f"test_{stem}.py"
            rows.append(
                f"Selected file coverage: {'present' if expected in test_files else 'missing'} ({expected})"
            )
        rows.append("Exact percentage requires runtime coverage tool integration.")
        return rows

    def analyze_architecture(self) -> List[str]:
        imports: Dict[str, List[str]] = {}
        for path, rel, tree in self._iter_python_modules(limit=60):
            imports[rel] = self._local_import_edges(tree, path)
        hot = sorted(imports.items(), key=lambda item: (-len(item[1]), item[0]))
        if not hot:
            return ["No architecture data collected."]
        rows = [f"{path}: {len(lines)} local imports" for path, lines in hot[:8]]
        for path, edges in hot[:8]:
            module = (
                path[:-3].replace("/", ".")
                if path.endswith(".py")
                else path.replace("/", ".")
            )
            for edge in edges:
                edge_path = edge.replace(".", "/") + ".py"
                reverse = imports.get(edge_path, [])
                if module in reverse:
                    rows.append(f"circular: {module} <-> {edge}")
                    break
        return rows

    def analyze_style(self) -> List[str]:
        rows = []
        for path in self._python_files(limit=20):
            rel = os.path.relpath(path, self._project_root()).replace("\\", "/")
            for line_no, line in enumerate(self._read_text(path).splitlines(), 1):
                if len(line) > 100:
                    rows.append(f"{rel}:{line_no} long line ({len(line)})")
                if "\t" in line:
                    rows.append(f"{rel}:{line_no} tab indentation")
                if line.rstrip() != line:
                    rows.append(f"{rel}:{line_no} trailing whitespace")
        return rows or ["No obvious style issues found by static scan."]

    def analyze_graph(self) -> List[str]:
        rows = []
        imports_total = 0
        imports: List[Tuple[str, int]] = []
        for path, rel, tree in self._iter_python_modules(limit=60):
            edges = self._local_import_edges(tree, path)
            imports_total += len(edges)
            imports.append((rel, len(edges)))
        imports = sorted(imports, key=lambda item: (-item[1], item[0]))
        summary = [
            f"Modules scanned: {len(imports)}",
            f"Local import edges: {imports_total}",
        ]
        rows.extend(f"{rel}: {count} edges" for rel, count in imports[:8])
        if len(imports) >= 2:
            rows.append(f"Most connected module: {imports[0][0]}")
        return summary + rows

    def analyze_dead_code(self) -> List[str]:
        rows = []
        for path in self._python_files(limit=20):
            text = self._read_text(path)
            rel = os.path.relpath(path, self._project_root()).replace("\\", "/")
            try:
                tree = ast.parse(text or "", filename=rel)
            except Exception:
                continue
            names = [
                node.name for node in tree.body if isinstance(node, ast.FunctionDef)
            ]
            for name in names:
                if text.count(f"{name}(") <= 1:
                    rows.append(f"{rel}: possible unused function {name}")
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_name = (
                            alias.asname or alias.name.split(".")[0]
                        ).strip()
                        if imported_name and text.count(imported_name) <= 1:
                            rows.append(
                                f"{rel}: possible unused import {imported_name}"
                            )
        return rows or ["No obvious dead code found by static scan."]

    def analyze_benchmark(self) -> List[str]:
        selected = self._selected_file()
        rows = ["Benchmark baseline is static in this version."]
        if selected:
            rel = os.path.relpath(selected, self._project_root()).replace("\\", "/")
            rows.append(f"Target for benchmark: {rel}")
            text = self._read_text(selected)
            try:
                tree = ast.parse(text or "", filename=rel)
                hottest = sorted(
                    (
                        (self._function_complexity(node), node.name, node.lineno)
                        for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                    ),
                    reverse=True,
                )
                if hottest:
                    score, name, line_no = hottest[0]
                    rows.append(
                        f"Profile first: {name}() at line {line_no} (complexity={score})"
                    )
            except Exception:
                pass
        rows.append(
            "Measure before/after for hottest functions and store median runtime."
        )
        return rows

    def build_todo_markdown(self) -> str:
        report = self.run_full_analysis()
        sections = [
            ("Dependencies", "deps"),
            ("Performance", "perf"),
            ("Refactor", "refactor"),
            ("Docs", "docs"),
            ("Complexity", "complexity"),
            ("Security", "security"),
            ("Coverage", "coverage"),
            ("Architecture", "architecture"),
            ("Style", "style"),
            ("Graph", "graph"),
            ("Dead Code", "deadcode"),
            ("Benchmark", "benchmark"),
        ]
        lines = ["# CMDAI TODO", "", "Generated by Analyst Mode.", ""]
        for title, key in sections:
            lines.append(f"## {title}")
            rows = report.get(key, [])
            if not rows:
                lines.append("- [ ] Review this area manually")
            else:
                for row in rows[:8]:
                    lines.append(f"- [ ] {row}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def run_full_analysis(self) -> Dict[str, List[str]]:
        report = {
            "deps": self.analyze_dependencies(),
            "perf": self.analyze_performance(),
            "refactor": self.analyze_refactor(),
            "docs": self.generate_docs_preview(),
            "complexity": self.analyze_complexity(),
            "security": self.analyze_security(),
            "coverage": self.analyze_coverage(),
            "architecture": self.analyze_architecture(),
            "style": self.analyze_style(),
            "graph": self.analyze_graph(),
            "deadcode": self.analyze_dead_code(),
            "benchmark": self.analyze_benchmark(),
        }
        cleaned = self._sanitize_report(report)
        self.last_report = dict(cleaned)
        return cleaned


class IDEIntegration:
    PROJECT_ROOT_MARKERS = (
        ".git",
        ".vscode",
        "pyproject.toml",
        "setup.py",
        "package.json",
        ".idea",
        "requirements.txt",
    )
    SUPPORTED_IDES = {
        "windsurf": {
            "name": "Windsurf",
            "windows": ["Windsurf.exe", "windsurf.exe"],
            "linux": ["windsurf"],
            "darwin": ["Windsurf.app"],
            "protocol": "windsurf://",
            "cli_args": "--goto {file}:{line}:{col}",
        },
        "vscode": {
            "name": "Visual Studio Code",
            "windows": ["code.exe", "code.cmd"],
            "linux": ["code", "code-oss"],
            "darwin": ["Visual Studio Code.app", "Code.app"],
            "protocol": "vscode://",
            "cli_args": "--goto {file}:{line}:{col}",
        },
        "cursor": {
            "name": "Cursor",
            "windows": ["cursor.exe", "Cursor.exe"],
            "linux": ["cursor"],
            "darwin": ["Cursor.app"],
            "protocol": "cursor://",
            "cli_args": "--goto {file}:{line}:{col}",
        },
        "pycharm": {
            "name": "PyCharm",
            "windows": ["pycharm64.exe", "pycharm.exe"],
            "linux": ["pycharm", "charm"],
            "darwin": ["PyCharm.app", "PyCharm CE.app"],
            "protocol": "pycharm://",
            "cli_args": "--line {line} {file}",
        },
        "intellij": {
            "name": "IntelliJ IDEA",
            "windows": ["idea64.exe", "idea.exe", "idea.bat", "idea.cmd"],
            "linux": ["idea", "idea.sh"],
            "darwin": ["IntelliJ IDEA.app"],
            "protocol": "idea://",
            "cli_args": "--line {line} {file}",
        },
        "sublime": {
            "name": "Sublime Text",
            "windows": ["subl.exe", "sublime_text.exe"],
            "linux": ["subl", "sublime_text"],
            "darwin": ["Sublime Text.app"],
            "protocol": "subl://",
            "cli_args": "{file}:{line}:{col}",
        },
        "vim": {
            "name": "Vim/Neovim",
            "windows": ["vim.exe", "nvim.exe"],
            "linux": ["vim", "nvim"],
            "darwin": ["vim", "nvim"],
            "protocol": "vim://",
            "cli_args": "+{line} {file}",
        },
    }

    def __init__(self):
        self.detected_ides = []
        self.active_ide = None
        self.project_root = None
        self.selected_file: Optional[str] = None
        self.selected_file_explicit: bool = False


        self._active_ide_locked: bool = False
        self._active_ide_locked_id: str = ""
        self._foreground_proc_cache_pid: int = 0
        self._foreground_proc_cache_name: str = ""
        self._foreground_proc_cache_path: str = ""
        self._foreground_proc_cache_time: float = 0.0
        self.host_ide_id: str = ""
        self._running_ids_cache: List[str] = []
        self._running_ids_cache_time = 0.0
        self._selected_file_cache = ""
        self._selected_file_cache_time = 0.0
        self._selected_file_cache_by_ide: Dict[str, str] = {}
        self._selected_file_cache_time_by_ide: Dict[str, float] = {}
        self._window_titles_cache: List[Tuple[str, str, str]] = []
        self._window_titles_cache_time = 0.0
        self._process_cmdlines_cache: List[Tuple[str, str, str]] = []
        self._process_cmdlines_cache_time = 0.0
        self._vscode_recent_cache: List[str] = []
        self._vscode_recent_cache_time = 0.0
        self._jetbrains_recent_project_dirs_cache: List[str] = []
        self._jetbrains_recent_project_dirs_cache_time = 0.0
        self._detect_ides()
        try:
            self.host_ide_id = self._infer_host_ide_id_from_env()
        except Exception:
            self.host_ide_id = ""

        try:
            preferred = str(self.host_ide_id or "").strip().lower()
            if not preferred:
                preferred = str(self._infer_ide_id_from_env() or "").strip().lower()
            if preferred:
                self.set_active(preferred)
        except Exception:
            pass

    @staticmethod
    def _path_has_segment(path_value: str, segment: str) -> bool:
        path_l = str(path_value or "").strip().lower()
        seg_l = str(segment or "").strip().lower()
        if not path_l or not seg_l:
            return False
        try:
            return bool(
                re.search(r"(^|[\\/])" + re.escape(seg_l) + r"([\\/]|$)", path_l)
            )
        except Exception:
            return False

    def _looks_like_windsurf_exe_path(self, exe_path: str, proc_name: str = "") -> bool:
        exe_l = os.path.normpath(str(exe_path or "")).replace("\\", "/").lower()
        name_l = str(proc_name or "").strip().lower()
        base_l = os.path.basename(exe_l)
        if base_l in {"windsurf.exe", "windsurf"} or name_l in {"windsurf.exe", "windsurf"}:
            return True

        if base_l in {"code.exe", "code", "code-insiders.exe", "code.cmd"} or name_l in {
            "code.exe",
            "code",
            "code-insiders.exe",
            "code.cmd",
        }:
            if (
                "microsoft vs code" in exe_l
                or "visual studio code" in exe_l
                or self._path_has_segment(exe_l, "vscode")
            ):
                return False

            if self._path_has_segment(exe_l, "codeium") and self._path_has_segment(
                exe_l, "windsurf"
            ):
                return True

            return self._path_has_segment(exe_l, "windsurf")
        return self._path_has_segment(exe_l, "codeium") and self._path_has_segment(
            exe_l, "windsurf"
        )

    def _infer_host_ide_id_from_env(self) -> str:
        try:
            env_keys = {str(k or "").upper() for k in os.environ.keys()}
        except Exception:
            env_keys = set()
        term_program = str(os.environ.get("TERM_PROGRAM", "") or "").strip().lower()

        is_vscode_family = (
            "VSCODE_IPC_HOOK_CLI" in env_keys
            or "VSCODE_PID" in env_keys
            or term_program == "vscode"
            or term_program == "visual studio code"
        )
        if is_vscode_family:
            if any(k.startswith("CURSOR_") for k in env_keys):
                return "cursor"
            if any(k.startswith("WINDSURF_") for k in env_keys):
                return "windsurf"
            return "vscode"

        if "IDEA_INITIAL_DIRECTORY" in env_keys or "JETBRAINS_CLIENT" in env_keys:
            if "PYCHARM_HOSTED" in env_keys:
                return "pycharm"
            return "intellij"


        try:
            return self._infer_host_ide_id_from_parent_process()
        except Exception:
            return ""

    def _infer_host_ide_id_from_parent_process(self, max_depth: int = 8) -> str:
        if os.name != "nt":
            return ""
        depth = max(1, min(int(max_depth or 0), 20))
        pid = int(os.getpid())

        ps_cmd = (
            "$pid = "
            + str(pid)
            + ";"
            + "$seen=@{};"
            + "$out=@();"
            + "for($i=0;$i -lt "
            + str(depth)
            + ";$i++){"
            + "if(-not $pid -or $seen[$pid]){break};"
            + "$seen[$pid]=$true;"
            + "$p = Get-CimInstance Win32_Process -Filter (\"ProcessId=\"+$pid) "
            + "| Select-Object ProcessId,ParentProcessId,Name,ExecutablePath;"
            + "if($p){$out+=$p};"
            + "$pid = $p.ParentProcessId;"
            + "}"
            + "$out | ConvertTo-Json -Compress"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return ""
        try:
            data = json.loads(output or "[]")
        except Exception:
            data = []
        if isinstance(data, dict):
            data = [data]
        chain: List[Tuple[str, str]] = []
        if isinstance(data, list):
            for item in data:
                try:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("Name", "") or "").strip().lower()
                    exe = (
                        str(item.get("ExecutablePath", "") or "")
                        .strip()
                        .replace("\\", "/")
                        .lower()
                    )
                    if name or exe:
                        chain.append((name, exe))
                except Exception:
                    continue

        for name, exe in chain:

            if self._looks_like_windsurf_exe_path(exe, name):
                return "windsurf"
            if "cursor" in exe or name == "cursor.exe":
                return "cursor"
            if "pycharm" in exe or name in {"pycharm.exe", "pycharm64.exe"}:
                return "pycharm"
            if "intellij" in exe or "/idea" in exe or name in {"idea.exe", "idea64.exe"}:
                return "intellij"

            is_code_name = (
                name in {"code.exe", "code", "code-insiders.exe", "code - insiders.exe", "code.cmd"}
                or name.endswith("code.exe")
                or name == "code.exe"
                or name == "code - insiders.exe"
                or name == "codeinsiders.exe"
                or name == "code-insiders.exe"
                or name == "code.exe"
                or name == "code.exe"
            )
            is_code_gui = name in {"code.exe", "code - insiders.exe", "code.exe"} or name == "code.exe"
            looks_like_vscode_path = (
                ("microsoft vs code" in exe)
                or ("/microsoft vs code/" in exe)
                or ("visual studio code" in exe)
                or ("/visual studio code/" in exe)
                or exe.endswith("/code.exe")
                or exe.endswith("/code - insiders.exe")
            )
            if is_code_name or is_code_gui or looks_like_vscode_path:
                if self._looks_like_windsurf_exe_path(exe, name):
                    return "windsurf"
                if "cursor" in exe:
                    return "cursor"
                return "vscode"
        return ""

    def _infer_ide_id_from_env(self) -> str:
        try:
            explicit = str(os.environ.get("CMDAI_IDE", "") or "").strip()
            if explicit:
                return _normalize_ide_id_for_match(explicit)
        except Exception:
            pass

        def _first_token(cmd: str) -> str:
            raw = str(cmd or "").strip()
            if not raw:
                return ""

            raw = raw.strip().strip("\"'")
            token = raw.split()[0].strip().strip("\"'")
            return os.path.basename(token).strip().lower()

        try:
            editor_cmd = str(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "")
            editor_token = _first_token(editor_cmd)
            if editor_token:
                mapped = _normalize_ide_id_for_match(editor_token)
                if mapped in self.SUPPORTED_IDES:
                    return mapped
        except Exception:
            pass


        try:
            env_keys = {str(k or "").upper() for k in os.environ.keys()}
            term_program = str(os.environ.get("TERM_PROGRAM", "") or "").strip().lower()
            is_vscode_family = (
                "VSCODE_IPC_HOOK_CLI" in env_keys
                or "VSCODE_PID" in env_keys
                or term_program == "vscode"
                or term_program == "visual studio code"
            )
            if is_vscode_family:

                if any(k.startswith("CURSOR_") for k in env_keys):
                    return "cursor"
                if term_program == "windsurf" or any(
                    k.startswith("WINDSURF_") for k in env_keys
                ):
                    return "windsurf"
                return "vscode"


            if "IDEA_INITIAL_DIRECTORY" in env_keys or "JETBRAINS_CLIENT" in env_keys:
                if "PYCHARM_HOSTED" in env_keys:
                    return "pycharm"
                return "intellij"
        except Exception:
            pass

        return ""

    def _foreground_window_pid(self) -> int:
        if os.name != "nt":
            return 0
        try:
            user32 = ctypes.windll.user32
        except Exception:
            return 0
        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return 0
            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value or 0)
        except Exception:
            return 0

    def _foreground_process_info_cached(
        self, max_age_sec: float = 1.0
    ) -> Tuple[str, str, int]:
        if os.name != "nt":
            return ("", "", 0)
        now = time.time()
        pid = self._foreground_window_pid()
        if (
            pid
            and pid == int(self._foreground_proc_cache_pid or 0)
            and (now - float(self._foreground_proc_cache_time or 0.0)) <= max_age_sec
        ):
            return (
                str(self._foreground_proc_cache_name or ""),
                str(self._foreground_proc_cache_path or ""),
                pid,
            )
        if not pid:
            return ("", "", 0)
        ps_cmd = (
            f"Get-Process -Id {pid} | Select-Object ProcessName,Path | ConvertTo-Json -Compress"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return ("", "", pid)
        try:
            data = json.loads(output or "{}")
        except Exception:
            data = {}
        name = str((data or {}).get("ProcessName", "") or "").strip()
        path = str((data or {}).get("Path", "") or "").strip().replace("\\", "/")
        self._foreground_proc_cache_pid = pid
        self._foreground_proc_cache_name = name
        self._foreground_proc_cache_path = path
        self._foreground_proc_cache_time = now
        return (name, path, pid)

    def _infer_foreground_ide_id(self) -> str:
        if os.name != "nt":
            return ""
        name, path, _pid = self._foreground_process_info_cached(max_age_sec=1.0)
        name_l = str(name or "").strip().lower()
        exe_name = (name_l + ".exe") if name_l and not name_l.endswith(".exe") else name_l
        path_l = str(path or "").strip().lower()

        if self._looks_like_windsurf_exe_path(path_l, exe_name):
            return "windsurf"
        if exe_name in {"cursor.exe", "cursor"} or "cursor" in path_l:
            return "cursor"
        if exe_name in {"code.exe", "code", "code-insiders.exe"}:

            return "windsurf" if self._looks_like_windsurf_exe_path(path_l, exe_name) else "vscode"
        if exe_name in {"pycharm64.exe", "pycharm.exe", "pycharm"} or "pycharm" in path_l:
            return "pycharm"
        if exe_name in {"idea64.exe", "idea.exe", "idea", "idea.sh"} or (
            "intellij" in path_l or "/idea" in path_l or "\\idea" in path_l
        ):
            return "intellij"
        if exe_name in {"sublime_text.exe", "subl.exe", "sublime_text"} or "sublime" in path_l:
            return "sublime"
        if exe_name in {"vim.exe", "nvim.exe", "vim", "nvim"}:
            return "vim"
        return ""

    def _detect_ides(self):
        import platform

        system = platform.system().lower()

        for ide_id, ide_info in self.SUPPORTED_IDES.items():
            exe_names = ide_info.get(system, [])
            if not exe_names:
                exe_names = ide_info.get("linux", [])

            for exe in exe_names:
                path = self._find_executable(exe)
                if path:
                    resolved_id = self._classify_ide_from_path(ide_id, path)
                    resolved_info = self.SUPPORTED_IDES.get(resolved_id, ide_info)
                    path_norm = os.path.normcase(os.path.abspath(path))
                    already_added = any(
                        item.get("id") == resolved_id
                        and os.path.normcase(os.path.abspath(item.get("path", "")))
                        == path_norm
                        for item in self.detected_ides
                    )
                    if already_added:
                        break
                    self.detected_ides.append(
                        {
                            "id": resolved_id,
                            "name": resolved_info["name"],
                            "path": path,
                            "protocol": resolved_info["protocol"],
                            "cli_args": resolved_info["cli_args"],
                        }
                    )
                    if not self.active_ide:
                        self.active_ide = self.detected_ides[-1]
                    break
        self._augment_detected_ides_from_running_processes()
        self._dedupe_detected_ides()

    def _score_detected_ide(self, ide: Dict[str, Any]) -> Tuple[int, int, str]:
        ide_id = str(ide.get("id", "") or "").strip().lower()
        path_value = os.path.abspath(str(ide.get("path", "") or "").strip()) if str(ide.get("path", "") or "").strip() else ""
        path_l = path_value.lower().replace("\\", "/")
        is_exe = 1 if path_l.endswith(".exe") else 0
        looks_running_specific = 0
        if ide_id == "windsurf":
            looks_running_specific = 1 if self._looks_like_windsurf_exe_path(path_l, os.path.basename(path_l)) else 0
        elif ide_id == "vscode":
            looks_running_specific = 1 if (
                path_l and not self._looks_like_windsurf_exe_path(path_l, os.path.basename(path_l))
            ) else 0
        elif ide_id == "cursor":
            looks_running_specific = 1 if "cursor" in path_l else 0
        elif ide_id == "pycharm":
            looks_running_specific = 1 if "pycharm" in path_l else 0
        elif ide_id == "intellij":
            looks_running_specific = 1 if ("intellij" in path_l or "/idea" in path_l) else 0
        return (looks_running_specific, is_exe, path_l)

    def _dedupe_detected_ides(self) -> None:
        if not self.detected_ides:
            return
        best_by_id: Dict[str, Dict[str, Any]] = {}
        for ide in self.detected_ides:
            try:
                ide_id = str(ide.get("id", "") or "").strip().lower()
            except Exception:
                ide_id = ""
            if not ide_id:
                continue
            current = best_by_id.get(ide_id)
            if current is None:
                best_by_id[ide_id] = ide
                continue
            if self._score_detected_ide(ide) > self._score_detected_ide(current):
                best_by_id[ide_id] = ide
        ordered_ids: List[str] = []
        seen_ids = set()
        for ide in self.detected_ides:
            ide_id = str(ide.get("id", "") or "").strip().lower()
            if ide_id and ide_id not in seen_ids and ide_id in best_by_id:
                seen_ids.add(ide_id)
                ordered_ids.append(ide_id)
        self.detected_ides = [best_by_id[ide_id] for ide_id in ordered_ids if ide_id in best_by_id]
        if self.active_ide:
            active_id = str(self.active_ide.get("id", "") or "").strip().lower()
            if active_id in best_by_id:
                self.active_ide = best_by_id[active_id]

    def _augment_detected_ides_from_running_processes(self) -> None:
        if os.name != "nt":
            return
        try:
            rows = self._process_cmdlines_cached(max_age_sec=0.0)
        except Exception:
            rows = []
        if not rows:
            return
        known_paths = {
            os.path.normcase(os.path.abspath(str(item.get("path", "") or "")))
            for item in self.detected_ides
            if str(item.get("path", "") or "").strip()
        }
        for name, exe_path, _cmd in rows:
            try:
                pname = str(name or "").strip().lower()
                pexe = os.path.abspath(str(exe_path or "").strip())
                if not pexe or not os.path.exists(pexe):
                    continue
                ide_id = ""
                if self._looks_like_windsurf_exe_path(pexe, pname):
                    ide_id = "windsurf"
                elif pname in {"code", "code.exe", "code-insiders.exe"}:
                    ide_id = "vscode"
                elif pname in {"cursor", "cursor.exe"} or "cursor" in pexe.lower():
                    ide_id = "cursor"
                elif pname in {"pycharm", "pycharm.exe", "pycharm64", "pycharm64.exe"}:
                    ide_id = "pycharm"
                elif pname in {"idea", "idea.exe", "idea64", "idea64.exe", "idea.cmd", "idea.bat"}:
                    ide_id = "intellij"
                if not ide_id:
                    continue
                norm_path = os.path.normcase(pexe)
                if norm_path in known_paths:
                    continue
                ide_info = self.SUPPORTED_IDES.get(ide_id)
                if not ide_info:
                    continue
                self.detected_ides.append(
                    {
                        "id": ide_id,
                        "name": ide_info["name"],
                        "path": pexe,
                        "protocol": ide_info["protocol"],
                        "cli_args": ide_info["cli_args"],
                    }
                )
                known_paths.add(norm_path)
            except Exception:
                continue

    def _classify_ide_from_path(self, fallback_id: str, exe_path: str) -> str:
        path_l = os.path.normpath(str(exe_path or "")).replace("\\", "/").lower()
        base_l = os.path.basename(path_l)

        if self._looks_like_windsurf_exe_path(path_l, base_l):
            return "windsurf"


        if (
            "microsoft vs code" in path_l
            or "/vscode/" in path_l
            or base_l in {"code.exe", "code.cmd", "code", "code-insiders.exe"}
        ):
            return "vscode"

        if (
            "intellij idea" in path_l
            or "intellijidea" in path_l
            or "/idea-" in path_l
            or "/jetbrains toolbox/" in path_l
            and (
                "/idea" in path_l
                or base_l in {"idea.cmd", "idea.bat", "idea64.exe", "idea.exe"}
            )
            or base_l in {"idea64.exe", "idea.exe", "idea.bat", "idea.cmd", "idea.sh"}
        ):
            return "intellij"

        return fallback_id

    def _find_executable(self, name):
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            full_path = os.path.join(path_dir, name)
            if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                return full_path
            if sys.platform == "win32":
                full_path_exe = full_path + ".exe"
                if os.path.isfile(full_path_exe):
                    return full_path_exe

        common_paths = self._get_common_paths(name)
        for path in common_paths:
            if os.path.exists(path):
                return path
        return None

    def _get_common_paths(self, name):
        paths = []
        import platform

        system = platform.system()

        if system == "Windows":
            program_files = [
                os.environ.get("ProgramFiles", "C:\\Program Files"),
                os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
                os.environ.get("LOCALAPPDATA", ""),
                os.path.expanduser("~\\AppData\\Local\\Programs"),
            ]
            for pf in program_files:
                if pf:
                    paths.extend(
                        [
                            os.path.join(pf, "Windsurf", name),
                            os.path.join(pf, "Windsurf", "bin", name),
                            os.path.join(pf, "Codeium", "Windsurf", name),
                            os.path.join(pf, "Microsoft VS Code", "bin", name),
                            os.path.join(pf, "Cursor", name),
                            os.path.join(pf, "JetBrains", "PyCharm", "bin", name),
                            os.path.join(pf, "JetBrains", "IntelliJ IDEA", "bin", name),
                            os.path.join(
                                pf,
                                "JetBrains",
                                "IntelliJ IDEA Community Edition",
                                "bin",
                                name,
                            ),
                            os.path.join(pf, "JetBrains", "Toolbox", "scripts", name),
                            os.path.join(
                                pf,
                                "JetBrains",
                                "Toolbox",
                                "apps",
                                "IDEA-U",
                                "bin",
                                name,
                            ),
                            os.path.join(
                                pf,
                                "JetBrains",
                                "Toolbox",
                                "apps",
                                "IDEA-C",
                                "bin",
                                name,
                            ),
                            os.path.join(pf, "Sublime Text", name),
                        ]
                    )
                    if str(name or "").lower() in {
                        "idea64.exe",
                        "idea.exe",
                        "idea.cmd",
                        "idea.bat",
                    }:
                        patterns = [
                            os.path.join(
                                pf, "JetBrains", "IntelliJ IDEA*", "bin", name
                            ),
                            os.path.join(
                                pf,
                                "JetBrains",
                                "Toolbox",
                                "apps",
                                "IDEA-U",
                                "*",
                                "*",
                                "bin",
                                name,
                            ),
                            os.path.join(
                                pf,
                                "JetBrains",
                                "Toolbox",
                                "apps",
                                "IDEA-C",
                                "*",
                                "*",
                                "bin",
                                name,
                            ),
                        ]
                        for pattern in patterns:
                            try:
                                paths.extend(glob.glob(pattern))
                            except Exception:
                                pass
        elif system == "Darwin":
            applications = "/Applications"
            paths.extend(
                [
                    os.path.join(
                        applications, f"{name}.app", "Contents", "MacOS", name
                    ),
                    os.path.join(
                        os.path.expanduser("~/Applications"),
                        f"{name}.app",
                        "Contents",
                        "MacOS",
                        name,
                    ),
                ]
            )
        else:
            paths.extend(
                [
                    f"/usr/bin/{name}",
                    f"/usr/local/bin/{name}",
                    f"/opt/{name}/bin/{name}",
                    os.path.expanduser(f"~/.local/bin/{name}"),
                ]
            )

        return paths

    def _find_project_root(self):
        current = os.getcwd()
        markers = list(self.PROJECT_ROOT_MARKERS)

        while current != os.path.dirname(current):
            for marker in markers:
                if os.path.exists(os.path.join(current, marker)):
                    self.project_root = current
                    return
            current = os.path.dirname(current)

        self.project_root = os.getcwd()

    def _detect_project_root_from_path(self, path: str) -> str:
        candidate = os.path.abspath(str(path or "").strip())
        if not candidate:
            return ""
        current = candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
        if not current:
            return ""
        strong_markers = [m for m in self.PROJECT_ROOT_MARKERS if m != ".git"]
        depth = 0
        while current and current != os.path.dirname(current):
            for marker in strong_markers:
                if os.path.exists(os.path.join(current, marker)):
                    return current


            if os.path.exists(os.path.join(current, ".git")) and depth <= 2:
                return current
            current = os.path.dirname(current)
            depth += 1
        fallback = candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
        return os.path.abspath(fallback) if fallback else ""

    def _sync_project_root_from_target(self, path: str) -> str:
        candidate = os.path.abspath(str(path or "").strip())
        if not candidate:
            return ""

        if os.path.isdir(candidate):
            self.project_root = candidate
            return candidate
        root = self._detect_project_root_from_path(candidate)
        if root and os.path.isdir(root):
            self.project_root = root
        return root

    def _is_selected_file_target(self, path: str) -> bool:
        candidate = os.path.abspath(str(path or "").strip())
        if not candidate or os.path.isdir(candidate):
            return False
        if os.path.isfile(candidate):
            return True
        parent_dir = os.path.dirname(candidate)
        if not parent_dir or not os.path.isdir(parent_dir):
            return False
        base = os.path.basename(candidate)
        return bool(base and "." in base)

    def sync_project_context(self, force_refresh: bool = False) -> Dict[str, str]:
        ide_id = self.active_ide.get("id", "").lower() if self.active_ide else ""
        selected = ""
        if ide_id:
            selected = self.ensure_selected_file(force_refresh=force_refresh) or ""
            if not selected and hasattr(self, "get_open_file_for_ide_cached"):
                try:
                    selected = (
                        self.get_open_file_for_ide_cached(
                            ide_id,
                            max_age_sec=0.0 if force_refresh else 6.0,
                        )
                        or ""
                    )
                except Exception:
                    selected = ""
        if selected:
            selected_abs = os.path.abspath(selected)
            if self._is_selected_file_target(selected_abs):
                self.selected_file = selected_abs
            self._sync_project_root_from_target(selected_abs)
        return {
            "project_root": os.path.abspath(self.project_root)
            if self.project_root
            else "",
            "selected_file": os.path.abspath(self.selected_file)
            if self.selected_file
            else "",
        }

    def list_ides(self):
        return self.detected_ides

    def refresh_ides(self):
        previous_active = self.active_ide["id"] if self.active_ide else None
        self.detected_ides = []
        self.active_ide = None
        self._detect_ides()
        try:


            self.host_ide_id = self._infer_host_ide_id_from_env()
        except Exception:
            self.host_ide_id = ""
        running_ids = set(self.detect_running_ide_ids())
        self._running_ids_cache = sorted(running_ids)
        self._running_ids_cache_time = time.time()


        locked_id = str(self._active_ide_locked_id or "").strip().lower()
        if self._active_ide_locked and locked_id:
            if self.set_active(locked_id):
                return self.detected_ides


        env_id = self._infer_ide_id_from_env()
        if env_id and self.set_active(env_id):
            return self.detected_ides


        fg_id = self._infer_foreground_ide_id()
        if fg_id and fg_id in running_ids and self.set_active(fg_id):
            return self.detected_ides

        if previous_active and self.set_active(previous_active):
            return self.detected_ides

        for preferred in ("cursor", "vscode", "windsurf", "pycharm", "intellij", "sublime", "vim"):
            if preferred in running_ids and self.set_active(preferred):
                return self.detected_ides
        return self.detected_ides

    def set_active(self, ide_id):
        previous_id = self.active_ide.get("id", "").lower() if self.active_ide else ""
        if previous_id and self.selected_file and self._is_selected_file_target(self.selected_file):
            self._selected_file_cache_by_ide[previous_id] = os.path.abspath(
                self.selected_file
            )
            self._selected_file_cache_time_by_ide[previous_id] = time.time()
        for ide in self.detected_ides:
            if ide["id"] == ide_id:
                self.active_ide = ide
                cached_for_target = self._selected_file_cache_by_ide.get(
                    str(ide_id or "").lower(), ""
                )
                self.selected_file = (
                    os.path.abspath(cached_for_target)
                    if cached_for_target and self._is_selected_file_target(cached_for_target)
                    else ""
                )
                context = self.sync_project_context(force_refresh=True)
                inferred = str(context.get("selected_file", "") or "").strip()
                if inferred:
                    self.selected_file = inferred
                self.selected_file_explicit = False
                return True
        return False

    def open_file(self, filepath, line=1, col=1):
        if not self.active_ide:
            return {"success": False, "error": "No IDE detected"}

        if not os.path.isabs(filepath) and self.project_root:
            filepath = os.path.join(self.project_root, filepath)

        if not os.path.exists(filepath):
            return {"success": False, "error": f"File not found: {filepath}"}

        try:
            filepath = os.path.abspath(filepath)
            cli_tmpl = self.active_ide.get("cli_args", "{file}")
            exe_path = str(self.active_ide.get("path", "") or "")

            if sys.platform == "win32":
                lower = exe_path.lower()
                if lower.endswith(".cmd") or lower.endswith(".bat"):
                    candidate_exe = os.path.splitext(exe_path)[0] + ".exe"
                    if os.path.exists(candidate_exe):
                        exe_path = candidate_exe
            args = [exe_path]
            if "--goto" in cli_tmpl:
                args.extend(["--goto", f"{filepath}:{int(line)}:{int(col)}"])
            elif "--line" in cli_tmpl:
                args.extend(["--line", str(int(line)), filepath])
            elif cli_tmpl.strip().startswith("+{line}"):
                args.extend([f"+{int(line)}", filepath])
            elif "{file}:{line}:{col}" in cli_tmpl:
                args.append(f"{filepath}:{int(line)}:{int(col)}")
            else:
                args.append(filepath)

            if sys.platform == "win32":


                lower_exe = str(exe_path or "").lower()
                if lower_exe.endswith(".cmd") or lower_exe.endswith(".bat"):
                    full_cmd = subprocess.list2cmdline(args)
                    subprocess.Popen(
                        ["cmd.exe", "/d", "/s", "/c", full_cmd],
                        shell=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        args,
                        shell=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            else:
                subprocess.Popen(
                    args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

            self.selected_file = filepath
            self.selected_file_explicit = True
            active_id = self.active_ide.get("id", "").lower() if self.active_ide else ""
            if active_id:
                self._selected_file_cache_by_ide[active_id] = filepath
                self._selected_file_cache_time_by_ide[active_id] = time.time()
            self._sync_project_root_from_target(filepath)
            return {"success": True, "ide": self.active_ide["name"], "file": filepath}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_file(self, filepath, content=""):
        if not os.path.isabs(filepath) and self.project_root:
            filepath = os.path.join(self.project_root, filepath)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return self.open_file(filepath)

    def get_status(self):
        return {
            "active": self.active_ide["name"] if self.active_ide else None,
            "available": [ide["name"] for ide in self.detected_ides],
            "project_root": self.project_root,
            "selected_file": self.selected_file,
        }

    def set_selected_file(self, filepath: str) -> Dict[str, Any]:
        candidate = (filepath or "").strip().strip("\"'")
        if not candidate:
            return {"success": False, "error": "Missing file path"}
        if not os.path.isabs(candidate):
            preferred_root = ""
            active_id = self.active_ide.get("id", "").lower() if self.active_ide else ""
            if active_id and hasattr(self, "_infer_open_folder_for_ide_id"):
                try:
                    inferred_root = self._infer_open_folder_for_ide_id(active_id)
                    if inferred_root and os.path.isdir(inferred_root):
                        preferred_root = os.path.abspath(inferred_root)
                except Exception:
                    preferred_root = ""
            if not preferred_root and self.project_root and os.path.isdir(self.project_root):
                preferred_root = os.path.abspath(self.project_root)
            if not preferred_root:
                preferred_root = os.getcwd()
            candidate = os.path.join(preferred_root, candidate)
        candidate = os.path.abspath(candidate)
        if os.path.exists(candidate) and not os.path.isfile(candidate):
            return {"success": False, "error": f"Target is not a file: {candidate}"}
        parent_dir = os.path.dirname(candidate)
        if parent_dir and not os.path.isdir(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except Exception as exc:
                return {
                    "success": False,
                    "error": f"Target directory not available: {candidate} ({exc})",
                }
        self.selected_file = candidate
        self.selected_file_explicit = True
        self._sync_project_root_from_target(candidate)
        ide_id = self.active_ide.get("id", "").lower() if self.active_ide else ""
        if ide_id:
            self._selected_file_cache_by_ide[ide_id] = candidate
            self._selected_file_cache_time_by_ide[ide_id] = time.time()
        return {"success": True, "file": candidate}

    def detect_running_ide_ids(self) -> List[str]:
        if not self.detected_ides:
            return []

        running_names = set()
        running_paths_by_name: Dict[str, set] = {}
        running_exe_paths: set = set()
        cmdline_rows: List[Tuple[str, str, str]] = []
        try:
            if os.name == "nt":
                try:
                    proc_csv = subprocess.check_output(
                        [
                            "powershell",
                            "-NoProfile",
                            "-Command",
                            "Get-CimInstance Win32_Process | Select-Object Name,ExecutablePath | ConvertTo-Csv -NoTypeInformation",
                        ],
                        stderr=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                        errors="ignore",
                    )
                    for line in (proc_csv or "").splitlines():
                        row = (line or "").strip()
                        if not row or row.lower().startswith('"name"'):
                            continue
                        cols = [c.strip().strip('"') for c in row.split('","')]
                        if not cols:
                            continue
                        pname = (cols[0] or "").strip().lower()
                        ppath = ""
                        if len(cols) > 1:
                            ppath = (cols[1] or "").strip().lower().replace("\\", "/")
                        if not pname:
                            continue
                        running_names.add(pname)
                        if ppath:
                            running_paths_by_name.setdefault(pname, set()).add(ppath)
                            running_exe_paths.add(ppath)
                except Exception:
                    pass


                try:
                    cmdline_rows = self._process_cmdlines_cached(max_age_sec=0.0)
                except Exception:
                    cmdline_rows = []
                for name, exe, cmd in cmdline_rows:
                    try:
                        pname = str(name or "").strip().lower()
                        pexe = str(exe or "").strip().lower().replace("\\", "/")
                        if pname:
                            running_names.add(pname)
                        if pname and pexe:
                            running_paths_by_name.setdefault(pname, set()).add(pexe)
                            running_exe_paths.add(pexe)
                    except Exception:
                        continue

                output = subprocess.check_output(
                    ["tasklist", "/fo", "csv", "/nh"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                for line in (output or "").splitlines():
                    cols = [c.strip().strip('"') for c in line.split(",")]
                    if cols and cols[0]:
                        running_names.add(cols[0].lower())
            else:
                output = subprocess.check_output(
                    ["ps", "-A", "-o", "comm="],
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                for line in (output or "").splitlines():
                    name = os.path.basename((line or "").strip()).lower()
                    if name:
                        running_names.add(name)
        except Exception:
            return []

        running_ids: List[str] = []
        code_paths = set()
        for proc_name in ("code.exe", "code-insiders.exe", "code"):
            code_paths.update(running_paths_by_name.get(proc_name, set()))
        code_paths = {str(p or "").strip().lower().replace("\\", "/") for p in code_paths if p}


        windsurf_code_running = any(self._looks_like_windsurf_exe_path(p, "code.exe") for p in code_paths)
        non_windsurf_code_paths = {p for p in code_paths if p and not self._looks_like_windsurf_exe_path(p, "code.exe")}
        vscode_code_running = bool(non_windsurf_code_paths)
        if not (windsurf_code_running or vscode_code_running) and cmdline_rows:
            for name, exe, cmd in cmdline_rows:
                try:
                    pname = str(name or "").strip().lower()
                    if pname not in {"code", "code.exe", "code-insiders.exe"}:
                        continue
                    cmd_l = str(cmd or "").strip().lower()
                    if "windsurf" in cmd_l:
                        windsurf_code_running = True
                    elif cmd_l:

                        vscode_code_running = True
                except Exception:
                    continue

        running_exe_paths_norm = {
            str(p or "").strip().lower().replace("\\", "/") for p in (running_exe_paths or set()) if p
        }
        windsurf_exe_running = any(self._looks_like_windsurf_exe_path(p, "") for p in running_exe_paths_norm)
        vscode_exe_running = any(
            ("microsoft vs code" in p)
            or ("/microsoft vs code/" in p)
            or ("/code - insiders/" in p)
            or ("/visual studio code/" in p)
            for p in running_exe_paths_norm
        )

        for ide in self.detected_ides:
            exe = os.path.basename((ide.get("path") or "")).lower()
            ide_path_norm = (
                os.path.normcase(os.path.abspath(str(ide.get("path") or "")))
                .replace("\\", "/")
                .lower()
            )
            candidates = set()
            if exe:
                candidates.add(exe)
                if exe.endswith(".cmd"):
                    candidates.add(exe[:-4] + ".exe")
            if ide.get("id") == "vscode":
                candidates.update({"code.exe", "code", "code-insiders.exe"})
            elif ide.get("id") == "cursor":
                candidates.update({"cursor.exe", "cursor"})
            elif ide.get("id") == "windsurf":
                candidates.update({"windsurf.exe", "windsurf"})
            elif ide.get("id") == "pycharm":
                candidates.update({"pycharm64.exe", "pycharm.exe", "pycharm"})
            elif ide.get("id") == "intellij":
                candidates.update(
                    {
                        "idea64.exe",
                        "idea.exe",
                        "idea.bat",
                        "idea.cmd",
                        "idea",
                        "idea.sh",
                        "idea64",
                    }
                )
            elif ide.get("id") == "sublime":
                candidates.update({"sublime_text.exe", "subl.exe", "sublime_text"})
            elif ide.get("id") == "vim":
                candidates.update({"vim.exe", "nvim.exe", "vim", "nvim"})

            ide_id = str(ide.get("id", "") or "").strip().lower()


            is_running = bool(ide_path_norm) and (ide_path_norm in running_exe_paths_norm)

            if ide_id == "vscode":

                if not is_running:
                    is_running = vscode_exe_running or vscode_code_running
                if not is_running and any(c in running_names for c in {"code-insiders.exe"}):
                    is_running = True
            else:
                if not is_running:
                    is_running = any(c in running_names for c in candidates)

            if ide_id == "windsurf" and (windsurf_code_running or windsurf_exe_running):
                is_running = True

            if is_running:
                running_ids.append(ide["id"])
        return running_ids

    def get_running_ide_ids_cached(self, max_age_sec: float = 3.0) -> List[str]:
        now = time.time()
        if (
            self._running_ids_cache
            and (now - float(self._running_ids_cache_time or 0.0)) <= max_age_sec
        ):
            return list(self._running_ids_cache)
        try:
            running = self.detect_running_ide_ids()
        except Exception:
            running = list(self._running_ids_cache)
        self._running_ids_cache = list(running)
        self._running_ids_cache_time = now
        return list(self._running_ids_cache)

    def _active_ide_process_names(self) -> List[str]:
        ide_id = self.active_ide.get("id", "") if self.active_ide else ""
        return self._ide_process_names_for_id(ide_id)

    def _ide_process_names_for_id(self, ide_id: str) -> List[str]:
        if ide_id == "windsurf":
            return ["windsurf", "windsurf.exe", "code", "code.exe"]
        if ide_id == "vscode":
            return ["code", "code.exe", "code-insiders.exe"]
        if ide_id == "cursor":
            return ["cursor", "cursor.exe"]
        if ide_id == "pycharm":
            return ["pycharm64", "pycharm", "pycharm64.exe", "pycharm.exe"]
        if ide_id == "intellij":
            return ["idea64", "idea", "idea64.exe", "idea.exe", "idea.cmd", "idea.bat"]
        if ide_id == "sublime":
            return ["sublime_text", "sublime_text.exe", "subl.exe"]
        return []

    def _window_row_matches_ide(self, ide_id: str, pname: str, ppath: str, title: str) -> bool:
        target = str(ide_id or "").strip().lower()
        proc_name = str(pname or "").strip().lower()
        proc_path = str(ppath or "").strip().lower().replace("\\", "/")
        title_l = str(title or "").strip().lower()
        if not target:
            return False

        if target == "windsurf":
            if proc_name in {"windsurf", "windsurf.exe"}:
                return True
            if proc_path:
                return self._looks_like_windsurf_exe_path(proc_path, proc_name)
            return "windsurf" in title_l

        if target == "vscode":
            if proc_name not in {"code", "code.exe", "code-insiders.exe"}:
                return False
            if proc_path:
                return not self._looks_like_windsurf_exe_path(proc_path, proc_name)
            if "windsurf" in title_l:
                return False
            return "visual studio code" in title_l or "vs code" in title_l or "vscode" in title_l

        if target == "cursor":
            if proc_name in {"cursor", "cursor.exe"}:
                return True
            if proc_path:
                return "cursor" in proc_path
            return "cursor" in title_l

        if target == "pycharm":
            if proc_name in {"pycharm64", "pycharm", "pycharm64.exe", "pycharm.exe"}:
                return True
            if proc_path:
                return "pycharm" in proc_path
            return "pycharm" in title_l

        if target == "intellij":
            if proc_name in {"idea64", "idea", "idea64.exe", "idea.exe", "idea.cmd", "idea.bat"}:
                return True
            if proc_path:
                return "intellij" in proc_path or "/idea" in proc_path
            return "intellij" in title_l or "idea" in title_l

        if target == "sublime":
            if proc_name in {"sublime_text", "sublime_text.exe", "subl.exe"}:
                return True
            if proc_path:
                return "sublime" in proc_path
            return "sublime" in title_l

        return proc_name in {str(name).strip().lower() for name in self._ide_process_names_for_id(target)}

    def _window_titles_with_process_cached(
        self, max_age_sec: float = 4.0
    ) -> List[Tuple[str, str, str]]:
        if os.name != "nt":
            return []
        now = time.time()
        if (
            self._window_titles_cache
            and (now - float(self._window_titles_cache_time or 0.0)) <= max_age_sec
        ):
            return list(self._window_titles_cache)


        ps_cmd = (
            "$p = Get-Process | Where-Object { $_.MainWindowTitle } "
            "| Select-Object Id,ProcessName,MainWindowTitle; "
            "$w = Get-CimInstance Win32_Process | Select-Object ProcessId,ExecutablePath; "
            "($p | ConvertTo-Json -Compress), '---', ($w | ConvertTo-Json -Compress)"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return list(self._window_titles_cache)
        rows: List[Tuple[str, str, str]] = []
        parts = [p for p in (output or "").splitlines() if str(p or "").strip()]
        joined = "\n".join(parts)
        if "---" in joined:
            left, right = joined.split("---", 1)
        else:
            left, right = joined, ""
        try:
            proc_data = json.loads(left.strip() or "[]")
        except Exception:
            proc_data = []
        try:
            cim_data = json.loads(right.strip() or "[]")
        except Exception:
            cim_data = []
        if isinstance(proc_data, dict):
            proc_data = [proc_data]
        if isinstance(cim_data, dict):
            cim_data = [cim_data]
        exe_by_pid: Dict[int, str] = {}
        if isinstance(cim_data, list):
            for item in cim_data:
                try:
                    if not isinstance(item, dict):
                        continue
                    pid = int(item.get("ProcessId") or 0)
                    exe = str(item.get("ExecutablePath", "") or "").strip().replace(
                        "\\", "/"
                    )
                    if pid and exe:
                        exe_by_pid[pid] = exe
                except Exception:
                    continue
        if isinstance(proc_data, list):
            for item in proc_data:
                try:
                    if not isinstance(item, dict):
                        continue
                    pid = int(item.get("Id") or 0)
                    pname = str(item.get("ProcessName", "") or "").strip().lower()
                    title = str(item.get("MainWindowTitle", "") or "").strip()
                    ppath = str(exe_by_pid.get(pid, "") or "").strip().lower()
                    ppath = ppath.replace("\\", "/")
                    if pname and title:
                        rows.append((pname, title, ppath))
                except Exception:
                    continue
        self._window_titles_cache = rows
        self._window_titles_cache_time = now
        return list(self._window_titles_cache)

    def _process_cmdlines_cached(
        self, max_age_sec: float = 4.0
    ) -> List[Tuple[str, str, str]]:
        if os.name != "nt":
            return []
        now = time.time()
        if (
            self._process_cmdlines_cache
            and (now - float(self._process_cmdlines_cache_time or 0.0)) <= max_age_sec
        ):
            return list(self._process_cmdlines_cache)
        ps_cmd = (
            "Get-CimInstance Win32_Process | "
            "Select-Object Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        )
        try:
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            return list(self._process_cmdlines_cache)
        rows: List[Tuple[str, str, str]] = []
        try:
            data = json.loads(output or "[]")
        except Exception:
            data = []
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list):
            for item in data:
                try:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("Name", "") or "").strip().lower()
                    exe = (
                        str(item.get("ExecutablePath", "") or "")
                        .strip()
                        .replace("\\", "/")
                        .lower()
                    )
                    cmd = str(item.get("CommandLine", "") or "").strip()
                    if name:
                        rows.append((name, exe, cmd))
                except Exception:
                    continue
        self._process_cmdlines_cache = rows
        self._process_cmdlines_cache_time = now
        return list(self._process_cmdlines_cache)

    def _cmdline_path_candidates(self, cmdline: str) -> List[str]:
        raw = str(cmdline or "").strip()
        if not raw:
            return []
        tokens: List[str] = []
        try:
            tokens = shlex.split(raw, posix=False)
        except Exception:
            tokens = raw.split()

        out: List[str] = []
        i = 0
        while i < len(tokens):
            tok = str(tokens[i] or "").strip()
            i += 1
            if not tok:
                continue
            lower = tok.lower()
            if lower in {
                "--folder-uri",
                "--file-uri",
                "--unity-launch",
                "--open-url",
            } and i < len(tokens):
                tok = str(tokens[i] or "").strip()
                i += 1
                lower = tok.lower()
            if tok.startswith("-"):
                continue
            value = tok.strip().strip("\"'")
            if not value:
                continue
            if value.lower().startswith("file://"):
                value = unquote(value[len("file://") :])
                value = value.lstrip("/\\")
            value = value.replace("/", os.sep).replace("\\", os.sep)
            try:
                value = os.path.abspath(os.path.normpath(value))
            except Exception:
                pass
            base = os.path.basename(value).lower()
            if base.endswith((".exe", ".cmd", ".bat")):
                continue
            if value not in out:
                out.append(value)
        return out

    def _is_likely_ide_internal_path(self, path_value: str) -> bool:
        value = os.path.normcase(str(path_value or ""))
        if not value:
            return False

        blocked_parts = [
            os.path.normcase(r"\.vscode\extensions"),
            os.path.normcase(r"\.windsurf\extensions"),
            os.path.normcase(r"\AppData\Local\Microsoft\TypeScript"),
            os.path.normcase(r"\AppData\Local\Programs"),
            os.path.normcase(r"\AppData\Local\JetBrains"),
            os.path.normcase(r"\AppData\Local\Microsoft VS Code"),
            os.path.normcase(r"\Program Files"),
            os.path.normcase(r"\Program Files (x86)"),
            os.path.normcase(r"\tests\_tmp"),
            os.path.normcase(r"\tests\_tmp_visual"),
        ]
        return any(part in value for part in blocked_parts)

    def _normalize_vscode_path(self, value: str) -> str:
        raw = str(value or "").strip().strip("\"'")
        if not raw:
            return ""
        if raw.lower().startswith("file://"):
            raw = unquote(raw[len("file://") :])
        raw = raw.replace("/", os.sep).replace("\\", os.sep)
        if raw.startswith(os.sep) and re.match(rf"^{re.escape(os.sep)}[A-Za-z]:", raw):
            raw = raw[1:]
        raw = os.path.abspath(os.path.normpath(raw))
        if raw.lower().endswith(".code-workspace"):
            raw = os.path.dirname(raw)
        return raw

    def _vscode_recent_workspaces(self, max_age_sec: float = 20.0) -> List[str]:
        now = time.time()
        if (
            self._vscode_recent_cache
            and (now - float(self._vscode_recent_cache_time or 0.0)) <= max_age_sec
        ):
            return list(self._vscode_recent_cache)
        paths: List[str] = []
        seen = set()
        candidates = []
        appdata = os.environ.get("APPDATA", "")
        localapp = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            candidates.extend(
                [
                    os.path.join(appdata, "Code", "User", "storage.json"),
                    os.path.join(
                        appdata, "Code", "User", "globalStorage", "storage.json"
                    ),
                    os.path.join(
                        appdata, "Code", "User", "globalStorage", "recentlyOpened.json"
                    ),
                    os.path.join(appdata, "Code", "User", "recently-opened.json"),
                    os.path.join(appdata, "Code - Insiders", "User", "storage.json"),
                    os.path.join(
                        appdata,
                        "Code - Insiders",
                        "User",
                        "globalStorage",
                        "storage.json",
                    ),
                    os.path.join(
                        appdata,
                        "Code - Insiders",
                        "User",
                        "globalStorage",
                        "recentlyOpened.json",
                    ),
                    os.path.join(
                        appdata, "Code - Insiders", "User", "recently-opened.json"
                    ),
                ]
            )
        if localapp:
            candidates.extend(
                [
                    os.path.join(localapp, "Code", "User", "storage.json"),
                    os.path.join(
                        localapp, "Code", "User", "globalStorage", "storage.json"
                    ),
                    os.path.join(
                        localapp, "Code", "User", "globalStorage", "recentlyOpened.json"
                    ),
                    os.path.join(localapp, "Code", "User", "recently-opened.json"),
                    os.path.join(localapp, "Code - Insiders", "User", "storage.json"),
                    os.path.join(
                        localapp,
                        "Code - Insiders",
                        "User",
                        "globalStorage",
                        "storage.json",
                    ),
                    os.path.join(
                        localapp,
                        "Code - Insiders",
                        "User",
                        "globalStorage",
                        "recentlyOpened.json",
                    ),
                    os.path.join(
                        localapp, "Code - Insiders", "User", "recently-opened.json"
                    ),
                ]
            )

        def _looks_like_path_token(value: str) -> bool:
            if not value:
                return False
            low = value.lower()
            if low.startswith("file://"):
                return True
            return bool(re.match(r"^[a-zA-Z]:[\\/]", value))

        def _extract_paths_from_obj(obj: Any) -> List[str]:
            found: List[str] = []
            if isinstance(obj, str):
                if _looks_like_path_token(obj):
                    found.append(obj)
            elif isinstance(obj, dict):
                for key in (
                    "configPath",
                    "folderUri",
                    "fileUri",
                    "uri",
                    "path",
                    "workspace",
                ):
                    value = obj.get(key)
                    if isinstance(value, str):
                        if _looks_like_path_token(value):
                            found.append(value)
                    elif isinstance(value, dict):
                        nested = value.get("uri") or value.get("path")
                        if isinstance(nested, str):
                            if _looks_like_path_token(nested):
                                found.append(nested)
                if (
                    "id" in obj
                    and isinstance(obj["id"], str)
                    and _looks_like_path_token(obj["id"])
                ):
                    found.append(obj["id"])
            return found

        for path in candidates:
            if not path or not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
            except Exception:
                continue
            if isinstance(data, dict):
                opened = data.get("openedPathsList") or {}
                workspaces = opened.get("workspaces") or []
                folders = opened.get("folders") or []
                files = opened.get("files") or []
                recent = data.get("recentlyOpened") or {}
                recent_workspaces = recent.get("workspaces") or []
                recent_files = recent.get("files") or []
                backup = data.get("backupWorkspaces") or {}
                backup_workspaces = backup.get("workspaces") or []
                backup_folders = backup.get("folders") or []
                windows_state = data.get("windowsState") or {}
                last_active = windows_state.get("lastActiveWindow") or {}
                opened_windows = windows_state.get("openedWindows") or []
                profile_assoc = data.get("profileAssociations") or {}
                profile_workspaces = profile_assoc.get("workspaces") or {}

                items = (
                    list(workspaces)
                    + list(folders)
                    + list(files)
                    + list(recent_workspaces)
                    + list(recent_files)
                    + list(backup_workspaces)
                    + list(backup_folders)
                    + list(opened_windows)
                )

                if isinstance(last_active, dict):
                    items.append(last_active)
                if isinstance(profile_workspaces, dict):
                    for key in profile_workspaces.keys():
                        items.append(key)
            elif isinstance(data, list):
                items = list(data)
            else:
                items = []
            for item in items:
                for raw in _extract_paths_from_obj(item):
                    value = self._normalize_vscode_path(raw)
                    if (
                        not value
                        or self._is_likely_ide_internal_path(value)
                        or _is_test_tmp_path(value)
                    ):
                        continue
                    if value not in seen and os.path.exists(value):
                        seen.add(value)
                        paths.append(value)


        storage_roots = []
        if appdata:
            storage_roots.append(
                os.path.join(appdata, "Code", "User", "workspaceStorage")
            )
            storage_roots.append(
                os.path.join(appdata, "Code - Insiders", "User", "workspaceStorage")
            )
        if localapp:
            storage_roots.append(
                os.path.join(localapp, "Code", "User", "workspaceStorage")
            )
            storage_roots.append(
                os.path.join(localapp, "Code - Insiders", "User", "workspaceStorage")
            )
        workspace_files: List[Tuple[float, str]] = []
        for root in storage_roots:
            if not root or not os.path.isdir(root):
                continue
            try:
                for entry in os.scandir(root):
                    if not entry.is_dir():
                        continue
                    workspace_json = os.path.join(entry.path, "workspace.json")
                    if os.path.isfile(workspace_json):
                        try:
                            mtime = os.path.getmtime(workspace_json)
                        except Exception:
                            mtime = 0.0
                        workspace_files.append((mtime, workspace_json))
            except Exception:
                continue
        workspace_files.sort(key=lambda item: item[0], reverse=True)
        for _, workspace_json in workspace_files[:20]:
            try:
                with open(workspace_json, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
            except Exception:
                continue
            raw_folder = data.get("folder") or ""
            raw_workspace = data.get("workspace") or ""
            cand_raw = raw_folder or raw_workspace
            if isinstance(cand_raw, dict):
                cand_raw = cand_raw.get("uri") or cand_raw.get("path") or ""
            cand = self._normalize_vscode_path(cand_raw)
            if not cand or self._is_likely_ide_internal_path(cand):
                continue
            if cand not in seen and os.path.exists(cand):
                seen.add(cand)
                paths.append(cand)
                break


        state_candidates: List[str] = []
        if appdata:
            state_candidates.extend(
                [
                    os.path.join(
                        appdata, "Code", "User", "globalStorage", "state.vscdb"
                    ),
                    os.path.join(
                        appdata,
                        "Code - Insiders",
                        "User",
                        "globalStorage",
                        "state.vscdb",
                    ),
                ]
            )
        if localapp:
            state_candidates.extend(
                [
                    os.path.join(
                        localapp, "Code", "User", "globalStorage", "state.vscdb"
                    ),
                    os.path.join(
                        localapp,
                        "Code - Insiders",
                        "User",
                        "globalStorage",
                        "state.vscdb",
                    ),
                ]
            )
        for db_path in state_candidates:
            if not db_path or not os.path.isfile(db_path):
                continue
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            except Exception:
                try:
                    conn = sqlite3.connect(db_path)
                except Exception:
                    continue
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT value FROM ItemTable WHERE key LIKE '%recentlyOpened%' OR key LIKE '%openedPathsList%'"
                )
                rows = cur.fetchall()
            except Exception:
                rows = []
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            for (val,) in rows:
                try:
                    data = json.loads(val) if isinstance(val, str) else val
                except Exception:
                    continue
                if isinstance(data, dict):
                    entries = (
                        data.get("entries")
                        or data.get("workspaces")
                        or data.get("folders")
                        or data.get("files")
                        or []
                    )
                elif isinstance(data, list):
                    entries = data
                else:
                    entries = []
                for item in entries:
                    for raw in _extract_paths_from_obj(item):
                        value = self._normalize_vscode_path(raw)
                        if (
                            not value
                            or self._is_likely_ide_internal_path(value)
                            or _is_test_tmp_path(value)
                        ):
                            continue
                        if value not in seen and os.path.exists(value):
                            seen.add(value)
                            paths.append(value)
                if paths:
                    break
        self._vscode_recent_cache = list(paths)
        self._vscode_recent_cache_time = now
        return list(self._vscode_recent_cache)

    def _resolve_vscode_recent_workspace_by_name(self, name_token: str) -> str:
        wanted = self._normalize_project_name_token(name_token)
        if not wanted:
            return ""
        try:
            recent = self._vscode_recent_workspaces()
        except Exception:
            recent = []
        for cand in recent:
            try:
                cand_abs = os.path.abspath(str(cand or ""))
                base = os.path.basename(cand_abs)
                if self._normalize_project_name_token(base) == wanted and os.path.isdir(
                    cand_abs
                ):
                    return cand_abs
            except Exception:
                continue
        return ""

    def _guess_windows_root_folder_by_name(self, name_token: str) -> str:
        if os.name != "nt":
            return ""
        raw = str(name_token or "").strip().strip("\"'")
        if not raw:
            return ""

        if ":" in raw or "/" in raw or "\\" in raw:
            return ""
        if len(raw) < 2 or len(raw) > 64:
            return ""

        if re.search(r"\b\d+\.\d+\.\d+\b", raw) or raw.lower().startswith("release notes"):
            return ""
        for drive in ("C:\\", "D:\\", "E:\\"):
            try:
                candidate = os.path.join(drive, raw)
                if os.path.isdir(candidate):
                    return os.path.abspath(candidate)
            except Exception:
                continue
        return ""

    def _infer_open_folder_from_cmdline(self, ide_id: str, process_set: set) -> str:
        target = str(ide_id or "").strip().lower()
        if os.name != "nt" or not target:
            return ""

        if target not in {"vscode", "windsurf"}:
            return ""
        rows = self._process_cmdlines_cached(max_age_sec=3.0)
        for pname, exe_path, cmdline in rows:
            if pname not in process_set:
                continue
            exe_l = str(exe_path or "").lower()
            cmd_l = str(cmdline or "").lower()
            looks_like_windsurf = (
                ("windsurf" in exe_l)
                or ("codeium" in exe_l)
                or ("windsurf" in cmd_l)
                or ("codeium" in cmd_l)
            )
            if target == "windsurf" and not looks_like_windsurf:
                continue
            if target == "vscode" and looks_like_windsurf:
                continue
            for cand in self._cmdline_path_candidates(cmdline):
                try:
                    if self._is_likely_ide_internal_path(cand):
                        continue
                    if os.path.isdir(cand):
                        return os.path.abspath(cand)
                    if os.path.isfile(cand):

                        return os.path.abspath(os.path.dirname(cand))
                except Exception:
                    continue
        return ""

    def _candidate_files_by_basename(
        self, basename: str, limit: int = 2500
    ) -> List[str]:
        root = self.project_root or os.getcwd()
        wanted = (basename or "").strip().lower()
        if not wanted:
            return []
        matches: List[str] = []
        scanned = 0
        for base, dirs, files in os.walk(root):
            dirs[:] = [
                d
                for d in dirs
                if d not in {".git", "__pycache__", ".idea", "node_modules"}
                and not d.startswith("cmdai_test_")
                and d != "_tmp"
            ]
            for name in files:
                scanned += 1
                if scanned > limit:
                    return matches
                if name.lower() == wanted:
                    matches.append(os.path.abspath(os.path.join(base, name)))
        return matches

    def _resolve_project_title_candidate(self, candidate: str) -> str:
        root = os.path.abspath(self.project_root or os.getcwd())
        value = str(candidate or "").strip().strip("\"'")
        if not value:
            return ""
        normalized = value.replace("\\", "/").lstrip("./")
        if os.path.isabs(value) and os.path.exists(value):
            return os.path.abspath(value)
        if "/" in normalized:
            joined = os.path.abspath(os.path.join(root, normalized))
            try:
                if os.path.commonpath([root, joined]) == root and os.path.exists(
                    joined
                ):
                    return joined
            except Exception:
                pass
        return ""

    def _resolve_project_folder_candidate(self, candidate: str) -> str:
        root = os.path.abspath(self.project_root or os.getcwd())
        value = str(candidate or "").strip().strip("\"'")
        if not value:
            return ""
        normalized = value.replace("\\", "/").lstrip("./")
        if os.path.isabs(value) and os.path.isdir(value):
            return os.path.abspath(value)
        if "/" in normalized:
            joined = os.path.abspath(os.path.join(root, normalized))
            try:
                if os.path.commonpath([root, joined]) == root and os.path.isdir(joined):
                    return joined
            except Exception:
                pass
        project_names = {os.path.basename(root).strip().lower()}
        idea_name_path = os.path.join(root, ".idea", ".name")
        try:
            if os.path.isfile(idea_name_path):
                with open(idea_name_path, "r", encoding="utf-8") as f:
                    idea_name = (f.read() or "").strip().lower()
                if idea_name:
                    project_names.add(idea_name)
        except Exception:
            pass
        wanted = self._normalize_project_name_token(value)
        known_names = {
            self._normalize_project_name_token(name) for name in project_names if name
        }
        if wanted and wanted in known_names:
            return root
        return ""

    def _normalize_project_name_token(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    def _read_idea_project_name(self, project_dir: str) -> str:
        idea_name_path = os.path.join(str(project_dir or ""), ".idea", ".name")
        try:
            if os.path.isfile(idea_name_path):
                with open(idea_name_path, "r", encoding="utf-8") as f:
                    return (f.read() or "").strip()
        except Exception:
            pass
        return ""

    def _jetbrains_config_roots(self) -> List[str]:
        roots: List[str] = []
        for base in (
            os.path.join(os.environ.get("APPDATA", ""), "JetBrains"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "JetBrains"),
            os.path.join(os.environ.get("USERPROFILE", ""), ".config", "JetBrains"),
        ):
            candidate = os.path.abspath(base) if base else ""
            if candidate and os.path.isdir(candidate) and candidate not in roots:
                roots.append(candidate)
        return roots

    def _extract_jetbrains_path_tokens(self, text: str) -> List[str]:
        raw = str(text or "")
        if not raw:
            return []
        patterns = [
            r"file://[^\"'<>\s]+",
            r"\$USER_HOME\$[\\/][^\"'<>\r\n]+",
            r"[A-Za-z]:[\\/][^\"'<>\r\n]+",
        ]
        tokens: List[str] = []
        seen = set()
        for pattern in patterns:
            for match in re.findall(pattern, raw):
                token = str(match or "").strip().rstrip("/>,);]")
                if token and token not in seen:
                    seen.add(token)
                    tokens.append(token)
        return tokens

    def _normalize_jetbrains_project_path(self, token: str) -> str:
        raw = str(token or "").strip().strip("\"'")
        if not raw:
            return ""
        value = raw.replace("&quot;", '"').replace("&amp;", "&")
        if value.startswith("file://"):
            value = unquote(value[len("file://") :])
        value = value.replace("$USER_HOME$", os.path.expanduser("~"))
        value = value.replace("/", os.sep).replace("\\", os.sep)
        if value.startswith(os.sep) and re.match(
            rf"^{re.escape(os.sep)}[A-Za-z]:", value
        ):
            value = value[1:]
        value = os.path.abspath(os.path.normpath(value))
        if value.lower().endswith(f"{os.sep}.idea"):
            value = os.path.dirname(value)
        elif value.lower().endswith(".ipr"):
            value = os.path.dirname(value)
        elif os.path.isfile(value):
            base_name = os.path.basename(value).lower()
            if base_name in {"workspace.xml", "modules.xml", "misc.xml"}:
                parent = os.path.dirname(value)
                if os.path.basename(parent).lower() == ".idea":
                    value = os.path.dirname(parent)
                else:
                    value = os.path.dirname(value)
            else:
                value = os.path.dirname(value)
        if os.path.isdir(value):
            return value
        return ""

    def _jetbrains_recent_project_dirs(self, max_age_sec: float = 20.0) -> List[str]:
        now = time.time()
        if (
            self._jetbrains_recent_project_dirs_cache
            and (now - float(self._jetbrains_recent_project_dirs_cache_time or 0.0))
            <= max_age_sec
        ):
            return list(self._jetbrains_recent_project_dirs_cache)
        options_files: List[str] = []
        for root in self._jetbrains_config_roots():
            for pattern in (
                os.path.join(root, "*", "options", "*project*.xml"),
                os.path.join(root, "*", "options", "*Project*.xml"),
            ):
                for path in glob.glob(pattern):
                    if path not in options_files:
                        options_files.append(path)
        project_dirs: List[str] = []
        seen = set()
        for path in options_files:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
            for token in self._extract_jetbrains_path_tokens(content):
                normalized = self._normalize_jetbrains_project_path(token)
                if not normalized:
                    continue
                normcase = os.path.normcase(normalized)
                if normcase in seen:
                    continue
                seen.add(normcase)
                project_dirs.append(normalized)
        self._jetbrains_recent_project_dirs_cache = list(project_dirs)
        self._jetbrains_recent_project_dirs_cache_time = now
        return list(self._jetbrains_recent_project_dirs_cache)

    def _resolve_jetbrains_recent_project_candidate(self, candidate: str) -> str:
        wanted = self._normalize_project_name_token(candidate)
        if not wanted:
            return ""
        for project_dir in self._jetbrains_recent_project_dirs():
            if not os.path.isdir(project_dir):
                continue
            base_name = self._normalize_project_name_token(
                os.path.basename(project_dir)
            )
            idea_name = self._normalize_project_name_token(
                self._read_idea_project_name(project_dir)
            )
            if wanted and wanted in {base_name, idea_name}:
                return os.path.abspath(project_dir)
        return ""

    def _title_file_candidates(self, title: str) -> List[str]:
        raw = str(title or "").strip()
        if not raw:
            return []
        parts = [raw]
        for sep in (" - ", " — ", " | ", " • ", " · ", " —", " -"):
            next_parts: List[str] = []
            for item in parts:
                next_parts.extend(piece.strip() for piece in item.split(sep))
            parts = next_parts
        candidates: List[str] = []
        seen = set()
        for item in parts:
            value = item.strip().strip("\"'")
            lower = value.lower()
            if not value:
                continue
            if lower.endswith(
                (
                    ".py",
                    ".js",
                    ".ts",
                    ".tsx",
                    ".jsx",
                    ".json",
                    ".md",
                    ".txt",
                    ".yaml",
                    ".yml",
                    ".html",
                    ".css",
                )
            ):
                if value not in seen:
                    seen.add(value)
                    candidates.append(value)
        return candidates

    def _title_folder_candidates(self, title: str) -> List[str]:
        raw = str(title or "").strip()
        if not raw:
            return []
        parts = [raw]
        for sep in (" - ", " — ", " | ", " • ", " · ", " —", " -"):
            next_parts: List[str] = []
            for item in parts:
                next_parts.extend(piece.strip() for piece in item.split(sep))
            parts = next_parts
        for item in re.findall(r"\[([^\]]+)\]|\(([^\)]+)\)", raw):
            bracket_value = next((piece for piece in item if piece), "").strip()
            if bracket_value:
                parts.append(bracket_value)
        candidates: List[str] = []
        seen = set()
        skip_values = {
            "windsurf",
            "visual studio code",
            "vscode",
            "cursor",
            "pycharm",
            "intellij idea",
            "intellij",
            "workspace",
            "project",
        }
        for item in parts:
            value = item.strip().strip("\"'")
            lower = value.lower()
            if not value:
                continue
            if re.search(r"\.[a-z0-9_-]{1,12}$", lower):
                continue
            if lower in skip_values:
                continue
            if len(value) < 3:
                continue
            if value not in seen:
                seen.add(value)
                candidates.append(value)
        return candidates

    def _infer_selected_file_for_ide_id(self, ide_id: str) -> str:
        if os.name != "nt" or not ide_id:
            return ""
        process_names = self._ide_process_names_for_id(str(ide_id).strip().lower())
        if not process_names:
            return ""
        process_set = {
            str(name).strip().lower() for name in process_names if str(name).strip()
        }
        rows = self._window_titles_with_process_cached()
        parsed: List[Tuple[str, str, str]] = []
        for row in rows:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    pname, title, ppath = row[0], row[1], row[2]
                elif isinstance(row, (list, tuple)) and len(row) == 2:
                    pname, title = row[0], row[1]
                    ppath = ""
                else:
                    continue
                parsed.append(
                    (
                        str(pname or "").strip().lower(),
                        str(title or "").strip(),
                        str(ppath or "").strip().lower().replace("\\", "/"),
                    )
                )
            except Exception:
                continue
        titles = [
            title
            for pname, title, ppath in parsed
            if pname in process_set
            and title
            and self._window_row_matches_ide(ide_id, pname, ppath, title)
        ]
        if not titles and str(ide_id).strip().lower() not in {"vscode", "windsurf"}:
            all_titles = [title for pname, title, ppath in parsed if title]
            titles = list(all_titles)

        has_paths = any(bool(ppath) for pname, title, ppath in parsed)
        target_lower = ide_id.strip().lower()

        if has_paths:

            if target_lower == "windsurf":
                titles = [
                    title
                    for pname, title, ppath in parsed
                    if pname in process_set
                    and title
                    and ("windsurf" in (ppath or "") or "codeium" in (ppath or ""))
                ] or titles
            elif target_lower == "vscode":
                titles = [
                    title
                    for pname, title, ppath in parsed
                    if pname in process_set
                    and title
                    and ("windsurf" not in (ppath or "") and "codeium" not in (ppath or ""))
                ] or titles
            elif target_lower == "intellij":
                titles = [
                    title
                    for pname, title, ppath in parsed
                    if pname in process_set and title and "idea" in (ppath or "")
                ] or titles
            elif target_lower == "pycharm":
                titles = [
                    title
                    for pname, title, ppath in parsed
                    if pname in process_set and title and "pycharm" in (ppath or "")
                ] or titles
            elif target_lower == "cursor":
                titles = [
                    title
                    for pname, title, ppath in parsed
                    if pname in process_set and title and "cursor" in (ppath or "")
                ] or titles
        for title in titles:
            for candidate in self._title_file_candidates(title):
                resolved = self._resolve_project_title_candidate(candidate)
                if resolved:
                    return resolved
                if os.path.isabs(candidate) and os.path.exists(candidate):
                    return os.path.abspath(candidate)
                matches = self._candidate_files_by_basename(os.path.basename(candidate))
                if len(matches) == 1:
                    return matches[0]
        return ""

    def _infer_open_folder_for_ide_id(self, ide_id: str) -> str:
        if os.name != "nt" or not ide_id:
            return ""
        target = str(ide_id).strip().lower()
        process_names = self._ide_process_names_for_id(target)
        if not process_names:
            return ""
        process_set = {
            str(name).strip().lower() for name in process_names if str(name).strip()
        }
        cmdline_folder = self._infer_open_folder_from_cmdline(target, process_set)
        if cmdline_folder and os.path.isdir(cmdline_folder):
            return os.path.abspath(cmdline_folder)
        rows = self._window_titles_with_process_cached()
        titles_with_proc: List[Tuple[str, str, str]] = []
        for row in rows:
            try:
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    pname, title, ppath = row[0], row[1], row[2]
                elif isinstance(row, (list, tuple)) and len(row) == 2:
                    pname, title = row[0], row[1]
                    ppath = ""
                else:
                    continue
                titles_with_proc.append(
                    (
                        str(pname or "").strip().lower(),
                        str(title or "").strip(),
                        str(ppath or "").strip().lower().replace("\\", "/"),
                    )
                )
            except Exception:
                continue

        all_titles = [title for pname, title, ppath in titles_with_proc if title]
        titles = [
            title
            for pname, title, ppath in titles_with_proc
            if pname in process_set and title
            and self._window_row_matches_ide(target, pname, ppath, title)
        ]
        if not titles and target not in {"vscode", "windsurf"}:
            titles = list(all_titles)


        has_paths = any(bool(ppath) for pname, title, ppath in titles_with_proc)
        if has_paths and target == "windsurf":
            titles = [
                title
                for pname, title, ppath in titles_with_proc
                if pname in process_set
                and title
                and ("windsurf" in (ppath or "") or "codeium" in (ppath or ""))
            ] or titles
        elif has_paths and target == "vscode":
            titles = [
                title
                for pname, title, ppath in titles_with_proc
                if pname in process_set
                and title
                and ("windsurf" not in (ppath or "") and "codeium" not in (ppath or ""))
            ] or titles

        is_jetbrains = target in {"intellij", "pycharm"}
        for title in titles:
            for candidate in self._title_folder_candidates(title):
                if target == "vscode":


                    resolved_recent = self._resolve_vscode_recent_workspace_by_name(candidate)
                    if resolved_recent:
                        return os.path.abspath(resolved_recent)
                    guessed = self._guess_windows_root_folder_by_name(candidate)
                    if guessed:
                        return os.path.abspath(guessed)
                resolved = self._resolve_project_folder_candidate(candidate)
                if resolved:
                    return os.path.abspath(resolved)
                if is_jetbrains:
                    jb = self._resolve_jetbrains_recent_project_candidate(candidate)
                    if jb:
                        return os.path.abspath(jb)


        if is_jetbrains:
            for title in titles:
                for part in self._title_folder_candidates(title):
                    jb = self._resolve_jetbrains_recent_project_candidate(part)
                    if jb:
                        return os.path.abspath(jb)


        active_target = bool(
            self.active_ide
            and str(self.active_ide.get("id", "")).strip().lower() == target
        )
        if active_target and self.selected_file and os.path.exists(self.selected_file):
            selected_dir = os.path.abspath(os.path.dirname(self.selected_file))
            if not _is_test_tmp_path(
                selected_dir
            ) and not self._is_likely_ide_internal_path(selected_dir):
                return selected_dir
        if target == "vscode":

            recent = self._vscode_recent_workspaces()
            if recent:
                cwd = os.path.abspath(os.getcwd())

                for cand in recent:
                    try:
                        cand_abs = os.path.abspath(cand)
                        if os.path.commonpath([cand_abs, cwd]) == cand_abs:
                            return cand_abs
                    except Exception:
                        continue
                return os.path.abspath(recent[0])
        return ""

    def _infer_selected_file_from_windows_title(self) -> str:


        if os.name == "nt":
            title = _get_active_window_title_direct()
            ide_id = self.active_ide.get("id", "") if self.active_ide else ""
            try:
                proc_name, proc_path, _pid = self._foreground_process_info_cached(max_age_sec=0.5)
            except Exception:
                proc_name, proc_path = "", ""
            if ide_id and not self._window_row_matches_ide(ide_id, proc_name, proc_path, title):
                return ""
            simple_name = _parse_simple_filename_from_title(title)
            project_root = (
                os.path.abspath(self.project_root)
                if self.project_root
                else os.path.abspath(os.getcwd())
            )
            if simple_name:
                path = _find_file_in_index_or_project(simple_name, project_root)
                if path and os.path.exists(path):
                    return os.path.abspath(path)


        ide_id = self.active_ide.get("id", "") if self.active_ide else ""
        return self._infer_selected_file_for_ide_id(ide_id)

    def ensure_selected_file(
        self, max_age_sec: float = 6.0, force_refresh: bool = False
    ) -> str:
        ide_id = self.active_ide.get("id", "").lower() if self.active_ide else ""
        active_cached = self._selected_file_cache_by_ide.get(ide_id, "")
        if (
            not force_refresh
            and self.selected_file
            and self._is_selected_file_target(self.selected_file)
            and (
                not ide_id
                or (
                    active_cached
                    and self._is_selected_file_target(active_cached)
                    and os.path.normcase(os.path.abspath(self.selected_file))
                    == os.path.normcase(os.path.abspath(active_cached))
                )
            )
        ):
            self._sync_project_root_from_target(self.selected_file)
            return os.path.abspath(self.selected_file)
        now = time.time()
        cached = self._selected_file_cache_by_ide.get(ide_id, "")
        cached_time = float(self._selected_file_cache_time_by_ide.get(ide_id, 0.0))
        if (
            not force_refresh
            and cached
            and (now - cached_time) <= max_age_sec
            and self._is_selected_file_target(cached)
        ):
            self.selected_file = cached
            self._selected_file_cache = cached
            self._selected_file_cache_time = cached_time
            self._sync_project_root_from_target(cached)
            return os.path.abspath(self.selected_file)
        inferred = self._infer_selected_file_from_windows_title()
        if inferred and os.path.isfile(inferred):
            self.selected_file = inferred
            self._selected_file_cache = inferred
            self._selected_file_cache_time = now
            if ide_id:
                self._selected_file_cache_by_ide[ide_id] = inferred
                self._selected_file_cache_time_by_ide[ide_id] = now
            self._sync_project_root_from_target(inferred)
            return os.path.abspath(inferred)
        try:
            folder_inferred = self._infer_open_folder_for_ide_id(ide_id)
            if folder_inferred and os.path.isdir(folder_inferred):
                self._sync_project_root_from_target(folder_inferred)
        except Exception:
            pass
        return ""

    def get_open_file_for_ide_cached(
        self, ide_id: str, max_age_sec: float = 6.0
    ) -> str:
        target = (ide_id or "").strip().lower()
        if not target:
            return ""
        now = time.time()
        active_target = (
            self.active_ide
            and str(self.active_ide.get("id", "")).strip().lower() == target
        )
        if active_target:
            cached = self._selected_file_cache_by_ide.get(target, "")
            cached_time = float(self._selected_file_cache_time_by_ide.get(target, 0.0))
            if (
                cached
                and (now - cached_time) <= max_age_sec
                and self._is_selected_file_target(cached)
            ):
                self.selected_file = cached
                self._sync_project_root_from_target(cached)
                return os.path.abspath(cached)
            inferred = self._infer_selected_file_for_ide_id(target)
            if inferred and os.path.isfile(inferred):
                self._selected_file_cache_by_ide[target] = inferred
                self._selected_file_cache_time_by_ide[target] = now
                self.selected_file = inferred
                self._sync_project_root_from_target(inferred)
                return os.path.abspath(inferred)
            folder_inferred = self._infer_open_folder_for_ide_id(target)
            if folder_inferred and os.path.exists(folder_inferred):
                self._sync_project_root_from_target(folder_inferred)
            return ""
        cached = self._selected_file_cache_by_ide.get(target, "")
        cached_time = float(self._selected_file_cache_time_by_ide.get(target, 0.0))
        if (
            cached
            and (now - cached_time) <= max_age_sec
            and self._is_selected_file_target(cached)
        ):
            if active_target:
                self.selected_file = cached
                self._sync_project_root_from_target(cached)
            return os.path.abspath(cached)
        if active_target and self.selected_file and self._is_selected_file_target(self.selected_file):
            self._selected_file_cache_by_ide[target] = self.selected_file
            self._selected_file_cache_time_by_ide[target] = now
            self._sync_project_root_from_target(self.selected_file)
            return os.path.abspath(self.selected_file)
        inferred = self._infer_selected_file_for_ide_id(target)
        if inferred and os.path.isfile(inferred):
            self._selected_file_cache_by_ide[target] = inferred
            self._selected_file_cache_time_by_ide[target] = now
            if active_target:
                self.selected_file = inferred
                self._sync_project_root_from_target(inferred)
            return os.path.abspath(inferred)
        folder_inferred = self._infer_open_folder_for_ide_id(target)
        if folder_inferred and os.path.exists(folder_inferred):
            if active_target:
                self._sync_project_root_from_target(folder_inferred)
        return ""

    def explain_open_file_detection(self, ide_id: str) -> Dict[str, Any]:
        target = (ide_id or "").strip().lower()
        ide = None
        for item in self.detected_ides:
            if str(item.get("id", "")).lower() == target:
                ide = item
                break
        process_names = self._ide_process_names_for_id(target)
        titles: List[str] = []
        candidates: List[str] = []
        resolved_candidates: List[str] = []
        inferred = ""
        inferred_folder = ""
        if process_names:
            process_set = {
                str(name).strip().lower() for name in process_names if str(name).strip()
            }
            titles = []
            for row in self._window_titles_with_process_cached(max_age_sec=0.0):
                try:
                    if isinstance(row, (list, tuple)) and len(row) >= 2:
                        pname = str(row[0] or "").strip().lower()
                        title = str(row[1] or "").strip()
                    else:
                        continue
                    if pname in process_set and title:
                        titles.append(title)
                except Exception:
                    continue
            for title in titles:
                for candidate in self._title_file_candidates(title):
                    if candidate not in candidates:
                        candidates.append(candidate)
                    resolved = self._resolve_project_title_candidate(candidate)
                    if resolved and resolved not in resolved_candidates:
                        resolved_candidates.append(resolved)
            inferred = self._infer_selected_file_for_ide_id(target)
            inferred_folder = self._infer_open_folder_for_ide_id(target)
        return {
            "ide_id": target,
            "ide_name": (ide or {}).get("name", target or "unknown"),
            "process_names": process_names,
            "window_titles": titles,
            "candidates": candidates,
            "resolved_candidates": resolved_candidates,
            "cached_file": self._selected_file_cache_by_ide.get(target, ""),
            "selected_file": self.selected_file
            if self.active_ide and str(self.active_ide.get("id", "")).lower() == target
            else "",
            "inferred_file": inferred,
            "inferred_folder": inferred_folder,
        }


ide_integration = None


class CodeFileManager:
    MD_SKIP_DIRS = {
        ".git",
        "models",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
    }
    APP_META_FILES = {
        PLAN_FILENAME.lower(),
        TODO_FILENAME.lower(),
        ANALYST_TEST_FILENAME.lower(),
        ".cmdaisettings.json",
        ".cmdaidebug.json",
        "faq.md",
        "contributing.md",
        "security.md",
        "code_of_conduct.md",
    }

    def __init__(self):
        self.project_root = os.getcwd()
        self.forced_project_root = ""
        self.created_files = []
        self.current_plan = None
        self.current_plan_path = ""
        self.last_applied_changes: List[Dict[str, Any]] = []
        self.last_apply_warnings: List[str] = []

    def _is_inside_cmda_app_root(self, path_value: str) -> bool:
        candidate = os.path.abspath(str(path_value or "").strip())
        if not candidate:
            return False
        app_root = os.path.abspath(os.getcwd())
        try:
            return os.path.commonpath([app_root, candidate]) == app_root
        except Exception:
            return False

    def _project_root_from_target(self, path_value: str) -> str:
        candidate = os.path.abspath(str(path_value or "").strip())
        if not candidate:
            return ""
        if ide_integration and hasattr(ide_integration, "_detect_project_root_from_path"):
            try:
                root = ide_integration._detect_project_root_from_path(candidate)
                if root and os.path.isdir(root):
                    return os.path.abspath(root)
            except Exception:
                pass
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(candidate)
        return os.path.abspath(parent) if parent else ""

    def _effective_project_root(self) -> str:
        forced_root = os.path.abspath(str(self.forced_project_root or "").strip())
        if forced_root and os.path.isdir(forced_root):
            self.project_root = forced_root
            return forced_root
        if ide_integration:
            try:
                ide_integration.sync_project_context(force_refresh=False)
            except Exception:
                pass
            active_ide = getattr(ide_integration, "active_ide", None)
            active_id = str(active_ide.get("id", "")).strip().lower() if active_ide else ""
            project_root = getattr(ide_integration, "project_root", None)
            project_root_abs = ""
            if project_root:
                try:
                    candidate_root = os.path.abspath(project_root)
                    if os.path.isdir(candidate_root) and not ide_integration._is_likely_ide_internal_path(candidate_root):
                        project_root_abs = candidate_root
                except Exception:
                    project_root_abs = ""
            if active_id and hasattr(ide_integration, "get_open_file_for_ide_cached"):
                try:
                    cached_file = ide_integration.get_open_file_for_ide_cached(
                        active_id, max_age_sec=6.0
                    )
                    if cached_file and os.path.exists(os.path.abspath(cached_file)):
                        cached_abs = os.path.abspath(cached_file)
                        if (
                            project_root_abs
                            and not self._is_inside_cmda_app_root(project_root_abs)
                            and self._is_inside_cmda_app_root(cached_abs)
                        ):
                            cached_file = ""
                        else:
                            root = self._project_root_from_target(cached_abs)
                            if root:
                                self.project_root = root
                                return root
                except Exception:
                    pass
            if active_id and hasattr(ide_integration, "_infer_open_folder_for_ide_id"):
                try:
                    inferred_folder = ide_integration._infer_open_folder_for_ide_id(active_id)
                    if (
                        inferred_folder
                        and os.path.isdir(inferred_folder)
                        and not ide_integration._is_likely_ide_internal_path(inferred_folder)
                    ):
                        root = os.path.abspath(inferred_folder)
                        self.project_root = root
                        return root
                except Exception:
                    pass
            selected = getattr(ide_integration, "selected_file", None)
            if selected:
                try:
                    selected_abs = os.path.abspath(selected)
                    selected_root = self._project_root_from_target(selected_abs)
                    if selected_root:
                        if (
                            self._is_inside_cmda_app_root(selected_abs)
                            and project_root_abs
                            and not self._is_inside_cmda_app_root(project_root_abs)
                        ):
                            self.project_root = project_root_abs
                            return project_root_abs
                        self.project_root = os.path.abspath(selected_root)
                        return os.path.abspath(selected_root)
                except Exception:
                    pass
            if project_root_abs:
                self.project_root = project_root_abs
                return project_root_abs
            if selected:
                selected_root = os.path.dirname(os.path.abspath(selected))
                if selected_root:
                    self.project_root = selected_root
                    return selected_root
        self.project_root = os.path.abspath(self.project_root or os.getcwd())
        return self.project_root

    def _looks_like_root_backend_layout(self, root_abs: str) -> bool:
        try:
            root = os.path.abspath(root_abs or self._effective_project_root())
        except Exception:
            root = os.path.abspath(self._effective_project_root())
        return os.path.isfile(os.path.join(root, "package.json")) and os.path.isdir(
            os.path.join(root, "src")
        )

    def _should_reject_generated_relpath(self, rel_path: str, root_abs: str) -> bool:
        rel = str(rel_path or "").replace("\\", "/").strip("./")
        if not rel:
            return True
        rel_lower = rel.lower()
        parts = [part for part in rel_lower.split("/") if part]
        if any(parts[i] == parts[i + 1] for i in range(len(parts) - 1)):
            return True
        if rel_lower.startswith(("frontend/frontend/", "backend/backend/", "client/client/", "public/public/", "src/src/", "data/data/")):
            return True
        if self._looks_like_root_backend_layout(root_abs):
            if rel_lower.startswith("frontend/data/"):
                return True
            if re.match(r"^frontend/src/(api|controllers|middleware|models|routes)(/|$)", rel_lower):
                return True
            if re.match(r"^frontend/src/server\.(js|ts|jsx|tsx|py)$", rel_lower):
                return True
        return False

    def _should_skip_project_relpath(self, rel_path: str) -> bool:
        rel = str(rel_path or "").replace("\\", "/").strip("./")
        if not rel:
            return True
        rel_lower = rel.lower()
        if rel_lower.startswith("tests/_tmp/") or rel_lower == "tests/_tmp":
            return True
        if "/__pycache__/" in rel_lower or rel_lower.startswith("__pycache__/"):
            return True
        basename = os.path.basename(rel_lower)
        if basename in self.APP_META_FILES:
            return True
        return False

    def set_plan(self, plan_content: str):
        self.current_plan = plan_content

    def extract_code_blocks(self, ai_response: str) -> List[Dict[str, Any]]:
        code_blocks = []

        pattern = r"```([^\n`]*)\n(.*?)```"
        for match in re.finditer(pattern, ai_response or "", re.DOTALL):
            info = (match.group(1) or "").strip()
            code = (match.group(2) or "").strip()
            lang = "txt"

            if info:
                first = info.split()[0].strip().lower()
                if re.match(r"^[a-z][a-z0-9_+-]*$", first):
                    lang = first

            code_blocks.append(
                {
                    "language": lang,
                    "code": code,
                    "info": info,
                    "start_index": match.start(),
                }
            )

        return code_blocks

    def get_extension(self, language: str) -> str:
        extensions = {
            "python": ".py",
            "py": ".py",
            "javascript": ".js",
            "js": ".js",
            "typescript": ".ts",
            "ts": ".ts",
            "html": ".html",
            "htm": ".html",
            "css": ".css",
            "json": ".json",
            "markdown": ".md",
            "md": ".md",
            "bash": ".sh",
            "shell": ".sh",
            "sh": ".sh",
            "sql": ".sql",
            "yaml": ".yaml",
            "yml": ".yaml",
            "dockerfile": ".Dockerfile",
            "docker": ".Dockerfile",
        }
        return extensions.get(language.lower(), ".txt")

    def generate_filename(self, language: str, index: int) -> str:
        ext = self.get_extension(language)
        timestamp = int(time.time())
        if not ext.startswith("."):
            ext = f".{ext}"
        return f"generated_{language}_{timestamp}_{index}{ext}"

    def _normalize_relative_path(self, raw_path: str) -> str:
        candidate = (raw_path or "").strip()
        if not candidate:
            return ""

        candidate = candidate.strip("`").strip("\"'")
        candidate = candidate.rstrip(",:;")
        candidate = candidate.replace("\\", "/")


        if re.search(r"[\u2500-\u257f]", candidate):
            return ""
        if candidate.lstrip().startswith(("├", "└", "│")):
            return ""

        lowered = candidate.lower()
        prefixes = ("file:", "path:", "plik:", "sciezka:", "filename:")
        for prefix in prefixes:
            if lowered.startswith(prefix):
                candidate = candidate[len(prefix) :].strip()
                break

        if not candidate:
            return ""


        if re.search(r"[;&|<>$`\n\r]", candidate):
            return ""

        if re.search(r"\s{2,}", candidate):
            return ""

        candidate = candidate.strip()

        while candidate.startswith("./"):
            candidate = candidate[2:]

        candidate = os.path.normpath(candidate).replace("\\", "/")
        if candidate in {"", "."}:
            return ""
        if candidate == ".." or candidate.startswith("../"):
            return ""
        if os.path.isabs(candidate):
            return ""

        if candidate.lower() == "node.js":
            return ""

        if re.search(r'[<>:"|?*]', candidate):
            return ""
        return candidate

    def _looks_like_filepath(self, candidate: str) -> bool:
        token = (candidate or "").strip().strip("`").strip("\"'")
        if not token:
            return False
        if "/" in token or "\\" in token:
            return True

        known = {
            "Dockerfile",
            "Makefile",
            "CMakeLists.txt",
            ".gitignore",
            ".editorconfig",
            ".env",
            "README.md",
            PLAN_FILENAME,
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "tsconfig.json",
        }
        if token in known:
            return True

        return bool(re.search(r"\.[a-zA-Z0-9_-]{1,12}$", token))

    def _extract_path_from_info(self, info: str) -> str:
        if not info:
            return ""

        explicit = re.search(
            r"(?:path|file|filename)\s*[:=]\s*([^\s]+)", info, re.IGNORECASE
        )
        if explicit:
            candidate = self._normalize_relative_path(explicit.group(1))
            if candidate:
                return candidate

        tokens = [t.strip() for t in info.split() if t.strip()]
        for token in tokens[1:]:
            candidate = self._normalize_relative_path(token)
            if candidate and self._looks_like_filepath(candidate):
                return candidate

        if tokens:
            first = self._normalize_relative_path(tokens[0])
            if first and self._looks_like_filepath(first):
                return first
        return ""

    def _extract_path_from_prefix(self, text: str, block_start: int) -> str:
        lookback = (text or "")[max(0, block_start - 500) : block_start]
        lines = lookback.splitlines()
        scanned_non_empty = 0

        for raw_line in reversed(lines):
            line = (raw_line or "").strip()
            if not line:
                continue

            scanned_non_empty += 1
            if scanned_non_empty > 20:
                break

            if line.startswith("```"):
                continue

            label_match = re.match(
                r"(?i)^(?:file|plik|path|sciezka|filename)\s*[:=-]\s*(.+)$", line
            )
            if label_match:
                candidate = self._normalize_relative_path(label_match.group(1))
                if candidate and self._looks_like_filepath(candidate):
                    return candidate
                continue

            candidate_line = re.sub(r"^#{1,6}\s*", "", line)
            candidate_line = re.sub(r"^[-*+]\s*", "", candidate_line)


            if re.search(r"\s", candidate_line.strip()):
                continue
            candidate = self._normalize_relative_path(candidate_line)
            if candidate:


                token = candidate.replace("\\", "/")
                if ("/" not in token) and ("\\" not in token):
                    lowered = token.lower()
                    allowed_bare = {
                        "readme.md",
                        "license",
                        "license.md",
                        ".gitignore",
                        ".editorconfig",
                        ".env",
                        "package.json",
                        "pyproject.toml",
                        "requirements.txt",
                        "tsconfig.json",
                        PLAN_FILENAME.lower(),
                        TODO_FILENAME.lower(),
                    }
                    if lowered not in allowed_bare and not lowered.endswith(
                        (".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini")
                    ):
                        continue
                if self._looks_like_filepath(candidate):
                    return candidate
        return ""

    def _extract_patch_hunks(self, patch_text: str) -> List[Dict[str, str]]:
        patch_text = (patch_text or "").replace("\r\n", "\n")
        specs = [
            (
                "replace",
                re.compile(
                    r"<<<<<<<\s*SEARCH\s*\n(.*?)\n=======\n(.*?)\n>>>>>>>\s*REPLACE",
                    re.DOTALL,
                ),
            ),
            (
                "after",
                re.compile(
                    r"<<<<<<<\s*AFTER\s*\n(.*?)\n=======\n(.*?)\n>>>>>>>\s*INSERT",
                    re.DOTALL,
                ),
            ),
            (
                "before",
                re.compile(
                    r"<<<<<<<\s*BEFORE\s*\n(.*?)\n=======\n(.*?)\n>>>>>>>\s*INSERT",
                    re.DOTALL,
                ),
            ),
        ]
        matches: List[Tuple[int, Dict[str, str]]] = []
        for kind, pattern in specs:
            for match in pattern.finditer(patch_text):
                matches.append(
                    (
                        match.start(),
                        {
                            "kind": kind,
                            "anchor": (match.group(1) or "").replace("\r\n", "\n"),
                            "content": (match.group(2) or "").replace("\r\n", "\n"),
                        },
                    )
                )
        matches.sort(key=lambda item: item[0])
        return [item[1] for item in matches]

    def _apply_patch_hunks_to_text(
        self, original_text: str, patch_text: str
    ) -> Tuple[str, bool]:
        text = (original_text or "").replace("\r\n", "\n")
        hunks = self._extract_patch_hunks(patch_text)
        if not hunks:
            return text, False

        updated = text
        for hunk in hunks:
            kind = str(hunk.get("kind", "replace")).strip().lower()
            anchor = str(hunk.get("anchor", ""))
            content = str(hunk.get("content", ""))
            if kind == "replace":
                if anchor:
                    if anchor not in updated:
                        return text, False
                    updated = updated.replace(anchor, content, 1)
                else:
                    updated = updated + content
                continue
            if not anchor or anchor not in updated:
                return text, False
            anchor_index = updated.find(anchor)
            if anchor_index < 0:
                return text, False
            anchor_end = anchor_index + len(anchor)
            if kind == "after":
                insert_text = content
                if (
                    anchor_end < len(updated)
                    and updated[anchor_end] == "\n"
                    and insert_text
                    and not insert_text.startswith("\n")
                ):
                    insert_text = "\n" + insert_text
                updated = updated[:anchor_end] + insert_text + updated[anchor_end:]
                continue
            if kind == "before":
                insert_text = content
                if (
                    anchor_index > 0
                    and updated[anchor_index - 1] == "\n"
                    and insert_text
                    and not insert_text.endswith("\n")
                ):
                    insert_text = insert_text + "\n"
                updated = updated[:anchor_index] + insert_text + updated[anchor_index:]
                continue
            return text, False
        return updated, True

    def extract_file_changes(self, ai_response: str) -> List[Dict[str, Any]]:
        text = ai_response or ""


        direct_changes: List[Dict[str, Any]] = []
        direct_pattern = re.compile(
            r"(?ims)^[ \t]*(?:file|plik|path|sciezka|filename)\s*:\s*(.+?)\s*\n\s*```([^\n`]*)\n(.*?)```"
        )
        for match in direct_pattern.finditer(text):
            rel_path = self._normalize_relative_path(match.group(1) or "")
            if not rel_path:
                continue
            if os.path.basename(rel_path).lower() == PLAN_FILENAME.lower():
                continue
            info = (match.group(2) or "").strip()
            code = (match.group(3) or "").strip()
            if not code:
                continue
            lang = "txt"
            if info:
                first = info.split()[0].strip().lower()
                if re.match(r"^[a-z][a-z0-9_+-]*$", first):
                    lang = first
            direct_changes.append(
                {
                    "relative_path": rel_path,
                    "language": lang,
                    "code": code,
                    "change_kind": "full",
                }
            )
        if direct_changes:

            by_path: Dict[str, Dict[str, Any]] = {}
            for ch in direct_changes:
                path = ch["relative_path"]
                if path in by_path:

                    by_path[path]["code"] += "\n\n" + ch["code"]
                else:
                    by_path[path] = dict(ch)
            return list(by_path.values())

        blocks = self.extract_code_blocks(text)
        if not blocks:
            return []

        changes_by_path: Dict[str, Dict[str, Any]] = {}
        block_order: List[str] = []
        for i, block in enumerate(blocks):
            rel_path = self._extract_path_from_info(block.get("info", ""))
            if not rel_path:
                rel_path = self._extract_path_from_prefix(
                    text, block.get("start_index", 0)
                )
            if not rel_path:
                continue
            rel_path = self._normalize_relative_path(rel_path)
            if not rel_path:
                continue


            if os.path.basename(rel_path).lower() == PLAN_FILENAME.lower():
                continue

            code_text = str(block.get("code", ""))
            if not code_text.strip():
                continue

            is_patch = str(block.get("language", "")).strip().lower() == "patch"

            if rel_path in changes_by_path:


                if is_patch:
                    changes_by_path[rel_path]["code"] += "\n" + code_text
                    changes_by_path[rel_path]["change_kind"] = "patch"
                else:
                    changes_by_path[rel_path]["code"] += "\n\n" + code_text
            else:
                block_order.append(rel_path)
                changes_by_path[rel_path] = {
                    "relative_path": rel_path,
                    "language": block["language"],
                    "code": code_text,
                    "change_kind": "patch" if is_patch else "full",
                }

        return [changes_by_path[p] for p in block_order]

    def apply_file_changes(
        self, changes: List[Dict[str, Any]], open_in_ide: bool = False
    ) -> List[Dict[str, Any]]:
        self.last_apply_warnings = []
        applied = []
        root_abs = os.path.abspath(self._effective_project_root())
        self.project_root = root_abs
        selected_file_abs = None
        restrict_to_selected = _bool_from_any(
            APP_SETTINGS.get("restrict_writes_to_open_file"), True
        ) and bool(ide_integration and getattr(ide_integration, "selected_file_explicit", False))
        if restrict_to_selected:
            selected_file_abs = (
                getattr(ide_integration, "selected_file", None)
                if ide_integration
                else None
            )

            if (not selected_file_abs) and ide_integration and hasattr(
                ide_integration, "ensure_selected_file"
            ):
                try:
                    inferred = ide_integration.ensure_selected_file(
                        max_age_sec=2.0, force_refresh=False
                    )
                    if inferred and os.path.isfile(os.path.abspath(inferred)):
                        selected_file_abs = inferred
                except Exception:
                    selected_file_abs = None
            if not selected_file_abs:
                msg = "No file selected in IDE (use /ide open|file, or disable restrict_writes_to_open_file)."
                self.last_apply_warnings.append(msg)
                _ui_set_note(msg)
                return []
            if restrict_to_selected and selected_file_abs:
                selected_file_abs = os.path.abspath(selected_file_abs)
                if os.path.exists(selected_file_abs) and not os.path.isfile(selected_file_abs):
                    msg = f"Selected IDE target is not a file: {selected_file_abs}"
                    self.last_apply_warnings.append(msg)
                    _ui_set_note(msg)
                    return []
            if restrict_to_selected:
                selected_dir = os.path.dirname(selected_file_abs)
                if not selected_dir:
                    msg = f"Selected IDE file directory not found: {selected_file_abs}"
                    self.last_apply_warnings.append(msg)
                    _ui_set_note(msg)
                    return []
                try:
                    os.makedirs(selected_dir, exist_ok=True)
                except Exception as exc:
                    msg = f"Selected IDE file directory not available: {selected_dir} ({exc})"
                    self.last_apply_warnings.append(msg)
                    _ui_set_note(msg)
                    return []
                if ide_integration:
                    project_root = getattr(ide_integration, "project_root", None)
                    if project_root:
                        try:
                            ide_root = os.path.abspath(project_root)
                            if (
                                os.path.commonpath([ide_root, selected_file_abs])
                                != ide_root
                            ):
                                msg = (
                                    "Selected IDE file is outside the active IDE project root: "
                                    f"{selected_file_abs}"
                                )
                                self.last_apply_warnings.append(msg)
                                _ui_set_note(msg)
                                return []
                        except Exception:
                            pass
            if not restrict_to_selected:
                selected_file_abs = None
            if restrict_to_selected:

                if not changes:
                    return []
                best_change = None
                selected_rel = ""
                try:
                    selected_rel = os.path.relpath(selected_file_abs, root_abs).replace(
                        "\\", "/"
                    )
                except Exception:
                    selected_rel = ""
                selected_base = os.path.basename(selected_file_abs).lower()
                for change in changes:
                    rel_path = str((change or {}).get("relative_path", "")).strip()
                    rel_norm = rel_path.replace("\\", "/").lstrip("./")
                    if selected_rel and rel_norm.lower() == selected_rel.lower():
                        best_change = change
                        break
                    if rel_norm and os.path.basename(rel_norm).lower() == selected_base:
                        best_change = change
                        break
                if best_change is None:
                    msg = (
                        "Model did not return a File: block for the selected IDE target: "
                        f"{selected_file_abs}"
                    )
                    self.last_apply_warnings.append(msg)
                    _ui_set_note(msg)
                    return []
                code = str((best_change or {}).get("code", ""))
                if not code.strip():
                    msg = "Selected change is empty."
                    self.last_apply_warnings.append(msg)
                    _ui_set_note(msg)
                    return []
                change_kind = str(
                    (best_change or {}).get("change_kind", "full")
                ).lower()
                existed = os.path.exists(selected_file_abs)
                final_text = code
                if change_kind == "patch":
                    if not existed:
                        msg = f"Patch target does not exist: {selected_file_abs}"
                        self.last_apply_warnings.append(msg)
                        _ui_set_note(msg)
                        return []
                    try:
                        with open(selected_file_abs, "r", encoding="utf-8") as f:
                            current_text = f.read()
                        patched_text, patched_ok = self._apply_patch_hunks_to_text(
                            current_text, code
                        )
                        if not patched_ok:
                            msg = "Patch block did not match the selected file."
                            self.last_apply_warnings.append(msg)
                            _ui_set_note(msg)
                            return []
                        final_text = patched_text
                    except Exception as exc:
                        msg = f"Failed to apply patch to selected file: {exc}"
                        self.last_apply_warnings.append(msg)
                        _ui_set_note(msg)
                        return []
                with open(selected_file_abs, "w", encoding="utf-8") as f:
                    f.write(final_text)
                rel_selected = os.path.relpath(selected_file_abs, root_abs).replace(
                    "\\", "/"
                )
                applied.append(
                    {
                        "action": "updated" if existed else "created",
                        "path": selected_file_abs,
                        "relative_path": rel_selected,
                        "language": (best_change or {}).get("language", "txt"),
                    }
                )
                if ide_integration and ide_integration.active_ide and open_in_ide:
                    ide_integration.open_file(selected_file_abs)
                if ide_integration and ide_integration.active_ide:
                    try:
                        ide_integration.project_root = root_abs
                        active_id = str(ide_integration.active_ide.get("id", "")).strip().lower()
                        if active_id:
                            ide_integration._selected_file_cache_by_ide[active_id] = selected_file_abs
                            ide_integration._selected_file_cache_time_by_ide[active_id] = time.time()
                    except Exception:
                        pass
                self.last_applied_changes = list(applied)
                self.created_files.extend([item["path"] for item in applied])
                return applied

        for change in changes:
            rel_path = (change or {}).get("relative_path", "").strip()
            code = (change or {}).get("code", "")
            change_kind = str((change or {}).get("change_kind", "full")).lower()
            if not rel_path:
                continue
            if self._should_reject_generated_relpath(rel_path, root_abs):
                msg = f"Rejected suspicious generated path: {rel_path}"
                self.last_apply_warnings.append(msg)
                _ui_set_note(msg)
                continue

            target_path = os.path.abspath(os.path.join(root_abs, rel_path))
            try:
                if os.path.commonpath([root_abs, target_path]) != root_abs:
                    continue
            except Exception:
                continue
            if (
                ide_integration
                and getattr(ide_integration, "project_root", None)
                and not self._is_inside_cmda_app_root(getattr(ide_integration, "project_root", ""))
                and self._is_inside_cmda_app_root(target_path)
            ):
                msg = f"Rejected write into CMDAI app folder: {rel_path}"
                self.last_apply_warnings.append(msg)
                _ui_set_note(msg)
                continue
            if selected_file_abs:
                if os.path.normcase(target_path) != os.path.normcase(selected_file_abs):
                    continue

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            existed = os.path.exists(target_path)
            final_text = code
            if change_kind == "patch":
                if not existed:
                    msg = f"Patch target does not exist: {rel_path}"
                    self.last_apply_warnings.append(msg)
                    _ui_set_note(msg)
                    continue
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        current_text = f.read()
                    patched_text, patched_ok = self._apply_patch_hunks_to_text(
                        current_text, code
                    )
                    if not patched_ok:
                        msg = f"Patch block did not match file: {rel_path}"
                        self.last_apply_warnings.append(msg)
                        _ui_set_note(msg)
                        continue
                    final_text = patched_text
                except Exception as exc:
                    msg = f"Failed to apply patch for {rel_path}: {exc}"
                    self.last_apply_warnings.append(msg)
                    _ui_set_note(msg)
                    continue
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(final_text)

            action = "updated" if existed else "created"
            applied.append(
                {
                    "action": action,
                    "path": target_path,
                    "relative_path": rel_path,
                    "language": change.get("language", "txt"),
                }
            )


        if ide_integration and ide_integration.active_ide and applied:
            try:
                ide_integration.project_root = root_abs
                active_id = str(ide_integration.active_ide.get("id", "")).strip().lower()
                last_path = str(applied[-1].get("path", "") or "").strip()
                if active_id and last_path:
                    ide_integration._selected_file_cache_by_ide[active_id] = last_path
                    ide_integration._selected_file_cache_time_by_ide[active_id] = time.time()
                if open_in_ide:
                    for item in applied:
                        try:
                            if os.path.isfile(item["path"]):
                                ide_integration.open_file(item["path"])
                                time.sleep(0.1)
                        except Exception:
                            pass
            except Exception:
                pass

        if selected_file_abs and changes and not applied:
            rel = os.path.relpath(selected_file_abs, root_abs).replace("\\", "/")
            msg = f"No accepted changes for selected file: {rel}"
            self.last_apply_warnings.append(msg)
            _ui_set_note(msg)

        self.last_applied_changes = list(applied)
        self.created_files.extend([item["path"] for item in applied])
        return applied

    def save_code_blocks(self, ai_response: str) -> List[str]:
        changes = self.extract_file_changes(ai_response)
        applied = self.apply_file_changes(changes)
        return [item["path"] for item in applied]

    def create_plan_file(self, content: str) -> str:
        try:
            root_abs = _current_project_root_path(force_refresh=True)
            if not root_abs or not os.path.isdir(root_abs):
                root_abs = _resolve_plan_root(prefer_existing=False, force_refresh=True)
        except Exception:
            root_abs = self._effective_project_root()
        filepath = os.path.join(root_abs, PLAN_FILENAME)
        try:
            os.makedirs(root_abs, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            self.current_plan = content
            self.current_plan_path = filepath
            if (
                ide_integration
                and ide_integration.active_ide
                and _should_auto_open_written_files()
            ):
                previous_selected = getattr(ide_integration, "selected_file", None)

                now = time.time()
                ide_integration.open_file(filepath)
                if previous_selected and os.path.exists(previous_selected):
                    ide_integration.selected_file = previous_selected

                    active_id = str(ide_integration.active_ide.get("id", "")).lower()
                    for ide_id in list(
                        ide_integration._selected_file_cache_by_ide.keys()
                    ) + [active_id]:
                        if ide_id:
                            ide_integration._selected_file_cache_by_ide[ide_id] = (
                                previous_selected
                            )
                            ide_integration._selected_file_cache_time_by_ide[ide_id] = (
                                now + 30.0
                            )
                    ide_integration._selected_file_cache = previous_selected
                    ide_integration._selected_file_cache_time = now + 30.0

            return filepath
        except Exception as e:
            print(f"Error creating plan file: {e}")
            return ""

    def _iter_markdown_files(self):
        root_abs = self._effective_project_root()
        for root, dirs, files in os.walk(root_abs):
            dirs[:] = [d for d in dirs if d.lower() not in self.MD_SKIP_DIRS]
            for filename in files:
                if not filename.lower().endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(root, filename), root_abs).replace(
                    "\\", "/"
                )
                if self._should_skip_project_relpath(rel):
                    continue
                yield os.path.join(root, filename)

    def load_markdown_context(
        self,
        max_files: int = MD_CONTEXT_MAX_FILES,
        max_chars: int = MD_CONTEXT_MAX_CHARS,
    ) -> str:
        md_files = list(self._iter_markdown_files())
        if not md_files:
            return ""
        root_abs = self._effective_project_root()

        def _priority(path: str):
            rel = os.path.relpath(path, root_abs).replace("\\", "/")
            rel_lower = rel.lower()
            if rel_lower == PLAN_FILENAME.lower():
                return (0, rel_lower)
            if rel_lower == "readme.md":
                return (1, rel_lower)
            return (2, rel_lower)

        md_files = sorted(md_files, key=_priority)
        sections = []
        remaining = max_chars

        for path in md_files:
            if len(sections) >= max_files or remaining <= 0:
                break

            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except Exception:
                continue

            if not content:
                continue

            rel = os.path.relpath(path, root_abs).replace("\\", "/")
            chunk = content[: min(3000, remaining)]
            sections.append(f"### {rel}\n{chunk}")
            remaining -= len(chunk)

        return "\n\n".join(sections)

    def load_project_file_index(
        self, max_files: int = 160, max_chars: int = 7000
    ) -> str:
        rows: List[str] = []
        root_abs = self._effective_project_root()
        for root, dirs, files in os.walk(root_abs):
            dirs[:] = [d for d in dirs if d.lower() not in self.MD_SKIP_DIRS]
            for filename in files:
                rel = os.path.relpath(os.path.join(root, filename), root_abs).replace(
                    "\\", "/"
                )
                if self._should_skip_project_relpath(rel):
                    continue
                rows.append(rel)

        if not rows:
            return ""

        rows = sorted(set(rows))[:max_files]
        out = []
        total = 0
        for rel in rows:
            line = f"- {rel}"
            if total + len(line) + 1 > max_chars:
                break
            out.append(line)
            total += len(line) + 1
        return "\n".join(out)


code_file_manager = None

RECOMMENDED_RUNTIME_PACKAGES = ["llama-cpp-python"]
KNOWN_UNSUPPORTED_ARCH_BY_MAX_VERSION: Dict[str, Tuple[int, int, int]] = {}


def _is_env_flag_enabled(name: str, default: bool = False) -> bool:
    fallback = "1" if default else "0"
    value = os.environ.get(name, fallback).strip().lower()
    return value in {"1", "true", "yes", "on"}


def load_model_aliases():
    aliases = {}
    if not os.path.exists("models"):
        return aliases

    for filename in os.listdir("models"):
        if filename.endswith(".gguf") and "mmproj" not in filename.lower():
            alias_name = filename[:-5]
            aliases[alias_name] = {"url": f"file://{filename}", "filename": filename}
    return aliases


def _parse_version_tuple(version_text: str) -> Tuple[int, int, int]:
    parts = (version_text or "").strip().split(".")
    out: List[int] = []
    for part in parts[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def _is_arch_known_unsupported(arch: str, llama_version: str) -> bool:
    max_version = KNOWN_UNSUPPORTED_ARCH_BY_MAX_VERSION.get(
        (arch or "").strip().lower()
    )
    if not max_version:
        return False
    return _parse_version_tuple(llama_version) <= max_version


def _read_gguf_architecture(model_path: str) -> Optional[str]:
    def _read_exact(handle, size: int) -> bytes:
        data = handle.read(size)
        if len(data) != size:
            raise EOFError
        return data

    def _read_u32(handle) -> int:
        return int.from_bytes(_read_exact(handle, 4), "little", signed=False)

    def _read_u64(handle) -> int:
        return int.from_bytes(_read_exact(handle, 8), "little", signed=False)

    def _read_str(handle) -> str:
        size = _read_u64(handle)
        return _read_exact(handle, size).decode("utf-8", errors="ignore")

    def _skip_value(handle, value_type: int) -> None:
        if value_type in (0, 1, 7):
            _read_exact(handle, 1)
        elif value_type in (2, 3):
            _read_exact(handle, 2)
        elif value_type in (4, 5, 6):
            _read_exact(handle, 4)
        elif value_type in (10, 11, 12):
            _read_exact(handle, 8)
        elif value_type == 8:
            _read_exact(handle, _read_u64(handle))
        elif value_type == 9:
            element_type = _read_u32(handle)
            count = _read_u64(handle)
            for _ in range(count):
                _skip_value(handle, element_type)
        else:
            raise ValueError(f"Unknown GGUF value type: {value_type}")

    try:
        with open(model_path, "rb") as handle:
            if _read_exact(handle, 4) != b"GGUF":
                return None
            version = _read_u32(handle)
            if version < 2:
                return None
            _ = _read_u64(handle)
            kv_count = _read_u64(handle)
            for _ in range(kv_count):
                key = _read_str(handle)
                value_type = _read_u32(handle)
                if key == "general.architecture" and value_type == 8:
                    return _read_str(handle).strip().lower()
                _skip_value(handle, value_type)
    except Exception:
        return None
    return None


def _configure_llama_logging(llama_cpp) -> None:
    global _LLAMA_LOG_CONFIGURED, _LLAMA_LOG_CALLBACK
    if _LLAMA_LOG_CONFIGURED:
        return
    _LLAMA_LOG_CONFIGURED = True

    if os.environ.get("RUN_AI_VERBOSE_LLAMA", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return

    try:

        @llama_cpp.llama_log_callback
        def _quiet_log(level, text, user_data):
            return

        _LLAMA_LOG_CALLBACK = _quiet_log
        llama_cpp.llama_log_set(_LLAMA_LOG_CALLBACK, None)
    except Exception:
        pass


def _patch_llama_model_del(llama_cpp) -> None:
    try:
        internals = getattr(llama_cpp, "_internals", None)
        model_cls = getattr(internals, "LlamaModel", None) if internals else None
        if model_cls is None or getattr(model_cls, "_run_ai_safe_del_patched", False):
            return

        original_del = getattr(model_cls, "__del__", None)
        if not callable(original_del):
            return

        def _safe_del(self):
            try:
                original_del(self)
            except AttributeError:
                pass
            except Exception:
                pass

        model_cls.__del__ = _safe_del
        model_cls._run_ai_safe_del_patched = True
    except Exception:
        pass


def ensure_llama_cpp():
    try:
        import llama_cpp

        _configure_llama_logging(llama_cpp)
        _patch_llama_model_del(llama_cpp)
        return llama_cpp
    except Exception:
        return None


class OutputFilter:
    def __init__(self):
        self.buffer = ""
        self.in_tag = False
        self.current_tag = ""
        self.keep_tags = ["/im_end", "im_end"]

    def feed(self, text: str) -> str:
        self.buffer += text
        result = []
        i = 0
        n = len(self.buffer)

        while i < n:
            if self.buffer[i] == "<" and i + 1 < n and self.buffer[i + 1] == "|":
                tag_start = i
                i += 2
                tag_name = ""

                while i < n and self.buffer[i] != "|" and self.buffer[i] != ">":
                    tag_name += self.buffer[i]
                    i += 1

                if (
                    i < n
                    and self.buffer[i] == "|"
                    and i + 1 < n
                    and self.buffer[i + 1] == ">"
                ):
                    i += 2
                    tag = f"<|{tag_name}|>"

                    if (
                        tag_name.startswith("im_start")
                        or tag_name.startswith("system")
                        or tag_name.startswith("user")
                    ):
                        self.in_tag = True
                        self.current_tag = tag_name
                        continue
                    elif (
                        tag_name.startswith("/im_start")
                        or tag_name.startswith("/system")
                        or tag_name.startswith("/user")
                    ):
                        self.in_tag = False
                        self.current_tag = ""
                        continue
                    elif tag_name in self.keep_tags:
                        result.append(tag)
                else:
                    result.append(self.buffer[tag_start:i])
            else:
                if not self.in_tag:
                    result.append(self.buffer[i])
                i += 1

        self.buffer = self.buffer[i:] if i < n else ""
        return "".join(result)


class SimpleGGUFLoader:
    def __init__(self, models_dir="./models"):
        self.models_dir = models_dir
        self.model = None
        self.current_model = None
        self._pending_load_worker = None
        self._cached_total_ram_mb = None
        self._cached_gpu_vram_mb = None
        self._gpu_probe_attempted = False
        self.fast_load_mode = False

        if not os.path.exists(self.models_dir):
            os.makedirs(self.models_dir)

    def list_models(self):
        models = []
        if not os.path.exists(self.models_dir):
            return models

        for f in os.listdir(self.models_dir):
            if f.endswith(".gguf") and "mmproj" not in f.lower():
                full = os.path.join(self.models_dir, f)
                try:
                    size = os.path.getsize(full)
                    models.append(
                        {
                            "name": f,
                            "path": full,
                            "size_mb": round(size / (1024 * 1024), 2),
                        }
                    )
                except:
                    pass

        return sorted(models, key=lambda x: x["name"])

    def _find_mmproj(self, model_name):
        base = os.path.splitext(model_name)[0]
        candidates = [f"{base}.mmproj.gguf", "mmproj.gguf"]

        for f in os.listdir(self.models_dir):
            if "mmproj" in f.lower() and f.endswith(".gguf"):
                candidates.append(f)

        for c in candidates:
            path = os.path.join(self.models_dir, c)
            if os.path.exists(path):
                return path

        return None

    def _try_load_simple(self, params, timeout_s):
        result = {"model": None, "error": None}
        done = threading.Event()
        started = time.time()
        spinner_proc = None
        spinner_flag = None

        try:
            spinner_flag = os.path.join(
                self.models_dir,
                f".load_spinner_{os.getpid()}_{int(started * 1000)}.flag",
            )
            with open(spinner_flag, "w", encoding="utf-8") as handle:
                handle.write("1")

            spinner_code = (
                "import os,sys,time\n"
                "flag=sys.argv[1]\n"
                "t0=float(sys.argv[2])\n"
                "frames='|/-\\\\'\n"
                "i=0\n"
                "last=0\n"
                "while os.path.exists(flag):\n"
                "    elapsed=max(0.0,time.time()-t0)\n"
                "    h=int(elapsed//3600)\n"
                "    m=int((elapsed%3600)//60)\n"
                "    s=elapsed%60\n"
                "    line=f'\\r{frames[i%4]} [time: {h:02d}.{m:02d}.{s:05.2f}]'\n"
                "    sys.stdout.write(line)\n"
                "    sys.stdout.flush()\n"
                "    last=max(last,len(line))\n"
                "    i += 1\n"
                "    time.sleep(0.05)\n"
                "sys.stdout.write('\\r' + (' ' * max(40,last)) + '\\r')\n"
                "sys.stdout.flush()\n"
            )
            spinner_proc = subprocess.Popen(
                [sys.executable, "-u", "-c", spinner_code, spinner_flag, str(started)]
            )
        except Exception:
            spinner_proc = None
            spinner_flag = None

        def worker():
            try:
                llama_cpp = ensure_llama_cpp()
                if llama_cpp is None:
                    result["error"] = "llama-cpp-python not installed"
                    return
                result["model"] = llama_cpp.Llama(**params)
            except Exception as e:
                result["error"] = str(e)
            finally:
                done.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        while not done.wait(0.05):
            if (time.time() - started) >= timeout_s:
                break

        if spinner_flag:
            with contextlib.suppress(Exception):
                os.remove(spinner_flag)
        if spinner_proc is not None:
            with contextlib.suppress(Exception):
                spinner_proc.wait(timeout=0.4)
            if spinner_proc.poll() is None:
                with contextlib.suppress(Exception):
                    spinner_proc.terminate()
                with contextlib.suppress(Exception):
                    spinner_proc.wait(timeout=0.3)

        if not done.is_set():
            return False, f"Timeout > {timeout_s}s"

        if result["error"]:
            return False, result["error"]

        return True, result["model"]

    def _load_model_stable(self, path, try_gpu=True, timeout_s=40, mmproj=None):
        file_size_mb = os.path.getsize(path) / (1024 * 1024)
        cpu_threads = max(2, min(8, (os.cpu_count() or 8) // 2))
        raw_ctx = os.environ.get("RUN_AI_N_CTX", "").strip()
        try:
            preferred_ctx = int(raw_ctx) if raw_ctx else 8192
        except Exception:
            preferred_ctx = 8192
        preferred_ctx = max(2048, min(65536, preferred_ctx))
        fast_ctx = preferred_ctx
        cpu_ctx = preferred_ctx

        gpu_params = {
            "model_path": path,
            "n_gpu_layers": -1,
            "n_ctx": fast_ctx,
            "n_batch": 128,
            "n_threads": max(2, (os.cpu_count() or 8) - 1),
            "use_mmap": True,
            "use_mlock": False,
            "verbose": False,
        }

        cpu_params = {
            "model_path": path,
            "n_gpu_layers": 0,
            "n_ctx": cpu_ctx,
            "n_batch": 64,
            "n_threads": cpu_threads,
            "use_mmap": True,
            "use_mlock": False,
            "verbose": False,
        }

        if mmproj:
            gpu_params["mmproj"] = mmproj
            cpu_params["mmproj"] = mmproj

        if try_gpu:
            ok, model = self._try_load_simple(gpu_params, timeout_s)
            if ok:
                return {"ok": True, "model": model}

        ok, model = self._try_load_simple(cpu_params, timeout_s)
        if ok:
            return {"ok": True, "model": model}

        return {"ok": False, "error": model}

    def load(self, model_name, show_try_errors=False):
        llama_cpp = ensure_llama_cpp()
        if llama_cpp is None:
            print("ERROR: llama-cpp-python not installed")
            return False

        if self._pending_load_worker is not None:
            if self._pending_load_worker.is_alive():
                print("ERROR: Previous model load is still running in background.")
                print("   Wait a moment and try again.")
                return False
            self._pending_load_worker = None

        if model_name.isdigit():
            models = self.list_models()
            idx = int(model_name) - 1
            if 0 <= idx < len(models):
                model_name = models[idx]["name"]
            else:
                print("Invalid model number")
                return False

        model_path = os.path.join(self.models_dir, model_name)
        if not os.path.exists(model_path):
            print("Model not found:", model_name)
            return False

        is_vision = any(
            x in model_name.lower()
            for x in ["llava", "bakllava", "cogvlm", "minicpm-v", "qwen3-vl", "qwen-vl"]
        )

        mmproj = self._find_mmproj(model_name) if is_vision else None
        if is_vision and not mmproj:
            print("Vision model requires mmproj file!")
            return False

        timeout_s = 180
        raw_timeout = os.environ.get("RUN_AI_LOAD_TIMEOUT_S", "").strip()
        if raw_timeout:
            try:
                parsed_timeout = int(raw_timeout)
                if parsed_timeout > 0:
                    timeout_s = max(15, min(1800, parsed_timeout))
            except Exception:
                pass

        try_gpu_env = os.environ.get("RUN_AI_TRY_GPU", "auto").strip().lower()
        if try_gpu_env in {"1", "true", "yes", "on"}:
            try_gpu = True
        elif try_gpu_env in {"0", "false", "no", "off"}:
            try_gpu = False
        else:
            try_gpu = False
            try:
                import torch

                if torch.cuda.is_available():
                    vram_mb = int(
                        torch.cuda.get_device_properties(0).total_memory
                        // (1024 * 1024)
                    )
                    try_gpu = vram_mb >= max(
                        4096, int(os.path.getsize(model_path) / (1024 * 1024) * 0.75)
                    )
            except Exception:
                try_gpu = False

        load_started = time.time()
        result = self._load_model_stable(
            model_path, try_gpu=try_gpu, timeout_s=timeout_s, mmproj=mmproj
        )

        if result["ok"]:
            self.model = result["model"]
            self.current_model = model_name
            load_elapsed = max(0.0, time.time() - load_started)
            print(
                _format_command_chip(
                    f"load [{self.current_model}] {_format_elapsed_label(load_elapsed)}"
                )
            )
            return True

        print("Load failed:", result["error"])
        return False

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, None]:
        if not self.model:
            raise ValueError("No model loaded. Use 'load' first.")

        try:
            if stream:
                return self._stream_response(
                    prompt, max_tokens, temperature, top_p, **kwargs
                )
            response = self.model(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                **kwargs,
            )
            return response["choices"][0]["text"].strip()
        except Exception as e:
            print(f"Error while generating response: {e}")
            raise

    def _stream_response(
        self, prompt: str, max_tokens: int, temperature: float, top_p: float, **kwargs
    ) -> str:
        try:
            cancel_event = kwargs.pop("cancel_event", None)
            response = self.model(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stream=True,
                **kwargs,
            )

            for chunk in response:
                if cancel_event is not None and cancel_event.is_set():
                    break
                token = chunk["choices"][0]["text"]
                yield token
        except Exception as e:
            print(f"Error while streaming response: {e}")
            raise

    def unload(self) -> bool:
        if self.model:
            try:
                del self.model
                self.model = None
                self.current_model = None
                import gc

                gc.collect()
                return True
            except Exception as e:
                print(f"Error while unloading model: {e}")
                return False
        return True

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if not self.model:
            return max(1, len(text.split()))
        try:
            payload = text.encode("utf-8", errors="ignore")
            try:
                tokens = self.model.tokenize(payload, add_bos=False)
            except TypeError:
                tokens = self.model.tokenize(payload)
            return max(0, len(tokens))
        except Exception:
            return max(1, len(text.split()))

    def download_model(
        self, source: str, output_name: Optional[str] = None, overwrite: bool = False
    ) -> Dict[str, Any]:
        import urllib.request
        import urllib.error

        if not os.path.exists(self.models_dir):
            os.makedirs(self.models_dir)

        if source.startswith(("http://", "https://", "file://")):
            url = source
            filename = (
                output_name or os.path.basename(urlparse(source).path) or "model.gguf"
            )
        else:
            filename = output_name or source
            if not filename.endswith(".gguf"):
                filename += ".gguf"
            if os.path.exists(source):
                dest = os.path.join(self.models_dir, filename)
                if os.path.exists(dest) and not overwrite:
                    return {
                        "status": "already_exists",
                        "name": filename,
                        "path": dest,
                        "size": os.path.getsize(dest),
                        "sha256": self._sha256_file(dest),
                    }
                shutil.copy2(source, dest)
                return {
                    "status": "downloaded",
                    "name": filename,
                    "path": dest,
                    "size": os.path.getsize(dest),
                    "sha256": self._sha256_file(dest),
                }
            else:
                raise ValueError(f"Source not found: {source}")

        destination = os.path.join(self.models_dir, filename)

        if os.path.exists(destination) and not overwrite:
            return {
                "status": "already_exists",
                "name": filename,
                "path": destination,
                "size": os.path.getsize(destination),
                "sha256": self._sha256_file(destination),
            }

        print(f"Downloading from {url}...")
        urllib.request.urlretrieve(url, destination)

        return {
            "status": "downloaded",
            "name": filename,
            "path": destination,
            "size": os.path.getsize(destination),
            "sha256": self._sha256_file(destination),
        }

    def _sha256_file(self, filepath: str) -> str:
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def list_model_aliases(self):
        return []


class OllamaAPIHandler(http.server.BaseHTTPRequestHandler):
    def _set_headers(self, status_code=200, content_type="application/json"):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)
        self.wfile.write(b"")

    def do_GET(self):
        try:
            parsed_path = urlparse(self.path)
            path = parsed_path.path

            if path in ("", "/"):
                self._set_headers(200)
                self.wfile.write(
                    json.dumps(
                        {
                            "service": "CMDAI",
                            "host": f"http://localhost:{HTTP_PORT}",
                            "loaded_model": loader.current_model if loader else None,
                            "mode": CURRENT_MODE,
                            "ide": ide_integration.get_status()
                            if ide_integration
                            else None,
                        }
                    ).encode()
                )

            elif path in ("/api/tags", "/api/tags/", "/tags", "/tags/"):
                self._handle_tags()

            elif path.startswith("/api/show") or path.startswith("/show"):
                self._handle_show_model()

            elif path in ("/api/version", "/api/version/", "/version", "/version/"):
                self._handle_version()

            else:
                self._set_headers(404)
                self.wfile.write(
                    json.dumps({"error": f"Endpoint {path} does not exist"}).encode()
                )

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(
                json.dumps({"error": f"Internal server error: {str(e)}"}).encode()
            )

    def do_POST(self):
        try:
            parsed_path = urlparse(self.path)
            path = parsed_path.path

            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(post_data) if post_data else {}
            except json.JSONDecodeError:
                data = {}

            if path in ("/api/generate", "/generate"):
                self._handle_generate(data)

            elif path in ("/api/chat", "/chat"):
                self._handle_chat(data)

            elif path.startswith("/api/pull") or path.startswith("/pull"):
                self._handle_pull(data)

            elif path.startswith("/api/copy") or path.startswith("/copy"):
                self._handle_copy(data)

            elif path in ("", "/"):
                if isinstance(data, dict) and data.get("messages"):
                    self._handle_chat(data)
                elif isinstance(data, dict) and data.get("prompt"):
                    self._handle_generate(data)
                else:
                    self._set_headers(400)
                    self.wfile.write(
                        json.dumps(
                            {
                                "error": "Error: for POST / provide 'prompt' or 'messages'."
                            }
                        ).encode()
                    )

            else:
                self._set_headers(404)
                self.wfile.write(
                    json.dumps({"error": f"Endpoint {path} does not exist"}).encode()
                )

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(
                json.dumps({"error": f"Internal server error: {str(e)}"}).encode()
            )

    def _handle_tags(self):
        models = loader.list_models()

        response = {
            "models": [
                {
                    "name": m["name"],
                    "modified_at": datetime.now().isoformat() + "Z",
                    "size": int(m["size_mb"] * 1024 * 1024),
                    "digest": hashlib.sha256(m["name"].encode()).hexdigest(),
                    "details": {
                        "format": "gguf",
                        "family": "llama",
                        "families": ["llama"],
                        "parameter_size": "7B",
                        "quantization_level": "Q4_0",
                    },
                }
                for m in models
            ]
        }

        self._set_headers(200)
        self.wfile.write(json.dumps(response, indent=2).encode())

    def _handle_show_model(self):
        if not loader.current_model:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "No model loaded"}).encode())
            return

        model_info = {
            "license": "MIT",
            "modelfile": f"# Modelfile for {loader.current_model}\nFROM {loader.current_model}",
            "parameters": "num_ctx 4096",
            "template": "{{ if .System }}<|system|>\n{{ .System }}<|end|>\n{{ end }}{{ .Prompt }}<|end|>\n<|assistant|>",
            "details": {
                "family": "llama",
                "families": ["llama"],
                "format": "gguf",
                "parameter_size": "7B",
                "quantization_level": "Q4_0",
            },
        }

        self._set_headers(200)
        self.wfile.write(json.dumps(model_info, indent=2).encode())

    def _handle_version(self):
        version_info = {
            "version": "VERSION",
            "compatibility": {"ollama": "0.1.0", "llama.cpp": "master"},
        }

        self._set_headers(200)
        self.wfile.write(json.dumps(version_info, indent=2).encode())

    def _handle_generate(self, data: Dict):
        if not loader.current_model:
            self._set_headers(400)
            self.wfile.write(
                json.dumps(
                    {"error": "No model loaded. Use 'load' in terminal."}
                ).encode()
            )
            return

        prompt = data.get("prompt", "")
        model = data.get("model", "")
        stream = data.get("stream", False)
        options = data.get("options", {})

        max_tokens = options.get("num_predict", 512)
        temperature = options.get("temperature", 0.7)
        top_p = options.get("top_p", 0.9)

        if model and model != loader.current_model:
            models = loader.list_models()
            found = None
            for m in models:
                if m["name"] == model:
                    found = m
                    break

            if found:
                print(f"[API] Loading model on demand: {model}")
                success = loader.load(found["name"])
                if not success:
                    self._set_headers(500)
                    self.wfile.write(
                        json.dumps({"error": f"Failed to load model: {model}"}).encode()
                    )
                    return
            else:
                self._set_headers(404)
                self.wfile.write(
                    json.dumps(
                        {
                            "error": f"Model '{model}' not found. Available models: {[m['name'] for m in models]}"
                        }
                    ).encode()
                )
                return

        try:
            if stream:
                self._stream_generate(prompt, max_tokens, temperature, top_p)
            else:
                response = loader.generate(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    stream=False,
                )

                result = {
                    "model": loader.current_model,
                    "created_at": datetime.now().isoformat() + "Z",
                    "response": response,
                    "done": True,
                    "done_reason": "stop",
                    "context": [],
                    "total_duration": 0,
                    "load_duration": 0,
                    "prompt_eval_count": len(prompt.split()),
                    "eval_count": len(response.split()),
                    "eval_duration": 0,
                }

                self._set_headers(200)
                self.wfile.write(json.dumps(result, indent=2).encode())

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(
                json.dumps(
                    {"error": f"Error while generating response: {str(e)}"}
                ).encode()
            )

    def _stream_generate(
        self, prompt: str, max_tokens: int, temperature: float, top_p: float
    ):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            response_generator = loader.generate(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stream=True,
            )

            full_response = ""

            for token in response_generator:
                full_response += token

                response_obj = {
                    "model": loader.current_model,
                    "created_at": datetime.now().isoformat() + "Z",
                    "response": token,
                    "done": False,
                }

                self.wfile.write(f"data: {json.dumps(response_obj)}\n\n".encode())
                self.wfile.flush()

            final_response = {
                "model": loader.current_model,
                "created_at": datetime.now().isoformat() + "Z",
                "response": "",
                "done": True,
                "done_reason": "stop",
                "context": [],
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": len(prompt.split()),
                "eval_count": len(full_response.split()),
                "eval_duration": 0,
            }

            self.wfile.write(f"data: {json.dumps(final_response)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        except Exception as e:
            error_response = {"error": str(e)}
            self.wfile.write(f"data: {json.dumps(error_response)}\n\n".encode())

    def _handle_chat(self, data: Dict):
        if not loader.current_model:
            self._set_headers(400)
            self.wfile.write(
                json.dumps(
                    {"error": "No model loaded. Use 'load' in terminal."}
                ).encode()
            )
            return

        try:
            messages = data.get("messages", [])
            stream = data.get("stream", False)
            options = data.get("options", {})

            max_tokens = options.get("num_predict", 512)
            temperature = options.get("temperature", 0.7)
            top_p = options.get("top_p", 0.9)

            prompt = self._format_chat_messages(messages)

            if stream:
                self._stream_chat_response(
                    messages, prompt, max_tokens, temperature, top_p
                )
            else:
                response = loader.generate(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    stream=False,
                )

                response_obj = {
                    "model": loader.current_model,
                    "created_at": datetime.now().isoformat() + "Z",
                    "message": {"role": "assistant", "content": response},
                    "done": True,
                    "total_duration": 0,
                    "load_duration": 0,
                    "prompt_eval_count": len(prompt.split()),
                    "eval_count": len(response.split()),
                    "eval_duration": 0,
                }

                self._set_headers(200)
                self.wfile.write(json.dumps(response_obj, indent=2).encode())

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(
                json.dumps(
                    {"error": f"Error while generating response: {str(e)}"}
                ).encode()
            )

    def _format_chat_messages(self, messages: List[Dict]) -> str:
        formatted = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                formatted.append(f"<|system|>\n{content}\n<|end|>")
            elif role == "user":
                formatted.append(f"<|user|>\n{content}\n<|end|>")
            elif role == "assistant":
                formatted.append(f"<|assistant|>\n{content}\n<|end|>")

        return "\n".join(formatted) + "\n<|assistant|>\n"

    def _stream_chat_response(
        self,
        messages: List[Dict],
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            if not loader or not loader.model:
                self.wfile.write(b'data: {"error": "No model loaded"}\n\n')
                return

            full_response = ""
            for token in loader.generate(
                prompt, max_tokens, temperature, top_p, stream=True
            ):
                if token:
                    full_response += token
                    response_data = {
                        "model": loader.current_model or "unknown",
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "message": {"role": "assistant", "content": full_response},
                        "done": False,
                    }
                    self.wfile.write(f"data: {json.dumps(response_data)}\n\n".encode())
                    self.wfile.flush()

            final_data = {
                "model": loader.current_model or "unknown",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "message": {"role": "assistant", "content": full_response},
                "done": True,
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": 0,
                "prompt_eval_duration": 0,
                "eval_count": len(full_response.split()) if full_response else 0,
                "eval_duration": 0,
            }
            self.wfile.write(f"data: {json.dumps(final_data)}\n\n".encode())
            self.wfile.flush()

        except Exception as e:
            error_data = {"error": f"Streaming error: {str(e)}"}
            self.wfile.write(f"data: {json.dumps(error_data)}\n\n".encode())
            self.wfile.flush()

    def _handle_pull(self, data: Dict):
        source = str(
            data.get("name") or data.get("model") or data.get("source") or ""
        ).strip()
        output_name = data.get("filename") or data.get("output")
        auto_load = bool(data.get("load", False))

        if not source:
            self._set_headers(400)
            self.wfile.write(
                json.dumps(
                    {"error": "Missing field: provide 'name' (alias/URL)"}
                ).encode()
            )
            return

        try:
            result = loader.download_model(
                source=source,
                output_name=output_name,
                overwrite=bool(data.get("overwrite", False)),
            )

            loaded = False
            if auto_load:
                loaded = loader.load(result["name"])

            self._set_headers(200)
            self.wfile.write(
                json.dumps(
                    {
                        "status": "success",
                        "name": result["name"],
                        "path": result["path"],
                        "size": result["size"],
                        "sha256": result["sha256"],
                        "loaded": loaded,
                    }
                ).encode()
            )
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(
                json.dumps({"error": f"Failed to download model: {e}"}).encode()
            )

    def _handle_copy(self, data: Dict):
        self._set_headers(200)
        self.wfile.write(json.dumps({"status": "success"}).encode())


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def start_http_server(port: int = HTTP_PORT) -> socketserver.TCPServer:
    httpd = None
    selected_port = port
    last_error = None

    for candidate_port in range(port, port + 10):
        try:
            server_address = ("", candidate_port)
            httpd = ReusableTCPServer(server_address, OllamaAPIHandler)
            selected_port = candidate_port
            break
        except OSError as exc:
            last_error = exc
            continue

    if httpd is None:
        raise last_error or OSError("No free port for HTTP server.")

    def server_thread():
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.shutdown()
            httpd.server_close()

    thread = threading.Thread(target=server_thread, daemon=True)
    thread.start()
    return httpd


def clear_screen():
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok):
        return
    os.system("cls" if os.name == "nt" else "clear")
    sys.stdout.write("\x1b[r")
    sys.stdout.flush()


def _show_edited_files_picker() -> None:
    if not code_file_manager:
        return
    changes = list(getattr(code_file_manager, "last_applied_changes", []) or [])
    if not changes:
        _ui_set_note("No edited files yet.", ttl_sec=2.0)
        return
    options = [
        (c.get("path", ""), c.get("relative_path", c.get("path", "")))
        for c in changes
        if c.get("path")
    ]
    if not options:
        return
    selected = _read_arrow_choice(
        "[F1] Open edited file",
        [(path, rel) for path, rel in options],
        default_idx=0,
    )
    if selected and selected != "cancel" and ide_integration:
        try:
            ide_integration.open_file(selected)
        except Exception:
            pass


def _read_terminal_line(prompt: str) -> str:
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok):
        return _basic_input(prompt)
    if _is_jetbrains_terminal():
        return _basic_input(prompt)
    if not _should_pin_input_top():
        return _basic_input(prompt)

    if os.name != "nt":
        return input(prompt)

    try:
        import msvcrt
    except Exception:
        return _basic_input(prompt)

    buffer: List[str] = []
    selected_idx = 0
    last_width = get_terminal_width()
    last_height = get_terminal_height()
    prompt_plain = _strip_ansi(prompt)
    input_row = max(1, int(INPUT_AREA_START_ROW)) + 1
    dynamic_lines = max(8, min(13, int(max(8, get_terminal_height()) * 0.30)))
    fixed_lines = max(
        8, min(dynamic_lines, int(INPUT_AREA_CLEAR_LINES or dynamic_lines))
    )
    anchor_dirty = True

    def _buffer_text() -> str:
        return "".join(buffer)

    def _get_slash_matches() -> List[Tuple[str, str]]:
        text = _buffer_text()
        if not text.startswith("/"):
            return []

        lower_text = text.lower()
        visible_hints = _visible_command_hints()
        debug_hints = (
            _visible_debug_command_hints() if CURRENT_MODE == AppMode.DEBUG else []
        )
        analyst_hints = (
            _visible_analyst_command_hints() if CURRENT_MODE == AppMode.ANALYST else []
        )
        command_help_map = {cmd: desc for cmd, desc in visible_hints}
        command_help_map.update({cmd: desc for cmd, desc in debug_hints})
        command_help_map.update({cmd: desc for cmd, desc in analyst_hints})

        def _load_model_matches(model_prefix: str = "") -> List[Tuple[str, str]]:
            if not loader:
                return []
            try:
                models = loader.list_models()
            except Exception:
                return []
            if not models:
                return []

            prefix = (model_prefix or "").strip().lower()
            matches: List[Tuple[str, str]] = []
            for model in models:
                name = str(model.get("name", "")).strip()
                if not name:
                    continue
                if prefix and not name.lower().startswith(prefix):
                    continue
                size_mb = float(model.get("size_mb", 0) or 0)
                size_str = (
                    f"{size_mb / 1024:.1f} GB"
                    if size_mb > 1024
                    else f"{size_mb:.0f} MB"
                )
                matches.append((f"/load {name}", f"{name} ({size_str})"))
            return matches[:24]

        def _ide_matches(t: str) -> List[Tuple[str, str]]:
            raw = (t or "").strip()
            low = raw.lower()
            active_file = _current_ide_file_path()
            if low == "/ide" or low == "/ide ":
                out: List[Tuple[str, str]] = [("/ide", "Pick IDE")]
                if ide_integration:
                    try:
                        ides = ide_integration.list_ides()
                    except Exception:
                        ides = []
                    try:
                        running_ids = set(ide_integration.get_running_ide_ids_cached())
                    except Exception:
                        running_ids = set()
                    for ide in ides:
                        ide_id = str(ide.get("id", "")).strip()
                        ide_name = str(ide.get("name", "")).strip()
                        if ide_id:
                            run_mark = "RUNNING" if ide_id in running_ids else "idle"
                            details_path = _ide_open_target_for_display(
                                ide_id,
                                running_ids=running_ids,
                                active_file=active_file,
                            )
                            desc = f"{ide_name} [{run_mark}]"
                            if details_path:
                                desc += f" [{details_path}]"
                            else:
                                desc += " [None]"
                            out.append((f"/ide use {ide_id}", desc))
                return out[:24]
            base_options: List[Tuple[str, str]] = [
                ("/ide", "Pick IDE"),
                ("/ide use ", "Use IDE id/number"),
                ("/ide open ", "Open file in IDE"),
                ("/ide file ", "Set selected file"),
            ]
            if low.startswith("/ide use"):
                out = [("/ide use ", "Use IDE id/number")]
                if ide_integration:
                    try:
                        ides = ide_integration.list_ides()
                    except Exception:
                        ides = []
                    try:
                        running_ids = set(ide_integration.get_running_ide_ids_cached())
                    except Exception:
                        running_ids = set()
                    for ide in ides:
                        ide_id = str(ide.get("id", "")).strip()
                        ide_name = str(ide.get("name", "")).strip()
                        if ide_id:
                            run_mark = "RUNNING" if ide_id in running_ids else "idle"
                            details_path = _ide_open_target_for_display(
                                ide_id,
                                running_ids=running_ids,
                                active_file=active_file,
                            )
                            desc = f"{ide_name} [{run_mark}]"
                            if details_path:
                                desc += f" [{details_path}]"
                            else:
                                desc += " [None]"
                            out.append((f"/ide use {ide_id}", desc))
                return out[:24]
            return [item for item in base_options if item[0].lower().startswith(low)][
                :24
            ]

        def _settings_matches(t: str) -> List[Tuple[str, str]]:
            base_options: List[Tuple[str, str]] = [
                ("/settings", "App settings"),
                ("/settings set ", "Set on/off"),
                ("/settings timeout ", "Set timeout"),
                ("/settings reset", "Reset settings"),
                ("/settings save", "Save settings"),
            ]
            raw = (t or "").strip().lower()
            if raw == "/settings" or raw == "/settings ":
                return base_options
            return [item for item in base_options if item[0].lower().startswith(raw)][
                :24
            ]

        def _help_panel_matches(selected_cmd: str) -> List[Tuple[str, str]]:
            options: List[Tuple[str, str]] = []
            desc = command_help_map.get(selected_cmd, "")
            if desc:
                options.append((selected_cmd, desc))
            for cmd, help_desc in visible_hints:
                if cmd == selected_cmd:
                    continue
                options.append((cmd, help_desc))
            return options[:24]

        def _single_command_match(selected_cmd: str) -> List[Tuple[str, str]]:
            desc = command_help_map.get(selected_cmd)
            if not desc:
                return []
            return [(selected_cmd, desc)]

        def _swap_model_matches() -> List[Tuple[str, str]]:
            options = _single_command_match("/swap")
            if not loader:
                return options
            try:
                models = loader.list_models()
            except Exception:
                return options
            for model in models[:24]:
                name = str(model.get("name", "")).strip()
                if not name:
                    continue
                size_mb = float(model.get("size_mb", 0) or 0)
                size_str = (
                    f"{size_mb / 1024:.1f} GB"
                    if size_mb > 1024
                    else f"{size_mb:.0f} MB"
                )
                options.append((f"/swap {name}", f"{name} ({size_str})"))
            return options[:24]

        if lower_text == "/load":
            model_matches = _load_model_matches("")
            if model_matches:
                return model_matches
        if lower_text.startswith("/load "):
            model_matches = _load_model_matches(text[6:])
            if model_matches:
                return model_matches
        if lower_text == "/ide" or lower_text.startswith("/ide "):
            ide_matches = _ide_matches(text)
            if ide_matches:
                return ide_matches
        if lower_text == "/help":
            return _help_panel_matches("/help")
        if lower_text == "/dhelp" and CURRENT_MODE == AppMode.DEBUG:
            return _help_panel_matches("/dhelp")
        if lower_text == "/ahelp" and CURRENT_MODE == AppMode.ANALYST:
            return _help_panel_matches("/ahelp")
        if lower_text == "/status":
            return _single_command_match("/status")
        if lower_text == "/version":
            return _single_command_match("/version")
        if lower_text == "/update":
            return _single_command_match("/update")
        if lower_text == "/settings" or lower_text.startswith("/settings "):
            return _settings_matches(text)
        if lower_text == "/swap":
            return _swap_model_matches()
        if CURRENT_MODE == AppMode.DEBUG and lower_text in {
            "/debug",
            "/trace",
            "/stack",
            "/quickfix",
            "/patterns",
            "/autofix",
            "/tests",
        }:
            return _single_command_match(lower_text)
        if CURRENT_MODE == AppMode.ANALYST and lower_text in {
            "/analyst",
            "/deps",
            "/perf",
            "/refactor",
            "/docs",
            "/complexity",
            "/security",
            "/coverage",
            "/architecture",
            "/style",
            "/graph",
            "/deadcode",
            "/benchmark",
        }:
            return _single_command_match(lower_text)
        if lower_text in {
            "/unload",
            "/exit",
            "/go",
            "/pause",
            "/dhelp",
            "/ahelp",
        }:
            return _single_command_match(lower_text)
        if " " in text:
            return []

        prefix = lower_text
        matches: List[Tuple[str, str]] = []
        current_loaded = (
            loader.current_model if (loader and loader.current_model) else "none"
        )
        search_hints = list(visible_hints)
        if CURRENT_MODE == AppMode.DEBUG:
            search_hints.extend(
                [(cmd, desc) for cmd, desc in debug_hints if cmd != "/dhelp"]
            )
        if CURRENT_MODE == AppMode.ANALYST:
            search_hints.extend(
                [(cmd, desc) for cmd, desc in analyst_hints if cmd != "/ahelp"]
            )
        for cmd, desc in search_hints:
            if not cmd.startswith(prefix):
                continue
            if cmd == "/load":
                matches.append((cmd, f"Load model (current: {current_loaded})"))
            else:
                matches.append((cmd, desc))
        if lower_text.strip() == "/":
            return matches
        return matches[:24]

    def _render_line() -> None:
        nonlocal selected_idx, last_width, last_height, input_row, anchor_dirty
        width = max(32, get_terminal_width())
        height = max(6, get_terminal_height())
        if width != last_width or height != last_height:
            last_width = width
            last_height = height
            anchor_dirty = True

        matches = _get_slash_matches()
        if matches:
            selected_idx = max(0, min(selected_idx, len(matches) - 1))
        else:
            selected_idx = 0

        if anchor_dirty:
            _prepare_top_input_area(lines=fixed_lines)
            input_row = max(1, int(INPUT_AREA_START_ROW)) + 1
            anchor_dirty = False

        use_uni = _supports_unicode_ui()
        hz = "─" if use_uni else "-"
        marker_selected = "▶" if use_uni else ">"

        bottom_bar = f"\033[90m{hz * width}\033[0m"

        typed_text = _buffer_text()
        typed_render = typed_text
        if typed_text.startswith("/"):
            typed_render = _format_command_chip(typed_text)
        raw_input = f"{prompt_plain}{typed_text}"
        if len(raw_input) <= width:
            input_text = f"{prompt}{typed_render}"
            cursor_plain_len = len(raw_input)
        else:
            input_text = _visible_tail(raw_input, max(1, width))
            cursor_plain_len = len(_strip_ansi(input_text))

        hide_top_border = _bool_from_any(
            APP_SETTINGS.get("hide_top_input_border"), False
        )
        if not hide_top_border:
            top_bar = f"\033[90m{hz * width}\033[0m"
            sys.stdout.write("\r\x1b[2K" + top_bar + "\n")
        sys.stdout.write("\r\x1b[2K" + input_text + "\n")
        sys.stdout.write("\r\x1b[2K" + bottom_bar)

        rendered_lines = 2 if hide_top_border else 3
        if matches:
            max_rows = min(7, max(1, fixed_lines - 3))
            for idx, (cmd, desc) in enumerate(matches[:max_rows]):
                is_ide_quick_pick = cmd.startswith("/ide use ") and (
                    typed_text.lower() == "/ide" or typed_text.lower() == "/ide "
                )
                visible_desc = _visible_tail(
                    desc,
                    max(8, width - (6 if is_ide_quick_pick else 24)),
                )
                if idx == selected_idx:
                    if is_ide_quick_pick:
                        line = f"\x1b[96m{marker_selected} {visible_desc}\x1b[0m"
                    else:
                        line = (
                            f"\x1b[96m{marker_selected} {cmd:<16}\x1b[0m {visible_desc}"
                        )
                else:
                    if is_ide_quick_pick:
                        line = f"\033[90m  {visible_desc}\033[0m"
                    else:
                        line = f"\033[90m  {cmd:<16} {visible_desc}\033[0m"
                sys.stdout.write(f"\n\r\x1b[2K{line}")
                rendered_lines += 1
        while rendered_lines < fixed_lines:
            sys.stdout.write("\n\r\x1b[2K")
            rendered_lines += 1

        cursor_col = min(width, max(1, cursor_plain_len + 1))
        sys.stdout.write(f"\x1b[{input_row};{cursor_col}H")
        sys.stdout.flush()

    def _render_idle_footer() -> None:
        width = max(32, get_terminal_width())
        hz = "─" if _supports_unicode_ui() else "-"
        panel_lines = max(fixed_lines, int(INPUT_AREA_CLEAR_LINES or fixed_lines))
        _prepare_top_input_area(lines=panel_lines)

        for i in range(panel_lines):
            sys.stdout.write("\r\x1b[2K")
            if i < panel_lines - 1:
                sys.stdout.write("\n")
        sys.stdout.write(f"\x1b[{int(INPUT_AREA_START_ROW)};1H")
        if len(prompt_plain) <= width:
            prompt_line = prompt
        else:
            prompt_line = _visible_tail(prompt_plain, width)
        hide_top_border = _bool_from_any(
            APP_SETTINGS.get("hide_top_input_border"), False
        )
        if not hide_top_border:
            sys.stdout.write("\r\x1b[2K" + f"\033[90m{hz * width}\033[0m" + "\n")
        sys.stdout.write("\r\x1b[2K" + prompt_line + "\n")
        sys.stdout.write("\r\x1b[2K" + f"\033[90m{hz * width}\033[0m")
        idle_base_lines = 2 if hide_top_border else 3
        for _ in range(max(0, panel_lines - idle_base_lines)):
            sys.stdout.write("\n\r\x1b[2K")
        sys.stdout.write(f"\x1b[{_log_output_row()};1H")
        sys.stdout.flush()

    def _clear_input_panel() -> None:
        panel_lines = max(fixed_lines, int(INPUT_AREA_CLEAR_LINES or fixed_lines))
        _prepare_top_input_area(lines=panel_lines)
        for _ in range(panel_lines):
            sys.stdout.write("\r\x1b[2K\n")
        sys.stdout.write(f"\x1b[{_log_output_row()};1H")
        sys.stdout.flush()

    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()
    try:
        _render_line()

        while True:
            if (
                get_terminal_width() != last_width
                or get_terminal_height() != last_height
            ):
                _render_line()
            if not msvcrt.kbhit():
                time.sleep(0.03)
                continue

            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                matches = _get_slash_matches()
                current_text = _buffer_text()
                if (
                    matches
                    and current_text.startswith("/")
                    and (
                        " " not in current_text
                        or current_text.lower().startswith("/load")
                        or current_text.lower().startswith("/swap")
                    )
                ):
                    chosen_cmd = matches[selected_idx][0]
                    buffer.clear()
                    buffer.extend(list(chosen_cmd))
                final_text = _buffer_text()
                _render_line()
                out_row = _log_output_row()
                sys.stdout.write(f"\x1b[{out_row};1H")
                sys.stdout.write("\x1b[2K")
                if not final_text.lstrip().startswith("/"):
                    sys.stdout.write(prompt + final_text + "\n")
                _render_idle_footer()
                sys.stdout.flush()
                return final_text

            if ch == "\x03":
                raise KeyboardInterrupt

            if ch == "\x1a":
                raise EOFError

            if ch == "\x1b":
                _render_idle_footer()
                sys.stdout.flush()
                return "\x1b"

            if ch == "\t" or ch == "\x09":
                matches = _get_slash_matches()
                current_text = _buffer_text()
                if (
                    matches
                    and current_text.startswith("/")
                    and (
                        " " not in current_text
                        or current_text.lower().startswith("/load")
                    )
                ):
                    chosen_cmd = matches[selected_idx][0]
                    buffer.clear()
                    buffer.extend(list(chosen_cmd))
                    _render_line()
                    continue
                _render_idle_footer()
                sys.stdout.flush()
                return "__TAB__"

            if ch in ("\x00", "\xe0"):
                try:
                    next_ch = msvcrt.getwch()
                    if next_ch == "\x12" or next_ch == "R":
                        pass

                    if next_ch == "\x0f":
                        _render_idle_footer()
                        sys.stdout.flush()
                        return "__TAB__"
                    if next_ch in {"H", "K"}:
                        matches = _get_slash_matches()
                        if matches:
                            selected_idx = (selected_idx - 1) % len(matches)
                            _render_line()
                        continue
                    if next_ch in {"P", "M"}:
                        matches = _get_slash_matches()
                        if matches:
                            selected_idx = (selected_idx + 1) % len(matches)
                            _render_line()
                        continue
                    continue
                except Exception:
                    pass
                continue

            if ch == "\x08":
                if buffer:
                    buffer.pop()
                    _render_line()
                continue

            buffer.append(ch)
            _render_line()
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def _consume_escape_keypress() -> bool:
    if os.name != "nt":
        return False
    try:
        import msvcrt
    except Exception:
        return False

    pressed = False
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            try:
                _ = msvcrt.getwch()
            except Exception:
                pass
            continue
        if ch == "\x1b":
            pressed = True
    return pressed


def get_terminal_width():
    try:
        return shutil.get_terminal_size().columns
    except:
        return 80


def get_terminal_height():
    try:
        return shutil.get_terminal_size().lines
    except:
        return 24


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text or "")


def _basic_input(prompt: str) -> str:
    prompt_text = _strip_ansi(prompt or "")
    try:
        sys.stdout.write(prompt_text)
        sys.stdout.flush()
    except Exception:
        pass
    return input()


def _visible_tail(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    value = text or ""
    if len(value) <= max_len:
        return value
    if max_len <= 3:
        return value[-max_len:]
    return "..." + value[-(max_len - 3) :]


def _supports_unicode_ui() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "─┌└│▶".encode(encoding)
        return True
    except Exception:
        return False


def _is_jetbrains_terminal() -> bool:
    markers = [
        os.environ.get("TERMINAL_EMULATOR", ""),
        os.environ.get("TERM_PROGRAM", ""),
        os.environ.get("PYCHARM_HOSTED", ""),
        os.environ.get("IDEA_INITIAL_DIRECTORY", ""),
    ]
    text = " ".join(str(x or "") for x in markers).lower()
    return (
        ("jetbrains" in text)
        or ("jediterm" in text)
        or ("pycharm" in text)
        or ("intellij" in text)
    )


def _ui_line_char() -> str:
    return "─" if _supports_unicode_ui() else "-"


def _format_command_chip(text: str) -> str:
    payload = str(text or "")
    if not payload:
        return payload
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return payload
    return f"\x1b[48;5;238m\x1b[38;5;252m{payload}{Colors.ENDC}"


def _log_output_row() -> int:
    return max(1, int(INPUT_AREA_START_ROW) - 2)


def _clear_last_terminal_lines(line_count: int) -> None:
    if line_count <= 0:
        return
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok):
        return

    for _ in range(line_count):
        sys.stdout.write("\x1b[1A")
        sys.stdout.write("\x1b[2K")
    sys.stdout.write("\r")
    sys.stdout.flush()


def _clear_log_area() -> None:
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok):
        return
    if not _should_pin_input_top():
        return

    _prepare_top_input_area(lines=INPUT_AREA_CLEAR_LINES)
    log_bottom = _log_output_row()
    if log_bottom <= 0:
        return
    scroll_top = max(1, int(globals().get("LOG_SCROLL_TOP_ROW", 1) or 1))
    if scroll_top > log_bottom:
        scroll_top = 1

    sys.stdout.write(f"\x1b[{scroll_top};{log_bottom}r")
    sys.stdout.write(f"\x1b[{scroll_top};1H")
    for i in range(scroll_top, log_bottom + 1):
        sys.stdout.write("\x1b[2K")
        if i < log_bottom:
            sys.stdout.write("\n")
    sys.stdout.write(f"\x1b[{_log_output_row()};1H")
    sys.stdout.flush()


def _should_pin_input_top() -> bool:
    if _is_jetbrains_terminal():
        return False
    if "pin_input_top" in APP_SETTINGS:
        return _bool_from_any(APP_SETTINGS.get("pin_input_top"), True)
    value = os.environ.get("RUN_AI_PIN_INPUT_TOP", "1").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _prepare_top_input_area(lines: Optional[int] = None) -> None:
    global INPUT_AREA_CLEAR_LINES, INPUT_AREA_START_ROW
    if lines is None:
        lines = INPUT_AREA_CLEAR_LINES
    if lines <= 0:
        return
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok):
        return
    if not _should_pin_input_top():
        return

    term_h = max(6, get_terminal_height())
    lines = max(4, min(int(lines), max(4, term_h - 1)))
    preferred_row = max(1, int(INPUT_AREA_START_ROW or 1))
    row = max(1, min(preferred_row, term_h - lines + 1))
    INPUT_AREA_START_ROW = row
    INPUT_AREA_CLEAR_LINES = lines

    log_bottom = max(1, row - 2)
    scroll_top = max(1, int(globals().get("LOG_SCROLL_TOP_ROW", 1) or 1))
    if scroll_top > log_bottom:
        scroll_top = 1
    sys.stdout.write(f"\x1b[{scroll_top};{log_bottom}r")

    sys.stdout.write(f"\x1b[{row};1H")
    for i in range(lines):
        sys.stdout.write("\x1b[2K")
        if i < lines - 1:
            sys.stdout.write("\n")
    if lines > 1:
        sys.stdout.write(f"\x1b[{lines - 1}A")
    sys.stdout.flush()


def _count_rendered_lines(text: str) -> int:
    if text is None:
        return 1
    return max(1, str(text).count("\n") + 1)


def _format_elapsed_label(elapsed_seconds: float) -> str:
    elapsed = max(0.0, float(elapsed_seconds))
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = elapsed % 60
    return f"[time: {hours:02d}.{minutes:02d}.{seconds:05.2f}]"


def _normalize_status_label(raw_status: str) -> str:
    text = " ".join(str(raw_status or "").strip().split())
    if not text:
        return "Working"
    words = text.split()[:2]
    candidate = " ".join(words)
    for allowed in ALLOWED_STATUS_LABELS:
        if candidate.lower() == allowed.lower():
            return allowed
    return "Working"


def _extract_plan_content(text: str) -> str:
    payload = (text or "").strip()
    if not payload:
        return ""

    fence_matches = re.findall(
        r"```(?:markdown|md)?\n(.*?)```", payload, flags=re.IGNORECASE | re.DOTALL
    )
    if len(fence_matches) == 1:
        payload = (fence_matches[0] or "").strip()

    payload = re.sub(r"(?im)^\s*file\s*:\s*.+$", "", payload)
    payload = re.sub(r"(?im)^\s*\*{0,2}uwaga\*{0,2}\s*:\s*.*$", "", payload)
    payload = re.sub(r"(?im)^.*czas\s+szacunkowy.*$", "", payload)
    payload = re.sub(r"(?im)^\s*\*{0,2}note\*{0,2}\s*:\s*.*$", "", payload)
    payload = re.sub(r"(?im)^.*estimated\s+time.*$", "", payload)
    cleaned_lines: List[str] = []
    drop_patterns = [
        r"(?im)^\s*ok[.\s…?]*$",
        r"(?im)^\s*we need to\b.*$",
        r"(?im)^\s*we should\b.*$",
        r"(?im)^\s*let'?s\b.*$",
        r"(?im)^\s*make sure\b.*$",
        r"(?im)^\s*let's produce\b.*$",
        r"(?im)^\s*assistant\s*:\s*.*$",
        r"(?im)^\s*<\|assistant\|.*$",
        r"(?im)^\s*<\|user\|.*$",
    ]
    for raw_line in payload.splitlines():
        line = raw_line.rstrip()
        if any(re.match(pattern, line) for pattern in drop_patterns):
            continue
        cleaned_lines.append(line)

    payload = "\n".join(cleaned_lines).strip()
    heading_match = re.search(r"(?m)^\s{0,3}#\s+\S", payload)
    if heading_match:
        payload = payload[heading_match.start() :].strip()

    payload = re.sub(r"\n{3,}", "\n\n", payload)
    payload = payload.strip()


    replacements = {
        "â€‘": "-",
        "â€“": "-",
        "â€”": "-",
        "â‰¤": "<=",
        "â‰¥": ">=",
        "â‰Ą": "<=",
        "â€ś": "\"",
        "â€ť": "\"",
        "â€ž": "\"",
    }
    for bad, good in replacements.items():
        if bad in payload:
            payload = payload.replace(bad, good)
    return payload.strip()


def _suggest_project_structure(plan_text: str, project_root: str) -> str:
    plan_l = str(plan_text or "").lower()
    root = os.path.abspath(project_root or os.getcwd())

    has_package_json = os.path.exists(os.path.join(root, "package.json"))
    has_pyproject = os.path.exists(os.path.join(root, "pyproject.toml"))
    has_requirements = os.path.exists(os.path.join(root, "requirements.txt"))
    has_root_src = os.path.isdir(os.path.join(root, "src"))
    has_root_data = os.path.isdir(os.path.join(root, "data"))

    wants_frontend = any(
        k in plan_l
        for k in (
            "react",
            "frontend",
            "front-end",
            "ui",
            "tailwind",
            "material ui",
            "mui",
            "vite",
            "next.js",
            "nextjs",
            "client",
        )
    )
    wants_backend = any(
        k in plan_l
        for k in (
            "backend",
            "back-end",
            "api",
            "fastapi",
            "flask",
            "django",
            "node",
            "express",
            "server",
        )
    )

    is_node = has_package_json or any(k in plan_l for k in ("node", "express", "nestjs", "next.js", "nextjs", "vite"))
    is_python = has_pyproject or has_requirements or any(k in plan_l for k in ("python", "fastapi", "flask", "django"))
    root_backend_layout = is_node and has_package_json and has_root_src

    lines: List[str] = []
    lines.append("## Sugerowana struktura projektu")
    lines.append("")
    lines.append("```text")


    prefers_server_client = any(
        k in plan_l for k in ("server/", "server\\", "client/", "client\\")
    )
    has_server_dir = os.path.isdir(os.path.join(root, "server"))
    has_client_dir = os.path.isdir(os.path.join(root, "client"))
    wants_spa_frontend = any(
        k in plan_l for k in ("react", "vite", "tsx", "spa", "single-page app")
    )

    if wants_frontend and wants_backend and (
        prefers_server_client or has_server_dir or has_client_dir
    ):
        lines.extend(
            [
                ".",
                "  server/",
                "    routes/",
                "    uploads/   (generated at runtime)",
                "    tracks.db  (generated at runtime)",
                "    package.json",
                "  client/",
                "    public/",
                "    src/",
                "    package.json",
                "  .vscode/",
                "  README.md",
                "  CMDAIPLAN.md",
            ]
        )

    elif wants_frontend and wants_backend and root_backend_layout and not wants_spa_frontend:
        lines.extend(
            [
                ".",
                "  src/",
                "  data/",
                "  public/",
                "    index.html",
                "    app.js",
                "  package.json",
                "  .vscode/",
                "  CMDAIPLAN.md",
            ]
        )
    elif wants_frontend and wants_backend and root_backend_layout:
        lines.extend(
            [
                ".",
                "  src/",
                "  data/",
                "  public/",
                "  frontend/",
                "    src/",
                "    public/",
                "    package.json",
                "  package.json",
                "  .vscode/",
                "  CMDAIPLAN.md",
            ]
        )
    elif wants_frontend and wants_backend:
        lines.extend(
            [
                ".",
                "  frontend/",
                "    public/",
                "    src/",
                "    package.json",
                "  backend/",
                "    src/",
                "    tests/",
                "    pyproject.toml  (lub requirements.txt)",
                "  shared/",
                "    README.md",
                "  docs/",
                "  .vscode/",
                "  CMDAIPLAN.md",
            ]
        )
    elif wants_frontend and not wants_backend:
        if is_node:
            lines.extend(
                [
                    ".",
                    "  src/",
                    "  public/",
                    "  package.json",
                    "  docs/",
                    "  .vscode/",
                    "  CMDAIPLAN.md",
                ]
            )
        else:
            lines.extend(
                [
                    ".",
                    "  frontend/",
                    "    src/",
                    "    public/",
                    "    package.json",
                    "  docs/",
                    "  .vscode/",
                    "  CMDAIPLAN.md",
                ]
            )
    elif wants_backend and not wants_frontend:
        if is_python:
            lines.extend(
                [
                    ".",
                    "  src/",
                    "  tests/",
                    "  pyproject.toml  (lub requirements.txt)",
                    "  docs/",
                    "  .vscode/",
                    "  CMDAIPLAN.md",
                ]
            )
        else:
            lines.extend(
                [
                    ".",
                    "  backend/",
                    "    src/",
                    "    tests/",
                    "    package.json",
                    "  docs/",
                    "  .vscode/",
                    "  CMDAIPLAN.md",
                ]
            )
    else:

        lines.extend(
            [
                ".",
                "  src/",
                "  tests/",
                "  docs/",
                "  .vscode/",
                "  CMDAIPLAN.md",
            ]
        )

    lines.append("```")


    lines.append("")
    lines.append("### Sugerowane pliki do utworzenia (MVP)")
    files: List[str] = []
    if wants_frontend and wants_backend and (
        prefers_server_client or has_server_dir or has_client_dir
    ):
        files.extend(
            [
                "server/index.js",
                "server/db.js",
                "server/routes/health.js",
                "server/routes/upload.js",
                "server/routes/tracks.js",
                "client/public/index.html",
                "client/src/index.js",
                "client/src/App.js",
                "client/src/api.js",
                "README.md",
            ]
        )
    elif wants_frontend and wants_backend and root_backend_layout and not wants_spa_frontend:
        files.extend(
            [
                "public/index.html",
                "public/app.js",
            ]
        )
    elif wants_frontend and wants_backend and root_backend_layout:
        files.extend(
            [
                "frontend/package.json",
                "frontend/src/App.tsx",
                "frontend/src/main.tsx",
                "frontend/index.html",
            ]
        )
    elif wants_frontend and wants_backend:
        files.extend(
            [
                "frontend/package.json",
                "frontend/src/App.tsx",
                "frontend/src/main.tsx",
                "frontend/index.html",
                "backend/pyproject.toml",
                "backend/src/app.py",
                "backend/README.md",
            ]
        )
    elif wants_frontend:
        if is_node:
            files.extend(["package.json", "src/App.tsx", "src/main.tsx", "index.html"])
        else:
            files.extend(
                ["frontend/package.json", "frontend/src/App.tsx", "frontend/src/main.tsx"]
            )
    elif wants_backend:
        if is_python:
            files.extend(["pyproject.toml", "src/app.py", "README.md"])
        else:
            files.extend(["backend/package.json", "backend/src/index.js"])
    else:
        files.extend(["README.md", "src/main.py"])

    files.extend([PLAN_FILENAME, TODO_FILENAME])
    for fpath in files[:14]:
        lines.append(f"- `{fpath}`")


    if has_package_json and not is_node:
        lines.append("")
        lines.append("- Uwaga: wykryto `package.json`, więc struktura moze byc Node/Frontend-first.")
    if (has_pyproject or has_requirements) and not is_python:
        lines.append("")
        lines.append("- Uwaga: wykryto pliki Pythona, więc struktura moze byc Backend/Python-first.")

    return "\n".join(lines).rstrip() + "\n"


def _append_plan_structure(plan_body: str, project_root: str) -> str:
    body = (plan_body or "").rstrip() + "\n"

    if re.search(r"(?im)^##\s+Sugerowana\s+struktura\s+projektu\s*$", body):
        return body
    return body + "\n" + _suggest_project_structure(body, project_root)


def _looks_like_diagram_only_plan(text: str) -> bool:
    content = (text or "").strip()
    if not content:
        return False

    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        return False

    numbered_steps = 0
    heading_lines = 0
    diagram_lines = 0

    for ln in lines:
        if re.match(r"^\d+\.\s+\S+", ln):
            numbered_steps += 1
        if ln.startswith("#"):
            heading_lines += 1

        if re.search(r"[┌┐└┘├┤┬┴│─]{2,}", ln):
            diagram_lines += 1
            continue
        if re.search(r"\+\-+\+|\|.+\|", ln):
            diagram_lines += 1
            continue
        if re.search(r"<[-=]+>|[-=]+>|=>|<=", ln):
            diagram_lines += 1
            continue

    too_many_diagrams = diagram_lines >= max(3, len(lines) // 3)
    too_few_plan_markers = numbered_steps < 4 and heading_lines < 3
    return too_many_diagrams and too_few_plan_markers


def _read_arrow_choice(
    title: str,
    options: List[Tuple[str, str]],
    default_idx: int = 0,
    option_help: Optional[Dict[str, str]] = None,
) -> str:
    if not options:
        return ""

    idx = max(0, min(default_idx, len(options) - 1))
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok) or os.name != "nt":
        try:
            raw = (
                input(f"{title} [{'/'.join(k for k, _ in options)}]: ").strip().lower()
            )
        except EOFError:
            return "cancel"
        if not raw:
            return options[idx][0]
        for key, _ in options:
            if raw == key.lower():
                return key
        for key, label in options:
            if key.lower().startswith(raw) or label.lower().startswith(raw):
                return key
        if raw in {"q", "quit", "cancel", "esc"}:
            return "cancel"
        return options[idx][0]

    try:
        import msvcrt
    except Exception:
        try:
            raw = (
                input(f"{title} [{'/'.join(k for k, _ in options)}]: ").strip().lower()
            )
        except EOFError:
            return "cancel"
        if not raw:
            return options[idx][0]
        for key, _ in options:
            if raw == key.lower():
                return key
        for key, label in options:
            if key.lower().startswith(raw) or label.lower().startswith(raw):
                return key
        if raw in {"q", "quit", "cancel", "esc"}:
            return "cancel"
        return options[idx][0]

    panel_lines = 0

    def _clear_panel() -> None:
        nonlocal panel_lines
        if panel_lines <= 0:
            return
        _prepare_top_input_area(lines=panel_lines)
        sys.stdout.flush()
        panel_lines = 0

    def _render_idle_footer(lines: int) -> None:
        width = max(32, get_terminal_width())
        hz = "─" if _supports_unicode_ui() else "-"
        _prepare_top_input_area(lines=lines)
        sys.stdout.write("\r\x1b[2K" + f"\033[90m{hz * width}\033[0m")
        sys.stdout.write(f"\x1b[{_log_output_row()};1H")
        sys.stdout.flush()

    def _render() -> None:
        nonlocal panel_lines
        footer_lines = max(11, min(13, int(INPUT_AREA_CLEAR_LINES or 11)))
        _prepare_top_input_area(lines=footer_lines)

        width = max(32, get_terminal_width())
        use_uni = _supports_unicode_ui()
        hz = "─" if use_uni else "-"
        marker = "▶" if use_uni else ">"

        lines: List[str] = []
        lines.append(f"\033[90m{hz * width}\033[0m")
        lines.append(_visible_tail(str(title), width))
        lines.append(f"\033[90m{hz * width}\033[0m")
        max_rows = min(5, max(1, len(options)))
        start_idx = 0
        if len(options) > max_rows:
            start_idx = max(0, min(idx - (max_rows // 2), len(options) - max_rows))
        visible = options[start_idx : start_idx + max_rows]
        for local_i, (_, label) in enumerate(visible):
            absolute_i = start_idx + local_i
            if absolute_i == idx:
                lines.append(f"\x1b[96m{marker} {label}\x1b[0m")
            else:
                lines.append(f"\033[90m  {label}\033[0m")
        if len(options) > max_rows:
            lines.append(
                f"\033[90m  [{idx + 1}/{len(options)}] use arrows to scroll\033[0m"
            )
        selected_key = options[idx][0]
        info_text = ""
        if option_help:
            info_text = str(option_help.get(selected_key, "")).strip()
        if info_text:
            info_line = _visible_tail(f"Info: {info_text}", max(12, width - 2))
            lines.append(f"\033[90m  {info_line}\033[0m")
        lines.append(f"\033[90m{hz * width}\033[0m")
        while len(lines) < footer_lines:
            lines.append("")

        for line in lines:
            sys.stdout.write("\r\x1b[2K" + line + "\n")
        sys.stdout.flush()
        panel_lines = footer_lines

    _render()
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            selected = options[idx][0]
            _clear_panel()
            _render_idle_footer(max(6, min(10, int(INPUT_AREA_CLEAR_LINES or 8))))
            return selected

        if ch == "\x03":
            raise KeyboardInterrupt

        if ch == "\x1b":
            _clear_panel()
            _render_idle_footer(max(6, min(10, int(INPUT_AREA_CLEAR_LINES or 8))))
            return "cancel"

        if ch in ("\x00", "\xe0"):
            try:
                next_ch = msvcrt.getwch()
            except Exception:
                continue

            if next_ch in {"H", "K"}:
                idx = (idx - 1) % len(options)
                _render()
            elif next_ch in {"P", "M"}:
                idx = (idx + 1) % len(options)
                _render()
            continue

        if ch and ch.isprintable():
            lowered = ch.lower()
            matched = False
            for i, (key, label) in enumerate(options):
                if key.lower().startswith(lowered) or label.lower().startswith(lowered):
                    idx = i
                    matched = True
                    break
            if matched:
                _render()


def _get_separator_line() -> str:
    term_width = get_terminal_width()
    return _ui_line_char() * term_width


def print_welcome():
    global INPUT_AREA_START_ROW, INPUT_AREA_CLEAR_LINES, LOG_SCROLL_TOP_ROW
    clear_screen()
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if stdin_ok and stdout_ok:
        sys.stdout.write("\x1b[r")
        sys.stdout.flush()

    terminal_width = get_terminal_width()
    terminal_width = max(terminal_width, 40)

    ascii_title = r"""
  ██████╗  ███╗   ███╗ ██████╗   █████╗  ██╗
 ██╔════╝  ████╗ ████║ ██╔══██╗ ██╔══██╗ ██║
 ██║       ██╔████╔██║ ██║  ██║ ███████║ ██║
 ██║       ██║╚██╔╝██║ ██║  ██║ ██╔══██║ ██║
 ╚██████╗  ██║ ╚═╝ ██║ ██████╔╝ ██║  ██║ ██║
  ╚═════╝  ╚═╝     ╚═╝ ╚═════╝  ╚═╝  ╚═╝ ╚═╝
"""

    if _supports_unicode_ui():
        sep = _get_separator_line()

        printed_lines = 0
        print(f"\n{sep}")
        printed_lines += 2

        for line in ascii_title.splitlines():
            if line.strip() == "":
                print()
                printed_lines += 1
            else:
                padding = (terminal_width - len(line)) // 2
                print(" " * max(padding, 0) + line)
                printed_lines += 1

        print(f"{sep}")
        print("Type '/help' for commands.")
        printed_lines += 2

        LOG_SCROLL_TOP_ROW = max(1, printed_lines + 1)


        INPUT_AREA_START_ROW = max(15, int(LOG_SCROLL_TOP_ROW) + 8)
        INPUT_AREA_CLEAR_LINES = min(
            10, max(6, get_terminal_height() - INPUT_AREA_START_ROW - 2)
        )
    else:
        ascii_sep = "-" * terminal_width
        fallback_title = r"""
   _____ __  __ ____    _    ___
  / ____|  \/  |  _ \  / \  |_ _|
 | |    | |\/| | | | |/ _ \  | |
 | |____| |  | | |_| / ___ \ | |
  \_____|_|  |_|____/_/   \_\___|
"""
        print(f"\n{ascii_sep}")
        printed_lines = 0
        printed_lines += 2
        for line in fallback_title.splitlines():
            if line.strip():
                padding = max((terminal_width - len(line)) // 2, 0)
                print(" " * padding + line)
                printed_lines += 1
            else:
                print()
                printed_lines += 1
        print(f"{ascii_sep}")
        print("Type '/help' for commands.")
        printed_lines += 2
        LOG_SCROLL_TOP_ROW = max(1, printed_lines + 1)
        INPUT_AREA_START_ROW = max(13, int(LOG_SCROLL_TOP_ROW) + 8)
        INPUT_AREA_CLEAR_LINES = min(
            10, max(6, get_terminal_height() - INPUT_AREA_START_ROW - 2)
        )


def _should_show_welcome() -> bool:
    value = os.environ.get("RUN_AI_SHOW_WELCOME", "1").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _ensure_windows_user_path_contains(path_entry: str) -> bool:
    if os.name != "nt" or not winreg or not path_entry:
        return False

    try:
        env_key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Environment",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        )
    except Exception:
        return False

    try:
        current_path, value_type = winreg.QueryValueEx(env_key, "Path")
    except FileNotFoundError:
        current_path, value_type = "", winreg.REG_EXPAND_SZ
    except Exception:
        winreg.CloseKey(env_key)
        return False

    try:
        parts = [p.strip() for p in str(current_path).split(";") if p.strip()]
        lowered = {p.lower() for p in parts}
        if path_entry.lower() in lowered:
            return True

        new_parts = parts + [path_entry]
        new_path = ";".join(new_parts)
        winreg.SetValueEx(env_key, "Path", 0, value_type, new_path)
        process_parts = [
            p.strip() for p in os.environ.get("PATH", "").split(";") if p.strip()
        ]
        process_lower = {p.lower() for p in process_parts}
        if path_entry.lower() not in process_lower:
            process_parts.append(path_entry)
            os.environ["PATH"] = ";".join(process_parts)
        return True
    except Exception:
        return False
    finally:
        winreg.CloseKey(env_key)


def install_global_launcher(silent: bool = False) -> bool:
    if os.name != "nt":
        if not silent:
            print("Launcher install is currently available on Windows only.")
        return False

    try:
        script_path = os.path.abspath(__file__)
        script_dir = os.path.dirname(script_path)
        launcher_dir = os.path.join(os.path.expanduser("~"), LAUNCHER_DIR_NAME)
        os.makedirs(launcher_dir, exist_ok=True)

        cmd_path = os.path.join(launcher_dir, "CMDAI.cmd")
        cmd_content = (
            "@echo off\n"
            f'set "CMDAI_HOME={script_dir}"\n'
            'pushd "%CMDAI_HOME%" >nul 2>&1\n'
            'if "%~1"=="" (\n'
            '  py -3 "%CMDAI_HOME%\\run.py" launch\n'
            ") else (\n"
            '  py -3 "%CMDAI_HOME%\\run.py" %*\n'
            ")\n"
            "set ERR=%ERRORLEVEL%\n"
            "popd >nul 2>&1\n"
            "exit /b %ERR%\n"
        )
        with open(cmd_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(cmd_content)

        path_ok = _ensure_windows_user_path_contains(launcher_dir)

        if not silent:
            print(f"Launcher installed: {cmd_path}")
            if path_ok:
                print("Command available in new terminal sessions: CMDAI launch")
            else:
                print(f"Add this folder to PATH manually: {launcher_dir}")
        return True
    except Exception as e:
        if not silent:
            print(f"Launcher install failed: {e}")
        return False


def _print_help_section(title: str, rows: List[Tuple[str, str]]) -> None:
    if not rows:
        return
    print(f"\n{title}")
    print(_ui_line_char() * 60)
    command_width = max(len(cmd) for cmd, _ in rows)
    for command, description in rows:
        print(f"  {command.ljust(command_width)}  {description}")


def _pick_command_from_help_panel() -> str:
    options: List[Tuple[str, str]] = []
    options.append(("exit", "exit  Close command list"))
    for cmd, desc in _visible_command_hints():
        if cmd == "/help":
            continue
        options.append((cmd, f"{cmd}  {desc}"))
    if not options:
        return ""
    selected = _read_arrow_choice("[COMMANDS] Select", options, default_idx=0)
    if selected in {"cancel", "exit"}:
        return ""
    return selected


def _pick_debug_command_panel() -> str:
    if CURRENT_MODE != AppMode.DEBUG:
        return ""
    options: List[Tuple[str, str]] = [("exit", "exit  Close debug commands")]
    for cmd, desc in _visible_debug_command_hints():
        if cmd == "/dhelp":
            continue
        options.append((cmd, f"{cmd}  {desc}"))
    selected = _read_arrow_choice("[DEBUG COMMANDS] Select", options, default_idx=0)
    if selected in {"cancel", "exit"}:
        return ""
    return selected


def _pick_analyst_command_panel() -> str:
    if CURRENT_MODE != AppMode.ANALYST:
        return ""
    options: List[Tuple[str, str]] = [("exit", "exit  Close analyst commands")]
    for cmd, desc in _visible_analyst_command_hints():
        if cmd == "/ahelp":
            continue
        options.append((cmd, f"{cmd}  {desc}"))
    selected = _read_arrow_choice("[ANALYST COMMANDS] Select", options, default_idx=0)
    if selected in {"cancel", "exit"}:
        return ""
    return selected


def show_help():
    sep = _get_separator_line()
    print(f"\n{sep}")
    print("HELP")
    print(sep)
    _print_help_section("Commands", list(_visible_command_hints()))
    print("\nNotes")
    print(_ui_line_char() * 60)
    print("  /ide supports: list, doctor, use, open, file")
    print("  Tab switches mode between enabled modes.")
    print(sep)


def show_debug_help():
    if CURRENT_MODE != AppMode.DEBUG:
        print("ERROR: /dhelp is available only in DEBUG mode.")
        return
    sep = _get_separator_line()
    print(f"\n{sep}")
    print("DEBUG HELP")
    print(sep)
    _print_help_section("Debug Commands", list(_visible_debug_command_hints()))
    print(sep)


def show_analyst_help():
    if CURRENT_MODE != AppMode.ANALYST:
        print("ERROR: /ahelp is available only in ANALYST mode.")
        return
    sep = _get_separator_line()
    print(f"\n{sep}")
    print("ANALYST HELP")
    print(sep)
    _print_help_section("Analyst Commands", list(_visible_analyst_command_hints()))
    print(sep)


def _handle_visualtest_command() -> bool:
    sep = _get_separator_line()
    print(f"\n{sep}")
    print("VISUAL TEST")
    print(sep)
    print("Preview of terminal UI elements.")
    print("")
    print("1. Prompt styles")
    print(f"  {Colors.MODE_CHAT}[CHAT]{Colors.ENDC}> example chat")
    print(f"  {Colors.MODE_PLAN}[PLAN]{Colors.ENDC}> example plan")
    print(f"  {Colors.MODE_CODE}[CODE]{Colors.ENDC}> example code")
    print(f"  {Colors.MODE_DEBUG}[DEBUG]{Colors.ENDC}> example debug")
    print(f"  {Colors.MODE_ANALYST}[ANALYST]{Colors.ENDC}> example analyst")
    print("")
    print("2. Command chip")
    print(f"  {_format_command_chip('load [mock-model] [time: 00.00.01.20]')}")
    print(f"  {_format_ide_command_chip()}")
    print("")
    print("3. IDE list preview")
    _handle_ide_command("/ide list")
    print("")
    print("4. Loading preview")
    print("  Loading model...")
    print(f"  {_format_command_chip('load [mock-model] [time: 00.00.01.20]')}")
    print("")
    print("5. Status preview")
    show_status()
    print("")
    print("6. Help preview")
    show_help()
    print("")
    print("7. Debug preview")
    print("[DEBUG] UnknownError: no message")
    print("[DEBUG] fixes:")
    print("  1. Add validation")
    print("  2. Add defensive handling")
    print("")
    print("8. Analyst preview")
    print("[ANALYST] Report saved: ANALYST_TEST.md")
    print("|_ report [ANALYST_TEST.md]")
    print(sep)
    return True


def show_mode_help():
    print("\nMODE HELP")
    print(_ui_line_char() * 60)
    print("  Prompt format: > (before model load), [MODE]> (after model load)")
    print("  Modes:")
    print("    chat  - standard conversation")
    plan_accept = (
        "auto-accept on"
        if _bool_from_any(APP_SETTINGS.get("auto_accept_plan"), False)
        else "requires accept"
    )
    code_accept = (
        "auto-accept on"
        if _bool_from_any(APP_SETTINGS.get("auto_accept_code"), False)
        else "requires accept"
    )
    if _bool_from_any(APP_SETTINGS.get("plan_mode_enabled"), True):
        print(f"    plan  - generates {PLAN_FILENAME} ({plan_accept})")
    if _bool_from_any(APP_SETTINGS.get("code_mode_enabled"), True):
        print(f"    code  - proposes file changes ({code_accept}, works without plan)")
    if _is_debug_mode_enabled():
        debug_accept = (
            "auto-accept on" if _should_auto_accept_debug() else "requires accept"
        )
        print(
            f"    debug - diagnoses bugs and can apply focused fixes ({debug_accept})"
        )
    if _is_analyst_mode_enabled():
        print("    analyst - runs static project analysis and reports")
    print("  Commands:")
    print("    /settings")
    print("    Tab (or Shift+Tab) to switch mode")
    print(_ui_line_char() * 60)


def show_models_menu() -> Tuple[List[Dict[str, Any]], int]:
    lines_printed = 0
    sep60 = _ui_line_char() * 60

    def _emit(text: str = "") -> None:
        nonlocal lines_printed
        print(text)
        lines_printed += _count_rendered_lines(text)

    if not HAS_AI_ENGINE:
        _emit("\n" + sep60)
        _emit("ERROR: AI ENGINE NOT INSTALLED")
        _emit(sep60)
        _emit("\nRun command: /install")
        _emit(sep60)
        return [], lines_printed

    models = loader.list_models()

    if not models:
        _emit("\n" + sep60)
        _emit("ERROR: NO GGUF MODELS IN FOLDER")
        _emit(sep60)
        _emit("\n1. Create folder 'models/' if missing")
        _emit("2. Put .gguf files into ./models/")
        _emit("3. Restart the program")
        _emit(sep60)
        return [], lines_printed

    _emit("\n" + sep60)
    _emit("  AVAILABLE GGUF MODELS")
    _emit(sep60)

    for i, model in enumerate(models, 1):
        size_mb = model["size_mb"]
        if size_mb > 1024:
            size_str = f"{size_mb / 1024:.1f} GB"
        else:
            size_str = f"{size_mb:.0f} MB"
        _emit(f"\n{i:2d}. {model['name']} ({size_str})")

    _emit("\n" + sep60)
    _emit("  Enter model number to load (or 'q' / Esc to cancel):")
    return models, lines_printed


def get_load_prompt() -> str:
    current = loader.current_model if (loader and loader.current_model) else "none"
    return f"load [{current}] "


def _pick_model_name(models: List[Dict[str, Any]], title: str = "/load") -> str:
    if not models:
        return ""

    options: List[Tuple[str, str]] = []
    for model in models:
        size_mb = float(model.get("size_mb", 0) or 0)
        size_str = f"{size_mb / 1024:.1f} GB" if size_mb > 1024 else f"{size_mb:.0f} MB"
        key = str(model.get("name", "")).strip()
        if not key:
            continue
        options.append((key, f"{key} ({size_str})"))

    if not options:
        return ""

    selected = _read_arrow_choice(title, options, default_idx=0)
    if selected == "cancel":
        return ""
    return selected


def show_download_catalog():
    aliases = loader.list_model_aliases() if loader else []
    sep60 = _ui_line_char() * 60
    print("\n" + sep60)
    print("  DOWNLOADABLE MODEL ALIASES")
    print(sep60)
    if not aliases:
        print("  No aliases defined.")
        print(sep60)
        return

    for entry in aliases:
        print(f"  {entry['alias']:<28} -> {entry['filename']}")
    print(sep60)
    print("Usage: /download <alias|url|owner/repo/file.gguf> [file.gguf]")


def show_status():
    sep = _get_separator_line()
    print(f"\n{sep}")
    print("  SYSTEM STATUS")
    print(f"{sep}")

    mode_color = (
        Colors.MODE_CHAT
        if CURRENT_MODE == AppMode.CHAT
        else (
            Colors.MODE_PLAN
            if CURRENT_MODE == AppMode.PLAN
            else (
                Colors.MODE_CODE
                if CURRENT_MODE == AppMode.CODE
                else (
                    Colors.MODE_DEBUG
                    if CURRENT_MODE == AppMode.DEBUG
                    else Colors.MODE_ANALYST
                )
            )
        )
    )

    print(f"\n  Mode: {mode_color}{CURRENT_MODE.upper()}{Colors.ENDC}")

    print("\nSYSTEM:")
    print(f"  OS: {sys.platform}")
    print(f"  Python: {sys.version.split()[0]}")

    print("\nMODEL:")
    if loader and loader.current_model:
        print(f"  LOADED: {loader.current_model}")
        if hasattr(loader, "model") and loader.model:
            print(f"  Context: {loader.model.n_ctx} tokens")
    else:
        print("  No model loaded")

    print("\nIDE INTEGRATION:")
    if ide_integration:
        status = ide_integration.get_status()
        print(f"  Active: {status['active'] or 'None'}")
        print(f"  Available: {', '.join(status['available']) or 'None detected'}")
        if status.get("project_root"):
            print(f"  Project root: {os.path.abspath(status['project_root'])}")
        if status.get("selected_file"):
            print(f"  Integrated file: {os.path.abspath(status['selected_file'])}")
        else:
            print("  Integrated file: None")

    print("\nSETTINGS:")
    print(f"  auto_accept_plan: {APP_SETTINGS.get('auto_accept_plan')}")
    print(f"  auto_accept_code: {APP_SETTINGS.get('auto_accept_code')}")
    print(f"  auto_accept_debug: {APP_SETTINGS.get('auto_accept_debug')}")
    print(f"  allow_ai_commands: {APP_SETTINGS.get('allow_ai_commands')}")
    print(
        f"  restrict_writes_to_open_file: {APP_SETTINGS.get('restrict_writes_to_open_file')}"
    )
    print(f"  prefer_fragment_edits: {APP_SETTINGS.get('prefer_fragment_edits')}")
    print(f"  debug_mode_enabled: {APP_SETTINGS.get('debug_mode_enabled')}")
    print(f"  analyst_mode_enabled: {APP_SETTINGS.get('analyst_mode_enabled')}")

    print(f"\n{sep}")


def _parse_toggle_value(raw: str) -> Optional[bool]:
    value = str(raw or "").strip().lower()
    if value in {"1", "on", "true", "yes", "y"}:
        return True
    if value in {"0", "off", "false", "no", "n"}:
        return False
    return None


def _parse_timeout_value(raw: str) -> Optional[int]:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    match = re.match(r"^(\d+)\s*s?$", text)
    if not match:
        return None
    try:
        return max(3, min(120, int(match.group(1))))
    except Exception:
        return None


def _scan_code_quality(changes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    sensitive_names = ("password", "passwd", "secret", "token", "apikey", "api_key")
    node_path = ""
    try:
        node_path = shutil.which("node") or ""
    except Exception:
        node_path = ""

    for change in changes:
        rel_path = str((change or {}).get("relative_path", "")).strip() or "unknown"
        code = str((change or {}).get("code", ""))
        ext = os.path.splitext(rel_path)[1].lower()

        if ext == ".py":
            try:
                compile(code, rel_path, "exec")
            except SyntaxError as exc:
                findings.append(
                    {
                        "severity": "critical",
                        "path": rel_path,
                        "message": f"syntax error at line {getattr(exc, 'lineno', '?')}",
                    }
                )
        elif ext == ".json":
            try:
                json.loads(code)
            except Exception as exc:
                findings.append(
                    {
                        "severity": "critical",
                        "path": rel_path,
                        "message": f"invalid JSON: {exc}",
                    }
                )
        elif ext in {".js", ".mjs", ".cjs"} and node_path:

            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", delete=False, suffix=ext
                ) as tmp:
                    tmp.write(code)
                    tmp_path = tmp.name
                try:
                    completed = subprocess.run(
                        [node_path, "--check", tmp_path],
                        capture_output=True,
                        text=True,
                        timeout=8,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if completed.returncode != 0:
                        msg = (completed.stderr or completed.stdout or "").strip()
                        msg = msg.splitlines()[0] if msg else "invalid JavaScript syntax"
                        findings.append(
                            {
                                "severity": "critical",
                                "path": rel_path,
                                "message": msg,
                            }
                        )
                finally:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
            except Exception:
                pass

        lines = code.splitlines()
        for line_no, line in enumerate(lines, 1):
            lowered = line.lower()
            stripped = line.strip()
            if "eval(" in line or "exec(" in line:
                findings.append(
                    {
                        "severity": "critical",
                        "path": rel_path,
                        "message": f"line {line_no}: use of eval/exec",
                    }
                )
            if any(name in lowered for name in sensitive_names) and "=" in line:
                rhs = line.split("=", 1)[1].strip()
                if rhs.startswith(("'", '"')) and len(rhs) > 2:
                    findings.append(
                        {
                            "severity": "critical",
                            "path": rel_path,
                            "message": f"line {line_no}: possible hardcoded secret",
                        }
                    )
            if "subprocess" in lowered and "shell=true" in lowered:
                findings.append(
                    {
                        "severity": "warning",
                        "path": rel_path,
                        "message": f"line {line_no}: subprocess with shell=True",
                    }
                )
            if "pickle.loads(" in lowered or "yaml.load(" in lowered:
                findings.append(
                    {
                        "severity": "warning",
                        "path": rel_path,
                        "message": f"line {line_no}: unsafe deserialization pattern",
                    }
                )
            if stripped.startswith("print(") and (
                "password" in lowered or "token" in lowered
            ):
                findings.append(
                    {
                        "severity": "warning",
                        "path": rel_path,
                        "message": f"line {line_no}: possible sensitive data logging",
                    }
                )
    return findings


def _format_quality_gate_summary(findings: List[Dict[str, str]]) -> str:
    critical = sum(1 for item in findings if item.get("severity") == "critical")
    warnings = sum(1 for item in findings if item.get("severity") == "warning")
    if not findings:
        return "0 critical issues, 0 warnings"
    return f"{critical} critical issues, {warnings} warnings"


def _describe_code_readiness(changes: List[Dict[str, Any]]) -> str:
    findings = _scan_code_quality(changes)
    critical = sum(1 for item in findings if item.get("severity") == "critical")
    warnings = sum(1 for item in findings if item.get("severity") == "warning")
    if not findings:
        return "Code check: looks good."
    if critical:
        return f"Code check: {critical} critical issues, {warnings} warnings."
    return f"Code check: no critical issues, {warnings} warnings."


def _quality_gate_revision_feedback(findings: List[Dict[str, str]]) -> str:
    feedback_lines = [
        "Fix all quality-gate findings before saving files.",
        f"Summary: {_format_quality_gate_summary(findings)}.",
    ]
    for finding in findings[:12]:
        feedback_lines.append(
            f"- {finding.get('severity', 'warning')}: {finding.get('path', 'unknown')} - {finding.get('message', '')}"
        )
    return "\n".join(feedback_lines)


def _run_quality_gate(
    changes: List[Dict[str, Any]],
    mode: str,
    interactive: bool = True,
    auto_action: str = "fix",
) -> Tuple[str, str]:
    if not changes or not _bool_from_any(
        APP_SETTINGS.get("quality_gate_enabled"), True
    ):
        return "accept", ""

    findings = _scan_code_quality(changes)
    if not findings:
        return "accept", ""

    print(f"[{mode}] Quality gate: {_format_quality_gate_summary(findings)}")
    if not interactive:
        if auto_action == "fix":
            return "revise", _quality_gate_revision_feedback(findings)
        if auto_action in {"force", "accept"}:
            return "accept", ""
        return "cancel", ""
    while True:
        action = _read_arrow_choice(
            f"[{mode}] Quality gate",
            [
                ("fix", "Fix issues"),
                ("review", "Review report"),
                ("force", "Force save"),
                ("cancel", "Cancel"),
            ],
            default_idx=0,
        )
        if action == "review":
            print(f"[{mode}] Report:")
            for finding in findings[:12]:
                sev = finding.get("severity", "warning").upper()
                path = finding.get("path", "unknown")
                message = finding.get("message", "")
                print(f"  {sev}: {path} - {message}")
            if len(findings) > 12:
                print(f"  ... {len(findings) - 12} more")
            continue
        if action == "fix":
            return "revise", _quality_gate_revision_feedback(findings)
        if action == "force":
            return "accept", ""
        return "cancel", ""


def _selected_python_file_for_tests(raw_command: str) -> Tuple[str, str]:
    requested = (raw_command or "").strip().split(maxsplit=1)
    if len(requested) > 1 and requested[1].strip():
        raw_path = requested[1].strip().strip("\"'")
        if ide_integration and getattr(ide_integration, "project_root", None):
            candidate = os.path.abspath(
                os.path.join(ide_integration.project_root, raw_path)
            )
        else:
            candidate = os.path.abspath(raw_path)
    else:
        candidate = (
            getattr(ide_integration, "selected_file", None) if ide_integration else None
        )
        if candidate:
            candidate = os.path.abspath(candidate)
    if not candidate:
        return "", "No open file selected in IDE. Use /ide file <path> first."
    if not os.path.exists(candidate):
        return "", f"Selected file not found: {candidate}"
    if os.path.splitext(candidate)[1].lower() != ".py":
        return "", "Test generator currently works only for Python files."
    return candidate, ""


def _build_python_test_file(source_path: str) -> Tuple[str, str]:
    root_abs = (
        code_file_manager._effective_project_root()
        if code_file_manager
        else os.getcwd()
    )
    rel_path = os.path.relpath(source_path, root_abs).replace("\\", "/")
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as exc:
        return "", f"Failed to read source file: {exc}"

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return (
            "",
            f"Cannot generate tests because source has syntax error at line {exc.lineno}.",
        )

    functions = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            arg_names = [arg.arg for arg in node.args.args]
            if arg_names and arg_names[0] == "self":
                continue
            functions.append({"name": node.name, "args": arg_names})

    if not functions:
        return "", "No top-level functions found for test generation."

    module_rel = rel_path[:-3] if rel_path.lower().endswith(".py") else rel_path
    module_import = module_rel.replace("/", ".").replace("\\", ".")
    stem = os.path.splitext(os.path.basename(source_path))[0]
    test_rel_path = f"tests/test_{stem}.py"

    lines: List[str] = []
    lines.append("import unittest")
    lines.append("")
    lines.append(
        f"from {module_import} import {', '.join(item['name'] for item in functions)}"
    )
    lines.append("")
    lines.append("")
    lines.append(
        f"class Test{''.join(part.capitalize() for part in stem.split('_'))}(unittest.TestCase):"
    )

    def _arg_values(arg_name: str) -> List[str]:
        low = arg_name.lower()
        if any(token in low for token in ("text", "name", "str", "message")):
            return ["'sample'", "''", "None"]
        if any(
            token in low
            for token in ("count", "num", "size", "index", "age", "amount", "value")
        ):
            return ["1", "0", "-1"]
        if any(token in low for token in ("flag", "enabled", "active")):
            return ["True", "False"]
        if any(token in low for token in ("items", "values", "data", "rows", "list")):
            return ["[]", "[1, 2]", "None"]
        return ["1", "None", "''"]

    for item in functions:
        func_name = item["name"]
        args = item["args"]
        lines.append(f"    def test_{func_name}_basic(self):")
        if args:
            call_args = ", ".join(values[0] for values in map(_arg_values, args))
            lines.append(f"        result = {func_name}({call_args})")
            lines.append("        self.assertIsNotNone(result)")
        else:
            lines.append(f"        result = {func_name}()")
            lines.append("        self.assertIsNotNone(result)")
        lines.append("")

        edge_cases = []
        for idx, arg in enumerate(args):
            for value in _arg_values(arg)[1:]:
                edge_args = []
                for j, inner in enumerate(args):
                    values = _arg_values(inner)
                    edge_args.append(value if j == idx else values[0])
                edge_cases.append((arg, value, edge_args))
        if not edge_cases:
            lines.append(f"    def test_{func_name}_edge(self):")
            lines.append(f"        result = {func_name}()")
            lines.append("        self.assertIsNotNone(result)")
            lines.append("")
        else:
            for edge_idx, (arg_name, value, edge_args) in enumerate(edge_cases[:4], 1):
                safe_value = (
                    re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_")
                    or f"case_{edge_idx}"
                )
                lines.append(
                    f"    def test_{func_name}_edge_{edge_idx}_{arg_name}_{safe_value}(self):"
                )
                lines.append("        try:")
                lines.append(
                    f"            result = {func_name}({', '.join(edge_args)})"
                )
                lines.append(
                    "            self.assertTrue(result is None or result is not None)"
                )
                lines.append("        except Exception:")
                lines.append("            pass")
                lines.append("")

    lines.append("")
    lines.append("if __name__ == '__main__':")
    lines.append("    unittest.main()")
    return test_rel_path, "\n".join(lines).rstrip() + "\n"


def _handle_tests_command(raw_command: str) -> bool:
    if CURRENT_MODE != AppMode.DEBUG:
        print("ERROR: /tests is available only in DEBUG mode.")
        return False
    if not code_file_manager:
        print("ERROR: File manager unavailable.")
        return False

    source_path, error = _selected_python_file_for_tests(raw_command)
    if error:
        print(f"ERROR: {error}")
        return False

    test_rel_path, test_content_or_error = _build_python_test_file(source_path)
    if not test_rel_path:
        print(f"ERROR: {test_content_or_error}")
        return False

    changes = [
        {
            "relative_path": test_rel_path,
            "language": "python",
            "code": test_content_or_error,
        }
    ]
    quality_decision, quality_feedback = _run_quality_gate(
        changes,
        "TESTS",
        interactive=True,
        auto_action="force",
    )
    if quality_decision == "cancel":
        print("[TESTS] Cancelled by quality gate.")
        return False
    if quality_decision == "revise":
        print("[TESTS] Fix issues in source file first, then run /tests again.")
        print(quality_feedback)
        return False

    root_abs = code_file_manager._effective_project_root()
    test_abs_path = os.path.abspath(os.path.join(root_abs, test_rel_path))
    os.makedirs(os.path.dirname(test_abs_path), exist_ok=True)
    existed = os.path.exists(test_abs_path)
    try:
        with open(test_abs_path, "w", encoding="utf-8") as f:
            f.write(test_content_or_error)
    except Exception as exc:
        print(f"[TESTS] ERROR: Failed to write test file: {exc}")
        return False
    if ide_integration and ide_integration.active_ide:
        ide_integration.open_file(test_abs_path)
    code_file_manager.last_applied_changes = [
        {
            "action": "updated" if existed else "created",
            "path": test_abs_path,
            "relative_path": test_rel_path,
            "language": "python",
        }
    ]
    code_file_manager.created_files.append(test_abs_path)

    print(f"[TESTS] create [1 file]")
    print(f"[TESTS] source: {os.path.basename(source_path)}")
    print(f"[TESTS] file: {test_rel_path}")
    return True


debug_engine = DebugEngine()
analyst_engine = AnalystEngine()


def _require_debug_command(command_name: str) -> bool:
    if CURRENT_MODE == AppMode.DEBUG:
        return True
    print(f"ERROR: /{command_name} is available only in DEBUG mode.")
    return False


def _require_analyst_command(command_name: str) -> bool:
    if CURRENT_MODE == AppMode.ANALYST:
        return True
    print(f"ERROR: /{command_name} is available only in ANALYST mode.")
    return False


def _debug_command_text(raw_command: str) -> str:
    parts = (raw_command or "").split(maxsplit=1)
    if len(parts) > 1:
        return parts[1].strip()
    return ""


def _resolve_debug_analysis(
    raw_command: str, command_name: str
) -> Tuple[Optional[Dict[str, Any]], str]:
    if not _require_debug_command(command_name):
        return None, ""
    if ide_integration and hasattr(ide_integration, "sync_project_context"):
        try:
            ide_integration.sync_project_context(force_refresh=True)
        except Exception:
            pass
    trace_text = _debug_command_text(raw_command) or debug_engine.last_traceback_text
    if not trace_text:
        selected_file = _current_ide_file_path()
        if selected_file and os.path.exists(selected_file):
            trace_text = (
                "No traceback provided.\n"
                f"Target file: {selected_file}\n"
                f"Project root: {_current_project_root_path(force_refresh=False)}\n"
                "Use static diagnosis and propose safest minimal fix."
            )
        else:
            trace_text = (
                "No traceback provided.\n"
                f"Project root: {_current_project_root_path(force_refresh=False)}\n"
                "Use static diagnosis and propose safest minimal fix."
            )
    analysis = debug_engine.analyze_traceback(trace_text)
    debug_engine.record_error(command_name, trace_text, analysis)
    return analysis, trace_text


def _handle_trace_command(raw_command: str) -> bool:
    analysis, _ = _resolve_debug_analysis(raw_command, "trace")
    if not analysis:
        return False

    use_uni = _supports_unicode_ui()
    box_h = "─" if use_uni else "-"
    box_v = "│" if use_uni else "|"
    box_tl = "┌" if use_uni else "+"
    box_tr = "┐" if use_uni else "+"
    box_bl = "└" if use_uni else "+"
    box_br = "┘" if use_uni else "+"
    bullet = "•" if use_uni else "*"
    arrow = "→" if use_uni else "->"


    error_type = analysis.get('error_type') or 'UnknownError'
    error_msg = analysis.get('error_message') or 'no message'
    print(f"{Colors.BOLD}{box_tl}{box_h*50}{box_tr}{Colors.ENDC}")
    print(f"{Colors.BOLD}{box_v}{Colors.ENDC} {Colors.BOLD_RED}TRACE{Colors.ENDC} {Colors.DIM}{error_type}{Colors.ENDC}")
    print(f"{Colors.BOLD}{box_v}{Colors.ENDC} {Colors.FAIL}{error_msg}{Colors.ENDC}")
    print(f"{Colors.BOLD}{box_bl}{box_h*50}{box_br}{Colors.ENDC}")


    if analysis.get("primary_file"):
        loc = f"{analysis.get('primary_file')}:{analysis.get('primary_line')}"
        func = analysis.get('primary_function') or 'unknown'
        print(f"{Colors.INFO}{arrow}{Colors.ENDC} {Colors.DIM}Location:{Colors.ENDC} {Colors.BOLD}{loc}{Colors.ENDC}")
        print(f"{Colors.INFO}{arrow}{Colors.ENDC} {Colors.DIM}Function:{Colors.ENDC} {func}")


    if analysis.get("primary_code"):
        code = analysis.get('primary_code', '').strip()
        if code:
            print(f"\n{Colors.DIM}Code:{Colors.ENDC}")
            print(f"{Colors.SYNTAX_COMMENT}{box_v}{Colors.ENDC} {Colors.WARNING}{code}{Colors.ENDC}")


    root_cause = analysis.get('root_cause') or 'Unknown'
    print(f"\n{Colors.BOLD_YELLOW}Root Cause:{Colors.ENDC}")
    print(f"  {root_cause}")


    solutions = analysis.get("solutions", [])
    if solutions:
        print(f"\n{Colors.BOLD_GREEN}Suggested Fixes:{Colors.ENDC}")
        for idx, option in enumerate(solutions[:3], 1):
            title = option.get('title') or 'Untitled fix'
            pros = option.get('pros') or 'N/A'
            cons = option.get('cons') or 'N/A'
            print(f"  {Colors.OKGREEN}{bullet}{Colors.ENDC} {Colors.BOLD}{idx}. {title}{Colors.ENDC}")
            print(f"    {Colors.DIM}Pros:{Colors.ENDC} {Colors.OKGREEN}{pros}{Colors.ENDC}")
            print(f"    {Colors.DIM}Cons:{Colors.ENDC} {Colors.WARNING}{cons}{Colors.ENDC}")

    return True


def _handle_stack_command(raw_command: str) -> bool:
    analysis, _ = _resolve_debug_analysis(raw_command, "stack")
    if not analysis:
        return False
    print("[STACK]")
    for row in debug_engine.stack_summary(analysis):
        print(f"  {row}")
    return True


def _handle_quickfix_command(raw_command: str) -> bool:
    analysis, _ = _resolve_debug_analysis(raw_command, "quickfix")
    if not analysis:
        return False
    print("[QUICKFIX]")
    for idx, row in enumerate(debug_engine.quick_fixes(analysis), 1):
        print(f"  {idx}. {row}")
    return True


def _handle_patterns_command() -> bool:
    if not _require_debug_command("patterns"):
        return False
    print("[PATTERNS]")
    for row in debug_engine.recurring_patterns():
        print(f"  {row}")
    return True


def _handle_autofix_command(raw_command: str) -> bool:
    analysis, trace_text = _resolve_debug_analysis(raw_command, "autofix")
    if not analysis:
        return False
    fix_prompt = _build_debug_fix_request(analysis, trace_text, include_tests=False)
    if not loader or not loader.current_model:
        print("[AUTOFIX] No model loaded; using fallback plan only.")
        print(
            f"[AUTOFIX] target: {analysis.get('primary_file')}:{analysis.get('primary_line')}"
        )
        for idx, row in enumerate(debug_engine.quick_fixes(analysis), 1):
            print(f"  {idx}. {row}")
        return True
    return send_terminal_prompt(fix_prompt, max_tokens=1600, temperature=0.2, top_p=0.9)


def _build_debug_fix_request(
    analysis: Dict[str, Any],
    trace_text: str,
    include_tests: bool = True,
) -> str:
    primary_file = str(analysis.get("primary_file") or "").strip()
    primary_line = int(analysis.get("primary_line") or 0)
    root_cause = str(analysis.get("root_cause") or "Unknown root cause").strip()
    error_type = str(analysis.get("error_type") or "UnknownError").strip()
    project_root = _current_project_root_path(force_refresh=False)
    selected_file = _current_ide_file_path()
    lines = [
        "Debug request.",
        f"Fix this bug: {error_type} at {primary_file or '[unknown file]'}:{primary_line}.",
        f"Root cause: {root_cause}",
    ]
    if project_root:
        lines.append(f"Project root: {project_root}")
    if selected_file:
        lines.append(f"Selected IDE file: {selected_file}")
    if include_tests:
        lines.append(
            "If you can identify the affected code path, also add or update a focused regression test."
        )
    lines.append("Return only file updates when a fix is possible.")
    if trace_text:
        lines.append("")
        lines.append("Diagnostic context:")
        lines.append(trace_text.strip())
    return "\n".join(lines).strip()


def _handle_debug_command(raw_command: str) -> bool:
    analysis, trace_text = _resolve_debug_analysis(raw_command, "debug")
    if not analysis:
        return False

    use_uni = _supports_unicode_ui()
    spinner = "◐◓◑◒" if use_uni else "|/-\\"
    arrow = "→" if use_uni else "->"
    bullet = "•" if use_uni else "*"

    error_type = str(analysis.get("error_type") or "UnknownError")
    target = str(analysis.get("primary_file") or _current_ide_file_path() or "None")
    root_cause = str(analysis.get("root_cause") or "Unknown root cause")

    if loader and loader.current_model:

        print(f"\n{Colors.BOLD_BLUE}╔{'═'*48}╗{Colors.ENDC}")
        print(f"{Colors.BOLD_BLUE}║{Colors.ENDC} {Colors.BOLD}{Colors.MODE_DEBUG}DEBUG ANALYSIS{Colors.ENDC}{' '*32}{Colors.BOLD_BLUE}║{Colors.ENDC}")
        print(f"{Colors.BOLD_BLUE}╠{'═'*48}╣{Colors.ENDC}")
        print(f"{Colors.BOLD_BLUE}║{Colors.ENDC} {Colors.DIM}Error:{Colors.ENDC} {Colors.BOLD_RED}{error_type}{Colors.ENDC}")
        print(f"{Colors.BOLD_BLUE}║{Colors.ENDC} {Colors.DIM}Target:{Colors.ENDC} {Colors.BOLD}{target}{Colors.ENDC}")
        print(f"{Colors.BOLD_BLUE}║{Colors.ENDC} {Colors.DIM}Root Cause:{Colors.ENDC} {root_cause[:40]}{'...' if len(root_cause) > 40 else ''}{Colors.ENDC}")
        print(f"{Colors.BOLD_BLUE}╚{'═'*48}╝{Colors.ENDC}\n")

        print(f"{Colors.INFO}{arrow} Sending to AI for fix generation...{Colors.ENDC}")

        fix_prompt = _build_debug_fix_request(analysis, trace_text, include_tests=True)
        ok = send_terminal_prompt(
            fix_prompt,
            max_tokens=1800,
            temperature=0.2,
            top_p=0.9,
            suppress_empty_output_error=True,
        )
        if ok:
            return True


        print(f"\n{Colors.WARNING}{bullet} AI returned empty output. Fallback to summary:{Colors.ENDC}")
        print(f"  {Colors.DIM}Error Type:{Colors.ENDC} {Colors.BOLD}{error_type}{Colors.ENDC}")
        print(f"  {Colors.DIM}Target:{Colors.ENDC} {target}")
        print(f"  {Colors.DIM}Root Cause:{Colors.ENDC} {root_cause}")
        print(f"\n{Colors.INFO}{arrow} Next steps:{Colors.ENDC}")
        print(f"  1. Rerun /debug after loading a model")
        print(f"  2. Provide a traceback with more context")
        return True


    print(f"\n{Colors.BOLD_YELLOW}╔{'═'*48}╗{Colors.ENDC}")
    print(f"{Colors.BOLD_YELLOW}║{Colors.ENDC} {Colors.BOLD}{Colors.MODE_DEBUG}DEBUG SUMMARY{Colors.ENDC}{' '*33}{Colors.BOLD_YELLOW}║{Colors.ENDC}")
    print(f"{Colors.BOLD_YELLOW}╠{'═'*48}╣{Colors.ENDC}")
    print(f"{Colors.BOLD_YELLOW}║{Colors.ENDC} {Colors.DIM}Error:{Colors.ENDC} {Colors.BOLD_RED}{error_type}{Colors.ENDC}")
    print(f"{Colors.BOLD_YELLOW}║{Colors.ENDC} {Colors.DIM}Target:{Colors.ENDC} {Colors.BOLD}{target}{Colors.ENDC}")
    print(f"{Colors.BOLD_YELLOW}║{Colors.ENDC} {Colors.DIM}Root Cause:{Colors.ENDC} {root_cause[:40]}{'...' if len(root_cause) > 40 else ''}{Colors.ENDC}")
    print(f"{Colors.BOLD_YELLOW}╚{'═'*48}╝{Colors.ENDC}\n")

    print(f"{Colors.WARNING}{bullet} No model loaded{Colors.ENDC}")
    print(f"{Colors.INFO}{arrow} To apply an autofix, load a model with /load <model>{Colors.ENDC}")
    return True


def _print_analyst_rows(tag: str, rows: List[str]) -> None:
    print(f"[{tag}]")
    for row in rows[:6]:
        print(f"  {row}")
    if len(rows) > 6:
        print(f"  ... {len(rows) - 6} more")


def _print_analyst_summary(report: Dict[str, List[str]]) -> None:
    use_uni = _supports_unicode_ui()
    check = "✓" if use_uni else "OK"
    warn = "⚠" if use_uni else "!"
    bullet = "•" if use_uni else "*"
    arrow = "→" if use_uni else "->"


    category_styles = {
        "deps": (Colors.BOLD_CYAN, "📦" if use_uni else "[D]"),
        "perf": (Colors.BOLD_YELLOW, "⚡" if use_uni else "[P]"),
        "refactor": (Colors.BOLD_BLUE, "🔧" if use_uni else "[R]"),
        "docs": (Colors.BOLD, "📄" if use_uni else "[O]"),
        "complexity": (Colors.BOLD_RED, "📊" if use_uni else "[C]"),
        "security": (Colors.BOLD_RED, "🔒" if use_uni else "[S]"),
        "coverage": (Colors.BOLD_GREEN, "🎯" if use_uni else "[V]"),
        "architecture": (Colors.BOLD_BLUE, "🏗" if use_uni else "[A]"),
        "style": (Colors.BOLD_CYAN, "🎨" if use_uni else "[T]"),
        "graph": (Colors.BOLD, "🕸" if use_uni else "[G]"),
        "deadcode": (Colors.DIM, "🗑" if use_uni else "[X]"),
        "benchmark": (Colors.BOLD_YELLOW, "⏱" if use_uni else "[B]"),
    }

    order = (
        "deps",
        "perf",
        "refactor",
        "docs",
        "complexity",
        "security",
        "coverage",
        "architecture",
        "style",
        "graph",
        "deadcode",
        "benchmark",
    )


    print(f"\n{Colors.BOLD}{Colors.MODE_ANALYST}╔{'═'*50}╗{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC} {'ANALYST REPORT':^48} {Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.MODE_ANALYST}╠{'═'*50}╣{Colors.ENDC}")

    total_issues = 0
    for key in order:
        rows = report.get(key, [])
        label = key.upper()
        count = len(rows)
        total_issues += count
        color, icon = category_styles.get(key, (Colors.BOLD, ""))

        if not rows:
            print(f"{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC} {color}{icon}{Colors.ENDC} {Colors.DIM}{label:12}{Colors.ENDC} {Colors.SUCCESS}{check} OK{Colors.ENDC}{' '*22}{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC}")
            continue


        if count >= 10:
            count_color = Colors.BOLD_RED
        elif count >= 5:
            count_color = Colors.WARNING
        else:
            count_color = Colors.BOLD_YELLOW

        print(f"{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC} {color}{icon}{Colors.ENDC} {color}{label:12}{Colors.ENDC} {count_color}{warn} {count} issue(s){Colors.ENDC}{' '*15}{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC}")

    print(f"{Colors.BOLD}{Colors.MODE_ANALYST}╠{'═'*50}╣{Colors.ENDC}")
    if total_issues == 0:
        print(f"{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC} {Colors.SUCCESS}{check} No issues found - code looks good!{' '*16}{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC}")
    else:
        print(f"{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC} {Colors.WARNING}{warn} Total: {total_issues} issue(s) to review{' '*18}{Colors.BOLD}{Colors.MODE_ANALYST}║{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.MODE_ANALYST}╚{'═'*50}╝{Colors.ENDC}\n")


    for key in order:
        rows = report.get(key, [])
        if not rows:
            continue
        label = key.upper()
        color, icon = category_styles.get(key, (Colors.BOLD, ""))
        print(f"{color}{icon} {label}{Colors.ENDC}")
        for row in rows[:3]:
            print(f"  {Colors.DIM}{bullet}{Colors.ENDC} {row}")
        if len(rows) > 3:
            print(f"  {Colors.DIM}... {len(rows) - 3} more{Colors.ENDC}")
        print()


def _format_analyst_report_context(report: Dict[str, List[str]]) -> str:
    order = (
        "deps",
        "perf",
        "refactor",
        "docs",
        "complexity",
        "security",
        "coverage",
        "architecture",
        "style",
        "graph",
        "deadcode",
        "benchmark",
    )
    lines: List[str] = []
    for key in order:
        rows = list((report or {}).get(key, []) or [])
        lines.append(f"[{key.upper()}] {len(rows)} item(s)")
        for row in rows[:3]:
            lines.append(f"- {row}")
    return "\n".join(lines).strip()


def _handle_analyst_command() -> bool:
    if not _require_analyst_command("analyst"):
        return False
    written = analyst_engine.ensure_project_basics()
    report = analyst_engine.run_full_analysis()
    report_path = analyst_engine.write_analyst_test_report(report)
    if not report_path:
        print(f"[ANALYST] ERROR: Failed to write {ANALYST_TEST_FILENAME}")
        return False


    if written:
        for fname in written:
            root = analyst_engine._project_root()
            fpath = os.path.join(root, fname)
            if os.path.exists(fpath) and ide_integration and ide_integration.active_ide:
                try:
                    ide_integration.open_file(fpath)
                except Exception:
                    pass
        print(f"[ANALYST] {Colors.SUCCESS}Created docs:{Colors.ENDC} {', '.join(written)}")
    if loader and loader.current_model:
        analyst_request = (
            "Run a full project analysis using the static scan as baseline. "
            "Summarize the most important issues, prioritize fixes, and suggest next steps. "
            "Keep the terminal output short and actionable. "
            "Also produce a clean Markdown report body suitable for ANALYST_TEST.md.\n\n"
            f"Static report context:\n{_format_analyst_report_context(report)}"
        )
        return send_terminal_prompt(
            analyst_request, max_tokens=1800, temperature=0.2, top_p=0.9
        )
    if written:
        print(f"[ANALYST] Generated docs: {', '.join(written)}")
    else:
        print("[ANALYST] No docs were written.")
    print(f"[ANALYST] Report saved: {os.path.basename(report_path)}")
    return True


def _handle_todo_command() -> bool:
    if not _require_analyst_command("todo"):
        return False
    root = analyst_engine._project_root()
    path = os.path.join(root, TODO_FILENAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(analyst_engine.build_todo_markdown())
    except Exception as exc:
        print(f"[TODO] ERROR: Failed to write {TODO_FILENAME}: {exc}")
        return False
    print(f"[TODO] wrote {TODO_FILENAME}")
    return True


def _handle_deps_command() -> bool:
    if not _require_analyst_command("deps"):
        return False
    _print_analyst_rows("DEPS", analyst_engine.analyze_dependencies())
    return True


def _handle_perf_command() -> bool:
    if not _require_analyst_command("perf"):
        return False
    _print_analyst_rows("PERF", analyst_engine.analyze_performance())
    return True


def _handle_refactor_command() -> bool:
    if not _require_analyst_command("refactor"):
        return False
    _print_analyst_rows("REFACTOR", analyst_engine.analyze_refactor())
    return True


def _handle_docs_command() -> bool:
    if not _require_analyst_command("docs"):
        return False
    _print_analyst_rows("DOCS", analyst_engine.generate_docs_preview())
    return True


def _handle_complexity_command() -> bool:
    if not _require_analyst_command("complexity"):
        return False
    _print_analyst_rows("COMPLEXITY", analyst_engine.analyze_complexity())
    return True


def _handle_security_command() -> bool:
    if not _require_analyst_command("security"):
        return False
    _print_analyst_rows("SECURITY", analyst_engine.analyze_security())
    return True


def _handle_coverage_command() -> bool:
    if not _require_analyst_command("coverage"):
        return False
    _print_analyst_rows("COVERAGE", analyst_engine.analyze_coverage())
    return True


def _handle_architecture_command() -> bool:
    if not _require_analyst_command("architecture"):
        return False
    _print_analyst_rows("ARCHITECTURE", analyst_engine.analyze_architecture())
    return True


def _handle_style_command() -> bool:
    if not _require_analyst_command("style"):
        return False
    _print_analyst_rows("STYLE", analyst_engine.analyze_style())
    return True


def _handle_graph_command() -> bool:
    if not _require_analyst_command("graph"):
        return False
    _print_analyst_rows("GRAPH", analyst_engine.analyze_graph())
    return True


def _handle_deadcode_command() -> bool:
    if not _require_analyst_command("deadcode"):
        return False
    _print_analyst_rows("DEADCODE", analyst_engine.analyze_dead_code())
    return True


def _handle_benchmark_command() -> bool:
    if not _require_analyst_command("benchmark"):
        return False
    _print_analyst_rows("BENCHMARK", analyst_engine.analyze_benchmark())
    return True


def _handle_analyst_named_command(command_name: str) -> bool:
    if command_name == "ahelp":
        show_analyst_help()
        return True
    if command_name == "analyst":
        return _handle_analyst_command()
    if command_name == "todo":
        return _handle_todo_command()
    if command_name == "deps":
        return _handle_deps_command()
    if command_name == "perf":
        return _handle_perf_command()
    if command_name == "refactor":
        return _handle_refactor_command()
    if command_name == "docs":
        return _handle_docs_command()
    if command_name == "complexity":
        return _handle_complexity_command()
    if command_name == "security":
        return _handle_security_command()
    if command_name == "coverage":
        return _handle_coverage_command()
    if command_name == "architecture":
        return _handle_architecture_command()
    if command_name == "style":
        return _handle_style_command()
    if command_name == "graph":
        return _handle_graph_command()
    if command_name == "deadcode":
        return _handle_deadcode_command()
    if command_name == "benchmark":
        return _handle_benchmark_command()
    return False


def _settings_sections() -> List[Tuple[str, str]]:
    return [
        ("modes", "Modes"),
        ("automation", "Automation"),
        ("commands", "Commands"),
        ("quality", "Quality"),
        ("interface", "Interface"),
        ("files", "Files"),
        ("timeout", "Command timeout"),
        ("save", "Save and exit"),
        ("reset", "Reset all settings"),
        ("cancel", "Exit"),
    ]


def _settings_section_items() -> Dict[str, List[str]]:
    return {
        "modes": [
            "plan_mode_enabled",
            "code_mode_enabled",
            "debug_mode_enabled",
            "analyst_mode_enabled",
        ],
        "automation": [
            "auto_accept_plan",
            "auto_accept_code",
            "auto_accept_debug",
            "confirm_exit",
        ],
        "commands": ["allow_ai_commands", "confirm_ai_commands"],
        "quality": ["quality_gate_enabled"],
        "interface": ["pin_input_top", "hide_top_input_border"],
        "files": ["code_mode_without_plan", "prefer_fragment_edits", "require_fragment_edits"],
    }


def _settings_labels() -> Dict[str, str]:
    return {
        "plan_mode_enabled": "Plan mode",
        "code_mode_enabled": "Code mode",
        "debug_mode_enabled": "Debug mode",
        "analyst_mode_enabled": "Analyst mode",
        "auto_accept_plan": "Auto-accept plan",
        "auto_accept_code": "Auto-accept code",
        "auto_accept_debug": "Auto-accept debug",
        "confirm_exit": "Confirm exit",
        "allow_ai_commands": "Allow AI commands",
        "confirm_ai_commands": "Confirm AI commands",
        "quality_gate_enabled": "Code quality gate",
        "pin_input_top": "Pin input top",
        "hide_top_input_border": "Hide top border",
        "code_mode_without_plan": "Code without plan",
        "prefer_fragment_edits": "Prefer fragment edits",
        "require_fragment_edits": "Require fragment edits",
    }


def _settings_help_map() -> Dict[str, str]:
    return {
        "plan_mode_enabled": "Pokazuje i wlacza tryb PLAN w aplikacji.",
        "code_mode_enabled": "Pokazuje i wlacza tryb CODE w aplikacji.",
        "debug_mode_enabled": "Pokazuje i wlacza tryb DEBUG w aplikacji.",
        "analyst_mode_enabled": "Pokazuje i wlacza tryb ANALYST w aplikacji.",
        "auto_accept_plan": "Automatycznie akceptuje wynik PLAN bez pytania.",
        "auto_accept_code": "Automatycznie akceptuje zapis zmian CODE bez pytania.",
        "auto_accept_debug": "Automatycznie akceptuje zapis zmian DEBUG bez pytania.",
        "confirm_exit": "Pyta o potwierdzenie przed wyjsciem z aplikacji.",
        "allow_ai_commands": "Pozwala AI uruchamiac komendy przez [[CALL:CMD]].",
        "confirm_ai_commands": "Wymaga potwierdzenia przed uruchomieniem kazdej komendy AI.",
        "quality_gate_enabled": "Sprawdza zapis plikow pod katem syntax/security przed save.",
        "pin_input_top": "Przypina panel wpisywania na gorze terminala.",
        "hide_top_input_border": "Ukrywa gorna linie obramowania panelu wpisywania.",
        "code_mode_without_plan": "Pozwala trybowi CODE dzialac bez pliku planu.",
        "prefer_fragment_edits": "Preferuje male patche SEARCH/REPLACE zamiast nadpisywania calego pliku.",
        "require_fragment_edits": "Wymusza patche (```patch) dla istniejacych plikow; blokuje nadpisywanie calego pliku.",
        "timeout": "Maksymalny czas wykonania pojedynczej komendy AI.",
        "save": "Zapisuje ustawienia do pliku i wychodzi z menu.",
        "reset": "Przywraca ustawienia domyslne.",
        "cancel": "Zamyka menu ustawien bez dodatkowych zmian.",
        "modes": "Tryby aplikacji, ktore maja byc dostepne.",
        "automation": "Automatyczne potwierdzenia i wyjscie.",
        "commands": "Uruchamianie komend przez AI.",
        "quality": "Kontrola jakosci przed zapisem plikow.",
        "interface": "Wyglad i polozenie panelu wpisywania.",
        "files": "Zasady zapisu plikow i pracy CODE.",
    }


def _print_settings_help() -> None:
    print("Usage:")
    print("  /settings")
    print("  /settings set <key> <on|off>")
    print("  /settings timeout <seconds>")
    print("  /settings reset")
    print("  /settings save")
    print("  /ide file [path]")
    print("Sections:")
    print("  Modes")
    print("  Automation")
    print("  Commands")
    print("  Quality")
    print("  Interface")
    print("  Files")


def _handle_settings_command(raw_command: str) -> bool:
    parts = (raw_command or "").strip().split()
    if len(parts) == 1:
        settings_help_map = _settings_help_map()
        labels = _settings_labels()
        section_items = _settings_section_items()
        while True:
            top_options = []
            for key, label in _settings_sections():
                if key == "save":
                    top_options.append((key, label))
                elif key == "timeout":
                    top_options.append(
                        (
                            key,
                            f"{label} [{APP_SETTINGS.get('ai_command_timeout_sec')}s]",
                        )
                    )
                else:
                    top_options.append((key, label))

            selected_section = _read_arrow_choice(
                "[SETTINGS] Sections",
                top_options,
                default_idx=0,
                option_help=settings_help_map,
            )
            if selected_section in {"cancel", "save"}:
                if selected_section == "save" and not save_app_settings():
                    print("ERROR: Failed to save settings.")
                return True
            if selected_section == "reset":
                APP_SETTINGS.clear()
                APP_SETTINGS.update(DEFAULT_APP_SETTINGS)
                if not save_app_settings():
                    print("ERROR: Settings reset but failed to save.")
                continue
            if selected_section == "timeout":
                raw_timeout = _read_terminal_line(
                    "[SETTINGS] timeout (3-120)> "
                ).strip()
                if not raw_timeout or raw_timeout == "\x1b":
                    continue
                timeout_val = _parse_timeout_value(raw_timeout)
                if timeout_val is None:
                    print("ERROR: timeout must be integer from 3 to 120.")
                    continue
                APP_SETTINGS["ai_command_timeout_sec"] = timeout_val
                if not save_app_settings():
                    print("ERROR: Failed to save settings.")
                continue

            keys = section_items.get(selected_section, [])
            if not keys:
                continue

            while True:
                section_options: List[Tuple[str, str]] = []
                for key in keys:
                    state = (
                        "on" if _bool_from_any(APP_SETTINGS.get(key), False) else "off"
                    )
                    section_options.append((key, f"{labels.get(key, key)} [{state}]"))
                section_options.append(("back", "Back"))

                selected_key = _read_arrow_choice(
                    f"[SETTINGS] {selected_section.title()}",
                    section_options,
                    default_idx=0,
                    option_help=settings_help_map,
                )
                if selected_key == "back":
                    break

                current_on = _bool_from_any(APP_SETTINGS.get(selected_key), False)
                set_choice = _read_arrow_choice(
                    f"[SETTINGS] {labels.get(selected_key, selected_key)}",
                    [("on", "On"), ("off", "Off"), ("cancel", "Cancel")],
                    default_idx=(0 if current_on else 1),
                )
                if set_choice == "cancel":
                    continue
                APP_SETTINGS[selected_key] = set_choice == "on"
                if not save_app_settings():
                    print("ERROR: Failed to save settings.")
        return True

    sub = parts[1].lower()
    if sub in {"help", "h", "?"}:
        _print_settings_help()
        return True
    if sub == "reset":
        APP_SETTINGS.clear()
        APP_SETTINGS.update(DEFAULT_APP_SETTINGS)
        ok = save_app_settings()
        if not ok:
            print("ERROR: Settings reset but failed to save.")
        return True
    if sub == "save":
        ok = save_app_settings()
        if not ok:
            print("ERROR: Failed to save settings.")
        return ok
    if sub == "timeout":
        if len(parts) < 3:
            print(f"Current timeout: {APP_SETTINGS.get('ai_command_timeout_sec')}s")
            return True
        timeout_val = _parse_timeout_value(parts[2])
        if timeout_val is None:
            print("ERROR: timeout must be integer from 3 to 120.")
            return False
        APP_SETTINGS["ai_command_timeout_sec"] = timeout_val
        if not save_app_settings():
            print("ERROR: Failed to save settings.")
        return True
    if sub == "set":
        if len(parts) < 4:
            _print_settings_help()
            return False
        key = parts[2].strip().lower()
        if key not in DEFAULT_APP_SETTINGS:
            print(f"ERROR: Unknown setting key '{key}'")
            return False
        if key == "ai_command_timeout_sec":
            print("Use: /settings timeout <seconds>")
            return False
        toggle_val = _parse_toggle_value(parts[3])
        if toggle_val is None:
            print("ERROR: Value must be on/off")
            return False
        APP_SETTINGS[key] = bool(toggle_val)
        if not save_app_settings():
            print("ERROR: Failed to save settings.")
        return True

    _print_settings_help()
    return False


def _extract_ai_commands(text: str) -> Tuple[List[str], str]:
    commands: List[str] = []

    def _collect(match: re.Match) -> str:
        cmd_text = (match.group(1) or "").strip()
        if cmd_text:
            commands.append(cmd_text)
        return ""

    cleaned = CMD_CALL_RE.sub(_collect, text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return commands, cleaned.strip()


def _execute_ai_commands(commands: List[str]) -> List[Dict[str, str]]:
    if not commands:
        return []
    if not _bool_from_any(APP_SETTINGS.get("allow_ai_commands"), False):
        print("[AI CMD] Ignored (allow_ai_commands=off).")
        return []

    timeout_sec = int(APP_SETTINGS.get("ai_command_timeout_sec", 25))
    verbose_output = _bool_from_any(os.environ.get("CMDAI_AI_CMD_VERBOSE", ""), False)
    results: List[Dict[str, str]] = []
    current_cwd = os.getcwd()
    for idx, command in enumerate(commands, 1):
        cmd = command.strip()
        if not cmd:
            continue

        if re.match(r"(?i)^[a-z]:[\\\\/]", cmd) and re.search(r"(?i)\\bcd\\s+", cmd):
            try:
                tail = re.search(r"(?is)\\b(cd|chdir)\\s+.+$", cmd)
                if tail:
                    cmd = tail.group(0).strip()
            except Exception:
                pass

        cd_match = re.match(r"(?is)^(?:cd|chdir)\s+(.+)$", cmd.strip())
        if cd_match:
            raw_target = cd_match.group(1).strip().strip("\"'")
            target = raw_target
            if not os.path.isabs(target):
                target = os.path.abspath(os.path.join(current_cwd, target))
            if os.path.isdir(target):
                current_cwd = target
                results.append({"command": cmd, "status": "cwd", "output": target})
                _ui_set_note(f"[AI CMD] cwd -> {target}", ttl_sec=3.0)
                continue
            results.append({"command": cmd, "status": "error", "output": f"dir not found: {target}"})
            _ui_set_note("AI command failed (invalid directory).", ttl_sec=4.0)
            continue
        if _bool_from_any(APP_SETTINGS.get("confirm_ai_commands"), True):
            action = _read_arrow_choice(
                f"[AI CMD {idx}/{len(commands)}]",
                [("accept", "Run"), ("skip", "Skip"), ("cancel", "Cancel all")],
                default_idx=1,
            )
            if action == "cancel":
                break
            if action != "accept":
                continue
        print(f"[AI CMD] {cmd}")
        try:
            completed = subprocess.run(
                cmd,
                cwd=current_cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                encoding="utf-8",
                errors="replace",
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            output = output.strip()
            if len(output) > 2000:
                output = output[:2000] + "\n... [truncated]"
            status = f"exit={completed.returncode}"
        except subprocess.TimeoutExpired:
            output = f"Timeout after {timeout_sec}s"
            status = "timeout"
        except Exception as exc:

            output = "Execution failed."
            status = "error"
            _ui_set_note("AI command failed to execute.", ttl_sec=4.0)
        print(f"[AI CMD] {status}")
        if verbose_output and output:
            print(output)
        results.append({"command": cmd, "status": status, "output": output})
    return results


def _build_chat_prompt(
    user_text: str, history: Optional[List[Dict[str, str]]] = None
) -> str:
    lines = []
    lines.append("<|system|>")
    lines.append("You are a helpful AI assistant. Respond clearly and concisely.")
    if _bool_from_any(APP_SETTINGS.get("allow_ai_commands"), False):
        lines.append(
            "If command execution is necessary, emit exactly: [[CALL:CMD]]your command[[/CALL]]"
        )
        lines.append("Keep commands short and safe. Do not use multiline scripts.")
    lines.append("<|end|>")

    if history:
        for msg in history[-10:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                lines.append("<|user|>")
                lines.append(content)
                lines.append("<|end|>")
            elif role == "assistant":
                lines.append("<|assistant|>")
                lines.append(content)
                lines.append("<|end|>")

    lines.append("<|user|>")
    lines.append(user_text)
    lines.append("<|end|>")
    lines.append("<|assistant|>")
    return "\n".join(lines)


def _build_plan_prompt(
    user_text: str, history: Optional[List[Dict[str, str]]] = None
) -> str:
    lines = []
    lines.append("<|system|>")
    lines.append(
        "You are a project planning expert. Produce a detailed technical plan in Markdown."
    )
    lines.append("")
    lines.append("")
    lines.append(f"Your response will be saved directly to {PLAN_FILENAME}.")
    lines.append("Do not generate source code. Return plan content only.")
    lines.append("Write concrete sections and subsections, step by step.")
    lines.append(
        "Required sections: Goal, Scope, Assumptions, Implementation plan step by step, Risks, Tests, Next steps."
    )
    lines.append("The implementation plan must include at least 10 numbered steps.")
    lines.append(
        "Each step must include: what to do, where (file/module), and expected result."
    )
    lines.append(
        "FORBIDDEN: ASCII/Unicode diagrams, graphs, box drawings, line-art tables."
    )
    lines.append(
        "Do not return blocks like +---, | ... |, ┌─, └─, architecture arrows, or diagram-only output."
    )
    lines.append("Return only plan content ready to be written to a .md file.")
    if ide_integration and ide_integration.active_ide:
        lines.append(f"Active IDE: {ide_integration.active_ide.get('name', 'unknown')}")
    if ide_integration and ide_integration.project_root:
        lines.append(f"Project root: {ide_integration.project_root}")

    if code_file_manager:
        project_index = code_file_manager.load_project_file_index(
            max_files=120, max_chars=4500
        )
        if project_index:
            lines.append("Current project files (context):")
            lines.append(project_index)
    lines.append("<|end|>")

    if history:
        for msg in history[-10:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                lines.append("<|user|>")
                lines.append(content)
                lines.append("<|end|>")
            elif role == "assistant":
                lines.append("<|assistant|>")
                lines.append(content)
                lines.append("<|end|>")

    lines.append("<|user|>")
    lines.append(f"[PLAN MODE] {user_text}")
    lines.append("<|end|>")
    lines.append("<|assistant|>")
    return "\n".join(lines)


def _build_code_prompt(
    user_text: str, history: Optional[List[Dict[str, str]]] = None
) -> str:
    lines = []
    lines.append("<|system|>")
    lines.append(
        "You are an experienced software engineer. Return complete, ready-to-write file contents."
    )
    lines.append("")
    lines.append("")
    lines.append("Use Markdown code fences only.")
    lines.append(
        "Before EVERY code block, provide a line: File: relative/path/to/file.ext"
    )
    if _bool_from_any(APP_SETTINGS.get("prefer_fragment_edits"), True):
        lines.append(
            "Prefer focused fragment edits for existing files. Use ```patch blocks with:"
        )
        lines.append("<<<<<<< SEARCH")
        lines.append("existing text")
        lines.append("=======")
        lines.append("new text")
        lines.append(">>>>>>> REPLACE")
        lines.append("or use anchor inserts:")
        lines.append("<<<<<<< AFTER")
        lines.append("anchor text")
        lines.append("=======")
        lines.append("inserted text")
        lines.append(">>>>>>> INSERT")
        lines.append("<<<<<<< BEFORE")
        lines.append("anchor text")
        lines.append("=======")
        lines.append("inserted text")
        lines.append(">>>>>>> INSERT")
        lines.append("Use full file content only for new files or larger rewrites.")
        if _bool_from_any(APP_SETTINGS.get("require_fragment_edits"), False):
            lines.append(
                "IMPORTANT: For existing files you MUST use ```patch blocks. Do NOT output full file contents for existing files."
            )
    else:
        lines.append("If you update a file, return its full new content.")
    lines.append("Do not add long explanations outside file paths and code.")
    lines.append("Do not create code blocks without an explicit File: path.")
    lines.append("Add only necessary technical comments.")
    lines.append(f"Do NOT modify {PLAN_FILENAME}.")
    if _bool_from_any(APP_SETTINGS.get("code_mode_without_plan"), True):
        lines.append(
            "CODE mode can run without any plan file. Implement directly from the user request when needed."
        )
    if _bool_from_any(APP_SETTINGS.get("allow_ai_commands"), False):
        lines.append(
            "If command execution is required, emit exactly: [[CALL:CMD]]your command[[/CALL]]"
        )
        lines.append("Do not emit command tags unless truly needed.")
    project_root = _current_project_root_path(force_refresh=False)
    if ide_integration and ide_integration.active_ide:
        lines.append(f"Active IDE: {ide_integration.active_ide.get('name', 'unknown')}")
    if project_root:
        lines.append(f"Project root: {project_root}")
    if _request_needs_frontend_assets(user_text, project_root):
        lines.append(
            "This request includes frontend/UI work. Create the required HTML/CSS/JS/TS/TSX files when needed."
        )
        lines.append(
            "If backend files are also needed, return BOTH backend and frontend file blocks in the same answer."
        )
        lines.append(
            "Frontend files are allowed even when the backend/runtime is Python."
        )
    if _request_needs_backend_assets(user_text, project_root):
        lines.append(
            "This request includes backend/server work. Create the required API/server/database/auth files when needed."
        )
        lines.append(
            "Do not omit backend files just because README/frontend files are also useful."
        )

    try:
        plan_root = project_root or os.getcwd()
        plan_path = os.path.join(plan_root, PLAN_FILENAME)
        if os.path.exists(plan_path):
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_text = (f.read() or "").strip()
            if plan_text:

                lines.append(f"{PLAN_FILENAME} (read-only context):")
                lines.append(plan_text[:6000])
    except Exception:
        pass
    selected_file = _current_ide_file_path()
    if selected_file:
        try:
            selected_path = os.path.relpath(
                selected_file, project_root or os.getcwd()
            ).replace("\\", "/")
        except Exception:
            selected_path = selected_file
        lines.append(f"Open file in IDE: {selected_path}")
        if _bool_from_any(APP_SETTINGS.get("restrict_writes_to_open_file"), True) and _has_explicit_selected_file():
            lines.append(f"You may modify ONLY this file: {selected_path}")
            lines.append(
                "Do NOT create any other files. Edit the contents of this file only."
            )
        else:
            lines.append("You may create or modify project files under the active project root.")
    else:
        lines.append(
            "No specific file selected. You may create or modify project files."
        )
        lines.append(
            f"Write all files relative to project root: {project_root or os.getcwd()}"
        )

    if code_file_manager:
        md_context = code_file_manager.load_markdown_context()
        if md_context:
            lines.append("Follow these project .md files:")
            lines.append(md_context)
        project_index = code_file_manager.load_project_file_index(
            max_files=160, max_chars=6000
        )
        if project_index:
            lines.append("Current project files (context):")
            lines.append(project_index)

    lines.append("<|end|>")

    if history:
        for msg in history[-10:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                lines.append(f"<|user|>")
                lines.append(content)
                lines.append("<|end|>")
            elif role == "assistant":
                lines.append(f"<|assistant|>")
                lines.append(content)
                lines.append("<|end|>")

    lines.append("<|user|>")
    lines.append(f"[CODE MODE] {user_text}")
    lines.append("<|end|>")
    lines.append("<|assistant|>")

    return "\n".join(lines)


def _build_debug_prompt(
    user_text: str, history: Optional[List[Dict[str, str]]] = None
) -> str:
    lines = []
    lines.append("<|system|>")
    lines.append(
        "You are a senior debugging engineer. Diagnose the bug, verify the likely cause, and apply the smallest reliable fix."
    )
    lines.append("")
    lines.append("Return short factual output.")
    lines.append("When file changes are needed, use Markdown code fences only.")
    lines.append(
        "Before EVERY code block, provide a line: File: relative/path/to/file.ext"
    )
    if _bool_from_any(APP_SETTINGS.get("prefer_fragment_edits"), True):
        lines.append(
            "Prefer focused fragment edits for existing files. Use ```patch blocks with:"
        )
        lines.append("<<<<<<< SEARCH")
        lines.append("existing text")
        lines.append("=======")
        lines.append("new text")
        lines.append(">>>>>>> REPLACE")
        lines.append("or use anchor inserts:")
        lines.append("<<<<<<< AFTER")
        lines.append("anchor text")
        lines.append("=======")
        lines.append("inserted text")
        lines.append(">>>>>>> INSERT")
        lines.append("<<<<<<< BEFORE")
        lines.append("anchor text")
        lines.append("=======")
        lines.append("inserted text")
        lines.append(">>>>>>> INSERT")
        lines.append("Use full file content only for new files or larger rewrites.")
        if _bool_from_any(APP_SETTINGS.get("require_fragment_edits"), False):
            lines.append(
                "IMPORTANT: For existing files you MUST use ```patch blocks. Do NOT output full file contents for existing files."
            )
    else:
        lines.append("If you update a file, return its full new content.")
    lines.append("Prefer focused fixes over rewrites.")
    lines.append(
        "If no file changes are needed, return a short diagnosis only (max 8 lines)."
    )
    lines.append("Do not create code blocks without an explicit File: path.")
    if _bool_from_any(APP_SETTINGS.get("allow_ai_commands"), False):
        lines.append(
            "If command execution is required, emit exactly: [[CALL:CMD]]your command[[/CALL]]"
        )
        lines.append("Do not emit command tags unless truly needed.")
    project_root = _current_project_root_path(force_refresh=False)
    if ide_integration and ide_integration.active_ide:
        lines.append(f"Active IDE: {ide_integration.active_ide.get('name', 'unknown')}")
    if project_root:
        lines.append(f"Project root: {project_root}")
    if _request_needs_frontend_assets(user_text, project_root):
        lines.append(
            "This bug/task includes frontend/UI files. Do not omit HTML/CSS/JS/TS/TSX fixes just because backend files are present."
        )
        lines.append(
            "Return all required frontend and backend file blocks together when both sides are affected."
        )
    selected_file = _current_ide_file_path()
    if selected_file:
        try:
            selected_path = os.path.relpath(
                selected_file, project_root or os.getcwd()
            ).replace("\\", "/")
        except Exception:
            selected_path = selected_file
        lines.append(f"Open file in IDE: {selected_path}")
        if _bool_from_any(APP_SETTINGS.get("restrict_writes_to_open_file"), True) and _has_explicit_selected_file():
            lines.append(f"You may modify ONLY this file: {selected_path}")
            lines.append(
                "Do NOT create any other files. Edit the contents of this file only."
            )
        else:
            lines.append("You may create or modify project files under the active project root.")
    else:
        lines.append("No specific file is open in IDE.")
        lines.append(
            f"You may create or modify any project files under: {project_root or os.getcwd()}"
        )
        lines.append("Use relative paths for all File: headers.")
    if code_file_manager:
        md_context = code_file_manager.load_markdown_context()
        if md_context:
            lines.append("Follow these project .md files:")
            lines.append(md_context)
        project_index = code_file_manager.load_project_file_index(
            max_files=160, max_chars=6000
        )
        if project_index:
            lines.append("Current project files (context):")
            lines.append(project_index)
    lines.append("<|end|>")

    if history:
        for msg in history[-10:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                lines.append("<|user|>")
                lines.append(content)
                lines.append("<|end|>")
            elif role == "assistant":
                lines.append("<|assistant|>")
                lines.append(content)
                lines.append("<|end|>")

    lines.append("<|user|>")
    lines.append(f"[DEBUG MODE] {user_text}")
    lines.append("<|end|>")
    lines.append("<|assistant|>")
    return "\n".join(lines)


def _build_analyst_prompt(
    user_text: str, history: Optional[List[Dict[str, str]]] = None
) -> str:
    lines = []
    lines.append("<|system|>")
    lines.append(
        "You are a senior code analyst. Analyze the project using the provided workspace context and return concise, high-signal findings."
    )
    lines.append("")
    lines.append("Return Markdown only.")
    lines.append("Keep terminal output short and actionable.")
    lines.append(
        f"Your final answer should be suitable for saving into {ANALYST_TEST_FILENAME}."
    )
    lines.append(
        "Base every finding on the provided workspace evidence or mark it as a static inference."
    )
    lines.append(
        "If you create supporting docs or report artifacts, append them as File: blocks after the Markdown report body."
    )
    lines.append("Start the report with '# Summary'.")
    lines.append("Required sections: Summary, Top Issues, Recommendations, Next Steps.")
    lines.append(
        "When evidence is weak, say it is a static inference instead of claiming certainty."
    )
    lines.append(
        "Do not include JSON, tool calls, command objects, shell transcripts, or chain-of-thought."
    )
    if _bool_from_any(APP_SETTINGS.get("allow_ai_commands"), False):
        lines.append(
            "If command execution is required, emit exactly: [[CALL:CMD]]your command[[/CALL]]"
        )
        lines.append("Do not emit command tags unless truly needed.")
    project_root = _current_project_root_path(force_refresh=False)
    if ide_integration and ide_integration.active_ide:
        lines.append(f"Active IDE: {ide_integration.active_ide.get('name', 'unknown')}")
    if project_root:
        lines.append(f"Project root: {project_root}")
    selected_file = _current_ide_file_path()
    if selected_file:
        try:
            rel_selected = os.path.relpath(
                selected_file, project_root or os.getcwd()
            ).replace("\\", "/")
        except Exception:
            rel_selected = selected_file
        lines.append(f"Open file in IDE: {rel_selected}")
    static_report = analyst_engine.run_full_analysis()
    report_context = _format_analyst_report_context(static_report)
    if report_context:
        lines.append("Static analysis baseline:")
        lines.append(report_context)
    if code_file_manager:
        md_context = code_file_manager.load_markdown_context()
        if md_context:
            lines.append("Follow these project .md files:")
            lines.append(md_context)
        project_index = code_file_manager.load_project_file_index(
            max_files=160, max_chars=6000
        )
        if project_index:
            lines.append("Current project files (context):")
            lines.append(project_index)
    lines.append("<|end|>")

    if history:
        for msg in history[-10:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                lines.append("<|user|>")
                lines.append(content)
                lines.append("<|end|>")
            elif role == "assistant":
                lines.append("<|assistant|>")
                lines.append(content)
                lines.append("<|end|>")

    lines.append("<|user|>")
    lines.append(f"[ANALYST MODE] {user_text}")
    lines.append("<|end|>")
    lines.append("<|assistant|>")
    return "\n".join(lines)


def _get_mode_prompt(
    user_text: str, history: Optional[List[Dict[str, str]]] = None
) -> str:
    if CURRENT_MODE == AppMode.PLAN:
        return _build_plan_prompt(user_text, history)
    elif CURRENT_MODE == AppMode.CODE:
        return _build_code_prompt(user_text, history)
    elif CURRENT_MODE == AppMode.DEBUG:
        return _build_debug_prompt(user_text, history)
    elif CURRENT_MODE == AppMode.ANALYST:
        return _build_analyst_prompt(user_text, history)
    else:
        return _build_chat_prompt(user_text, history)


def _run_with_spinner(
    func,
    desc: str = "",
    allow_cancel: bool = False,
    cancel_event: Optional[threading.Event] = None,
    show_progress: bool = True,
):
    outcome: Dict[str, Any] = {"value": None, "error": None}
    finished = threading.Event()
    cancel_requested = False

    def _target():
        try:
            outcome["value"] = func()
        except Exception as exc:
            outcome["error"] = exc
        finally:
            finished.set()

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()

    frames = ["|", "/", "-", "\\"]
    idx = 0
    started = time.time()
    last_len = 0
    show_spinner = bool(show_progress)
    pinned_input = _should_pin_input_top()
    msvcrt_mod = None
    if allow_cancel and os.name == "nt":
        try:
            import msvcrt as _msvcrt

            msvcrt_mod = _msvcrt
        except Exception:
            msvcrt_mod = None

    while not finished.wait(0.1):
        if allow_cancel and cancel_event is not None and msvcrt_mod is not None:
            try:
                while msvcrt_mod.kbhit():
                    key = msvcrt_mod.getwch()
                    if key in ("\x00", "\xe0"):
                        try:
                            _ = msvcrt_mod.getwch()
                        except Exception:
                            pass
                        continue
                    if key == "\x1b":
                        cancel_event.set()
                        cancel_requested = True
            except Exception:
                pass

        if show_spinner:
            elapsed = max(0.0, time.time() - started)
            note = _ui_get_note()
            if desc:
                plain_line = f"{_format_elapsed_label(elapsed)} {desc}"
            else:
                plain_line = f"{_format_elapsed_label(elapsed)}"
            if note:
                plain_line = (
                    f"{plain_line} {Colors.ACTION_STATUS}{note}{Colors.ENDC}"
                )
            plain_line = f"{plain_line} {frames[idx % len(frames)]}"
            line = plain_line
            if pinned_input:
                sys.stdout.write(f"\x1b[{_log_output_row()};1H\x1b[2K{line}")
            else:
                visible_len = len(_strip_ansi(line))
                padding = " " * max(0, last_len - visible_len)
                sys.stdout.write(f"\r{line}{padding}")
            sys.stdout.flush()
            last_len = len(_strip_ansi(plain_line))
        idx += 1

        if cancel_requested:
            finished.wait(0.15)
            break

    if show_spinner:
        if pinned_input:
            sys.stdout.write(f"\x1b[{_log_output_row()};1H\x1b[2K")
        else:
            sys.stdout.write("\r" + " " * last_len + "\r")
    sys.stdout.flush()

    if cancel_requested and not finished.is_set():
        return outcome["value"], time.time() - started

    worker.join(timeout=0.2)
    if outcome["error"] is not None:
        raise outcome["error"]
    return outcome["value"], time.time() - started


def _get_mode_status_hints(mode: str, chunk_count: int) -> str:
    restrict_to_selected = _bool_from_any(
        APP_SETTINGS.get("restrict_writes_to_open_file"), True
    )
    if mode == AppMode.CHAT:
        if chunk_count < 5:
            return "thinking"
        return ""
    elif mode == AppMode.CODE:
        if chunk_count < 5:
            return "thinking"
        elif chunk_count < 30:
            return "writing selected file" if restrict_to_selected else "writing"
        else:
            return "writing selected file" if restrict_to_selected else "creating files"
    elif mode == AppMode.DEBUG:
        if chunk_count < 5:
            return "analyzing"
        elif chunk_count < 20:
            return "diagnosing"
        else:
            return "writing fix"
    elif mode == AppMode.PLAN:
        if chunk_count < 5:
            return "thinking"
        elif chunk_count < 30:
            return "planning"
        else:
            return "writing steps"
    elif mode == AppMode.ANALYST:
        if chunk_count < 5:
            return "scanning"
        elif chunk_count < 20:
            return "analyzing"
        else:
            return "writing report"
    return "working"


def _generate_with_live_status(
    loader,
    full_prompt: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    cancel_event: threading.Event,
    show_progress: bool = True,
    started_at: Optional[float] = None,
    on_text_update: Optional[Callable[[str], None]] = None,
) -> Tuple[str, float]:
    started = float(started_at) if started_at is not None else time.time()
    if started <= 0:
        started = time.time()
    chunks: List[str] = []
    current_status = ""
    accumulated_text = ""
    last_callback_len = 0
    last_callback_fence_count = 0
    frames = ["|", "/", "-", "\\"]
    idx = 0
    last_len = 0

    pinned_input = _should_pin_input_top()

    status_pattern = re.compile(r"\[\[STATUS\]\](.*?)\[\[/STATUS\]\]", re.DOTALL)

    spinner_running = threading.Event()
    spinner_running.set()

    def _clear_progress_line() -> None:
        nonlocal last_len
        if pinned_input:
            sys.stdout.write(f"\x1b[{_log_output_row()};1H\x1b[2K")
        else:

            sys.stdout.write("\r" + " " * max(last_len, 80) + "\r")
        sys.stdout.flush()
        last_len = 0

    def _write_progress_line(plain_line: str) -> None:
        nonlocal last_len
        visible_len = len(_strip_ansi(plain_line))
        if pinned_input:
            sys.stdout.write(f"\x1b[{_log_output_row()};1H\x1b[2K{plain_line}")
        else:
            padding = " " * max(0, last_len - visible_len)
            sys.stdout.write(f"\r{plain_line}{padding}")
        sys.stdout.flush()
        last_len = visible_len

    def update_spinner():
        local_idx = 0
        while spinner_running.is_set():

            if show_progress and len(chunks) < 2:
                elapsed = max(0.0, time.time() - started)
                note = _ui_get_note()

                if not note:
                    if CURRENT_MODE == AppMode.CHAT:
                        note = "thinking"
                    elif CURRENT_MODE == AppMode.CODE:
                        note = "thinking"
                    elif CURRENT_MODE == AppMode.DEBUG:
                        note = "analyzing"
                    elif CURRENT_MODE == AppMode.PLAN:
                        note = "planning"
                    elif CURRENT_MODE == AppMode.ANALYST:
                        note = "scanning"
                plain_line = (
                    f"{_format_elapsed_label(elapsed)} "
                    f"{Colors.DIM}{note}{Colors.ENDC} "
                    f"{frames[local_idx % len(frames)]}"
                )
                _write_progress_line(plain_line)
                local_idx += 1
            time.sleep(0.1)

    if show_progress:
        spinner_thread = threading.Thread(target=update_spinner, daemon=True)
        spinner_thread.start()

    try:
        token_stream = loader.generate(
            prompt=full_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stream=True,
            cancel_event=cancel_event,
        )

        for token in token_stream:
            if chunks and spinner_running.is_set():
                spinner_running.clear()

            if cancel_event.is_set():
                break

            chunks.append(token)
            accumulated_text += token

            status_match = status_pattern.search(accumulated_text)
            if status_match:
                new_status = status_match.group(1).strip()
                current_status = _normalize_status_label(new_status)

                accumulated_text = status_pattern.sub("", accumulated_text, count=1)

            if on_text_update and accumulated_text:
                fence_count = accumulated_text.count("```")
                should_callback = False
                if fence_count > last_callback_fence_count:
                    should_callback = True
                elif (len(accumulated_text) - last_callback_len) >= 256:
                    should_callback = True
                if should_callback:
                    try:
                        on_text_update(accumulated_text)
                    except Exception:
                        pass
                    last_callback_len = len(accumulated_text)
                    last_callback_fence_count = fence_count

            if show_progress:
                elapsed = max(0.0, time.time() - started)
                note = _ui_get_note()
                chunk_count = len(chunks)


                display_status = current_status
                if not display_status:
                    if chunk_count < 8:
                        display_status = "thinking"
                    elif CURRENT_MODE == AppMode.CODE:
                        display_status = "writing" if chunk_count < 60 else "creating files"
                    elif CURRENT_MODE == AppMode.DEBUG:
                        display_status = "diagnosing" if chunk_count < 30 else "writing fix"
                    elif CURRENT_MODE == AppMode.PLAN:
                        display_status = "planning" if chunk_count < 60 else "writing steps"
                    elif CURRENT_MODE == AppMode.ANALYST:
                        display_status = "analyzing" if chunk_count < 30 else "writing report"

                status_part = f"{Colors.DIM}{display_status}{Colors.ENDC} " if display_status else ""
                note_part = f"{Colors.DIM}· {note}{Colors.ENDC} " if note else ""
                plain_line = (
                    f"{_format_elapsed_label(elapsed)} "
                    f"{status_part}{note_part}"
                    f"{frames[idx % len(frames)]}"
                )
                _write_progress_line(plain_line)

            idx += 1

        spinner_running.clear()

    except Exception as e:
        spinner_running.clear()
        if show_progress:
            _clear_progress_line()
        raise e

    if show_progress:
        _clear_progress_line()

    elapsed = time.time() - started
    full_response = "".join(chunks)

    clean_response = status_pattern.sub("", full_response)
    if on_text_update and clean_response:
        try:
            on_text_update(clean_response)
        except Exception:
            pass

    return clean_response, elapsed


def _extract_and_display_actions(text: str) -> Tuple[List[str], str]:
    actions = []
    clean_text = text

    for match in ACTION_RE.finditer(text):
        action_text = match.group(1).strip()
        if action_text:
            actions.append(action_text)
            print(f"{Colors.ACTION_STATUS}→ {action_text}{Colors.ENDC}")

    clean_text = ACTION_RE.sub("", text).strip()

    return actions, clean_text


def _build_mode_result_note(
    mode: "AppMode", text: str, user_text: str, fallback: str
) -> str:
    def _shorten(value: str, limit: int = 88) -> str:
        result = (value or "").strip()
        if len(result) > limit:
            result = result[: limit - 3].rstrip() + "..."
        return result

    def _user_note() -> str:
        cleaned = (user_text or "").strip()
        cleaned = re.sub(r"^\[[A-Z ]+\]\s*", "", cleaned).strip()
        return _shorten(cleaned) if cleaned else fallback

    raw_text = (text or "").strip()
    if not raw_text:
        return _user_note()

    ignored_headers = {"summary", "overview", "result", "response", "notes"}
    for raw_line in raw_text.splitlines():
        line = (raw_line or "").strip()
        if not line:
            continue
        if line.startswith("File: "):
            continue
        if line.startswith("```"):
            continue
        if line.startswith("|_ "):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line).strip()
        line = re.sub(r"^[*\-\d\.\)\s]+", "", line).strip()
        if not line:
            continue
        if line.lower() in ignored_headers:
            continue
        if re.match(
            r"^(?:it looks like\b|the user wants\b|user wants\b|we need to\b|we should\b|i(?:'ll| will)\b|probably\b|looks like\b)",
            line,
            re.IGNORECASE,
        ):
            return _user_note()
        line = _shorten(line)
        return line

    return _user_note()


def _sanitize_analyst_markdown(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return (
            "# Summary\n\nNo analyst report content was returned.\n\n"
            "## Top Issues\n- No concrete issues extracted.\n\n"
            "## Recommendations\n- Re-run Analyst Mode with the target file open in IDE.\n\n"
            "## Next Steps\n- Review the project context and repeat the analysis.\n"
        )

    filtered_lines: List[str] = []
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if filtered_lines and filtered_lines[-1] != "":
                filtered_lines.append("")
            continue
        if stripped.startswith("[[STATUS]]") or stripped.startswith("[[/STATUS]]"):
            continue
        if stripped.startswith("[[CALL:CMD]]") or stripped.startswith("[[/CALL]]"):
            continue
        if stripped.startswith("{") and stripped.endswith("}") and '"cmd"' in stripped:
            continue
        if re.match(
            r"^(?:we need to|let's\b|i will\b|i need to\b|first,\s*i\b)",
            stripped,
            re.IGNORECASE,
        ):
            continue
        filtered_lines.append(line)

    while filtered_lines and filtered_lines[0] == "":
        filtered_lines.pop(0)
    while filtered_lines and filtered_lines[-1] == "":
        filtered_lines.pop()

    cleaned = "\n".join(filtered_lines).strip()
    if not cleaned:
        return (
            "# Summary\n\nStatic analysis completed, but the AI response contained no report body.\n\n"
            "## Top Issues\n- No concrete issues extracted.\n\n"
            "## Recommendations\n- Re-run Analyst Mode and review the selected file context.\n\n"
            "## Next Steps\n- Open the target file in IDE and retry.\n"
        )

    if re.match(r"(?im)^\s*#\s*summary\b", cleaned):
        return cleaned.rstrip() + "\n"

    if re.search(
        r"(?im)^\s*#{2,6}\s*(top issues|recommendations|next steps)\b", cleaned
    ):
        preamble: List[str] = []
        remaining: List[str] = []
        seen_heading = False
        for line in cleaned.splitlines():
            if not seen_heading and re.match(r"^\s*#", line):
                seen_heading = True
            if seen_heading:
                remaining.append(line)
            else:
                preamble.append(line)
        summary_text = "\n".join(preamble).strip() or "Static analysis completed."
        rest = "\n".join(remaining).strip()
        parts = ["# Summary", "", summary_text]
        if rest:
            parts.extend(["", rest])
        return "\n".join(parts).rstrip() + "\n"

    summary_text = cleaned.strip()
    return (
        "# Summary\n\n"
        f"{summary_text}\n\n"
        "## Top Issues\n- No concrete issue list was extracted from the AI output.\n\n"
        "## Recommendations\n- Review the summary and inspect the selected file for the most likely risk area.\n\n"
        "## Next Steps\n- Re-run Analyst Mode on a narrower file scope if you need a more detailed report.\n"
    )


def _strip_file_change_blocks(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(
        r"(?ims)^[ \t]*(?:file|plik|path|sciezka|filename)\s*:\s*(.+?)\s*\n\s*```[^\n`]*\n.*?```[ \t]*\n?",
        "",
        raw,
    ).strip()


def _stream_apply_ready_file_blocks(
    text: str,
    mode: str,
    seen_signatures: Optional[set] = None,
) -> List[Dict[str, Any]]:
    if not text or not code_file_manager:
        return []
    mode_upper = str(mode or "").strip().upper()
    if mode_upper == "CODE" and not _bool_from_any(APP_SETTINGS.get("auto_accept_code"), False):
        return []
    if mode_upper == "DEBUG" and not _should_auto_accept_debug():
        return []

    changes = code_file_manager.extract_file_changes(text)
    if not changes:
        return []

    ready: List[Dict[str, Any]] = []
    signatures = seen_signatures if seen_signatures is not None else set()
    for change in changes:
        rel_path = str((change or {}).get("relative_path", "")).strip()
        code = str((change or {}).get("code", ""))
        kind = str((change or {}).get("change_kind", "full")).strip().lower()
        if not rel_path or not code.strip():
            continue
        signature = (
            rel_path,
            kind,
            hashlib.sha1(code.encode("utf-8", errors="ignore")).hexdigest(),
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        ready.append(change)

    if not ready:
        return []
    try:
        return code_file_manager.apply_file_changes(ready)
    except Exception:
        return []


def _extract_required_go_targets(prompt_text: str) -> List[str]:
    text = str(prompt_text or "")
    match = re.search(
        r"IMPORTANT:\s*The following target files are missing and MUST be created:\s*(.*?)\nDo NOT return NO_FILE_CHANGES\.",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    targets: List[str] = []
    seen = set()
    for raw_line in str(match.group(1) or "").splitlines():
        line = str(raw_line or "").strip()
        if not line.startswith("-"):
            continue
        rel = line[1:].strip().replace("\\", "/").lstrip("./")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        targets.append(rel)
    return targets


def _print_mode_result(
    question: str,
    mode_tag: str,
    note: str,
    total_tokens: int,
    elapsed: float,
    summary_line: str,
) -> None:
    mode_match = re.match(
        r"^(?P<prefix>.*)\[(?P<label>[A-Z]+)\](?P<suffix>.*)$", mode_tag
    )
    if mode_match:
        ai_tag = (
            f"{mode_match.group('prefix')}"
            f"[{mode_match.group('label')}AI]"
            f"{mode_match.group('suffix')}"
        )
    else:
        ai_tag = "[AI]>"
    print()
    cleaned_q = (question or "").strip()
    cleaned_q = re.sub(r"^\[[A-Z ]+\]\s*", "", cleaned_q).strip()
    if cleaned_q:
        req = cleaned_q

        if req.lower().startswith("execute the implementation based on"):
            req = "/go"
        if len(req) > 120:
            req = req[:117].rstrip() + "..."
        print(f"{mode_tag} {req}")
    if note:
        print(f"{ai_tag} {note}")
    print(f"|_ tokens: {total_tokens} {_format_elapsed_label(elapsed)}")
    print(f"|_ {summary_line}")


def _merge_applied_change_lists(
    base: List[Dict[str, Any]], extra: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in list(base or []) + list(extra or []):
        path = str((item or {}).get("path", "") or "").strip()
        rel = str((item or {}).get("relative_path", "") or "").strip()
        key = os.path.normcase(os.path.abspath(path)) if path else rel
        if not key:
            continue
        if key not in merged:
            order.append(key)
            merged[key] = dict(item or {})
        else:
            merged[key].update(dict(item or {}))
    return [merged[key] for key in order]


def _summarize_applied_changes(applied: List[Dict[str, Any]]) -> str:
    created_count = 0
    updated_count = 0
    for item in applied or []:
        action = str((item or {}).get("action", "") or "").strip().lower()
        if action == "created":
            created_count += 1
        elif action == "updated":
            updated_count += 1
    if not created_count and not updated_count:
        return "create [0 files]"
    parts: List[str] = []
    if created_count:
        parts.append(
            f"create [{created_count} {'file' if created_count == 1 else 'files'}]"
        )
    if updated_count:
        parts.append(
            f"edit [{updated_count} {'file' if updated_count == 1 else 'files'}]"
        )
    return ", ".join(parts)


def send_terminal_prompt(
    prompt: str,
    max_tokens: int = -1,
    temperature: float = 0.7,
    top_p: float = 0.9,
    suppress_empty_output_error: bool = False,
) -> bool:
    global TERMINAL_CHAT_HISTORY, CURRENT_MODE, code_file_manager

    if not loader or not loader.current_model:
        print("ERROR: No model loaded. Use 'load' first.")
        return False

    text = (prompt or "").strip()
    if not text:
        return False

    try:
        requested_max_tokens = int(max_tokens) if max_tokens is not None else -1
        if requested_max_tokens == 0:
            requested_max_tokens = -1
        safe_temperature = min(max(float(temperature), 0.0), 0.7)
        safe_top_p = min(max(float(top_p), 0.1), 0.95)
        active_mode = CURRENT_MODE
        revision_feedback = ""
        retry_reason = ""
        auto_plan_retries = 0
        auto_code_retries = 0
        auto_debug_retries = 0
        auto_apply_retries = 0
        plan_body = ""
        overall_started = time.time()
        attempt_no = 0
        show_retry_notes = _bool_from_any(
            os.environ.get("CMDAI_VERBOSE_RETRY", ""), False
        ) or _bool_from_any(APP_SETTINGS.get("debug_mode_enabled"), False)
        require_fragment_edits = _bool_from_any(
            APP_SETTINGS.get("require_fragment_edits"), False
        )


        while True:
            should_continue = False
            attempt_no += 1
            if show_retry_notes and attempt_no > 1 and retry_reason:
                _ui_set_note(f"Retry {attempt_no - 1}: {retry_reason}", ttl_sec=3.5)
                retry_reason = ""
            user_input_for_model = text
            if revision_feedback:
                user_input_for_model = _build_revision_request(
                    text, revision_feedback, active_mode
                )

            full_prompt = _get_mode_prompt(user_input_for_model, TERMINAL_CHAT_HISTORY)
            cancel_event = threading.Event()

            show_progress = True
            streamed_signatures: set = set()
            streamed_applied_changes: List[Dict[str, Any]] = []
            on_text_update = None
            graceful_stop = {"done": False}
            single_file_intent = bool(
                re.search(r"(?i)\breadme\b|(?:^|\s)\.bat\b|plik\s+bat|single\s+file|jeden\s+plik", text or "")
            )
            required_go_targets = (
                _extract_required_go_targets(text)
                if active_mode == AppMode.CODE
                else []
            )
            if active_mode in {AppMode.CODE, AppMode.DEBUG} and code_file_manager:
                def _on_text_update(stream_text: str) -> None:
                    applied = _stream_apply_ready_file_blocks(
                        stream_text,
                        active_mode,
                        seen_signatures=streamed_signatures,
                    )
                    if applied:
                        streamed_applied_changes[:] = _merge_applied_change_lists(
                            streamed_applied_changes, applied
                        )
                        _ui_set_note(
                            f"Saved {len(applied)} file{'s' if len(applied) != 1 else ''} during generation.",
                            ttl_sec=1.5,
                        )
                        if _bool_from_any(
                            APP_SETTINGS.get("restrict_writes_to_open_file"), True
                        ):
                            _ui_set_note(
                                "Selected file saved. Finishing generation...",
                                ttl_sec=2.0,
                            )
                            graceful_stop["done"] = True
                            cancel_event.set()
                            return
                        if single_file_intent:
                            try:
                                current_changes = code_file_manager.extract_file_changes(
                                    stream_text
                                )
                            except Exception:
                                current_changes = []
                            rels = {
                                str((item or {}).get("relative_path", "")).strip().lower()
                                for item in current_changes
                                if str((item or {}).get("relative_path", "")).strip()
                            }
                            if len(rels) == 1:
                                only_rel = next(iter(rels))
                                base = os.path.basename(only_rel)
                                if base in {"readme.md", "readme.txt"} or base.endswith(".bat"):
                                    _ui_set_note(
                                        "Single-file task saved. Finishing generation...",
                                        ttl_sec=2.0,
                                    )
                                    graceful_stop["done"] = True
                                    cancel_event.set()
                                    return
                    if required_go_targets:
                        root_abs = code_file_manager._effective_project_root()
                        remaining = [
                            rel
                            for rel in required_go_targets
                            if not os.path.exists(os.path.join(root_abs, rel))
                        ]
                        if not remaining:
                            _ui_set_note("Required files created. Finishing generation...", ttl_sec=2.0)
                            graceful_stop["done"] = True
                            cancel_event.set()
                on_text_update = _on_text_update

            try:
                response, elapsed = _generate_with_live_status(
                    loader,
                    full_prompt,
                    requested_max_tokens,
                    safe_temperature,
                    safe_top_p,
                    cancel_event,
                    show_progress=show_progress,
                    started_at=overall_started,
                    on_text_update=on_text_update,
                )
            except KeyboardInterrupt:
                print("\nINFO: Generation interrupted.")
                return False

            if cancel_event.is_set() and not graceful_stop["done"]:
                print("INFO: Generation interrupted.")
                return False

            filtered = _extract_visible_answer(response or "")
            if not filtered:
                if streamed_applied_changes:
                    filtered = "NO_FILE_CHANGES"
                else:
                    if active_mode != AppMode.CHAT and revision_feedback == "":
                        revision_feedback = (
                            "Your previous output was empty. "
                            "Return a short response. If no changes, return exactly: NO_FILE_CHANGES."
                        )
                        retry_reason = "model returned empty output"
                        should_continue = True
                    if not suppress_empty_output_error:
                        print(
                            "ERROR: Model returned empty response. Try reloading/swapping the model or reducing prompt size."
                        )
                    return False

            if should_continue:
                continue

            cmd_calls, filtered = _extract_ai_commands(filtered)
            cmd_results = _execute_ai_commands(cmd_calls) if cmd_calls else []
            if not filtered and cmd_results and active_mode == AppMode.CHAT:
                summary = f"Executed {len(cmd_results)} command(s)."
                print(
                    f"{Colors.MODE_CHAT}[CHAT]{Colors.ENDC} {_format_elapsed_label(elapsed)}"
                )
                print(f"AI: {summary}")
                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append({"role": "assistant", "content": summary})
                return True

            prompt_tokens = loader.count_tokens(full_prompt)
            output_tokens = loader.count_tokens(filtered)
            total_tokens = int(prompt_tokens) + int(output_tokens)

            if active_mode == AppMode.CHAT:
                mode_color = Colors.MODE_CHAT
                print(
                    f"{mode_color}[CHAT]{Colors.ENDC} [tokens: {total_tokens}] {_format_elapsed_label(elapsed)}"
                )


                if cmd_results:
                    print(f"{Colors.CMDAI_GRAY}AI Commands:{Colors.ENDC}")
                    for result in cmd_results:
                        cmd = result.get('command', '')
                        status = result.get('status', '')
                        print(f"{Colors.CMDAI_GRAY}  $ {cmd} [{status}]{Colors.ENDC}")
                    print()


                if cmd_calls:
                    print(f"{Colors.CMDAI_GRAY}[Working] Executing commands...{Colors.ENDC}")

                if "\n" in filtered or len(filtered) > 140:
                    _print_text_panel("AI", filtered, tone="normal", max_lines=120)
                else:
                    print(f"AI: {filtered}")

                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append({"role": "assistant", "content": filtered})
                return True

            if active_mode == AppMode.PLAN:
                plan_body = _extract_plan_content(filtered)
                if not plan_body:
                    print("[PLAN] Empty output. Try again.")
                    return False


                try:
                    plan_root = _current_project_root_path(force_refresh=False) or os.getcwd()
                    plan_body = _append_plan_structure(plan_body, plan_root)
                except Exception:
                    pass

                if _looks_like_diagram_only_plan(plan_body):
                    if auto_plan_retries < 2:
                        auto_plan_retries += 1
                        revision_feedback = (
                            "This is not an implementation plan. Remove diagrams/graphs and return a full plan "
                            "with sections and at least 10 numbered implementation steps."
                        )
                        continue
                    print("[PLAN] Output looked like diagram, not implementation plan.")
                    return False

                if not code_file_manager:
                    print("[PLAN] ERROR: File manager unavailable.")
                    return False

                plan_path = code_file_manager.create_plan_file(plan_body)
                if not plan_path:
                    print("[PLAN] ERROR: Failed to save plan file.")
                    return False

                if _bool_from_any(APP_SETTINGS.get("auto_accept_plan"), False):
                    decision, feedback = "accept", ""
                else:
                    decision, feedback = _request_mode_approval(
                        "PLAN",
                        target_paths=_approval_target_paths("PLAN"),
                        ide_file=_current_ide_file_path(),
                    )

                if decision == "cancel":
                    try:
                        if os.path.exists(plan_path):
                            os.remove(plan_path)
                    except Exception:
                        pass
                    print("[PLAN] Cancelled.")
                    return False
                if decision == "revise":
                    try:
                        if os.path.exists(plan_path):
                            os.remove(plan_path)
                    except Exception:
                        pass
                    revision_feedback = feedback
                    continue
                plan_tag = f"{Colors.MODE_PLAN}[PLAN]{Colors.ENDC}>"
                plan_note = _build_mode_result_note(
                    AppMode.PLAN, plan_body, text, "Prepared implementation plan."
                )
                _print_mode_result(
                    text,
                    plan_tag,
                    f"{plan_note} [{os.path.abspath(plan_path)}]",
                    total_tokens,
                    elapsed,
                    "create [1 file] (next: /go)",
                )
                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append(
                    {"role": "assistant", "content": "[PLAN] create [1 file]"}
                )
                return True

            if active_mode == AppMode.CODE:


                is_go_like = bool(
                    re.match(
                        r"(?is)^\s*execute\s+the\s+implementation\s+based\s+on\b",
                        text or "",
                    )
                )
                max_code_retries = 4 if is_go_like else 2
                if not code_file_manager:
                    _ui_set_note("File manager unavailable.", ttl_sec=4.0)
                    return False

                plan_root = _current_project_root_path(force_refresh=False)
                plan_path = os.path.join(plan_root, PLAN_FILENAME)

                if not _bool_from_any(APP_SETTINGS.get("code_mode_without_plan"), True):
                    if not os.path.exists(plan_path):
                        _ui_set_note(
                            f"{PLAN_FILENAME} is required (code_mode_without_plan=off).",
                            ttl_sec=5.0,
                        )
                        return False
                    try:
                        with open(plan_path, "r", encoding="utf-8") as f:
                            if not (f.read() or "").strip():
                                _ui_set_note(
                                    f"{PLAN_FILENAME} is empty (code_mode_without_plan=off).",
                                    ttl_sec=5.0,
                                )
                                return False
                    except Exception as e:
                        _ui_set_note(
                            f"Failed to read {PLAN_FILENAME}: {e}", ttl_sec=6.0
                        )
                        return False

                code_tag_base = "[CODE]"

                if filtered.strip() in {"{", "}", "{}", ""}:
                    filtered = "NO_FILE_CHANGES"

                if filtered.strip() == "NO_FILE_CHANGES":
                    if streamed_applied_changes:
                        created_count = len(streamed_applied_changes)
                        summary_line = f"create [{created_count} {'file' if created_count == 1 else 'files'}]"
                        code_tag = f"{Colors.MODE_CODE}{code_tag_base}{Colors.ENDC}>"
                        code_note = _build_mode_result_note(
                            AppMode.CODE,
                            filtered,
                            text,
                            "Applied streamed code changes.",
                        )
                        _print_mode_result(
                            text,
                            code_tag,
                            code_note,
                            total_tokens,
                            elapsed,
                            summary_line,
                        )
                        TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                        TERMINAL_CHAT_HISTORY.append(
                            {"role": "assistant", "content": f"[CODE] {summary_line}"}
                        )
                        return True
                    code_tag = f"{Colors.MODE_CODE}{code_tag_base}{Colors.ENDC}>"
                    code_note = _build_mode_result_note(
                        AppMode.CODE, filtered, text, "No file changes were needed."
                    )
                    _print_mode_result(
                        text,
                        code_tag,
                        code_note,
                        total_tokens,
                        elapsed,
                        "create [0 files]",
                    )
                    TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                    TERMINAL_CHAT_HISTORY.append(
                        {"role": "assistant", "content": f"[CODE] create [0 files]"}
                    )
                    return True

                proposed = code_file_manager.extract_file_changes(filtered)
                should_continue = False
                if not proposed:
                    if streamed_applied_changes:
                        created_count = len(streamed_applied_changes)
                        summary_line = f"create [{created_count} {'file' if created_count == 1 else 'files'}]"
                        code_tag = f"{Colors.MODE_CODE}{code_tag_base}{Colors.ENDC}>"
                        code_note = _build_mode_result_note(
                            AppMode.CODE,
                            filtered,
                            text,
                            "Applied streamed code changes.",
                        )
                        _print_mode_result(
                            text,
                            code_tag,
                            code_note,
                            total_tokens,
                            elapsed,
                            summary_line,
                        )
                        TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                        TERMINAL_CHAT_HISTORY.append(
                            {"role": "assistant", "content": f"[CODE] {summary_line}"}
                        )
                        return True
                    if auto_code_retries < max_code_retries:
                        auto_code_retries += 1
                        revision_feedback = (
                            "Your previous output did not contain any valid file blocks.\n"
                            "You MUST return code changes using Markdown code fences, and before EACH code block you MUST provide a line:\n"
                            "File: relative/path/to/file.ext\n\n"
                            "Then output the code block for that file. Do not output prose-only answers."
                        )
                        retry_reason = "missing File: blocks"
                        should_continue = True
                    else:
                        _ui_set_note("CODE: missing File: blocks in model output.", ttl_sec=5.0)
                        code_tag = f"{Colors.MODE_CODE}{code_tag_base}{Colors.ENDC}>"
                        code_note = _build_mode_result_note(
                            AppMode.CODE,
                            filtered,
                            text,
                            "No valid file blocks detected (missing 'File:' headers).",
                        )
                        _print_mode_result(
                            text,
                            code_tag,
                            code_note,
                            total_tokens,
                            elapsed,
                            "create [0 files]",
                        )
                        TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                        TERMINAL_CHAT_HISTORY.append(
                            {
                                "role": "assistant",
                                "content": "[CODE] create [0 files] (invalid output: missing File: blocks)",
                            }
                        )
                        return False

                if should_continue:
                    continue
                if auto_code_retries < 2 and not proposed:
                    auto_code_retries += 1
                    revision_feedback = (
                        "You returned HTML or prose without File: blocks. "
                        "You MUST respond with File: lines and code fences only. "
                        "If you cannot produce file changes, return exactly: NO_FILE_CHANGES."
                    )
                    retry_reason = "prose/HTML without File: blocks"
                    should_continue = True

                if require_fragment_edits:
                    root_abs = code_file_manager._effective_project_root()
                    violations: List[str] = []
                    for change in proposed:
                        try:
                            rel = str((change or {}).get("relative_path", "")).strip()
                            kind = str((change or {}).get("change_kind", "full")).lower()
                            if not rel or kind == "patch":
                                continue
                            target = os.path.abspath(os.path.join(root_abs, rel))
                            if os.path.exists(target):
                                violations.append(rel)
                        except Exception:
                            continue
                    if violations:
                        violations = sorted(set(violations))[:10]
                        revision_feedback = (
                            "You must NOT overwrite existing files with full content.\n"
                            "Return ONLY fragment edits using ```patch blocks for these existing files:\n- "
                            + "\n- ".join(violations)
                            + "\n\nFor new files, you may return full content.\n"
                            "Reminder: each patch must be preceded by: File: relative/path"
                        )
                        retry_reason = "full file rewrite blocked (require_fragment_edits=on)"
                        should_continue = True

                if should_continue:
                    continue

                accepted_changes: List[Dict[str, Any]] = []
                total_files = len(proposed)
                if _bool_from_any(APP_SETTINGS.get("auto_accept_code"), False):
                    accepted_changes = list(proposed)
                else:
                    decision, feedback = _request_mode_approval(
                        "CODE",
                        allow_debug=True,
                        target_paths=_approval_target_paths("CODE", proposed),
                        ide_file=_current_ide_file_path(),
                    )
                    if decision == "cancel":
                        print("[CODE] Cancelled. No files saved.")
                        return False
                    if decision == "debug":
                        active_mode = AppMode.DEBUG
                        CURRENT_MODE = AppMode.DEBUG
                        revision_feedback = (
                            "Analyze the current code proposal carefully. "
                            "If something does not fit, debug it and return a safer corrected result."
                        )
                        continue
                    if decision == "revise":
                        revision_feedback = feedback
                        continue
                    accepted_changes = list(proposed)

                if not accepted_changes:
                    print("[CODE] No files accepted.")
                    return False

                quality_decision, quality_feedback = _run_quality_gate(
                    accepted_changes, "CODE"
                )
                if quality_decision == "cancel":
                    print("[CODE] Cancelled by quality gate.")
                    return False
                if quality_decision == "revise":
                    revision_feedback = quality_feedback
                    retry_reason = "quality gate requested fixes"
                    continue


                _ui_set_note("Applying file changes...", ttl_sec=2.0)
                applied, _apply_elapsed = _run_with_spinner(
                    lambda: code_file_manager.apply_file_changes(accepted_changes),
                    desc="Applying changes",
                    show_progress=True,
                )
                applied = _merge_applied_change_lists(streamed_applied_changes, applied)
                if not applied:
                    warnings = list(
                        getattr(code_file_manager, "last_apply_warnings", []) or []
                    )
                    if auto_apply_retries < 2:
                        auto_apply_retries += 1
                        retry_reason = "apply produced no writes"
                        if require_fragment_edits:
                            revision_feedback = (
                                "Your previous output could not be applied (no files were written). "
                                "Return ONLY ```patch blocks for existing files. "
                                "Use the CMDAI patch format with a longer, unique SEARCH anchor copied exactly from the current file, then REPLACE/INSERT. "
                                f"Do NOT modify {PLAN_FILENAME}. "
                                "Reminder: each patch block must be preceded by: File: relative/path"
                            )
                        else:
                            revision_feedback = (
                                "Your previous output could not be applied (no files were written). "
                                "Return full file contents for the intended files (avoid ```patch unless anchors match). "
                                f"Do NOT modify {PLAN_FILENAME}. "
                                "Reminder: each code block must be preceded by: File: relative/path"
                            )
                        if warnings:
                            _ui_set_note(warnings[-1], ttl_sec=4.0)
                        continue
                    _ui_set_note("No files were written.", ttl_sec=4.0)
                    return False

                summary_line = _summarize_applied_changes(applied)
                code_tag = f"{Colors.MODE_CODE}{code_tag_base}{Colors.ENDC}>"
                code_note = _build_mode_result_note(
                    AppMode.CODE, filtered, text, "Prepared code changes."
                )
                _print_mode_result(
                    text,
                    code_tag,
                    code_note,
                    total_tokens,
                    elapsed,
                    summary_line,
                )

                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append(
                    {"role": "assistant", "content": f"[CODE] {summary_line}"}
                )
                return True

            if active_mode == AppMode.DEBUG:
                if not code_file_manager:
                    _ui_set_note("File manager unavailable.", ttl_sec=4.0)
                    return False

                proposed = code_file_manager.extract_file_changes(filtered)
                if filtered.strip() == "NO_FILE_CHANGES" or not proposed:
                    if streamed_applied_changes:
                        summary_line = _summarize_applied_changes(
                            streamed_applied_changes
                        )
                        debug_tag = f"{Colors.MODE_DEBUG}[DEBUG]{Colors.ENDC}>"
                        debug_note = _build_mode_result_note(
                            AppMode.DEBUG,
                            filtered,
                            text,
                            "Applied streamed debug fix.",
                        )
                        _print_mode_result(
                            text,
                            debug_tag,
                            debug_note,
                            total_tokens,
                            elapsed,
                            summary_line,
                        )
                        TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                        TERMINAL_CHAT_HISTORY.append(
                            {"role": "assistant", "content": f"[DEBUG] {summary_line}"}
                        )
                        return True
                    if auto_debug_retries < 2 and filtered.strip() != "NO_FILE_CHANGES":
                        auto_debug_retries += 1
                        revision_feedback = (
                            "Your previous output did not include any File: blocks. "
                            "Return file changes using File: lines and code fences. "
                            "If no changes are needed, return exactly: NO_FILE_CHANGES."
                        )
                        continue
                    debug_tag = f"{Colors.MODE_DEBUG}[DEBUG]{Colors.ENDC}>"
                    debug_note = _build_mode_result_note(
                        AppMode.DEBUG,
                        filtered,
                        text,
                        "No debug file changes were needed.",
                    )
                    _print_mode_result(
                        text,
                        debug_tag,
                        debug_note,
                        total_tokens,
                        elapsed,
                        _summarize_applied_changes([]),
                    )
                    TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                    TERMINAL_CHAT_HISTORY.append(
                        {
                            "role": "assistant",
                            "content": f"[DEBUG] {_summarize_applied_changes([])}",
                        }
                    )
                    return True

                if require_fragment_edits:
                    root_abs = code_file_manager._effective_project_root()
                    violations: List[str] = []
                    for change in proposed:
                        try:
                            rel = str((change or {}).get("relative_path", "")).strip()
                            kind = str((change or {}).get("change_kind", "full")).lower()
                            if not rel or kind == "patch":
                                continue
                            target = os.path.abspath(os.path.join(root_abs, rel))
                            if os.path.exists(target):
                                violations.append(rel)
                        except Exception:
                            continue
                    if violations and auto_debug_retries < 2:
                        auto_debug_retries += 1
                        violations = sorted(set(violations))[:10]
                        revision_feedback = (
                            "You must NOT overwrite existing files with full content.\n"
                            "Return ONLY fragment edits using ```patch blocks for these existing files:\n- "
                            + "\n- ".join(violations)
                            + "\n\nFor new files, you may return full content.\n"
                            "Reminder: each patch must be preceded by: File: relative/path"
                        )
                        retry_reason = "full file rewrite blocked (require_fragment_edits=on)"
                        continue

                accepted_changes: List[Dict[str, Any]] = []
                total_files = len(proposed)
                accepted_changes = list(proposed)

                quality_decision, quality_feedback = _run_quality_gate(
                    accepted_changes,
                    "DEBUG",
                    interactive=False,
                    auto_action="fix",
                )
                if quality_decision == "cancel":
                    print("[DEBUG] Cancelled by quality gate.")
                    return False
                if quality_decision == "revise":
                    auto_debug_retries += 1
                    if auto_debug_retries > 2:
                        print(
                            "[DEBUG] Quality gate could not auto-fix after 2 retries."
                        )
                        return False
                    revision_feedback = quality_feedback
                    continue

                if _should_auto_accept_debug():
                    decision, feedback = "accept", ""
                else:
                    decision, feedback = _request_mode_approval(
                        "DEBUG",
                        target_paths=_approval_target_paths("DEBUG", accepted_changes),
                        ide_file=_current_ide_file_path(),
                    )
                if decision == "cancel":
                    print("[DEBUG] Cancelled. No files saved.")
                    return False
                if decision == "revise":
                    revision_feedback = feedback
                    continue

                _ui_set_note("Applying debug fix...", ttl_sec=2.0)
                applied, _apply_elapsed = _run_with_spinner(
                    lambda: code_file_manager.apply_file_changes(accepted_changes),
                    desc="Applying changes",
                    show_progress=True,
                )
                applied = _merge_applied_change_lists(streamed_applied_changes, applied)
                if not applied:
                    warnings = list(
                        getattr(code_file_manager, "last_apply_warnings", []) or []
                    )
                    if warnings:
                        _ui_set_note(warnings[-1], ttl_sec=4.0)
                    else:
                        _ui_set_note("No files were written.", ttl_sec=4.0)
                    return False

                summary_line = _summarize_applied_changes(applied)
                debug_tag = f"{Colors.MODE_DEBUG}[DEBUG]{Colors.ENDC}>"
                debug_note = _build_mode_result_note(
                    AppMode.DEBUG, filtered, text, "Prepared debug fix."
                )
                _print_mode_result(
                    text,
                    debug_tag,
                    debug_note,
                    total_tokens,
                    elapsed,
                    summary_line,
                )

                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append(
                    {"role": "assistant", "content": f"[DEBUG] {summary_line}"}
                )
                return True

            if active_mode == AppMode.ANALYST:
                analyst_root = analyst_engine._project_root()
                analyst_report_path = os.path.join(analyst_root, ANALYST_TEST_FILENAME)
                analyst_changes = (
                    code_file_manager.extract_file_changes(filtered)
                    if code_file_manager
                    else []
                )
                analyst_report_body = _strip_file_change_blocks(filtered)
                analyst_body = _sanitize_analyst_markdown(analyst_report_body)
                try:
                    with open(analyst_report_path, "w", encoding="utf-8") as f:
                        f.write(analyst_body)
                except Exception as exc:
                    print(
                        f"[ANALYST] ERROR: Failed to write {ANALYST_TEST_FILENAME}: {exc}"
                    )
                    return False
                analyst_written: List[Dict[str, Any]] = []
                if (
                    ide_integration
                    and ide_integration.active_ide
                    and _should_auto_open_written_files()
                ):
                    try:
                        ide_integration.open_file(analyst_report_path)
                    except Exception:
                        pass
                if analyst_changes and code_file_manager:
                    _ui_set_note("Applying analyst artifacts...", ttl_sec=2.0)
                    analyst_written, _apply_elapsed = _run_with_spinner(
                        lambda: code_file_manager.apply_file_changes(analyst_changes),
                        desc="Applying analyst files",
                        show_progress=True,
                    )

                analyst_tag = f"{Colors.MODE_ANALYST}[ANALYST]{Colors.ENDC}>"
                analyst_note = (
                    "Created analytic report files and refreshed the findings summary."
                )
                if not analyst_note:
                    analyst_note = _build_mode_result_note(
                        AppMode.ANALYST, analyst_body, text, "Prepared analyst report."
                    )
                _print_mode_result(
                    text,
                    analyst_tag,
                    analyst_note,
                    total_tokens,
                    elapsed,
                    (
                        f"report [{ANALYST_TEST_FILENAME}] + "
                        f"create [{len(analyst_written)} {'file' if len(analyst_written) == 1 else 'files'}]"
                        if analyst_written
                        else f"report [{ANALYST_TEST_FILENAME}]"
                    ),
                )

                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"[ANALYST] report [{ANALYST_TEST_FILENAME}] + "
                            f"create [{len(analyst_written)} {'file' if len(analyst_written) == 1 else 'files'}]"
                            if analyst_written
                            else f"[ANALYST] report [{ANALYST_TEST_FILENAME}]"
                        ),
                    }
                )
                return True

            print(f"ERROR: Unsupported mode '{active_mode}'")
            return False

    except Exception as e:

        _ui_set_note("Chat generation failed.", ttl_sec=4.0)
        return False


def _extract_visible_answer(raw_text: str) -> str:
    if not raw_text:
        return ""

    original = str(raw_text)
    text = original.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"\r\n?", "\n", text)
    text = text.replace("\x00", "")

    block_patterns = [
        r"<\s*think\s*>.*?<\s*/\s*think\s*>",
        r"<\s*analysis\s*>.*?<\s*/\s*analysis\s*>",
        r"<\s*reasoning\s*>.*?<\s*/\s*reasoning\s*>",
    ]
    for pattern in block_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)

    text = re.sub(
        r"(?is)<\|\s*assistant\b[^>]*?to=[^>]*?\|>.*?(?=(?:<\|\s*(?:assistant|user)\b)|\Z)",
        "",
        text,
    )
    assistant_marker = re.search(r"(?is)<\|\s*assistant\s*\|>(.*)$", text)
    if assistant_marker:
        text = assistant_marker.group(1)

    text = re.sub(r"<\|[^>]+?\|>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?im)^\s*(?:chat|plan|code|ai|assistant|user)\s*>\s*", "", text)
    text = re.sub(r"(?im)^\s*\[(?:chat|plan|code)\]\s*>\s*", "", text)
    text = re.sub(r"(?im)^\s*(?:ai|assistant)\s*:\s*", "", text)
    text = re.sub(r"(?im)^\s*(?:user|human)\s*:\s*.*$", "", text)
    cleaned_lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if stripped.startswith("{") and stripped.endswith("}") and '"cmd"' in stripped:
            continue
        if re.match(
            r"^(?:we need to\b|let'?s\b|i will\b|i need to\b|first,\s*i\b|probably they want\b|the .* currently\?)",
            stripped,
            re.IGNORECASE,
        ):
            continue
        cleaned_lines.append(line)

    while cleaned_lines and cleaned_lines[0] == "":
        cleaned_lines.pop(0)
    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()
    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    out = text.strip()
    if out:
        return out


    fallback = original.replace("\ufeff", "").replace("\u200b", "")
    fallback = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", fallback)
    fallback = re.sub(r"\r\n?", "\n", fallback)
    fallback = fallback.replace("\x00", "")
    for pattern in block_patterns:
        fallback = re.sub(pattern, "", fallback, flags=re.IGNORECASE | re.DOTALL)
    fallback = re.sub(
        r"(?is)<\|\s*assistant\b[^>]*?to=[^>]*?\|>.*?(?=(?:<\|\s*(?:assistant|user)\b)|\Z)",
        "",
        fallback,
    )
    fallback = re.sub(r"<\|[^>]+?\|>", "", fallback, flags=re.IGNORECASE)
    fallback = re.sub(r"(?im)^\s*(?:chat|plan|code|ai|assistant|user)\s*>\s*", "", fallback)
    fallback = re.sub(r"(?im)^\s*\[(?:chat|plan|code)\]\s*>\s*", "", fallback)
    fallback = re.sub(r"\n{3,}", "\n\n", fallback)
    return fallback.strip()


def _absolute_project_path(relative_path: str) -> str:
    base_root = (
        code_file_manager._effective_project_root()
        if code_file_manager
        else os.getcwd()
    )
    return os.path.abspath(os.path.join(base_root, str(relative_path or "").strip()))


def _approval_target_paths(
    mode: str, changes: Optional[List[Dict[str, Any]]] = None
) -> List[str]:
    if mode.upper() == "PLAN":
        root = (
            code_file_manager._effective_project_root()
            if code_file_manager
            else os.getcwd()
        )
        return [os.path.abspath(os.path.join(root, PLAN_FILENAME))]
    if mode.upper() == "TODO":
        root = (
            code_file_manager._effective_project_root()
            if code_file_manager
            else os.getcwd()
        )
        return [os.path.abspath(os.path.join(root, TODO_FILENAME))]
    explicit_selected = bool(
        ide_integration and getattr(ide_integration, "selected_file_explicit", False)
    )
    if _bool_from_any(APP_SETTINGS.get("restrict_writes_to_open_file"), True) and explicit_selected:
        selected = _current_ide_file_path()
        if selected:
            try:
                selected_abs = os.path.abspath(selected)
                project_root_abs = _current_project_root_path(force_refresh=False)
                meta_names = {
                    PLAN_FILENAME.lower(),
                    TODO_FILENAME.lower(),
                    ANALYST_TEST_FILENAME.lower(),
                    ".cmdaisettings.json",
                    ".cmdaidebug.json",
                    "faq.md",
                    "contributing.md",
                    "security.md",
                    "code_of_conduct.md",
                }
                selected_in_app = (
                    code_file_manager._is_inside_cmda_app_root(selected_abs)
                    if code_file_manager
                    else False
                )
                project_outside_app = (
                    bool(project_root_abs)
                    and code_file_manager is not None
                    and not code_file_manager._is_inside_cmda_app_root(project_root_abs)
                )
                if (
                    os.path.basename(selected_abs).lower() not in meta_names
                    and not (selected_in_app and project_outside_app)
                ):
                    return [selected_abs]
            except Exception:
                pass
    paths: List[str] = []
    for change in changes or []:
        rel_path = str((change or {}).get("relative_path", "")).strip()
        if rel_path:
            paths.append(_absolute_project_path(rel_path))
    deduped: List[str] = []
    seen = set()
    for path in paths:
        norm = os.path.normcase(path)
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(path)
    return deduped


def _current_ide_file_path() -> str:
    if not ide_integration:
        return ""
    is_selected_target = getattr(ide_integration, "_is_selected_file_target", None)


    if getattr(ide_integration, "active_ide", None) and hasattr(
        ide_integration, "ensure_selected_file"
    ):
        try:
            resolved = ide_integration.ensure_selected_file(
                max_age_sec=6.0, force_refresh=False
            )
            if resolved:
                resolved_abs = os.path.abspath(resolved)
                if callable(is_selected_target) and is_selected_target(resolved_abs):
                    return resolved_abs
        except Exception:
            pass


    cached = getattr(ide_integration, "selected_file", None)
    if (
        not getattr(ide_integration, "active_ide", None)
        and cached
    ):
        cached_abs = os.path.abspath(cached)
        if callable(is_selected_target) and is_selected_target(cached_abs):
            return cached_abs

    if ide_integration.active_ide and hasattr(
        ide_integration, "get_open_file_for_ide_cached"
    ):
        try:
            ide_id = str(ide_integration.active_ide.get("id", "")).lower()
            cached_file = ide_integration.get_open_file_for_ide_cached(
                ide_id, max_age_sec=6.0
            )
            if cached_file:
                abs_path = os.path.abspath(cached_file)
                if callable(is_selected_target) and is_selected_target(abs_path):
                    return abs_path
        except Exception:
            pass

    return ""


def _has_explicit_selected_file() -> bool:
    return bool(ide_integration and getattr(ide_integration, "selected_file_explicit", False))


def _normalize_ide_id_for_match(ide_id: str) -> str:
    value = str(ide_id or "").strip().lower()
    if not value:
        return ""
    compact = re.sub(r"[^a-z0-9]+", "", value)
    if not compact:
        return value
    if "windsurf" in compact:
        return "windsurf"
    if compact in {
        "vscode",
        "code",
        "visualstudiocode",
        "visualstudiocodeinsiders",
        "codeinsiders",
    }:
        return "vscode"
    if "cursor" in compact:
        return "cursor"
    if "pycharm" in compact or compact.endswith("charm"):
        return "pycharm"
    if "intellij" in compact or compact.startswith("idea"):
        return "intellij"
    return value


def _find_git_root(start_path: str) -> str:
    current = os.path.abspath(start_path)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return start_path


def _current_project_root_path(force_refresh: bool = False) -> str:
    if ide_integration and hasattr(ide_integration, "sync_project_context"):
        try:
            ide_integration.sync_project_context(force_refresh=force_refresh)
        except Exception:
            pass
    active_ide_id = ""
    if ide_integration and getattr(ide_integration, "active_ide", None):
        active_ide_id = str(ide_integration.active_ide.get("id", "")).strip().lower()

    running_ids: set = set()
    if ide_integration and active_ide_id:
        try:
            running_ids = set(ide_integration.get_running_ide_ids_cached())
        except Exception:
            try:
                running_ids = set(ide_integration.detect_running_ide_ids())
            except Exception:
                running_ids = set()
    active_norm = _normalize_ide_id_for_match(active_ide_id)
    active_running = any(
        _normalize_ide_id_for_match(rid) == active_norm for rid in running_ids
    )

    if (
        active_running
        and ide_integration
        and ide_integration.active_ide
        and hasattr(ide_integration, "_infer_open_folder_for_ide_id")
    ):
        try:
            folder = ide_integration._infer_open_folder_for_ide_id(active_ide_id)
            if folder and os.path.isdir(folder):
                return os.path.abspath(folder)
        except Exception:
            pass

    ide_root = getattr(ide_integration, "project_root", None)
    if ide_root and "temp" not in ide_root.lower() and "tmp" not in ide_root.lower():

        selected = (
            getattr(ide_integration, "selected_file", "") if ide_integration else ""
        )
        selected_ok = bool(selected) and os.path.exists(selected)
        if active_running or selected_ok:
            return os.path.abspath(ide_root)

    if ide_integration and getattr(ide_integration, "selected_file", None):
        try:
            selected_abs = os.path.abspath(ide_integration.selected_file)
            if os.path.exists(selected_abs):
                return os.path.abspath(os.path.dirname(selected_abs))
        except Exception:
            pass

    if code_file_manager:
        return os.path.abspath(code_file_manager._effective_project_root())
    return os.path.abspath(os.getcwd())


def _project_root_from_candidate_path(path_value: str) -> str:
    candidate = os.path.abspath(str(path_value or "").strip())
    if not candidate:
        return ""
    if ide_integration and hasattr(ide_integration, "_detect_project_root_from_path"):
        try:
            root = ide_integration._detect_project_root_from_path(candidate)
            if root and os.path.isdir(root):
                return os.path.abspath(root)
        except Exception:
            pass
    if os.path.isdir(candidate):
        return candidate
    parent = os.path.dirname(candidate)
    return os.path.abspath(parent) if parent else ""


def _resolve_plan_root(prefer_existing: bool = True, force_refresh: bool = False) -> str:
    candidates: List[str] = []
    seen: set = set()
    app_root = os.path.abspath(os.getcwd())

    def _add(path_value: str) -> None:
        raw = str(path_value or "").strip()
        if not raw:
            return
        root = _project_root_from_candidate_path(raw)
        if not root or not os.path.isdir(root):
            return
        norm = os.path.normcase(root)
        if norm in seen:
            return
        seen.add(norm)
        candidates.append(os.path.abspath(root))

    if ide_integration:
        try:
            ide_integration.sync_project_context(force_refresh=force_refresh)
        except Exception:
            pass
        active_ide = getattr(ide_integration, "active_ide", None)
        active_id = str(active_ide.get("id", "") or "").strip().lower() if active_ide else ""
        if active_id:
            if hasattr(ide_integration, "get_open_file_for_ide_cached"):
                try:
                    active_target = ide_integration.get_open_file_for_ide_cached(
                        active_id, max_age_sec=0.0 if force_refresh else 6.0
                    )
                    _add(active_target)
                except Exception:
                    pass
            if hasattr(ide_integration, "_infer_open_folder_for_ide_id"):
                try:
                    _add(ide_integration._infer_open_folder_for_ide_id(active_id))
                except Exception:
                    pass
        _add(getattr(ide_integration, "project_root", "") or "")
        _add(getattr(ide_integration, "selected_file", "") or "")

    if code_file_manager:
        _add(getattr(code_file_manager, "forced_project_root", "") or "")
        _add(getattr(code_file_manager, "project_root", "") or "")
        try:
            _add(code_file_manager._effective_project_root())
        except Exception:
            pass

    _add(app_root)

    if prefer_existing:
        for root in candidates:
            if os.path.isfile(os.path.join(root, PLAN_FILENAME)):
                return root
    return candidates[0] if candidates else app_root


def _sanitize_ide_open_target_path(ide_id: str, target_path: str) -> str:
    value = str(target_path or "").strip().strip("\"'")
    if not value:
        return ""
    lower_value = os.path.normcase(os.path.abspath(value))
    if _is_test_tmp_path(lower_value):
        return ""
    launcher_paths = set()
    if ide_integration and hasattr(ide_integration, "list_ides"):
        try:
            for ide in ide_integration.list_ides():
                if (
                    str(ide.get("id", "")).strip().lower()
                    != str(ide_id or "").strip().lower()
                ):
                    continue
                ide_path = str(ide.get("path", "")).strip()
                if ide_path:
                    launcher_paths.add(os.path.normcase(os.path.abspath(ide_path)))
        except Exception:
            pass
    if lower_value in launcher_paths:
        return ""
    basename = os.path.basename(lower_value)
    meta_files = {
        PLAN_FILENAME.lower(),
        TODO_FILENAME.lower(),
        ANALYST_TEST_FILENAME.lower(),
        ".cmdaisettings.json",
        ".cmdaidebug.json",
        "faq.md",
        "contributing.md",
        "security.md",
        "code_of_conduct.md",
    }
    if basename in meta_files:


        if ide_integration and getattr(ide_integration, "project_root", None):
            try:
                return os.path.abspath(ide_integration.project_root)
            except Exception:
                pass
        parent_dir = os.path.dirname(lower_value)
        return os.path.abspath(parent_dir) if parent_dir else ""
    if code_file_manager:
        try:
            if basename in code_file_manager.APP_META_FILES:
                parent_dir = os.path.dirname(lower_value)
                return os.path.abspath(parent_dir) if parent_dir else ""
        except Exception:
            pass
    if basename in {

        "windsurf.exe",
        "winsurf.exe",
        "code.exe",
        "code.cmd",
        "code-insiders.exe",
        "code-insiders.cmd",
        "cursor.exe",
        "cursor.cmd",
        "idea64.exe",
        "idea.exe",
        "idea.cmd",
        "idea.bat",
        "pycharm64.exe",
        "pycharm.exe",
        "subl.exe",
        "sublime_text.exe",
    }:
        return ""
    return os.path.abspath(value) if os.path.exists(value) else value


def _ide_open_target_for_display(
    ide_id: str, running_ids: set, active_file: str = ""
) -> str:
    if not ide_integration:
        return ""
    target_norm = _normalize_ide_id_for_match(ide_id)
    is_running = any(
        _normalize_ide_id_for_match(rid) == target_norm
        for rid in (running_ids or set())
    )
    is_active = bool(
        ide_integration.active_ide
        and _normalize_ide_id_for_match(ide_integration.active_ide.get("id", ""))
        == target_norm
    )
    if not is_running:
        return ""
    if hasattr(ide_integration, "explain_open_file_detection"):
        try:
            info = ide_integration.explain_open_file_detection(ide_id)
            inferred_file = _sanitize_ide_open_target_path(
                ide_id, str(info.get("inferred_file", "") or "").strip()
            ) or ""
            if inferred_file:
                return inferred_file
            cached_file = _sanitize_ide_open_target_path(
                ide_id, str(info.get("cached_file", "") or "").strip()
            ) or ""
            if cached_file:
                return cached_file
            inferred_folder = _sanitize_ide_open_target_path(
                ide_id, str(info.get("inferred_folder", "") or "").strip()
            ) or ""
            if inferred_folder:
                return inferred_folder
        except Exception:
            pass

    direct = ide_integration.get_open_file_for_ide_cached(ide_id)
    if direct:
        path = _sanitize_ide_open_target_path(ide_id, direct) or ""
        return path

    for rid in running_ids or set():
        if _normalize_ide_id_for_match(rid) == target_norm:
            candidate = ide_integration.get_open_file_for_ide_cached(rid)
            if candidate:
                path = _sanitize_ide_open_target_path(ide_id, candidate) or ""
                return path

    path = _sanitize_ide_open_target_path(ide_id, active_file) or ""
    return path


def _print_transient_muted_lines(lines: List[str]) -> int:
    count = 0
    for line in lines:
        text = str(line or "").rstrip()
        if not text:
            continue
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            print(f"\033[90m{text}{Colors.ENDC}")
        else:
            print(text)
        count += 1
    return count


def _request_mode_approval(
    mode: str,
    allow_debug: bool = False,
    target_paths: Optional[List[str]] = None,
    ide_file: str = "",
) -> Tuple[str, str]:
    options = [("accept", "Accept"), ("decline", "Decline"), ("revise", "Revise")]
    if allow_debug:
        options.append(("debug", "Debug"))
    muted_lines: List[str] = []
    paths = [
        os.path.abspath(path)
        for path in (target_paths or [])
        if str(path or "").strip()
    ]
    if (
        paths
        and ide_integration
        and hasattr(ide_integration, "open_file")
        and _bool_from_any(APP_SETTINGS.get("ide_open_target_on_approval"), False)
    ):
        for fpath in paths[:1]:
            try:
                if os.path.isfile(fpath):
                    ide_integration.open_file(fpath, line=1, col=1)
                    time.sleep(0.1)
            except Exception:
                pass
    if len(paths) == 1:
        muted_lines.append(f"[{mode.upper()}] Target file: {paths[0]}")
    elif len(paths) > 1:
        muted_lines.append(f"[{mode.upper()}] Target files: {len(paths)}")
        for path in paths[:8]:
            muted_lines.append(f"  {path}")
        if len(paths) > 8:
            muted_lines.append(f"  ... {len(paths) - 8} more")
    try:
        scope = (
            "selected file only"
            if (
                _bool_from_any(APP_SETTINGS.get("restrict_writes_to_open_file"), True)
                and _has_explicit_selected_file()
            )
            else "project root"
        )
        muted_lines.append(f"[{mode.upper()}] Write scope: {scope}")
    except Exception:
        pass
    ide_abs = os.path.abspath(ide_file) if str(ide_file or "").strip() else ""
    if ide_abs and os.path.isfile(ide_abs):
        muted_lines.append(f"[{mode.upper()}] IDE integration file: {ide_abs}")
    muted_count = _print_transient_muted_lines(muted_lines)
    decision = _read_arrow_choice(f"[{mode.upper()}] Confirm", options, default_idx=0)

    if decision == "accept":
        return "accept", ""
    if decision in {"cancel", "decline"}:
        return "cancel", ""
    if decision == "debug":
        return "debug", ""

    feedback = _read_terminal_line(
        f"[{mode.upper()}][FEEDBACK]> What to improve: "
    ).strip()
    if not feedback or "\x1b" in feedback:
        return "cancel", ""

    return "revise", feedback


def _build_revision_request(base_text: str, feedback: str, mode: str) -> str:
    return (
        f"{base_text}\n\n"
        f"[{mode.upper()} REVISION REQUEST]\n"
        "User did not accept previous result.\n"
        f"Required changes:\n{feedback}\n\n"
        "Return a full updated result."
    )


def _print_code_structure(title: str, items: List[Dict[str, Any]]) -> None:
    print(f"\n[CODE] {title}")
    for item in items:
        rel_path = item.get("relative_path", "")
        action = item.get("action", "write")
        print(f"  - {action}: {rel_path}")


def _format_code_change_summary(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "no file changes"
    paths = [
        str(item.get("relative_path", "")).strip()
        for item in items
        if str(item.get("relative_path", "")).strip()
    ]
    if not paths:
        return f"applied {len(items)} file changes"
    if len(paths) == 1:
        return f"applied {paths[0]}"
    if len(paths) == 2:
        return f"applied {paths[0]} and {paths[1]}"
    return f"applied {paths[0]} (+{len(paths) - 1} more)"


def _print_ide_integration_summary(prefix: str = "[IDE]") -> int:
    lines = 0
    active = (
        ide_integration.active_ide.get("name")
        if ide_integration and ide_integration.active_ide
        else "None"
    )
    print(f"{prefix} Active IDE: {active}")
    lines += 1
    project_root = (
        os.path.abspath(ide_integration.project_root)
        if ide_integration and getattr(ide_integration, "project_root", None)
        else ""
    )
    if project_root:
        print(f"{prefix} Project root: {project_root}")
        lines += 1
    selected = _current_ide_file_path()
    if selected:
        print(f"{prefix} Integrated file: {selected}")
        lines += 1
    else:
        print(f"{prefix} Integrated file: None")
        lines += 1
    return lines


def _format_ide_command_chip() -> str:
    active_name = (
        ide_integration.active_ide.get("name")
        if ide_integration and ide_integration.active_ide
        else "None"
    )
    if ide_integration and ide_integration.active_ide:
        ide_id = str(ide_integration.active_ide.get("id", "") or "").strip().lower()
        if ide_id == "windsurf":
            active_name = "Windsurf"
    selected = _current_ide_file_path() or "None"
    if (
        selected == "None"
        and ide_integration
        and ide_integration.active_ide
        and hasattr(ide_integration, "get_open_file_for_ide_cached")
    ):
        try:
            ide_id = str(ide_integration.active_ide.get("id", "")).lower()
            selected = (
                _sanitize_ide_open_target_path(
                    ide_id,
                    ide_integration.get_open_file_for_ide_cached(ide_id),
                )
                or "None"
            )
        except Exception:
            selected = "None"
    if selected != "None" and ide_integration and ide_integration.active_ide:
        try:
            ide_id = str(ide_integration.active_ide.get("id", "")).lower()
            selected = _sanitize_ide_open_target_path(ide_id, selected) or selected
        except Exception:
            pass
    if selected == "None":
        selected = _current_project_root_path() or "None"
    return _format_command_chip(f"ide [{active_name}] [{selected}]")


def _ensure_ide_scaffold_files() -> None:
    if not ide_integration or not getattr(ide_integration, "active_ide", None):
        return
    ide_id = str(ide_integration.active_ide.get("id", "") or "").strip().lower()
    if ide_id not in {"vscode", "windsurf", "cursor"}:
        return
    try:
        root_abs = (
            os.path.abspath(code_file_manager._effective_project_root())
            if "code_file_manager" in globals() and code_file_manager
            else os.path.abspath(getattr(ide_integration, "project_root", "") or os.getcwd())
        )
    except Exception:
        root_abs = os.path.abspath(os.getcwd())
    vscode_dir = os.path.join(root_abs, ".vscode")
    try:
        os.makedirs(vscode_dir, exist_ok=True)
    except Exception:
        return

    _ensure_json_file(
        os.path.join(vscode_dir, "extensions.json"),
        {"recommendations": ["kilocode.kilo-code"]},
    )
    _ensure_json_file(
        os.path.join(vscode_dir, "launch.json"),
        {
            "version": "0.2.0",
            "configurations": [
                {
                    "name": "Python: CMDAI (run.py)",
                    "type": "python",
                    "request": "launch",
                    "program": "${workspaceFolder}/run.py",
                    "console": "integratedTerminal",
                    "justMyCode": True,
                }
            ],
        },
    )


def _is_test_tmp_path(path_value: str) -> bool:
    try:
        normalized = os.path.normcase(str(path_value or ""))
    except Exception:
        return False
    if not normalized:
        return False
    normalized = normalized.replace("/", "\\")
    return "\\tests\\_tmp" in normalized or "\\tests\\_tmp_visual" in normalized


def _print_text_panel(
    title: str,
    text: str,
    tone: str = "normal",
    max_lines: int = 80,
    max_cols: int = 140,
) -> int:
    lines_printed = 0
    ansi_dim = "\033[90m"
    ansi_reset = Colors.ENDC
    color = ansi_dim if tone == "muted" else ""
    reset = ansi_reset if color else ""

    use_uni = _supports_unicode_ui()
    tl = "┌" if use_uni else "+"
    bl = "└" if use_uni else "+"
    vr = "│" if use_uni else "|"
    hz = "─" if use_uni else "-"

    max_cols = max(20, min(max_cols, max(20, get_terminal_width() - 6)))
    print(f"{color}{tl}{hz} {title}{reset}")
    lines_printed += 1
    rows = (text or "").splitlines()
    for row in rows[:max_lines]:
        line = row.replace("\t", "    ")
        if len(line) > max_cols:
            line = line[: max_cols - 3] + "..."
        print(f"{color}{vr} {line}{reset}")
        lines_printed += 1
    if len(rows) > max_lines:
        print(f"{color}{vr} ... ({len(rows) - max_lines} more lines){reset}")
        lines_printed += 1
    print(f"{color}{bl}{hz}{reset}")
    lines_printed += 1
    return lines_printed


def _print_code_change_preview(
    change: Dict[str, Any],
    index: int,
    total: int,
    max_lines: int = 18,
    max_cols: int = 120,
) -> int:
    rel_path = (change or {}).get("relative_path", "unknown")
    language = (change or {}).get("language", "txt")
    code = (change or {}).get("code", "")
    code_lines = str(code).splitlines()
    change_kind = (change or {}).get("change_kind", "full")

    use_uni = _supports_unicode_ui()
    tl = "┌" if use_uni else "+"
    bl = "└" if use_uni else "+"
    vr = "│" if use_uni else "|"
    hz = "─" if use_uni else "-"
    file_icon = "📄" if use_uni else "[F]"
    patch_icon = "📝" if use_uni else "[P]"


    if change_kind == "patch":
        header_color = Colors.BOLD_YELLOW
        kind_icon = patch_icon
    else:
        header_color = Colors.BOLD_GREEN
        kind_icon = file_icon

    lines_printed = 0


    term_width = get_terminal_width()
    max_cols = max(40, min(max_cols, term_width - 10))


    print(f"{header_color}{tl}{hz*3} {kind_icon} FILE {index}/{total} {hz*3}{Colors.ENDC}")
    lines_printed += 1


    print(f"{header_color}{vr}{Colors.ENDC} {Colors.BOLD}{rel_path}{Colors.ENDC} {Colors.DIM}({language}){Colors.ENDC}")
    lines_printed += 1


    print(f"{header_color}{vr}{hz*2}{Colors.ENDC}")
    lines_printed += 1


    for ln, content in enumerate(code_lines[:max_lines], 1):
        text = content.replace("\t", "    ")


        highlighted = _simple_syntax_highlight(text, language)


        if len(_strip_ansi(highlighted)) > max_cols - 6:
            plain_text = _strip_ansi(highlighted)
            truncated = plain_text[:max_cols - 6] + "..."
            highlighted = truncated


        line_num = f"{ln:>3}"
        print(f"{header_color}{vr}{Colors.ENDC} {Colors.DIM}{line_num}{Colors.ENDC} {highlighted}")
        lines_printed += 1

    if len(code_lines) > max_lines:
        remaining = len(code_lines) - max_lines
        print(f"{header_color}{vr}{Colors.ENDC} {Colors.DIM}... {remaining} more lines{Colors.ENDC}")
        lines_printed += 1


    print(f"{header_color}{bl}{hz*2}{Colors.ENDC}")
    lines_printed += 1

    return lines_printed


def _simple_syntax_highlight(text: str, language: str = "txt") -> str:
    if not text:
        return text


    keywords = {
        "python": {"def", "class", "if", "else", "elif", "for", "while", "return", "import", "from", "as", "try", "except", "finally", "with", "async", "await", "lambda", "yield", "raise", "pass", "break", "continue", "in", "is", "not", "and", "or", "True", "False", "None"},
        "javascript": {"function", "const", "let", "var", "if", "else", "for", "while", "return", "import", "export", "from", "class", "async", "await", "try", "catch", "finally", "throw", "new", "this", "true", "false", "null", "undefined"},
        "typescript": {"function", "const", "let", "var", "if", "else", "for", "while", "return", "import", "export", "from", "class", "async", "await", "try", "catch", "finally", "throw", "new", "this", "true", "false", "null", "undefined", "interface", "type", "namespace", "enum"},
    }

    lang_lower = language.lower()
    lang_keywords = keywords.get(lang_lower, set())

    result = text


    if '"' in result or "'" in result:

        result = re.sub(
            r'("[^"]*")',
            f'{Colors.SYNTAX_STRING}\\1{Colors.ENDC}',
            result
        )
        result = re.sub(
            r"('[^']*')",
            f'{Colors.SYNTAX_STRING}\\1{Colors.ENDC}',
            result
        )


    if language in ("python", "py"):
        if "#" in result:
            parts = result.split("#", 1)
            if len(parts) == 2:
                result = f"{parts[0]}{Colors.SYNTAX_COMMENT}#{parts[1]}{Colors.ENDC}"
    elif language in ("javascript", "typescript", "js", "ts", "java", "c", "cpp", "csharp", "go", "rust"):
        if "//" in result:
            parts = result.split("//", 1)
            if len(parts) == 2:
                result = f"{parts[0]}{Colors.SYNTAX_COMMENT}//{parts[1]}{Colors.ENDC}"


    result = re.sub(
        r'(?<![\w.])(\d+\.?\d*)(?![\w.])',
        f'{Colors.SYNTAX_NUMBER}\\1{Colors.ENDC}',
        result
    )


    if lang_keywords:
        for kw in lang_keywords:

            pattern = rf'\b{re.escape(kw)}\b'
            result = re.sub(pattern, f'{Colors.SYNTAX_KEYWORD}{kw}{Colors.ENDC}', result)

    return result


def _strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def _is_escape_input(text: str) -> bool:
    value = (text or "").strip().lower()
    return value in {"/exit", "/quit"}


def _is_chat_pause_toggle(text: str) -> bool:
    value = (text or "").strip().lower()
    return value in {"/pause"}


def _is_model_unload_shortcut(text: str) -> bool:
    value = (text or "").strip().lower()
    return value in {"/unload"}


def _is_mode_switch_shortcut(text: str) -> bool:
    value = (text or "").strip().lower()
    return value in {"/m", "/mode", "/switch"}


def _handle_go_command() -> bool:
    global CURRENT_MODE
    plan_root = _resolve_plan_root(prefer_existing=True, force_refresh=True)
    root_backend_layout = (
        os.path.isfile(os.path.join(plan_root, "package.json"))
        and os.path.isdir(os.path.join(plan_root, "src"))
    )
    root_public_layout = os.path.isdir(os.path.join(plan_root, "public"))
    plan_path = os.path.join(plan_root, PLAN_FILENAME)
    if not os.path.exists(plan_path):
        print(f"ERROR: Missing plan file: {PLAN_FILENAME}")
        print(f"       Expected at: {os.path.abspath(plan_path)}")
        if ide_integration and getattr(ide_integration, "project_root", None):
            try:
                print(
                    f"       Active IDE root: {os.path.abspath(ide_integration.project_root)}"
                )
            except Exception:
                pass
        return False

    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read().strip()
    except Exception as e:
        print(f"ERROR: Failed to read {PLAN_FILENAME}: {e}")
        print(f"       Path: {os.path.abspath(plan_path)}")
        return False

    if not plan_content:
        print(f"ERROR: {PLAN_FILENAME} is empty.")
        print(f"       Path: {os.path.abspath(plan_path)}")
        return False

    mode_color = Colors.MODE_CODE
    print(f"\n{mode_color}[CODE]{Colors.ENDC}> /go")

    go_verbose = _bool_from_any(os.environ.get("CMDAI_GO_VERBOSE", ""), False)
    if go_verbose:
        print(f"[GO] Plan: {os.path.abspath(plan_path)} ({len(plan_content)} chars)")
        if len(plan_content) > 6000:
            print("[GO] Note: Plan content will be truncated to 6000 chars for the model.")


    missing_targets: List[str] = []
    plan_lower = str(plan_content or "").lower()
    wants_spa_frontend = any(
        marker in plan_lower
        for marker in ("react", "vite", "tsx", "spa", "single-page app")
    )
    try:
        for raw in re.findall(r"`([^`]+)`", plan_content or ""):
            token = str(raw or "")
            token = token.replace("\ufeff", "").replace("\u200b", "").strip()
            if not token:
                continue
            lower = token.lower().strip()
            if lower.lstrip("\ufeff\u200b").startswith("#!"):
                continue
            if re.search(r"\s", token):
                continue
            looks_like_file = lower.endswith(
                (
                    ".py",
                    ".js",
                    ".jsx",
                    ".ts",
                    ".tsx",
                    ".json",
                    ".md",
                    ".txt",
                    ".html",
                    ".css",
                    ".yml",
                    ".yaml",
                    ".toml",
                    ".ini",
                    ".env",
                )
            )
            looks_like_relpath = ("/" in token or "\\" in token) and looks_like_file
            if looks_like_file or looks_like_relpath:

                if os.path.isabs(token):
                    continue
                rel = token.replace("\\", "/").lstrip("./")
                if not rel:
                    continue
                if root_backend_layout and rel.startswith("backend/"):
                    continue
                if (
                    root_backend_layout
                    and root_public_layout
                    and not wants_spa_frontend
                    and rel.startswith("frontend/")
                ):
                    continue
                if (
                    root_backend_layout
                    and wants_spa_frontend
                    and rel in {"public/index.html", "public/app.js"}
                ):
                    continue
                abs_path = os.path.abspath(os.path.join(plan_root, rel))
                if not os.path.exists(abs_path):
                    missing_targets.append(rel)
    except Exception:
        missing_targets = []


    try:
        must_exist: List[str] = []
        plan_l = plan_lower
        wants_frontend = any(
            marker in plan_l
            for marker in (
                "frontend",
                "front-end",
                "html",
                "css",
                "ui",
                "react",
                "vite",
                "client/public/index.html",
                "frontend/index.html",
            )
        )
        if os.path.isdir(os.path.join(plan_root, "server")):
            must_exist.extend(
                [
                    "server/index.js",
                    "server/db.js",
                    "server/routes/health.js",
                    "server/routes/upload.js",
                    "server/routes/tracks.js",
                ]
            )
        if os.path.isdir(os.path.join(plan_root, "client")):
            must_exist.extend(
                [
                    "client/public/index.html",
                    "client/src/index.js",
                    "client/src/App.js",
                    "client/src/api.js",
                ]
            )
        if must_exist:
            for rel in must_exist:
                abs_path = os.path.abspath(os.path.join(plan_root, rel))
                if not os.path.exists(abs_path):
                    missing_targets.append(rel)
        if wants_frontend:
            frontend_candidates: List[str] = []
            if os.path.isdir(os.path.join(plan_root, "client")):
                frontend_candidates.extend(
                    [
                        "client/public/index.html",
                        "client/src/index.js",
                        "client/src/App.js",
                        "client/src/api.js",
                    ]
                )
            elif root_backend_layout and root_public_layout and not wants_spa_frontend:
                frontend_candidates.extend(
                    [
                        "public/index.html",
                        "public/app.js",
                    ]
                )
            elif root_backend_layout:
                frontend_candidates.extend(
                    [
                        "frontend/index.html",
                        "frontend/package.json",
                        "frontend/src/main.tsx",
                        "frontend/src/App.tsx",
                    ]
                )
            else:
                frontend_candidates.extend(
                    [
                        "frontend/index.html",
                        "frontend/package.json",
                        "frontend/src/main.tsx",
                        "frontend/src/App.tsx",
                    ]
                )
            for rel in frontend_candidates:
                abs_path = os.path.abspath(os.path.join(plan_root, rel))
                if not os.path.exists(abs_path):
                    missing_targets.append(rel)
    except Exception:
        pass

    if missing_targets:
        missing_targets = sorted(set(missing_targets))[:10]
        if go_verbose:
            print(f"[GO] Missing targets (from plan): {', '.join(missing_targets)}")

    CURRENT_MODE = AppMode.CODE
    missing_note = ""
    if missing_targets:
        missing_note = (
            "\n\nIMPORTANT: The following target files are missing and MUST be created:\n- "
            + "\n- ".join(missing_targets)
            + "\nDo NOT return NO_FILE_CHANGES."
        )
    go_prompt = (
        f"Execute the implementation based on {PLAN_FILENAME}. "
        "Create or update project files accordingly. "
        "IMPORTANT RULES:\n"
        "- Return ONLY code blocks with 'File:' headers.\n"
        "- DO NOT include any prose/explanations, trees, bullet lists, or extra text outside file blocks.\n"
        "- DO NOT use patch/diff formats; always output FULL file contents.\n"
        "- File paths must be relative, must NOT contain spaces, and must NOT contain box-drawing characters.\n"
        "- Never create a file literally named 'Node.js' (that is a runtime name, not a source file).\n"
        "- If the project already has a root-level `public/` folder, prefer `public/*` over nested `frontend/frontend/*` paths.\n"
        "- If the plan mentions frontend/UI/HTML, you MUST create the missing frontend files in this run.\n"
        "- When both backend and frontend are needed, do not return backend-only output.\n"
        "Format for each file:\n"
        "File: relative/path/to/file.ext\n"
        "```\n"
        "<code content here>\n"
        "```\n"
        "If there are no file changes, return exactly: NO_FILE_CHANGES.\n\n"
        f"Project root:\n{os.path.abspath(plan_root)}"
        f"{missing_note}\n\n"
        f"Plan content:\n{plan_content[:6000]}"
    )

    prev_restrict = APP_SETTINGS.get("restrict_writes_to_open_file")
    prev_forced_root = getattr(code_file_manager, "forced_project_root", "") if code_file_manager else ""
    APP_SETTINGS["restrict_writes_to_open_file"] = False
    if code_file_manager:
        code_file_manager.forced_project_root = os.path.abspath(plan_root)
        code_file_manager.project_root = os.path.abspath(plan_root)
    if ide_integration:
        try:
            ide_integration.project_root = os.path.abspath(plan_root)
        except Exception:
            pass
    dynamic_max_tokens = min(5000, max(2200, 650 * max(1, len(missing_targets))))
    try:
        return send_terminal_prompt(
            go_prompt, max_tokens=dynamic_max_tokens, temperature=0.2, top_p=0.9
        )
    finally:
        APP_SETTINGS["restrict_writes_to_open_file"] = prev_restrict
        if code_file_manager:
            code_file_manager.forced_project_root = prev_forced_root


def _handle_ide_command(raw_command: str) -> bool:
    global ide_integration

    use_uni = _supports_unicode_ui()
    bullet = "•" if use_uni else "*"
    arrow = "→" if use_uni else "->"
    check = "✓" if use_uni else "OK"
    cross = "✗" if use_uni else "X"

    if not ide_integration:
        print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} IDE integration is not initialized.")
        print(f"{Colors.DIM}Hint: Restart CMDAI or check your installation.{Colors.ENDC}")
        return False

    if hasattr(ide_integration, "refresh_ides"):
        try:
            ide_integration.refresh_ides()
        except Exception:
            pass
    if hasattr(ide_integration, "sync_project_context"):
        try:
            ide_integration.sync_project_context(force_refresh=True)
        except Exception:
            pass
    parts = (raw_command or "").strip().split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    ides = ide_integration.list_ides()
    try:
        running_ids = set(ide_integration.get_running_ide_ids_cached())
    except Exception:
        running_ids = set(ide_integration.detect_running_ide_ids())

    connected_norm = _normalize_ide_id_for_match(
        getattr(ide_integration, "host_ide_id", "") or ""
    )
    require_host = _bool_from_any(APP_SETTINGS.get("ide_require_host"), False)

    def _print_ide_list() -> int:
        print(f"\n{Colors.BOLD}{Colors.OKCYAN}╔{'═'*50}╗{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.OKCYAN}║{Colors.ENDC} {'IDE STATUS':^48} {Colors.BOLD}{Colors.OKCYAN}║{Colors.ENDC}")
        print(f"{Colors.BOLD}{Colors.OKCYAN}╠{'═'*50}╣{Colors.ENDC}")

        ordered = sorted(
            ides,
            key=lambda item: (
                0 if str(item.get("id", "")) in running_ids else 1,
                str(item.get("name", "")).lower(),
            ),
        )
        for ide in ordered:
            ide_id = str(ide.get("id", "") or "").strip()
            ide_name = str(ide.get("name", "unknown") or "unknown").strip()


            if ide_id in running_ids:
                run_mark = f"{Colors.SUCCESS}{check} RUNNING{Colors.ENDC}"
            else:
                run_mark = f"{Colors.DIM}idle{Colors.ENDC}"

            ide_norm = _normalize_ide_id_for_match(ide_id)
            if connected_norm and ide_norm == connected_norm:
                conn_mark = f"{Colors.SUCCESS}{check} CONNECTED{Colors.ENDC}"
            else:
                conn_mark = f"{Colors.DIM}{cross} not connected{Colors.ENDC}"

            open_target = _ide_open_target_for_display(
                ide_id,
                running_ids=running_ids,
            )
            open_target = open_target or f"{Colors.DIM}None{Colors.ENDC}"


            is_active = (
                ide_integration.active_ide
                and ide_integration.active_ide.get("id") == ide_id
            )
            active_marker = f"{Colors.BOLD_YELLOW}{arrow}{Colors.ENDC} " if is_active else "  "

            print(f"{Colors.BOLD}{Colors.OKCYAN}║{Colors.ENDC} {active_marker}{Colors.BOLD}{ide_name}{Colors.ENDC}")
            print(f"{Colors.BOLD}{Colors.OKCYAN}║{Colors.ENDC}   Status: {run_mark} | {conn_mark}")
            print(f"{Colors.BOLD}{Colors.OKCYAN}║{Colors.ENDC}   Open: {open_target}")
            if ide != ordered[-1]:
                print(f"{Colors.BOLD}{Colors.OKCYAN}║{Colors.ENDC}")

        print(f"{Colors.BOLD}{Colors.OKCYAN}╚{'═'*50}╝{Colors.ENDC}\n")
        return len(ides)

    if subcommand in {"", "pick", "select"}:
        if not ides:
            _print_ide_list()
            return True

        ordered_ides = sorted(
            ides,
            key=lambda ide: (0 if ide["id"] in running_ids else 1, ide["name"].lower()),
        )
        default_idx = 0
        options = []
        for i, ide in enumerate(ordered_ides):
            if ide["id"] in running_ids and default_idx == 0:
                default_idx = i
            run_mark = "RUNNING" if ide["id"] in running_ids else "idle"
            ide_norm = _normalize_ide_id_for_match(str(ide.get("id", "")))
            conn_mark = "OK" if (connected_norm and ide_norm == connected_norm) else "X"
            open_file = _ide_open_target_for_display(
                str(ide.get("id", "")),
                running_ids=running_ids,
            )
            open_file = open_file or "None"
            options.append(
                (ide["id"], f"{ide['name']} [{run_mark}] [{conn_mark}] [{open_file}]")
            )

        selected = _read_arrow_choice("[IDE] Select", options, default_idx=default_idx)
        if selected == "cancel":
            return False

        if (
            require_host
            and connected_norm
            and _normalize_ide_id_for_match(selected) != connected_norm
        ):
            print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} Cannot connect to '{selected}' from this terminal.")
            print(f"  {Colors.DIM}Connected IDE:{Colors.ENDC} {Colors.BOLD}{connected_norm}{Colors.ENDC}")
            print(f"\n{Colors.INFO}{arrow} Hint:{Colors.ENDC} Start CMDAI from the IDE's integrated terminal")
            print(f"   (e.g., VS Code: Terminal → New Terminal)")
            return False
        if ide_integration.set_active(selected.lower()):
            print(_format_ide_command_chip())
            return True

        print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} IDE '{selected}' not found.")
        print(f"{Colors.INFO}{arrow} Use '/ide list' to see available IDEs{Colors.ENDC}")
        return False

    if subcommand in {"status", "list"}:
        _print_ide_list()
        return True

    if subcommand == "doctor":
        target = ""
        if len(parts) >= 3:
            target = parts[2].strip().lower()
        elif ide_integration.active_ide:
            target = str(ide_integration.active_ide.get("id", "")).lower()
        if not target:
            print("ERROR: No IDE selected. Use /ide use <id> first.")
            return False
        if not hasattr(ide_integration, "explain_open_file_detection"):
            print("ERROR: IDE doctor is unavailable.")
            return False
        info = ide_integration.explain_open_file_detection(target)
        print(f"[IDE DOCTOR] {info.get('ide_name', target)}")
        process_names = list(info.get("process_names", []) or [])
        print(f"  Processes: {', '.join(process_names) if process_names else 'none'}")
        cached_file = str(info.get("cached_file", "") or "").strip()
        print(f"  Cached: {cached_file or 'None'}")
        selected_file = str(info.get("selected_file", "") or "").strip()
        print(f"  Selected: {selected_file or 'None'}")
        inferred_file = str(info.get("inferred_file", "") or "").strip()
        print(f"  Inferred: {inferred_file or 'None'}")
        inferred_folder = str(info.get("inferred_folder", "") or "").strip()
        print(f"  Folder: {inferred_folder or 'None'}")
        window_titles = list(info.get("window_titles", []) or [])
        if window_titles:
            print("  Titles:")
            for title in window_titles[:4]:
                print(f"    - {title}")
        else:
            print("  Titles: none")
        candidates = list(info.get("candidates", []) or [])
        if candidates:
            print("  Candidates:")
            for candidate in candidates[:6]:
                print(f"    - {candidate}")
        else:
            print("  Candidates: none")
        resolved_candidates = list(info.get("resolved_candidates", []) or [])
        if resolved_candidates:
            print("  Resolved:")
            for candidate in resolved_candidates[:6]:
                print(f"    - {candidate}")
        else:
            print("  Resolved: none")
        return True

    if subcommand in {"use", "set"}:
        if len(parts) < 3:
            print("Usage: /ide use <id|number>")
            return False

        target = parts[2].strip()
        if target.strip().lower() in {"auto", "detect"}:
            if require_host and not connected_norm:
                print(
                    "ERROR: Not connected to any IDE. Start CMDAI from an IDE integrated terminal (e.g. VS Code: Terminal -> New Terminal) first."
                )
                return False
            try:
                ide_integration._active_ide_locked = False
                ide_integration._active_ide_locked_id = ""
            except Exception:
                pass

            if require_host and connected_norm:
                if ide_integration.set_active(connected_norm):
                    try:
                        ide_integration._active_ide_locked = True
                        ide_integration._active_ide_locked_id = connected_norm
                    except Exception:
                        pass
                    try:
                        _ensure_ide_scaffold_files()
                    except Exception:
                        pass
                    print(_format_ide_command_chip())
                    return True
                print(
                    f"ERROR: Connected IDE '{connected_norm}' not detected. Use '/ide list'."
                )
                return False

            ide_integration.refresh_ides()
            print(_format_ide_command_chip())
            return True
        if target.isdigit():
            index = int(target) - 1
            if index < 0 or index >= len(ides):
                print("ERROR: Invalid IDE number.")
                return False
            target = ides[index]["id"]

        if require_host and not connected_norm:
            print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} Not connected to any IDE.")
            print(f"{Colors.INFO}{arrow} Solution:{Colors.ENDC} Start CMDAI from an IDE integrated terminal")
            print(f"   (e.g., VS Code: Terminal → New Terminal)")
            return False
        if require_host and _normalize_ide_id_for_match(target) != connected_norm:
            print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} Cannot connect to '{target}' from this terminal.")
            print(f"  {Colors.DIM}Connected IDE:{Colors.ENDC} {Colors.BOLD}{connected_norm}{Colors.ENDC}")
            return False

        if ide_integration.set_active(target.lower()):
            try:
                ide_integration._active_ide_locked = True
                ide_integration._active_ide_locked_id = str(target).strip().lower()
            except Exception:
                pass
            try:
                _ensure_ide_scaffold_files()
            except Exception:
                pass
            print(_format_ide_command_chip())
            return True

        print(f"ERROR: IDE '{target}' not found. Use '/ide list'.")
        return False

    if subcommand == "open":
        if require_host and not connected_norm:
            print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} Not connected to any IDE.")
            print(f"{Colors.INFO}{arrow} Solution:{Colors.ENDC} Start CMDAI from an IDE integrated terminal")
            print(f"   (e.g., VS Code: Terminal → New Terminal)")
            return False
        if (
            require_host
            and (
            not ide_integration.active_ide
            or _normalize_ide_id_for_match(ide_integration.active_ide.get("id", ""))
            != connected_norm
            )
        ):
            print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} Active IDE is not connected.")
            print(f"{Colors.INFO}{arrow} Run:{Colors.ENDC} /ide use {connected_norm}")
            return False
        match = re.match(
            r"(?is)^/?ide\s+open\s+(.+?)(?:\s+(\d+))?(?:\s+(\d+))?\s*$",
            (raw_command or "").strip(),
        )
        if not match:
            print("Usage: /ide open <file> [line] [col]")
            return False

        filepath = match.group(1).strip().strip("\"'")
        line = int(match.group(2) or 1)
        col = int(match.group(3) or 1)
        result = ide_integration.open_file(filepath, line=line, col=col)
        if result.get("success"):
            print(f"Opened in {result.get('ide')}: {result.get('file')}")
            return True

        print(f"ERROR: {result.get('error', 'Failed to open file')}")
        return False

    if subcommand == "file":
        if require_host and not connected_norm:
            print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} Not connected to any IDE.")
            print(f"{Colors.INFO}{arrow} Solution:{Colors.ENDC} Start CMDAI from an IDE integrated terminal")
            print(f"   (e.g., VS Code: Terminal → New Terminal)")
            return False
        if (
            require_host
            and (
            not ide_integration.active_ide
            or _normalize_ide_id_for_match(ide_integration.active_ide.get("id", ""))
            != connected_norm
            )
        ):
            print(f"{Colors.BOLD_RED}ERROR:{Colors.ENDC} Active IDE is not connected.")
            print(f"{Colors.INFO}{arrow} Run:{Colors.ENDC} /ide use {connected_norm}")
            return False
        if len(parts) < 3:
            print(_format_ide_command_chip())
            return True
        raw_path = (raw_command or "").split(None, 2)[2]
        selected_result = ide_integration.set_selected_file(raw_path)
        if selected_result.get("success"):
            selected = selected_result.get("file")
            print(f"Open file set: {selected}")
            print(_format_ide_command_chip())
            return True
        print(f"ERROR: {selected_result.get('error', 'Failed to set selected file')}")
        return False

    print(
        "Usage: /ide [list|doctor [id]|use <id|number>|open <file> [line] [col]|file [path]]"
    )
    return False


def _handle_swap_command(raw_command: str) -> bool:
    global TERMINAL_CHAT_HISTORY
    if not loader:
        print("ERROR: Loader not initialized")
        return False

    parts = (raw_command or "").strip().split(maxsplit=1)
    target_model = parts[1].strip() if len(parts) > 1 else ""

    if target_model:
        preserved_history = list(TERMINAL_CHAT_HISTORY)
        swapped = loader.load(target_model, show_try_errors=True)
        if swapped:
            TERMINAL_CHAT_HISTORY = preserved_history
            print("INFO: Model swapped. Chat history kept.")
        return swapped

    models, menu_lines = show_models_menu()
    if not models:
        return False

    choice = _pick_model_name(models, "[SWAP] Select model")
    _clear_last_terminal_lines(menu_lines + 1)
    if not choice:
        print("INFO: Swap cancelled.")
        return False
    selected_name = choice

    preserved_history = list(TERMINAL_CHAT_HISTORY)
    swapped = loader.load(selected_name, show_try_errors=True)
    if swapped:
        TERMINAL_CHAT_HISTORY = preserved_history
        print("INFO: Model swapped. Chat history kept.")
    return swapped


def _handle_files_command() -> bool:
    if not code_file_manager:
        print("No file manager available.")
        return False
    changes = list(getattr(code_file_manager, "last_applied_changes", []) or [])
    if not changes:
        print("No edited files in this session.")
        return False

    use_uni = _supports_unicode_ui()
    check = "✓" if use_uni else "OK"

    print(f"\n{Colors.BOLD}Recently edited files:{Colors.ENDC}")
    for i, c in enumerate(changes, 1):
        action = c.get("action", "modified")
        rel = c.get("relative_path", c.get("path", "unknown"))
        color = Colors.SUCCESS if action == "created" else Colors.INFO
        print(f"  {color}{i}. [{action}]{Colors.ENDC} {rel}")

    if ide_integration and ide_integration.active_ide:
        options = [(c.get("path", ""), f"Open: {c.get('relative_path', '')}")
                   for c in changes if c.get("path")]
        options.append(("cancel", "Cancel"))
        selected = _read_arrow_choice("[FILES] Open in IDE", options, default_idx=0)
        if selected and selected != "cancel" and os.path.isfile(selected):
            ide_integration.open_file(selected)
    return True


def _handle_mode_command(raw_command: str) -> bool:
    global CURRENT_MODE

    parts = raw_command.strip().split(maxsplit=2)
    subcommand = parts[1].lower() if len(parts) > 1 else ""

    if subcommand in ["chat", "c"]:
        CURRENT_MODE = AppMode.CHAT
        return True
    elif subcommand in ["plan", "p"]:
        if not _bool_from_any(APP_SETTINGS.get("plan_mode_enabled"), True):
            print("PLAN mode is disabled in /settings.")
            return False
        CURRENT_MODE = AppMode.PLAN
        return True
    elif subcommand in ["code", "d"]:
        if not _bool_from_any(APP_SETTINGS.get("code_mode_enabled"), True):
            print("CODE mode is disabled in /settings.")
            return False
        CURRENT_MODE = AppMode.CODE
        return True
    elif subcommand in ["debug", "dbg"]:
        if not _is_debug_mode_enabled():
            print("DEBUG mode is disabled. Enable debug_mode_enabled in /settings.")
            return False
        CURRENT_MODE = AppMode.DEBUG
        return True
    elif subcommand in ["analyst", "a"]:
        if not _is_analyst_mode_enabled():
            print("ANALYST mode is disabled. Enable analyst_mode_enabled in /settings.")
            return False
        CURRENT_MODE = AppMode.ANALYST
        return True
    elif subcommand in ["next", "n", "switch", ""]:
        modes = _available_modes()
        try:
            current_index = modes.index(CURRENT_MODE)
        except ValueError:
            current_index = 0
        CURRENT_MODE = modes[(current_index + 1) % len(modes)]
        return True
    else:
        print(f"Unknown mode: {subcommand}")
        available = ", ".join(_available_modes() + ["next"])
        print(f"Available: {available}")
        return False


def _handle_files_command() -> bool:
    if not code_file_manager:
        print("No file manager available.")
        return False
    changes = list(getattr(code_file_manager, "last_applied_changes", []) or [])
    if not changes:
        print(f"{Colors.DIM}No files edited in this session.{Colors.ENDC}")
        return False

    use_uni = _supports_unicode_ui()
    hz = "─" if use_uni else "-"
    width = min(60, get_terminal_width())

    print(f"\n{Colors.CMDAI_BORDER}{hz * width}{Colors.ENDC}")
    print(f"  {Colors.BOLD}Recently edited files{Colors.ENDC}")
    print(f"{Colors.CMDAI_BORDER}{hz * width}{Colors.ENDC}")
    for i, c in enumerate(changes, 1):
        action = c.get("action", "modified")
        rel = c.get("relative_path", c.get("path", "unknown"))
        color = Colors.SUCCESS if action == "created" else Colors.INFO
        print(f"  {Colors.DIM}{i}.{Colors.ENDC} {color}[{action}]{Colors.ENDC} {rel}")
    print(f"{Colors.CMDAI_BORDER}{hz * width}{Colors.ENDC}\n")

    if ide_integration and ide_integration.active_ide:
        options = [
            (c.get("path", ""), f"Open: {c.get('relative_path', c.get('path', ''))}")
            for c in changes if c.get("path")
        ]
        if options:
            options.append(("cancel", "Cancel"))
            selected = _read_arrow_choice("[FILES] Open in IDE", options, default_idx=0)
            if selected and selected != "cancel" and os.path.isfile(selected):
                ide_integration.open_file(selected)
    return True


def _handle_run_command(raw_command: str) -> bool:
    parts = (raw_command or "").strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        print(f"Usage: /run <command>")
        print(f"  {Colors.DIM}Examples: /run npm install  |  /run python app.py  |  /run npm run build{Colors.ENDC}")
        return False

    cmd = parts[1].strip()
    project_root = _current_project_root_path(force_refresh=False) or os.getcwd()
    timeout_sec = max(int(APP_SETTINGS.get("ai_command_timeout_sec", 25)), 60)

    use_uni = _supports_unicode_ui()
    arrow = "→" if use_uni else "->"
    check = "✓" if use_uni else "OK"
    cross = "✗" if use_uni else "X"
    hz = "─" if use_uni else "-"
    width = min(60, get_terminal_width())

    print(f"\n{Colors.CMDAI_BORDER}{hz * width}{Colors.ENDC}")
    print(f"  {Colors.DIM}{arrow}{Colors.ENDC} {Colors.BOLD}{cmd}{Colors.ENDC}")
    print(f"  {Colors.DIM}cwd: {project_root}{Colors.ENDC}")
    print(f"{Colors.CMDAI_BORDER}{hz * width}{Colors.ENDC}")

    try:
        completed = subprocess.run(
            cmd, cwd=project_root, shell=True, capture_output=True,
            text=True, timeout=timeout_sec, encoding="utf-8", errors="replace",
        )
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        rc = completed.returncode
        rc_color = Colors.SUCCESS if rc == 0 else Colors.BOLD_RED
        rc_icon = check if rc == 0 else cross
        print(f"  {rc_color}{rc_icon} exit {rc}{Colors.ENDC}")

        if stdout:
            lines = stdout.splitlines()
            print(f"\n{Colors.DIM}output:{Colors.ENDC}")
            for line in lines[:60]:
                print(f"  {line}")
            if len(lines) > 60:
                print(f"  {Colors.DIM}... ({len(lines) - 60} more lines){Colors.ENDC}")

        if stderr:
            lines = stderr.splitlines()
            print(f"\n{Colors.WARNING}stderr:{Colors.ENDC}")
            for line in lines[:20]:
                print(f"  {Colors.WARNING}{line}{Colors.ENDC}")
            if len(lines) > 20:
                print(f"  {Colors.DIM}... ({len(lines) - 20} more lines){Colors.ENDC}")

        print(f"{Colors.CMDAI_BORDER}{hz * width}{Colors.ENDC}\n")
        return rc == 0

    except subprocess.TimeoutExpired:
        print(f"  {Colors.BOLD_RED}{cross} Timeout after {timeout_sec}s{Colors.ENDC}")
        print(f"{Colors.CMDAI_BORDER}{hz * width}{Colors.ENDC}\n")
        return False
    except Exception as e:
        print(f"  {Colors.BOLD_RED}{cross} Error: {e}{Colors.ENDC}")
        print(f"{Colors.CMDAI_BORDER}{hz * width}{Colors.ENDC}\n")
        return False


def run_terminal_chat_session() -> None:
    global TERMINAL_CHAT_HISTORY, CURRENT_MODE

    if not loader or not loader.current_model:
        print("ERROR: No model loaded. Use 'load' first.")
        return

    chat_paused = False

    while True:
        try:
            user_text = _read_terminal_line(get_mode_prompt())

            if user_text == "__TAB__":
                _handle_mode_command("/mode next")
                continue
            if user_text == "\x1b":
                continue

        except KeyboardInterrupt:
            print("\nChat interrupted.")
            break
        except EOFError:
            print("\nChat input stream closed.")
            break

        user_text = _strip_mode_prompt_prefix(user_text)

        if _is_chat_pause_toggle(user_text):
            chat_paused = not chat_paused
            if chat_paused:
                print("INFO: Chat paused. Use '/pause' to resume.")
            else:
                print("INFO: Chat resumed.")
            continue

        command_text = (user_text or "").strip()
        if not command_text:
            continue

        command_name = ""
        if command_text.startswith("/"):
            command_lower = command_text.lower()
            command_name_full = command_lower.split()[0]
            command_name = command_name_full[1:]

        if command_name:
            if command_name not in ALLOWED_USER_COMMANDS:
                print("ERROR: Unknown command. Use /help")
                continue
            if _is_model_unload_shortcut(command_text):
                if loader and loader.current_model:
                    if loader.unload():
                        print("SUCCESS: Model unloaded from memory")
                        TERMINAL_CHAT_HISTORY.clear()
                    else:
                        print("ERROR: Failed to unload model")
                else:
                    print("INFO: No model loaded")
                print("Exited chat mode.")
                break
            if _is_escape_input(command_text):
                print("Exited chat mode.")
                break


            if command_name in {"help", "pomoc", "?", "dhelp", "ahelp"}:
                if command_name == "dhelp":
                    picked = _pick_debug_command_panel()
                elif command_name == "ahelp":
                    picked = _pick_analyst_command_panel()
                else:
                    picked = _pick_command_from_help_panel()
                if not picked:
                    continue
                command_text = picked
                command_lower = command_text.lower()
                command_name_full = command_lower.split()[0]
                command_name = command_name_full[1:]
            if command_name in {"exit", "quit"}:
                if _bool_from_any(APP_SETTINGS.get("confirm_exit"), True):
                    confirm = _read_arrow_choice(
                        "Exit chat mode?", [("yes", "Yes"), ("no", "No")], default_idx=1
                    )
                    if confirm != "yes":
                        continue
                print("Exited chat mode.")
                break
            if command_name == "status":
                show_status()
                continue
            if command_name == "settings":
                _handle_settings_command(command_text)
                continue
            if command_name == "ide":
                _handle_ide_command(command_text)
                continue
            if command_name == "visualtest":
                _handle_visualtest_command()
                continue
            if command_name == "go":
                _handle_go_command()
                continue
            if command_name == "swap":
                _handle_swap_command(command_text)
                continue
            if command_name == "files":
                _handle_files_command()
                continue
            if command_name == "debug":
                _handle_debug_command(command_text)
                continue
            if command_name == "dhelp":
                show_debug_help()
                continue
            if command_name == "trace":
                _handle_trace_command(command_text)
                continue
            if command_name == "stack":
                _handle_stack_command(command_text)
                continue
            if command_name == "quickfix":
                _handle_quickfix_command(command_text)
                continue
            if command_name == "patterns":
                _handle_patterns_command()
                continue
            if command_name == "autofix":
                _handle_autofix_command(command_text)
                continue
            if command_name == "tests":
                _handle_tests_command(command_text)
                continue
            if _handle_analyst_named_command(command_name):
                continue
            if command_name == "load":
                parts = command_text.split(maxsplit=1)
                model_name = parts[1].strip() if len(parts) > 1 else ""
                if model_name:
                    loader.load(model_name, show_try_errors=True)
                else:
                    models = loader.list_models() if loader else []
                    if not models:
                        print("ERROR: NO GGUF MODELS IN FOLDER")
                        continue
                    choice = _pick_model_name(models, "/load")
                    if not choice:
                        print("INFO: Load cancelled.")
                        continue
                    loader.load(choice, show_try_errors=True)
                continue
            if command_name == "download":
                parts = command_text.split(maxsplit=2)
                source = parts[1].strip() if len(parts) > 1 else ""
                output_name = parts[2].strip() if len(parts) > 2 else None
                if not source:
                    print("Usage: /download <alias|url> [file.gguf]")
                    continue
                try:
                    result = loader.download_model(
                        source, output_name=output_name, overwrite=False
                    )
                    if result["status"] == "already_exists":
                        print(f"Model already exists: {result['name']}")
                    else:
                        print(f"Model downloaded: {result['name']}")
                except Exception as e:
                    print(f"Error: {e}")
                continue

            print("ERROR: Unknown command. Use /help")
            continue

        if chat_paused:
            print("INFO: Chat is paused. Use '/pause' to resume.")
            continue

        send_terminal_prompt(command_text)


def _should_keep_terminal_open() -> bool:
    value = os.environ.get("RUN_AI_KEEP_OPEN", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _wait_before_terminal_close() -> None:
    if not _should_keep_terminal_open():
        return
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok):
        return
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass


def main():
    global        http_server,        loader,        HAS_AI_ENGINE,        LAST_UPDATE_STATUS,        HTTP_PORT,        TERMINAL_CHAT_HISTORY,        ide_integration,        code_file_manager,        INPUT_AREA_START_ROW,        INPUT_AREA_CLEAR_LINES

    loader = SimpleGGUFLoader()
    load_app_settings()
    ide_integration = IDEIntegration()
    code_file_manager = CodeFileManager()
    if ide_integration and getattr(ide_integration, "project_root", None):
        code_file_manager.project_root = os.path.abspath(ide_integration.project_root)
    install_global_launcher(silent=True)

    try:
        import llama_cpp

        HAS_AI_ENGINE = True
    except ImportError:
        HAS_AI_ENGINE = False
        print("Warning: AI engine is not installed. Run 'install' to install it.")

    if HAS_AI_ENGINE and sys.version_info >= (3, 13):
        print(
            "Warning: Python 3.13 + llama-cpp-python can be unstable for some GGUF models."
        )
        print(r"         Recommended runtime: .\python311\python.exe run.py")

    try:
        http_server = start_http_server(HTTP_PORT)
        if http_server:
            try:
                HTTP_PORT = int(http_server.server_address[1])
            except Exception:
                pass
    except Exception as e:
        print(f"ERROR: Failed to start HTTP server: {e}")
        http_server = None

    if _should_show_welcome():
        print_welcome()
    else:
        print("CMDAI ready. Type '/help' for commands.")
        INPUT_AREA_START_ROW = 1
        INPUT_AREA_CLEAR_LINES = min(12, max(8, get_terminal_height() - 3))

    try:
        main_chat_paused = False
        while True:
            try:
                if loader and loader.current_model:
                    run_terminal_chat_session()
                    continue

                raw_command = _strip_mode_prompt_prefix(
                    _read_terminal_line(get_mode_prompt())
                ).strip()

                if raw_command == "__TAB__":
                    _handle_mode_command("/mode next")
                    continue
                if raw_command == "\x1b":
                    continue

                if not raw_command:
                    continue

                if not raw_command.startswith("/"):
                    print("ERROR: No model loaded. Use /load <model> first or /command (np. /help, /ide).")
                    continue

                if _is_chat_pause_toggle(raw_command):
                    if loader and loader.current_model:
                        main_chat_paused = not main_chat_paused
                        if main_chat_paused:
                            print("INFO: Chat paused. Use '/pause' to resume.")
                        else:
                            print("INFO: Chat resumed.")
                    else:
                        print("INFO: No model loaded")
                    continue

                if _is_model_unload_shortcut(raw_command):
                    if loader and loader.current_model:
                        if loader.unload():
                            print("SUCCESS: Model unloaded from memory")
                            TERMINAL_CHAT_HISTORY.clear()
                        else:
                            print("ERROR: Failed to unload model")
                    else:
                        print("INFO: No model loaded")
                    main_chat_paused = False
                    continue

                command = raw_command.lower()
                command_name_full = command.split()[0]
                command_name = command_name_full[1:]
                if command_name not in ALLOWED_USER_COMMANDS:
                    print(
                        "ERROR: Unknown command. Type '/help' to show available commands"
                    )
                    continue

                if command_name == "exit":
                    if not _bool_from_any(APP_SETTINGS.get("confirm_exit"), True):
                        print("Goodbye!")
                        break
                    confirm = _read_arrow_choice(
                        "Exit CMDAI?", [("yes", "Yes"), ("no", "No")], default_idx=1
                    )
                    if confirm == "yes":
                        print("Goodbye!")
                        break


                elif command_name in {"help", "pomoc", "?", "dhelp", "ahelp"}:
                    if command_name == "dhelp":
                        picked = _pick_debug_command_panel()
                    elif command_name == "ahelp":
                        picked = _pick_analyst_command_panel()
                    else:
                        picked = _pick_command_from_help_panel()
                    if not picked:
                        continue
                    raw_command = picked
                    command = raw_command.lower()
                    command_name_full = command.split()[0]
                    command_name = command_name_full[1:]
                    if command_name not in ALLOWED_USER_COMMANDS:
                        print(
                            "ERROR: Unknown command. Type '/help' to show available commands"
                        )
                        continue
                    if command_name == "help":
                        continue

                    if command_name == "exit":
                        if not _bool_from_any(APP_SETTINGS.get("confirm_exit"), True):
                            print("Goodbye!")
                            break
                        confirm = _read_arrow_choice(
                            "Exit CMDAI?", [("yes", "Yes"), ("no", "No")], default_idx=1
                        )
                        if confirm == "yes":
                            print("Goodbye!")
                            break
                    elif command_name == "load":
                        parts = (raw_command or "").strip().split(maxsplit=1)
                        model_name = parts[1].strip() if len(parts) > 1 else ""
                        if model_name:
                            loader.load(model_name, show_try_errors=True)
                        else:
                            models = loader.list_models() if loader else []
                            if not models:
                                print("ERROR: NO GGUF MODELS IN FOLDER")
                                continue
                            choice = _pick_model_name(models, "/load")
                            if not choice:
                                print("INFO: Load cancelled.")
                                continue
                            loader.load(choice, show_try_errors=True)
                    elif command_name == "swap":
                        _handle_swap_command(raw_command)
                    elif command_name == "files":
                        _handle_files_command()
                    elif command_name == "ide":
                        _handle_ide_command(raw_command)
                    elif command_name == "visualtest":
                        _handle_visualtest_command()
                    elif command_name == "go":
                        _handle_go_command()
                    elif command_name == "unload":
                        if loader.unload():
                            print("SUCCESS: Model unloaded from memory")
                            TERMINAL_CHAT_HISTORY.clear()
                        else:
                            print("ERROR: Failed to unload model")
                    elif command_name == "status":
                        show_status()
                    elif command_name == "settings":
                        _handle_settings_command(raw_command)
                    elif command_name == "version":
                        if HAS_AI_ENGINE:
                            import llama_cpp

                            print(f"llama-cpp-python: {llama_cpp.__version__}")
                        else:
                            print("ERROR: AI engine is not installed")
                    elif command_name == "update":
                        if HAS_AI_ENGINE:
                            print(
                                f"Updating runtime packages: {', '.join(RECOMMENDED_RUNTIME_PACKAGES)} ..."
                            )
                            try:
                                import subprocess

                                subprocess.check_call(
                                    [
                                        sys.executable,
                                        "-m",
                                        "pip",
                                        "install",
                                        "--upgrade",
                                        *RECOMMENDED_RUNTIME_PACKAGES,
                                    ]
                                )
                                print("SUCCESS: Updated. Restart the app.")
                                break
                            except Exception as e:
                                print(f"ERROR: Update failed: {e}")
                        else:
                            print("ERROR: AI engine is not installed")
                    elif command_name == "pause":
                        if loader and loader.current_model:
                            main_chat_paused = not main_chat_paused
                            if main_chat_paused:
                                print("INFO: Chat paused. Use '/pause' to resume.")
                            else:
                                print("INFO: Chat resumed.")
                        else:
                            print("INFO: No model loaded")
                    elif command_name == "debug":
                        _handle_debug_command(raw_command)
                    elif command_name == "dhelp":
                        show_debug_help()
                    elif command_name == "trace":
                        _handle_trace_command(raw_command)
                    elif command_name == "stack":
                        _handle_stack_command(raw_command)
                    elif command_name == "quickfix":
                        _handle_quickfix_command(raw_command)
                    elif command_name == "patterns":
                        _handle_patterns_command()
                    elif command_name == "autofix":
                        _handle_autofix_command(raw_command)
                    elif command_name == "tests":
                        _handle_tests_command(raw_command)
                    elif _handle_analyst_named_command(command_name):
                        pass
                    continue

                elif command_name == "load":
                    parts = (raw_command or "").strip().split(maxsplit=1)
                    model_name = parts[1].strip() if len(parts) > 1 else ""
                    if model_name:
                        loader.load(model_name, show_try_errors=True)
                    else:
                        models = loader.list_models() if loader else []
                        if not models:
                            print("ERROR: NO GGUF MODELS IN FOLDER")
                            continue
                        choice = _pick_model_name(models, "/load")
                        if not choice:
                            print("INFO: Load cancelled.")
                            continue
                        loader.load(choice, show_try_errors=True)

                elif command_name == "swap":
                    _handle_swap_command(raw_command)

                elif command_name == "files":
                    _handle_files_command()

                elif command_name == "ide":
                    _handle_ide_command(raw_command)

                elif command_name == "visualtest":
                    _handle_visualtest_command()

                elif command_name == "go":
                    _handle_go_command()

                elif command_name == "unload":
                    if loader.unload():
                        print("SUCCESS: Model unloaded from memory")
                        TERMINAL_CHAT_HISTORY.clear()
                    else:
                        print("ERROR: Failed to unload model")

                elif command_name == "status":
                    show_status()

                elif command_name == "settings":
                    _handle_settings_command(raw_command)

                elif command_name == "version":
                    if HAS_AI_ENGINE:
                        import llama_cpp

                        print(f"llama-cpp-python: {llama_cpp.__version__}")
                    else:
                        print("ERROR: AI engine is not installed")

                elif command_name == "update" and HAS_AI_ENGINE:
                    print(
                        f"Updating runtime packages: {', '.join(RECOMMENDED_RUNTIME_PACKAGES)} ..."
                    )
                    try:
                        import subprocess

                        subprocess.check_call(
                            [
                                sys.executable,
                                "-m",
                                "pip",
                                "install",
                                "--upgrade",
                                *RECOMMENDED_RUNTIME_PACKAGES,
                            ]
                        )
                        print("SUCCESS: Updated. Restart the app.")
                        break
                    except Exception as e:
                        print(f"ERROR: Update failed: {e}")

                elif command_name == "debug":
                    _handle_debug_command(raw_command)

                elif command_name == "dhelp":
                    show_debug_help()

                elif command_name == "trace":
                    _handle_trace_command(raw_command)

                elif command_name == "stack":
                    _handle_stack_command(raw_command)

                elif command_name == "quickfix":
                    _handle_quickfix_command(raw_command)

                elif command_name == "patterns":
                    _handle_patterns_command()

                elif command_name == "autofix":
                    _handle_autofix_command(raw_command)

                elif command_name == "tests":
                    _handle_tests_command(raw_command)

                elif _handle_analyst_named_command(command_name):
                    pass

                else:
                    if command_name == "update":
                        print("ERROR: AI engine is not installed")
                    else:
                        print(
                            "ERROR: Unknown command. Type '/help' to show available commands"
                        )

            except KeyboardInterrupt:
                print("")
            except EOFError:
                stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
                if stdin_ok:
                    print("\nInput stream is closed. Keeping terminal open...")
                    time.sleep(0.5)
                    continue
                break
            except Exception as e:
                print(f"ERROR: Exception occurred: {e}")
                import traceback

                traceback.print_exc()

    except Exception as e:
        print(f"ERROR: Critical error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        if http_server:
            print("\nStopping HTTP server...")
            http_server.shutdown()
            http_server.server_close()
        print("Goodbye!")


def handle_launch_command():
    args = sys.argv[1:]

    if len(args) == 0 or args[0].lower() in ("launch", "start", "run"):
        main()
        return

    if args[0].lower() in ("visualtest", "preview", "ui"):
        _handle_visualtest_command()
        return

    if args[0].lower() in ("help", "-h", "--help", "/?"):
        print("CMDAI Launcher")
        print("=" * 40)
        print("Usage: CMDAI [command]")
        print()
        print("Commands:")
        print("  launch    Launch CMDAI application (default)")
        print("  visualtest  Show visual preview of commands/UI")
        print("  help      Show this help message")
        print()
        print("Examples:")
        print("  CMDAI           # Launch application")
        print("  CMDAI launch    # Launch application")
        print("  CMDAI visualtest  # Show UI preview only")
        print("  python run.py   # Launch application")
        return

    print(f"Unknown command: {args[0]}")
    print("Use 'CMDAI help' for usage information.")


if __name__ == "__main__":
    try:
        handle_launch_command()
    except KeyboardInterrupt:
        print("\nGoodbye!")
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        _wait_before_terminal_close()
