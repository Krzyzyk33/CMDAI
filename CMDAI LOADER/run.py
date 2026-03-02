import os
import sys
import re
import json
import time
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
try:
    import winreg
except Exception:  # pragma: no cover - non-Windows
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
_AUTO_UPDATE_FLAG = "_AUTO_UPDATE_IN_PROGRESS"  
PLAN_FILENAME = "CMDAIPLAN.md"
MD_CONTEXT_MAX_FILES = 14
MD_CONTEXT_MAX_CHARS = 12000
LAUNCHER_DIR_NAME = "CMDAI"
SLASH_COMMAND_HINTS: List[Tuple[str, str]] = [
    ("/help", "Show commands"),
    ("/load", "Load model"),
    ("/models", "Show models"),
    ("/ide", "Pick IDE"),
    ("/mode", "Switch mode"),
    ("/m", "Next mode"),
    ("/unload", "Unload model"),
    ("/exit", "Exit app"),
    ("/go", "Execute current plan"),
    ("/swap", "Swap model"),
    ("/pause", "Pause chat"),
    ("/status", "Show status"),
]

# ============ MODE SYSTEM ============
class AppMode:
    CHAT = "chat"
    PLAN = "plan"  
    CODE = "code"
    
    @staticmethod
    def list():
        return [AppMode.CHAT, AppMode.PLAN, AppMode.CODE]
    
    @staticmethod
    def description(mode):
        descriptions = {
            AppMode.CHAT: "Standardowa rozmowa z AI",
            AppMode.PLAN: "Planowanie i architektura projektu",
            AppMode.CODE: "Programowanie - AI tworzy pliki"
        }
        return descriptions.get(mode, "Unknown")

CURRENT_MODE = AppMode.CHAT
MODE_INDICATOR = ""

def get_mode_prompt() -> str:
    """Zwraca prompt zależny od aktualnego trybu"""
    if not loader or not loader.current_model:
        return "> "
    if CURRENT_MODE == AppMode.CHAT:
        return f"{Colors.MODE_CHAT}[CHAT]{Colors.ENDC}> "
    elif CURRENT_MODE == AppMode.PLAN:
        return f"{Colors.MODE_PLAN}[PLAN]{Colors.ENDC}> "
    elif CURRENT_MODE == AppMode.CODE:
        return f"{Colors.MODE_CODE}[CODE]{Colors.ENDC}> "
    return "> "

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    MODE_CHAT = '\033[38;5;82m'    # Jasny zielony
    MODE_PLAN = '\033[38;5;208m'   # Pomarańczowy
    MODE_CODE = '\033[38;5;39m'    # Niebieski
    ACTION_STATUS = '\033[90m'     # Szary dla statusów akcji

HAS_AI_ENGINE = False
LAST_UPDATE_STATUS = None
http_server = None
loader = None
_LLAMA_LOG_CONFIGURED = False
_LLAMA_LOG_CALLBACK = None
TERMINAL_CHAT_HISTORY: List[Dict[str, str]] = []
INPUT_AREA_START_ROW = 1
INPUT_AREA_CLEAR_LINES = 4  # Zmniejszone z 8 do 4 - mniej pustych linii
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STATUS_CALL_RE = re.compile(r"\[\[CALL:STATUS\]\](.*?)\[\[/CALL\]\]", re.IGNORECASE | re.DOTALL)
ACTION_RE = re.compile(r"\[\[ACTION\]\](.*?)\[\[/ACTION\]\]", re.IGNORECASE | re.DOTALL)

# ============ IDE INTEGRATION ============
class IDEIntegration:
    SUPPORTED_IDES = {
        "windsurf": {
            "name": "Windsurf",
            "windows": ["windsurf.exe", "Windsurf.exe"],
            "linux": ["windsurf"],
            "darwin": ["Windsurf.app"],
            "protocol": "windsurf://",
            "cli_args": "--goto {file}:{line}:{col}"
        },
        "vscode": {
            "name": "Visual Studio Code",
            "windows": ["code.exe", "code.cmd"],
            "linux": ["code", "code-oss"],
            "darwin": ["Visual Studio Code.app", "Code.app"],
            "protocol": "vscode://",
            "cli_args": "--goto {file}:{line}:{col}"
        },
        "cursor": {
            "name": "Cursor",
            "windows": ["cursor.exe", "Cursor.exe"],
            "linux": ["cursor"],
            "darwin": ["Cursor.app"],
            "protocol": "cursor://",
            "cli_args": "--goto {file}:{line}:{col}"
        },
        "pycharm": {
            "name": "PyCharm",
            "windows": ["pycharm64.exe", "pycharm.exe"],
            "linux": ["pycharm", "charm"],
            "darwin": ["PyCharm.app", "PyCharm CE.app"],
            "protocol": "pycharm://",
            "cli_args": "--line {line} {file}"
        },
        "sublime": {
            "name": "Sublime Text",
            "windows": ["subl.exe", "sublime_text.exe"],
            "linux": ["subl", "sublime_text"],
            "darwin": ["Sublime Text.app"],
            "protocol": "subl://",
            "cli_args": "{file}:{line}:{col}"
        },
        "vim": {
            "name": "Vim/Neovim",
            "windows": ["vim.exe", "nvim.exe"],
            "linux": ["vim", "nvim"],
            "darwin": ["vim", "nvim"],
            "protocol": "vim://",
            "cli_args": "+{line} {file}"
        }
    }
    
    def __init__(self):
        self.detected_ides = []
        self.active_ide = None
        self.project_root = None
        self._detect_ides()
        self._find_project_root()
    
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
                    self.detected_ides.append({
                        "id": ide_id,
                        "name": ide_info["name"],
                        "path": path,
                        "protocol": ide_info["protocol"],
                        "cli_args": ide_info["cli_args"]
                    })
                    if not self.active_ide:
                        self.active_ide = self.detected_ides[-1]
                    break
    
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
                os.path.expanduser("~\\AppData\\Local\\Programs")
            ]
            for pf in program_files:
                if pf:
                    paths.extend([
                        os.path.join(pf, "Microsoft VS Code", "bin", name),
                        os.path.join(pf, "Cursor", name),
                        os.path.join(pf, "JetBrains", "PyCharm", "bin", name),
                        os.path.join(pf, "Sublime Text", name)
                    ])
        elif system == "Darwin":
            applications = "/Applications"
            paths.extend([
                os.path.join(applications, f"{name}.app", "Contents", "MacOS", name),
                os.path.join(os.path.expanduser("~/Applications"), f"{name}.app", "Contents", "MacOS", name)
            ])
        else:
            paths.extend([
                f"/usr/bin/{name}",
                f"/usr/local/bin/{name}",
                f"/opt/{name}/bin/{name}",
                os.path.expanduser(f"~/.local/bin/{name}")
            ])
        
        return paths
    
    def _find_project_root(self):
        current = os.getcwd()
        markers = [".git", ".vscode", "pyproject.toml", "setup.py", "package.json", ".idea", "requirements.txt"]
        
        while current != os.path.dirname(current):
            for marker in markers:
                if os.path.exists(os.path.join(current, marker)):
                    self.project_root = current
                    return
            current = os.path.dirname(current)
        
        self.project_root = os.getcwd()
    
    def list_ides(self):
        return self.detected_ides

    def refresh_ides(self):
        previous_active = self.active_ide["id"] if self.active_ide else None
        self.detected_ides = []
        self.active_ide = None
        self._detect_ides()
        if previous_active:
            self.set_active(previous_active)
        return self.detected_ides
    
    def set_active(self, ide_id):
        for ide in self.detected_ides:
            if ide["id"] == ide_id:
                self.active_ide = ide
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
            cli_tmpl = self.active_ide.get("cli_args", "{file}")
            args = [self.active_ide["path"]]
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
                full_cmd = subprocess.list2cmdline(args)
                subprocess.Popen(full_cmd, shell=True)
            else:
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            return {"success": True, "ide": self.active_ide["name"], "file": filepath}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def create_file(self, filepath, content=""):
        if not os.path.isabs(filepath) and self.project_root:
            filepath = os.path.join(self.project_root, filepath)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return self.open_file(filepath)
    
    def get_status(self):
        return {
            "active": self.active_ide["name"] if self.active_ide else None,
            "available": [ide["name"] for ide in self.detected_ides],
            "project_root": self.project_root
        }

    def detect_running_ide_ids(self) -> List[str]:
        if not self.detected_ides:
            return []

        running_names = set()
        try:
            if os.name == "nt":
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
        for ide in self.detected_ides:
            exe = os.path.basename((ide.get("path") or "")).lower()
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
            elif ide.get("id") == "sublime":
                candidates.update({"sublime_text.exe", "subl.exe", "sublime_text"})
            elif ide.get("id") == "vim":
                candidates.update({"vim.exe", "nvim.exe", "vim", "nvim"})

            if any(c in running_names for c in candidates):
                running_ids.append(ide["id"])
        return running_ids

ide_integration = None

# ============ CODE FILE MANAGER ============
class CodeFileManager:
    """Zarządza plikami kodu tworzonymi przez AI w trybie CODE"""

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

    def __init__(self):
        self.project_root = os.getcwd()
        self.created_files = []
        self.current_plan = None
        self.last_applied_changes: List[Dict[str, Any]] = []

    def set_plan(self, plan_content: str):
        """Ustawia plan z trybu PLAN do wykorzystania w CODE"""
        self.current_plan = plan_content

    def extract_code_blocks(self, ai_response: str) -> List[Dict[str, Any]]:
        """Wyciąga bloki kodu z odpowiedzi AI"""
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

            code_blocks.append({
                "language": lang,
                "code": code,
                "info": info,
                "start_index": match.start(),
            })

        return code_blocks

    def get_extension(self, language: str) -> str:
        """Zwraca rozszerzenie pliku na podstawie języka"""
        extensions = {
            "python": ".py", "py": ".py",
            "javascript": ".js", "js": ".js",
            "typescript": ".ts", "ts": ".ts",
            "html": ".html", "htm": ".html",
            "css": ".css",
            "json": ".json",
            "markdown": ".md", "md": ".md",
            "bash": ".sh", "shell": ".sh", "sh": ".sh",
            "sql": ".sql",
            "yaml": ".yaml", "yml": ".yaml",
            "dockerfile": ".Dockerfile",
            "docker": ".Dockerfile"
        }
        return extensions.get(language.lower(), ".txt")

    def generate_filename(self, language: str, index: int) -> str:
        """Generuje nazwę pliku na podstawie języka"""
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

        lowered = candidate.lower()
        prefixes = ("file:", "path:", "plik:", "sciezka:", "filename:")
        for prefix in prefixes:
            if lowered.startswith(prefix):
                candidate = candidate[len(prefix):].strip()
                break

        if not candidate:
            return ""

        if " " in candidate:
            first_token = candidate.split()[0]
            if self._looks_like_filepath(first_token):
                candidate = first_token

        while candidate.startswith("./"):
            candidate = candidate[2:]

        candidate = os.path.normpath(candidate).replace("\\", "/")
        if candidate in {"", "."}:
            return ""
        if candidate == ".." or candidate.startswith("../"):
            return ""
        if os.path.isabs(candidate):
            return ""
        return candidate

    def _looks_like_filepath(self, candidate: str) -> bool:
        token = (candidate or "").strip().strip("`").strip("\"'")
        if not token:
            return False
        if "/" in token or "\\" in token:
            return True

        known = {
            "Dockerfile", "Makefile", "CMakeLists.txt",
            ".gitignore", ".editorconfig", ".env",
            "README.md", PLAN_FILENAME, "package.json",
            "pyproject.toml", "requirements.txt", "tsconfig.json"
        }
        if token in known:
            return True

        return bool(re.search(r"\.[a-zA-Z0-9_-]{1,12}$", token))

    def _extract_path_from_info(self, info: str) -> str:
        if not info:
            return ""

        explicit = re.search(r"(?:path|file|filename)\s*[:=]\s*([^\s]+)", info, re.IGNORECASE)
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
        lookback = (text or "")[max(0, block_start - 500):block_start]
        lines = lookback.splitlines()
        scanned_non_empty = 0

        for raw_line in reversed(lines):
            line = (raw_line or "").strip()
            if not line:
                continue

            scanned_non_empty += 1
            if scanned_non_empty > 12:
                break

            if line.startswith("```"):
                continue

            label_match = re.match(r"(?i)^(?:file|plik|path|sciezka|filename)\s*[:=-]\s*(.+)$", line)
            if label_match:
                candidate = self._normalize_relative_path(label_match.group(1))
                if candidate and self._looks_like_filepath(candidate):
                    return candidate
                continue

            candidate_line = re.sub(r"^#{1,6}\s*", "", line)
            candidate_line = re.sub(r"^[-*+]\s*", "", candidate_line)
            candidate = self._normalize_relative_path(candidate_line)
            if candidate and self._looks_like_filepath(candidate):
                return candidate
        return ""

    def extract_file_changes(self, ai_response: str) -> List[Dict[str, Any]]:
        blocks = self.extract_code_blocks(ai_response)
        if not blocks:
            return []

        changes_by_path: Dict[str, Dict[str, Any]] = {}
        for i, block in enumerate(blocks):
            rel_path = self._extract_path_from_info(block.get("info", ""))
            if not rel_path:
                rel_path = self._extract_path_from_prefix(ai_response, block.get("start_index", 0))
            if not rel_path:
                # Ignore unnamed blocks to avoid fake/generated file names.
                continue
            rel_path = self._normalize_relative_path(rel_path)
            if not rel_path:
                continue

            code_text = str(block.get("code", ""))
            if not code_text.strip():
                continue

            change = {
                "relative_path": rel_path,
                "language": block["language"],
                "code": code_text,
            }
            changes_by_path[rel_path] = change

        return list(changes_by_path.values())

    def apply_file_changes(self, changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        applied = []
        root_abs = os.path.abspath(self.project_root)

        for change in changes:
            rel_path = (change or {}).get("relative_path", "").strip()
            code = (change or {}).get("code", "")
            if not rel_path:
                continue

            target_path = os.path.abspath(os.path.join(root_abs, rel_path))
            try:
                if os.path.commonpath([root_abs, target_path]) != root_abs:
                    continue
            except Exception:
                continue

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            existed = os.path.exists(target_path)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(code)

            action = "updated" if existed else "created"
            applied.append({
                "action": action,
                "path": target_path,
                "relative_path": rel_path,
                "language": change.get("language", "txt"),
            })

            if ide_integration and ide_integration.active_ide:
                ide_integration.open_file(target_path)

        self.last_applied_changes = list(applied)
        self.created_files.extend([item["path"] for item in applied])
        return applied

    def save_code_blocks(self, ai_response: str) -> List[str]:
        """Zachowana kompatybilność: zapisuje wykryte pliki i zwraca ścieżki."""
        changes = self.extract_file_changes(ai_response)
        applied = self.apply_file_changes(changes)
        return [item["path"] for item in applied]

    def create_plan_file(self, content: str) -> str:
        """Tworzy plik planu dla trybu PLAN."""
        filepath = os.path.join(self.project_root, PLAN_FILENAME)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            self.current_plan = content
            if ide_integration and ide_integration.active_ide:
                ide_integration.open_file(filepath)

            return filepath
        except Exception as e:
            print(f"Error creating plan file: {e}")
            return ""

    def _iter_markdown_files(self):
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d.lower() not in self.MD_SKIP_DIRS]
            for filename in files:
                if filename.lower().endswith(".md"):
                    yield os.path.join(root, filename)

    def load_markdown_context(self, max_files: int = MD_CONTEXT_MAX_FILES, max_chars: int = MD_CONTEXT_MAX_CHARS) -> str:
        md_files = list(self._iter_markdown_files())
        if not md_files:
            return ""

        def _priority(path: str):
            rel = os.path.relpath(path, self.project_root).replace("\\", "/")
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

            rel = os.path.relpath(path, self.project_root).replace("\\", "/")
            chunk = content[:min(3000, remaining)]
            sections.append(f"### {rel}\n{chunk}")
            remaining -= len(chunk)

        return "\n\n".join(sections)

    def load_project_file_index(self, max_files: int = 160, max_chars: int = 7000) -> str:
        rows: List[str] = []
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if d.lower() not in self.MD_SKIP_DIRS]
            for filename in files:
                rel = os.path.relpath(os.path.join(root, filename), self.project_root).replace("\\", "/")
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
        if filename.endswith('.gguf') and 'mmproj' not in filename.lower():
            alias_name = filename[:-5]
            aliases[alias_name] = {
                "url": f"file://{filename}",
                "filename": filename
            }
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
    max_version = KNOWN_UNSUPPORTED_ARCH_BY_MAX_VERSION.get((arch or "").strip().lower())
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

    if os.environ.get("RUN_AI_VERBOSE_LLAMA", "").strip().lower() in {"1", "true", "yes"}:
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
            if self.buffer[i] == '<' and i + 1 < n and self.buffer[i+1] == '|':
                tag_start = i
                i += 2  
                tag_name = ""
                
                while i < n and self.buffer[i] != '|' and self.buffer[i] != '>':
                    tag_name += self.buffer[i]
                    i += 1
                
                if i < n and self.buffer[i] == '|' and i + 1 < n and self.buffer[i+1] == '>':
                    i += 2  
                    tag = f"<|{tag_name}|>"
                    
                    if tag_name.startswith("im_start") or tag_name.startswith("system") or tag_name.startswith("user"):
                        self.in_tag = True
                        self.current_tag = tag_name
                        continue
                    elif tag_name.startswith("/im_start") or tag_name.startswith("/system") or tag_name.startswith("/user"):
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
                    models.append({
                        "name": f,
                        "path": full,
                        "size_mb": round(size / (1024 * 1024), 2)
                    })
                except:
                    pass

        return sorted(models, key=lambda x: x["name"])

    def _find_mmproj(self, model_name):
        base = os.path.splitext(model_name)[0]
        candidates = [
            f"{base}.mmproj.gguf",
            "mmproj.gguf"
        ]

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

        is_vision = any(x in model_name.lower() for x in [
            "llava", "bakllava", "cogvlm", "minicpm-v", "qwen3-vl", "qwen-vl"
        ])

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
                    vram_mb = int(torch.cuda.get_device_properties(0).total_memory // (1024 * 1024))
                    try_gpu = vram_mb >= max(4096, int(os.path.getsize(model_path) / (1024*1024) * 0.75))
            except Exception:
                try_gpu = False

        load_started = time.time()
        result = self._load_model_stable(
            model_path,
            try_gpu=try_gpu,
            timeout_s=timeout_s,
            mmproj=mmproj
        )

        if result["ok"]:
            self.model = result["model"]
            self.current_model = model_name
            load_elapsed = max(0.0, time.time() - load_started)
            print(_format_command_chip(f"load [{self.current_model}] {_format_elapsed_label(load_elapsed)}"))
            return True

        print("Load failed:", result["error"])
        return False

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7,
                top_p: float = 0.9, stream: bool = False, **kwargs) -> Union[str, None]:
        if not self.model:
            raise ValueError("No model loaded. Use 'load' first.")

        try:
            if stream:
                return self._stream_response(prompt, max_tokens, temperature, top_p, **kwargs)
            response = self.model(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                **kwargs,
            )
            return response['choices'][0]['text'].strip()
        except Exception as e:
            print(f"Error while generating response: {e}")
            raise

    def _stream_response(self, prompt: str, max_tokens: int, temperature: float,
                        top_p: float, **kwargs) -> str:
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
                token = chunk['choices'][0]['text']
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

    def download_model(self, source: str, output_name: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
        import urllib.request
        import urllib.error
        
        if not os.path.exists(self.models_dir):
            os.makedirs(self.models_dir)
        
        if source.startswith(('http://', 'https://', 'file://')):
            url = source
            filename = output_name or os.path.basename(urlparse(source).path) or "model.gguf"
        else:
            filename = output_name or source
            if not filename.endswith('.gguf'):
                filename += '.gguf'
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


# ============ HTTP SERVER HANDLER ============
class OllamaAPIHandler(http.server.BaseHTTPRequestHandler):
    
    def _set_headers(self, status_code=200, content_type='application/json'):
        self.send_response(status_code)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)
        self.wfile.write(b'')

    def do_GET(self):
        try:
            parsed_path = urlparse(self.path)
            path = parsed_path.path

            if path in ('', '/'):
                self._set_headers(200)
                self.wfile.write(json.dumps({
                    "service": "CMDAI",
                    "host": f"http://localhost:{HTTP_PORT}",
                    "loaded_model": loader.current_model if loader else None,
                    "mode": CURRENT_MODE,
                    "ide": ide_integration.get_status() if ide_integration else None
                }).encode())

            elif path in ('/api/tags', '/api/tags/', '/tags', '/tags/'):
                self._handle_tags()

            elif path.startswith('/api/show') or path.startswith('/show'):
                self._handle_show_model()

            elif path in ('/api/version', '/api/version/', '/version', '/version/'):
                self._handle_version()

            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({
                    "error": f"Endpoint {path} does not exist"
                }).encode())

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({
                "error": f"Internal server error: {str(e)}"
            }).encode())

    def do_POST(self):
        try:
            parsed_path = urlparse(self.path)
            path = parsed_path.path

            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(post_data) if post_data else {}
            except json.JSONDecodeError:
                data = {}

            if path in ('/api/generate', '/generate'):
                self._handle_generate(data)

            elif path in ('/api/chat', '/chat'):
                self._handle_chat(data)

            elif path.startswith('/api/pull') or path.startswith('/pull'):
                self._handle_pull(data)

            elif path.startswith('/api/copy') or path.startswith('/copy'):
                self._handle_copy(data)

            elif path in ('', '/'):
                if isinstance(data, dict) and data.get('messages'):
                    self._handle_chat(data)
                elif isinstance(data, dict) and data.get('prompt'):
                    self._handle_generate(data)
                else:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({
                        "error": "Error: for POST / provide 'prompt' or 'messages'."
                    }).encode())

            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({
                    "error": f"Endpoint {path} does not exist"
                }).encode())

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({
                "error": f"Internal server error: {str(e)}"
            }).encode())

    def _handle_tags(self):
        models = loader.list_models()
        
        response = {
            "models": [
                {
                    "name": m['name'],
                    "modified_at": datetime.now().isoformat() + "Z",
                    "size": int(m['size_mb'] * 1024 * 1024),
                    "digest": hashlib.sha256(m['name'].encode()).hexdigest(),
                    "details": {
                        "format": "gguf",
                        "family": "llama",
                        "families": ["llama"],
                        "parameter_size": "7B",
                        "quantization_level": "Q4_0"
                    }
                }
                for m in models
            ]
        }
        
        self._set_headers(200)
        self.wfile.write(json.dumps(response, indent=2).encode())
    
    def _handle_show_model(self):
        if not loader.current_model:
            self._set_headers(400)
            self.wfile.write(json.dumps({
                "error": "No model loaded"
            }).encode())
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
                "quantization_level": "Q4_0"
            }
        }

        self._set_headers(200)
        self.wfile.write(json.dumps(model_info, indent=2).encode())

    def _handle_version(self):
        version_info = {
            "version": VERSION,
            "compatibility": {
                "ollama": "0.1.0",
                "llama.cpp": "master"
            }
        }
        
        self._set_headers(200)
        self.wfile.write(json.dumps(version_info, indent=2).encode())
    
    def _handle_generate(self, data: Dict):
        if not loader.current_model:
            self._set_headers(400)
            self.wfile.write(json.dumps({
                "error": "No model loaded. Use 'load' in terminal."
            }).encode())
            return

        prompt = data.get('prompt', '')
        model = data.get('model', '')
        stream = data.get('stream', False)
        options = data.get('options', {})

        max_tokens = options.get('num_predict', 512)
        temperature = options.get('temperature', 0.7)
        top_p = options.get('top_p', 0.9)

        if model and model != loader.current_model:
            models = loader.list_models()
            found = None
            for m in models:
                if m['name'] == model:
                    found = m
                    break

            if found:
                print(f"[API] Loading model on demand: {model}")
                success = loader.load(found['name'])
                if not success:
                    self._set_headers(500)
                    self.wfile.write(json.dumps({
                        "error": f"Failed to load model: {model}"
                    }).encode())
                    return
            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({
                    "error": f"Model '{model}' not found. Available models: {[m['name'] for m in models]}"
                }).encode())
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
                    stream=False
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
                    "eval_duration": 0
                }

                self._set_headers(200)
                self.wfile.write(json.dumps(result, indent=2).encode())

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({
                "error": f"Error while generating response: {str(e)}"
            }).encode())

    def _stream_generate(self, prompt: str, max_tokens: int, temperature: float, top_p: float):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        
        try:
            response_generator = loader.generate(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stream=True
            )
            
            full_response = ""
            
            for token in response_generator:
                full_response += token
                
                response_obj = {
                    "model": loader.current_model,
                    "created_at": datetime.now().isoformat() + "Z",
                    "response": token,
                    "done": False
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
                "eval_duration": 0
            }
            
            self.wfile.write(f"data: {json.dumps(final_response)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            
        except Exception as e:
            error_response = {"error": str(e)}
            self.wfile.write(f"data: {json.dumps(error_response)}\n\n".encode())

    def _handle_chat(self, data: Dict):
        if not loader.current_model:
            self._set_headers(400)
            self.wfile.write(json.dumps({
                "error": "No model loaded. Use 'load' in terminal."
            }).encode())
            return

        try:
            messages = data.get('messages', [])
            stream = data.get('stream', False)
            options = data.get('options', {})

            max_tokens = options.get('num_predict', 512)
            temperature = options.get('temperature', 0.7)
            top_p = options.get('top_p', 0.9)

            prompt = self._format_chat_messages(messages)

            if stream:
                self._stream_chat_response(messages, prompt, max_tokens, temperature, top_p)
            else:
                response = loader.generate(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    stream=False
                )

                response_obj = {
                    "model": loader.current_model,
                    "created_at": datetime.now().isoformat() + "Z",
                    "message": {
                        "role": "assistant",
                        "content": response
                    },
                    "done": True,
                    "total_duration": 0,
                    "load_duration": 0,
                    "prompt_eval_count": len(prompt.split()),
                    "eval_count": len(response.split()),
                    "eval_duration": 0
                }

                self._set_headers(200)
                self.wfile.write(json.dumps(response_obj, indent=2).encode())

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({
                "error": f"Error while generating response: {str(e)}"
            }).encode())

    def _format_chat_messages(self, messages: List[Dict]) -> str:
        formatted = []
        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            
            if role == 'system':
                formatted.append(f"<|system|>\n{content}\n<|end|>")
            elif role == 'user':
                formatted.append(f"<|user|>\n{content}\n<|end|>")
            elif role == 'assistant':
                formatted.append(f"<|assistant|>\n{content}\n<|end|>")
        
        return "\n".join(formatted) + "\n<|assistant|>\n"

    def _stream_chat_response(self, messages: List[Dict], prompt: str, max_tokens: int, 
                            temperature: float, top_p: float):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        
        try:
            if not loader or not loader.model:
                self.wfile.write(b'data: {"error": "No model loaded"}\n\n')
                return
            
            full_response = ""
            for token in loader.generate(prompt, max_tokens, temperature, top_p, stream=True):
                if token:
                    full_response += token
                    response_data = {
                        "model": loader.current_model or "unknown",
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "message": {
                            "role": "assistant",
                            "content": full_response
                        },
                        "done": False
                    }
                    self.wfile.write(f'data: {json.dumps(response_data)}\n\n'.encode())
                    self.wfile.flush()
            
            final_data = {
                "model": loader.current_model or "unknown",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "message": {
                    "role": "assistant",
                    "content": full_response
                },
                "done": True,
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": 0,
                "prompt_eval_duration": 0,
                "eval_count": len(full_response.split()) if full_response else 0,
                "eval_duration": 0
            }
            self.wfile.write(f'data: {json.dumps(final_data)}\n\n'.encode())
            self.wfile.flush()
            
        except Exception as e:
            error_data = {"error": f"Streaming error: {str(e)}"}
            self.wfile.write(f'data: {json.dumps(error_data)}\n\n'.encode())
            self.wfile.flush()
        
    def _handle_pull(self, data: Dict):
        source = str(data.get("name") or data.get("model") or data.get("source") or "").strip()
        output_name = data.get("filename") or data.get("output")
        auto_load = bool(data.get("load", False))

        if not source:
            self._set_headers(400)
            self.wfile.write(json.dumps({
                "error": "Missing field: provide 'name' (alias/URL)"
            }).encode())
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
            self.wfile.write(json.dumps({
                "status": "success",
                "name": result["name"],
                "path": result["path"],
                "size": result["size"],
                "sha256": result["sha256"],
                "loaded": loaded,
            }).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({
                "error": f"Failed to download model: {e}"
            }).encode())

    def _handle_copy(self, data: Dict):
        self._set_headers(200)
        self.wfile.write(json.dumps({
            "status": "success"
        }).encode())


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def start_http_server(port: int = HTTP_PORT) -> socketserver.TCPServer:
    httpd = None
    selected_port = port
    last_error = None

    for candidate_port in range(port, port + 10):
        try:
            server_address = ('', candidate_port)
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
    os.system('cls' if os.name == 'nt' else 'clear')
    # Reset scroll region after full clear.
    sys.stdout.write("\x1b[r")
    sys.stdout.flush()


# ============ INPUT HANDLING ============

def _read_terminal_line(prompt: str) -> str:
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok):
        return input(prompt)

    if os.name != "nt":
        return input(prompt)

    try:
        import msvcrt
    except Exception:
        return input(prompt)

    buffer: List[str] = []
    selected_idx = 0
    last_width = get_terminal_width()
    last_height = get_terminal_height()
    prompt_plain = _strip_ansi(prompt)
    input_row = max(1, int(INPUT_AREA_START_ROW)) + 1
    fixed_lines = max(6, min(10, int(INPUT_AREA_CLEAR_LINES or 8)))
    anchor_dirty = True

    def _buffer_text() -> str:
        return "".join(buffer)

    def _get_slash_matches() -> List[Tuple[str, str]]:
        text = _buffer_text()
        if not text.startswith("/"):
            return []

        lower_text = text.lower()

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
                size_str = f"{size_mb/1024:.1f} GB" if size_mb > 1024 else f"{size_mb:.0f} MB"
                matches.append((f"/load {name}", f"{name} ({size_str})"))
            return matches[:8]

        # Dynamic picker for /load model names.
        if lower_text == "/load":
            model_matches = _load_model_matches("")
            if model_matches:
                return model_matches
        if lower_text.startswith("/load "):
            model_matches = _load_model_matches(text[6:])
            if model_matches:
                return model_matches

        if " " in text:
            return []

        prefix = lower_text
        matches: List[Tuple[str, str]] = []
        current_loaded = loader.current_model if (loader and loader.current_model) else "none"
        for cmd, desc in SLASH_COMMAND_HINTS:
            if not cmd.startswith(prefix):
                continue
            if cmd == "/load":
                matches.append((cmd, f"Load model (current: {current_loaded})"))
            else:
                matches.append((cmd, desc))
        return matches[:8]

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

        top_bar = f"\033[90m{hz * width}\033[0m"
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

        sys.stdout.write("\r\x1b[2K" + top_bar + "\n")
        sys.stdout.write("\r\x1b[2K" + input_text + "\n")
        sys.stdout.write("\r\x1b[2K" + bottom_bar)

        rendered_lines = 3
        if matches:
            max_rows = max(1, fixed_lines - 5)
            for idx, (cmd, desc) in enumerate(matches[:max_rows]):
                visible_desc = _visible_tail(desc, max(8, width - 24))
                if idx == selected_idx:
                    line = f"\x1b[96m{marker_selected} {cmd:<16}\x1b[0m {visible_desc}"
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
        _prepare_top_input_area(lines=fixed_lines)
        if len(prompt_plain) <= width:
            prompt_line = prompt
        else:
            prompt_line = _visible_tail(prompt_plain, width)
        sys.stdout.write("\r\x1b[2K" + f"\033[90m{hz * width}\033[0m" + "\n")
        sys.stdout.write("\r\x1b[2K" + prompt_line + "\n")
        sys.stdout.write("\r\x1b[2K" + f"\033[90m{hz * width}\033[0m")
        for _ in range(max(0, fixed_lines - 3)):
            sys.stdout.write("\n\r\x1b[2K")
        sys.stdout.write(f"\x1b[{_log_output_row()};1H")
        sys.stdout.flush()

    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()
    try:
        _render_line()

        while True:
            if get_terminal_width() != last_width or get_terminal_height() != last_height:
                _render_line()
            if not msvcrt.kbhit():
                time.sleep(0.03)
                continue

            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                matches = _get_slash_matches()
                current_text = _buffer_text()
                if matches and current_text.startswith("/") and (" " not in current_text or current_text.lower().startswith("/load")):
                    chosen_cmd = matches[selected_idx][0]
                    buffer.clear()
                    buffer.extend(list(chosen_cmd))
                final_text = _buffer_text()
                _render_line()
                out_row = max(1, int(INPUT_AREA_START_ROW) - 2)
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
                if matches and current_text.startswith("/") and (" " not in current_text or current_text.lower().startswith("/load")):
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
                    if next_ch == "\x0f":
                        # Shift+Tab - also switch mode
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


def _visible_tail(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    value = text or ""
    if len(value) <= max_len:
        return value
    if max_len <= 3:
        return value[-max_len:]
    return "..." + value[-(max_len - 3):]


def _supports_unicode_ui() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "─┌└│▶".encode(encoding)
        return True
    except Exception:
        return False


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

    sys.stdout.write(f"\x1b[1;{log_bottom}r")
    sys.stdout.write("\x1b[1;1H")
    for i in range(log_bottom):
        sys.stdout.write("\x1b[2K")
        if i < log_bottom - 1:
            sys.stdout.write("\n")
    sys.stdout.write(f"\x1b[{_log_output_row()};1H")
    sys.stdout.flush()


def _should_pin_input_top() -> bool:
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
    row = max(1, term_h - lines + 1)
    INPUT_AREA_START_ROW = row
    INPUT_AREA_CLEAR_LINES = lines

    # Keep logs above the input area in a dedicated scroll region.
    log_bottom = max(1, row - 2)  # one visual gap above input area
    sys.stdout.write(f"\x1b[1;{log_bottom}r")

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


def _extract_plan_content(text: str) -> str:
    payload = (text or "").strip()
    if not payload:
        return ""

    # If model returned single fenced block, use block content directly.
    fence_matches = re.findall(r"```(?:markdown|md)?\n(.*?)```", payload, flags=re.IGNORECASE | re.DOTALL)
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
        payload = payload[heading_match.start():].strip()

    payload = re.sub(r"\n{3,}", "\n\n", payload)
    return payload.strip()


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


def _read_arrow_choice(title: str, options: List[Tuple[str, str]], default_idx: int = 0) -> str:
    if not options:
        return ""

    idx = max(0, min(default_idx, len(options) - 1))
    stdin_ok = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    stdout_ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not (stdin_ok and stdout_ok) or os.name != "nt":
        raw = input(f"{title} [{'/'.join(k for k, _ in options)}]: ").strip().lower()
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
        import msvcrt  # type: ignore
    except Exception:
        raw = input(f"{title} [{'/'.join(k for k, _ in options)}]: ").strip().lower()
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
        # Wyświetl tylko jedną linię separatora (nie dwie)
        sys.stdout.write("\r\x1b[2K" + f"\033[90m{hz * width}\033[0m")
        sys.stdout.write(f"\x1b[{_log_output_row()};1H")
        sys.stdout.flush()

    def _render() -> None:
        nonlocal panel_lines
        footer_lines = max(6, min(10, int(INPUT_AREA_CLEAR_LINES or 8)))
        _prepare_top_input_area(lines=footer_lines)

        width = max(32, get_terminal_width())
        use_uni = _supports_unicode_ui()
        hz = "─" if use_uni else "-"
        marker = "▶" if use_uni else ">"

        lines: List[str] = []
        lines.append(f"\033[90m{hz * width}\033[0m")
        lines.append(_visible_tail(str(title), width))
        lines.append(f"\033[90m{hz * width}\033[0m")
        max_rows = max(1, footer_lines - 5)
        for i, (_, label) in enumerate(options[:max_rows]):
            if i == idx:
                lines.append(f"\x1b[96m{marker} {label}\x1b[0m")
            else:
                lines.append(f"\033[90m  {label}\033[0m")
        if len(options) > max_rows:
            lines.append(f"\033[90m  ... ({len(options) - max_rows} more)\033[0m")
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


# ============ SEPARATOR LINE - WHITE ============

def _get_separator_line() -> str:
    """Zwraca białą linię separatora"""
    term_width = get_terminal_width()
    return _ui_line_char() * term_width


# ============ WELCOME ============

def print_welcome():
    global INPUT_AREA_START_ROW, INPUT_AREA_CLEAR_LINES
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
        print(f"\n{sep}")

        for line in ascii_title.splitlines():
            if line.strip() == "":
                print()
            else:
                padding = (terminal_width - len(line)) // 2
                print(" " * max(padding, 0) + line)

        print(f"{sep}")
        print("Type '/help' for commands.")
        INPUT_AREA_START_ROW = 12
        INPUT_AREA_CLEAR_LINES = min(10, max(6, get_terminal_height() - INPUT_AREA_START_ROW - 2))
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
        for line in fallback_title.splitlines():
            if line.strip():
                padding = max((terminal_width - len(line)) // 2, 0)
                print(" " * padding + line)
            else:
                print()
        print(f"{ascii_sep}")
        print("Type '/help' for commands.")
        INPUT_AREA_START_ROW = 10
        INPUT_AREA_CLEAR_LINES = min(10, max(6, get_terminal_height() - INPUT_AREA_START_ROW - 2))

def _should_show_welcome() -> bool:
    value = os.environ.get("RUN_AI_SHOW_WELCOME", "1").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _ensure_windows_user_path_contains(path_entry: str) -> bool:
    if os.name != "nt" or not winreg or not path_entry:
        return False

    try:
        env_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
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
        # Refresh current process PATH for immediate use without dropping system entries.
        process_parts = [p.strip() for p in os.environ.get("PATH", "").split(";") if p.strip()]
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
            f"set \"CMDAI_HOME={script_dir}\"\n"
            "pushd \"%CMDAI_HOME%\" >nul 2>&1\n"
            "if \"%~1\"==\"\" (\n"
            "  py -3 \"%CMDAI_HOME%\\run.py\" launch\n"
            ") else (\n"
            "  py -3 \"%CMDAI_HOME%\\run.py\" %*\n"
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


# ============ HELP - ORIGINAL WITH MODE ============

def _print_help_section(title: str, rows: List[Tuple[str, str]]) -> None:
    if not rows:
        return
    print(f"\n{title}")
    print(_ui_line_char() * 60)
    command_width = max(len(cmd) for cmd, _ in rows)
    for command, description in rows:
        print(f"  {command.ljust(command_width)}  {description}")


def show_help():
    term_width = shutil.get_terminal_size().columns
    
    if term_width < 100:
        term_width = 100
    
    # 3 kolumny: MODELS, CHAT, MODE
    col_width = (term_width - 6) // 3
    sep_width = 2
    
    sep = _get_separator_line()
    print(f"\n{sep}")
    print(f"{'HELP':^{term_width}}")
    print(f"{sep}")
    
    # Nagłówki 3 kolumn
    models_header = f"{'MODELS':^{col_width}}"
    chat_header = f"{'CHAT':^{col_width}}"
    mode_header = f"{'MODE':^{col_width}}"
    print(f"\n{models_header}{' ' * sep_width}{chat_header}{' ' * sep_width}{mode_header}")
    print(_ui_line_char() * term_width)
    
    # Dane dla każdej kolumny
    models_cmds = [
        ("/models", "List local GGUF models"),
        ("/load", "Choose model from list"),
        ("/load <name>", "Load specific model"),
        ("/swap [name]", "Swap model and keep chat history"),
        ("/catalog", "Show downloadable aliases"),
        ("/download <url>", "Download model"),
        ("/pull <url>", "Alias for download"),
        ("/unload", "Unload model from RAM")
    ]
    
    chat_cmds = [
        ("/ai", "Start chat mode"),
        ("/chat <text>", "Send message"),
        ("/go", "Run implementation from plan"),
        ("/pause", "Pause/resume chat"),
        ("(plain text)", "Send as chat message"),
        ("/ide list", "List detected IDEs"),
        ("/ide use <id>", "Switch active IDE"),
        ("Tab", "Switch mode"),
        ("", "")
    ]
    
    mode_cmds = [
        (f"{Colors.MODE_CHAT}chat{Colors.ENDC}", "Standard chat mode"),
        (f"{Colors.MODE_PLAN}plan{Colors.ENDC}", f"Creates {PLAN_FILENAME}"),
        (f"{Colors.MODE_CODE}code{Colors.ENDC}", "Proposes file changes"),
        ("", ""),
        ("", ""),
        ("", ""),
        ("", ""),
        ("", "")
    ]
    
    # Wyświetl 3 kolumny równolegle
    max_rows = max(len(models_cmds), len(chat_cmds), len(mode_cmds))
    for i in range(max_rows):
        model_cmd = models_cmds[i] if i < len(models_cmds) else ("", "")
        chat_cmd = chat_cmds[i] if i < len(chat_cmds) else ("", "")
        mode_cmd = mode_cmds[i] if i < len(mode_cmds) else ("", "")
        
        if model_cmd[0]:
            left = f"{model_cmd[0]:<16} {model_cmd[1]}"
        else:
            left = ""
        left_formatted = f"{left:<{col_width}}"
        
        if chat_cmd[0]:
            middle = f"{chat_cmd[0]:<16} {chat_cmd[1]}"
        else:
            middle = ""
        middle_formatted = f"{middle:<{col_width}}"
        
        if mode_cmd[0]:
            right = f"{mode_cmd[0]:<12} {mode_cmd[1]}"
        else:
            right = ""
        right_formatted = f"{right:<{col_width}}"
        
        print(f"{left_formatted}{' ' * sep_width}{middle_formatted}{' ' * sep_width}{right_formatted}")
    
    # Druga sekcja: SYSTEM, SERVER, SHORTCUTS
    print(f"\n{'SYSTEM':^{col_width}}{' ' * sep_width}{'SERVER':^{col_width}}{' ' * sep_width}{'SHORTCUTS':^{col_width}}")
    print(_ui_line_char() * term_width)
    
    system_cmds = [
        ("/install-launcher", "Install CMDAI global command"),
        ("/status", "Show app status"),
        ("/version", "Show llama-cpp version"),
        ("/clear", "Clear terminal"),
        ("/help", "Show this help"),
        ("/exit", "Exit application")
    ]
    if not HAS_AI_ENGINE:
        system_cmds.insert(0, ("/install", "Install AI engine"))
    else:
        system_cmds.insert(0, ("/update", "Update llama-cpp"))
    
    server_cmds = [
        (f"http://localhost:{HTTP_PORT}", "API endpoint"),
        ("GET /tags", "List models"),
        ("POST /generate", "Generate text"),
        ("POST /chat", "Chat endpoint"),
        ("POST /pull", "Download model")
    ]
    
    shortcut_cmds = [
        ("Tab", "Switch mode"),
        ("Esc", "Cancel current input"),
        ("/unload", "Unload model"),
        ("/exit", "Exit app"),
        ("/pause", "Pause chat"),
        ("/swap", "Swap model"),
        ("/mode", "Change mode"),
        ("Enter", "Accept PLAN/CODE output")
    ]
    
    max_rows2 = max(len(system_cmds), len(server_cmds), len(shortcut_cmds))
    for i in range(max_rows2):
        sys_cmd = system_cmds[i] if i < len(system_cmds) else ("", "")
        srv_cmd = server_cmds[i] if i < len(server_cmds) else ("", "")
        sh_cmd = shortcut_cmds[i] if i < len(shortcut_cmds) else ("", "")
        
        if sys_cmd[0]:
            left = f"{sys_cmd[0]:<16} {sys_cmd[1]}"
        else:
            left = ""
        left_formatted = f"{left:<{col_width}}"
        
        if srv_cmd[0]:
            middle = f"{srv_cmd[0]:<16} {srv_cmd[1]}"
        else:
            middle = ""
        middle_formatted = f"{middle:<{col_width}}"
        
        if sh_cmd[0]:
            right = f"{sh_cmd[0]:<12} {sh_cmd[1]}"
        else:
            right = ""
        right_formatted = f"{right:<{col_width}}"
        
        print(f"{left_formatted}{' ' * sep_width}{middle_formatted}{' ' * sep_width}{right_formatted}")
    
    print(f"{sep}")

    if LAST_UPDATE_STATUS is not None:
        print(f"\n   Update status: {LAST_UPDATE_STATUS}")


def show_mode_help():
    print("\nMODE HELP")
    print(_ui_line_char() * 60)
    print("  Prompt format: > (before model load), [MODE]> (after model load)")
    print("  Modes:")
    print("    chat  - standard conversation")
    print(f"    plan  - generates {PLAN_FILENAME} (requires Enter to accept)")
    print("    code  - proposes file changes (requires Enter to accept)")
    print("  Commands:")
    print("    /mode chat|plan|code|next")
    print("    /mode chat | /mode plan | /mode code")
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
        size_mb = model['size_mb']
        if size_mb > 1024:
            size_str = f"{size_mb/1024:.1f} GB"
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
        size_str = f"{size_mb/1024:.1f} GB" if size_mb > 1024 else f"{size_mb:.0f} MB"
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

    mode_color = Colors.MODE_CHAT if CURRENT_MODE == AppMode.CHAT else (Colors.MODE_PLAN if CURRENT_MODE == AppMode.PLAN else Colors.MODE_CODE)
    
    print(f"\n  Mode: {mode_color}{CURRENT_MODE.upper()}{Colors.ENDC}")
    
    print("\nSYSTEM:")
    print(f"  OS: {sys.platform}")
    print(f"  Python: {sys.version.split()[0]}")

    print("\nMODEL:")
    if loader and loader.current_model:
        print(f"  LOADED: {loader.current_model}")
        if hasattr(loader, 'model') and loader.model:
            print(f"  Context: {loader.model.n_ctx} tokens")
    else:
        print("  No model loaded")
    
    print("\nIDE INTEGRATION:")
    if ide_integration:
        status = ide_integration.get_status()
        print(f"  Active: {status['active'] or 'None'}")
        print(f"  Available: {', '.join(status['available']) or 'None detected'}")
    
    print(f"\n{sep}")


# ============ PROMPT BUILDERS ============

def _build_chat_prompt(user_text: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    lines = []
    lines.append("<|system|>")
    lines.append("You are a helpful AI assistant. Respond clearly and concisely.")
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


def _build_plan_prompt(user_text: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    lines = []
    lines.append("<|system|>")
    lines.append("You are a project planning expert. Produce a detailed technical plan in Markdown.")
    lines.append("")
    lines.append("IMPORTANT: Show your thinking process using status tags (max 2 words):")
    lines.append("Format: [[STATUS]]Reading files[[/STATUS]] or [[STATUS]]Building plan[[/STATUS]]")
    lines.append("Place status tags WHILE you work, they will appear as progress indicators.")
    lines.append("Example statuses: 'Reading files', 'Analyzing code', 'Creating plan', 'Writing steps'")
    lines.append("")
    lines.append(f"Your response will be saved directly to {PLAN_FILENAME}.")
    lines.append("Do not generate source code. Return plan content only.")
    lines.append("Write concrete sections and subsections, step by step.")
    lines.append("Required sections: Goal, Scope, Assumptions, Implementation plan step by step, Risks, Tests, Next steps.")
    lines.append("The implementation plan must include at least 10 numbered steps.")
    lines.append("Each step must include: what to do, where (file/module), and expected result.")
    lines.append("FORBIDDEN: ASCII/Unicode diagrams, graphs, box drawings, line-art tables.")
    lines.append("Do not return blocks like +---, | ... |, ┌─, └─, architecture arrows, or diagram-only output.")
    lines.append("Return only plan content ready to be written to a .md file.")
    if ide_integration and ide_integration.active_ide:
        lines.append(f"Active IDE: {ide_integration.active_ide.get('name', 'unknown')}")
    if ide_integration and ide_integration.project_root:
        lines.append(f"Project root: {ide_integration.project_root}")

    if code_file_manager:
        project_index = code_file_manager.load_project_file_index(max_files=120, max_chars=4500)
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


def _build_code_prompt(user_text: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    lines = []
    lines.append("<|system|>")
    lines.append("You are an experienced software engineer. Return complete, ready-to-write file contents.")
    lines.append("")
    lines.append("IMPORTANT: Show your thinking process using status tags (max 2 words):")
    lines.append("Format: [[STATUS]]Reading plan[[/STATUS]] or [[STATUS]]Creating files[[/STATUS]]")
    lines.append("Place status tags WHILE you work, they will appear as progress indicators.")
    lines.append("Example statuses: 'Reading plan', 'Analyzing code', 'Writing files', 'Adding features'")
    lines.append("")
    lines.append("Use Markdown code fences only.")
    lines.append("Before EVERY code block, provide a line: File: relative/path/to/file.ext")
    lines.append("If you update a file, return its full new content.")
    lines.append("Do not add long explanations outside file paths and code.")
    lines.append("Do not create code blocks without an explicit File: path.")
    lines.append("If there are no file changes, return exactly: NO_FILE_CHANGES.")
    lines.append("Add only necessary technical comments.")
    if ide_integration and ide_integration.active_ide:
        lines.append(f"Active IDE: {ide_integration.active_ide.get('name', 'unknown')}")
    if ide_integration and ide_integration.project_root:
        lines.append(f"Project root: {ide_integration.project_root}")
    
    if code_file_manager:
        md_context = code_file_manager.load_markdown_context()
        if md_context:
            lines.append("Follow these project .md files:")
            lines.append(md_context)
        project_index = code_file_manager.load_project_file_index(max_files=160, max_chars=6000)
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


def _get_mode_prompt(user_text: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    if CURRENT_MODE == AppMode.PLAN:
        return _build_plan_prompt(user_text, history)
    elif CURRENT_MODE == AppMode.CODE:
        return _build_code_prompt(user_text, history)
    else:
        return _build_chat_prompt(user_text, history)


# ============ GENERATION ============

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
            import msvcrt as _msvcrt  # type: ignore
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
            if desc:
                plain_line = f"{_format_elapsed_label(elapsed)} {desc} {frames[idx % len(frames)]}"
            else:
                plain_line = f"{_format_elapsed_label(elapsed)} {frames[idx % len(frames)]}"
            line = plain_line
            if pinned_input:
                sys.stdout.write(f"\x1b[{_log_output_row()};1H\x1b[2K{line}")
            else:
                sys.stdout.write(f"\r{line}")
            sys.stdout.flush()
            last_len = len(plain_line)
        idx += 1

        if cancel_requested:
            # Do not block UI waiting too long for background cancellation.
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


def _generate_with_live_status(
    loader,
    full_prompt: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    cancel_event: threading.Event,
    show_progress: bool = True
) -> Tuple[str, float]:
    """
    Generuje odpowiedź ze streamingiem i wyświetlaniem statusów na żywo.
    Wychwytuje [[STATUS]]tekst[[/STATUS]] i pokazuje jako szary spinner.
    """
    started = time.time()
    chunks: List[str] = []
    current_status = ""
    accumulated_text = ""
    frames = ["|", "/", "-", "\\"]
    idx = 0
    last_len = 0
    
    pinned_input = _should_pin_input_top()
    
    # Regex do wychwytywania statusów
    status_pattern = re.compile(r'\[\[STATUS\]\](.*?)\[\[/STATUS\]\]', re.DOTALL)
    
    # Wątek do aktualizacji spinnera podczas oczekiwania
    spinner_running = threading.Event()
    spinner_running.set()
    
    def update_spinner():
        """Aktualizuje spinner co 100ms podczas oczekiwania na tokeny"""
        local_idx = 0
        while spinner_running.is_set():
            if show_progress and not chunks:  # Tylko gdy jeszcze nie ma tokenów
                elapsed = max(0.0, time.time() - started)
                plain_line = f"{_format_elapsed_label(elapsed)} {frames[local_idx % len(frames)]}"
                sys.stdout.write(f"\r{plain_line}")
                sys.stdout.flush()
                local_idx += 1
            time.sleep(0.1)
    
    # Uruchom wątek spinnera
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
            # Zatrzymaj wątek spinnera gdy przyjdzie pierwszy token
            if chunks and spinner_running.is_set():
                spinner_running.clear()
            
            if cancel_event.is_set():
                break
                
            chunks.append(token)
            accumulated_text += token
            
            # Szukaj statusów w zgromadzonym tekście
            status_match = status_pattern.search(accumulated_text)
            if status_match:
                new_status = status_match.group(1).strip()
                # Ogranicz do max 2 słów
                words = new_status.split()[:2]
                current_status = " ".join(words)
                
                # Usuń przetworzony status z accumulated_text
                accumulated_text = status_pattern.sub("", accumulated_text, count=1)
            
            # Wyświetl spinner z aktualnym statusem
            if show_progress:
                elapsed = max(0.0, time.time() - started)
                if current_status:
                    # Szary status od AI
                    plain_line = f"{_format_elapsed_label(elapsed)} {Colors.ACTION_STATUS}{current_status}{Colors.ENDC} {frames[idx % len(frames)]}"
                else:
                    plain_line = f"{_format_elapsed_label(elapsed)} {frames[idx % len(frames)]}"
                
                # Zawsze wyświetlaj w normalnym obszarze wyjścia (nie nad inputem)
                sys.stdout.write(f"\r{plain_line}")
                sys.stdout.flush()
                last_len = len(_strip_ansi(plain_line))
            
            idx += 1
        
        # Zatrzymaj wątek spinnera
        spinner_running.clear()
    
    except Exception as e:
        spinner_running.clear()  # Zatrzymaj wątek spinnera
        if show_progress:
            sys.stdout.write("\r" + " " * last_len + "\r")
            sys.stdout.flush()
        raise e
    
    # Wyczyść spinner
    if show_progress:
        sys.stdout.write("\r" + " " * last_len + "\r")
        sys.stdout.flush()
    
    elapsed = time.time() - started
    full_response = "".join(chunks)
    
    # Usuń wszystkie tagi STATUS z finalnej odpowiedzi
    clean_response = status_pattern.sub("", full_response)
    
    return clean_response, elapsed


def _extract_and_display_actions(text: str) -> Tuple[List[str], str]:
    """
    Wyciąga akcje z tekstu, wyświetla je na szaro i zwraca czystą odpowiedź.
    
    Format w odpowiedzi modelu: [[ACTION]]Tworzę plik config.json[[/ACTION]]
    
    Returns:
        Tuple[List[str], str]: (lista akcji, czysty tekst bez akcji)
    """
    actions = []
    clean_text = text
    
    # Znajdź wszystkie akcje
    for match in ACTION_RE.finditer(text):
        action_text = match.group(1).strip()
        if action_text:
            actions.append(action_text)
            # Wyświetl akcję na szaro
            print(f"{Colors.ACTION_STATUS}→ {action_text}{Colors.ENDC}")
    
    # Usuń tagi akcji z tekstu
    clean_text = ACTION_RE.sub("", text).strip()
    
    return actions, clean_text


def send_terminal_prompt(prompt: str, max_tokens: int = -1, temperature: float = 0.7, top_p: float = 0.9) -> bool:
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
        auto_plan_retries = 0

        while True:
            user_input_for_model = text
            if revision_feedback:
                user_input_for_model = _build_revision_request(text, revision_feedback, active_mode)

            full_prompt = _get_mode_prompt(user_input_for_model, TERMINAL_CHAT_HISTORY)
            cancel_event = threading.Event()

            # Pokazuj timer dla wszystkich trybów
            show_progress = True

            try:
                response, elapsed = _generate_with_live_status(
                    loader,
                    full_prompt,
                    requested_max_tokens,
                    safe_temperature,
                    safe_top_p,
                    cancel_event,
                    show_progress=show_progress
                )
            except KeyboardInterrupt:
                print("\nINFO: Generation interrupted.")
                return False

            if cancel_event.is_set():
                print("INFO: Generation interrupted.")
                return False

            filtered = _extract_visible_answer(response or "")
            if not filtered:
                print("AI: [no response]")
                return False

            prompt_tokens = loader.count_tokens(full_prompt)
            output_tokens = loader.count_tokens(filtered)
            total_tokens = int(prompt_tokens) + int(output_tokens)

            if active_mode == AppMode.CHAT:
                mode_color = Colors.MODE_CHAT
                print(f"{mode_color}[CHAT]{Colors.ENDC} [tokens: {total_tokens}] {_format_elapsed_label(elapsed)}")
                print(f"AI: {filtered}")

                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append({"role": "assistant", "content": filtered})
                return True

            if active_mode == AppMode.PLAN:
                plan_body = _extract_plan_content(filtered)
                if not plan_body:
                    print("[PLAN] Empty output. Try again.")
                    return False

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

                # Save draft immediately so the file exists before final confirmation.
                plan_path = code_file_manager.create_plan_file(plan_body)
                if not plan_path:
                    print("[PLAN] ERROR: Failed to save plan file.")
                    return False

                decision, feedback = _request_mode_approval("PLAN")

                if decision == "cancel":
                    print("[PLAN] Cancelled.")
                    return False
                if decision == "revise":
                    revision_feedback = feedback
                    continue

                CURRENT_MODE = AppMode.CODE
                plan_tag = f"{Colors.MODE_PLAN}[PLAN]{Colors.ENDC}>"
                print(f"{plan_tag} [tokens: {total_tokens}] {_format_elapsed_label(elapsed)} -> mode [CODE]")
                print("|_ create [1 file]")

                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append({"role": "assistant", "content": f"[PLAN] create [1 file]"})
                return True

            if active_mode == AppMode.CODE:
                if not code_file_manager:
                    print("[CODE] ERROR: File manager unavailable.")
                    return False

                if "NO_FILE_CHANGES" in filtered:
                    code_tag = f"{Colors.MODE_CODE}[CODE]{Colors.ENDC}>"
                    print(f"{code_tag} [tokens: {total_tokens}] {_format_elapsed_label(elapsed)}")
                    print("|_ create [0 files]")
                    TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                    TERMINAL_CHAT_HISTORY.append({"role": "assistant", "content": "[CODE] create [0 files]"})
                    return True

                proposed = code_file_manager.extract_file_changes(filtered)
                if not proposed:
                    print("[CODE] No valid file blocks detected (missing File: path).")
                    return False

                accepted_changes: List[Dict[str, Any]] = []
                total_files = len(proposed)
                for i, change in enumerate(proposed, 1):
                    expanded = False
                    while True:
                        preview_lines = _print_code_change_preview(
                            change,
                            i,
                            total_files,
                            max_lines=(120 if expanded else 20),
                            max_cols=max(70, get_terminal_width() - 10),
                        )
                        action = _read_arrow_choice(
                            f"[CODE {i}/{total_files}] Review",
                            [
                                ("accept", "Accept"),
                                ("skip", "Skip"),
                                ("expand", "Expand" if not expanded else "Collapse"),
                                ("cancel", "Cancel"),
                            ],
                            default_idx=0,
                        )
                        _clear_last_terminal_lines(preview_lines + 3)

                        if action == "expand":
                            expanded = not expanded
                            continue
                        if action == "cancel":
                            print("[CODE] Cancelled. No files saved.")
                            return False
                        if action == "accept":
                            accepted_changes.append(change)
                        break

                if not accepted_changes:
                    print("[CODE] No files accepted.")
                    return False

                applied = code_file_manager.apply_file_changes(accepted_changes)
                if not applied:
                    print("[CODE] ERROR: No files were written.")
                    return False

                created_count = len(applied)
                summary_line = f"create [{created_count} {'file' if created_count == 1 else 'files'}]"
                code_tag = f"{Colors.MODE_CODE}[CODE]{Colors.ENDC}>"
                print(f"{code_tag} [tokens: {total_tokens}] {_format_elapsed_label(elapsed)}")
                print(f"|_ {summary_line}")

                TERMINAL_CHAT_HISTORY.append({"role": "user", "content": text})
                TERMINAL_CHAT_HISTORY.append({"role": "assistant", "content": f"[CODE] {summary_line}"})
                return True

            print(f"ERROR: Unsupported mode '{active_mode}'")
            return False

    except Exception as e:
        print(f"ERROR: Chat generation failed: {e}")
        return False


def _extract_visible_answer(raw_text: str) -> str:
    if not raw_text:
        return ""
    
    text = raw_text.replace("\ufeff", "").replace("\u200b", "")
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

    assistant_marker = re.search(r"(?is)<\|\s*assistant\s*\|>(.*)$", text)
    if assistant_marker:
        text = assistant_marker.group(1)

    text = re.sub(r"<\|[^>]+?\|>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?im)^\s*(?:chat|plan|code|ai|assistant|user)\s*>\s*", "", text)
    text = re.sub(r"(?im)^\s*\[(?:chat|plan|code)\]\s*>\s*", "", text)
    text = re.sub(r"(?im)^\s*(?:ai|assistant)\s*:\s*", "", text)
    text = re.sub(r"(?im)^\s*(?:user|human)\s*:\s*.*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _request_mode_approval(mode: str) -> Tuple[str, str]:
    decision = _read_arrow_choice(
        f"[{mode.upper()}] Confirm",
        [("accept", "Accept"), ("decline", "Decline"), ("revise", "Revise")],
        default_idx=0,
    )

    if decision == "accept":
        return "accept", ""
    if decision in {"cancel", "decline"}:
        return "cancel", ""

    feedback = _read_terminal_line(f"[{mode.upper()}][FEEDBACK]> What to improve: ").strip()
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
    paths = [str(item.get("relative_path", "")).strip() for item in items if str(item.get("relative_path", "")).strip()]
    if not paths:
        return f"applied {len(items)} file changes"
    if len(paths) == 1:
        return f"applied {paths[0]}"
    if len(paths) == 2:
        return f"applied {paths[0]} and {paths[1]}"
    return f"applied {paths[0]} (+{len(paths) - 1} more)"


def _print_text_panel(title: str, text: str, tone: str = "normal", max_lines: int = 80, max_cols: int = 140) -> int:
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


def _print_code_change_preview(change: Dict[str, Any], index: int, total: int, max_lines: int = 18, max_cols: int = 120) -> int:
    rel_path = (change or {}).get("relative_path", "unknown")
    language = (change or {}).get("language", "txt")
    code = (change or {}).get("code", "")
    code_lines = str(code).splitlines()

    use_uni = _supports_unicode_ui()
    tl = "┌" if use_uni else "+"
    bl = "└" if use_uni else "+"
    vr = "│" if use_uni else "|"
    hz = "─" if use_uni else "-"

    lines_printed = 0
    print(f"{tl}{hz} [CODE {index}/{total}] file: {rel_path} ({language})")
    lines_printed += 1
    for ln, content in enumerate(code_lines[:max_lines], 1):
        text = content.replace("\t", "    ")
        if len(text) > max_cols:
            text = text[: max_cols - 3] + "..."
        print(f"{vr} {ln:>3}: {text}")
        lines_printed += 1

    if len(code_lines) > max_lines:
        print(f"{vr} ... ({len(code_lines) - max_lines} more lines, choose Expand)")
        lines_printed += 1
    print(f"{bl}{hz}")
    lines_printed += 1
    return lines_printed


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
    plan_path = os.path.join(os.getcwd(), PLAN_FILENAME)
    if not os.path.exists(plan_path):
        print(f"ERROR: Missing plan file: {PLAN_FILENAME}")
        return False

    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_content = f.read().strip()
    except Exception as e:
        print(f"ERROR: Failed to read {PLAN_FILENAME}: {e}")
        return False

    if not plan_content:
        print(f"ERROR: {PLAN_FILENAME} is empty.")
        return False

    # Wyświetl komendę użytkownika
    mode_color = Colors.MODE_CODE
    print(f"\n{mode_color}[CODE]{Colors.ENDC}> /go")
    
    CURRENT_MODE = AppMode.CODE
    go_prompt = (
        f"Execute the implementation based on {PLAN_FILENAME}. "
        "Create or update project files accordingly. "
        "Return complete file contents with explicit File: paths.\n\n"
        f"Plan content:\n{plan_content[:6000]}"
    )
    return send_terminal_prompt(go_prompt, max_tokens=1800, temperature=0.2, top_p=0.9)


def _handle_ide_command(raw_command: str) -> bool:
    global ide_integration

    if not ide_integration:
        print("ERROR: IDE integration is not initialized.")
        return False

    ide_integration.refresh_ides()
    parts = (raw_command or "").strip().split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    ides = ide_integration.list_ides()
    running_ids = set(ide_integration.detect_running_ide_ids())

    def _print_ide_list() -> int:
        if not ides:
            print("IDE: none detected")
            return 1

        active_id = ide_integration.active_ide["id"] if ide_integration.active_ide else None
        print("IDE:")
        for idx, ide in enumerate(ides, 1):
            marker = "*" if ide["id"] == active_id else " "
            run_mark = "RUNNING" if ide["id"] in running_ids else "idle"
            print(f"  {marker} {idx}. {ide['id']} ({ide['name']}) [{run_mark}] -> {ide['path']}")
        return 1 + len(ides)

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
            running_mark = "RUNNING" if ide["id"] in running_ids else "idle"
            if ide["id"] in running_ids and default_idx == 0:
                default_idx = i
            options.append((ide["id"], f"{ide['name']} [{running_mark}]"))

        selected = _read_arrow_choice("[IDE] Select", options, default_idx=default_idx)
        if selected == "cancel":
            return False

        if ide_integration.set_active(selected.lower()):
            active = ide_integration.active_ide
            print(_format_command_chip(f"ide [{active['name']}]"))
            return True

        print(f"ERROR: IDE '{selected}' not found.")
        return False

    if subcommand in {"status", "list"}:
        _print_ide_list()
        return True

    if subcommand in {"use", "set"}:
        if len(parts) < 3:
            print("Usage: /ide use <id|number>")
            return False

        target = parts[2].strip()
        if target.isdigit():
            index = int(target) - 1
            if index < 0 or index >= len(ides):
                print("ERROR: Invalid IDE number.")
                return False
            target = ides[index]["id"]

        if ide_integration.set_active(target.lower()):
            active = ide_integration.active_ide
            print(_format_command_chip(f"ide [{active['name']}]"))
            return True

        print(f"ERROR: IDE '{target}' not found. Use '/ide list'.")
        return False

    if subcommand == "open":
        match = re.match(r"(?is)^/?ide\s+open\s+(.+?)(?:\s+(\d+))?(?:\s+(\d+))?\s*$", (raw_command or "").strip())
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

    print("Usage: /ide [list|use <id|number>|open <file> [line] [col]]")
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


def _handle_mode_command(raw_command: str) -> bool:
    """Obsługuje komendy związane z trybami pracy"""
    global CURRENT_MODE
    
    parts = raw_command.strip().split(maxsplit=2)
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    
    if subcommand in ["chat", "c"]:
        CURRENT_MODE = AppMode.CHAT
        return True
    elif subcommand in ["plan", "p"]:
        CURRENT_MODE = AppMode.PLAN
        return True
    elif subcommand in ["code", "d"]:
        CURRENT_MODE = AppMode.CODE
        return True
    elif subcommand in ["next", "n", "switch", ""]:
        # Przełącz na następny tryb
        if CURRENT_MODE == AppMode.CHAT:
            CURRENT_MODE = AppMode.PLAN
        elif CURRENT_MODE == AppMode.PLAN:
            CURRENT_MODE = AppMode.CODE
        else:
            CURRENT_MODE = AppMode.CHAT
        return True
    else:
        print(f"Unknown mode: {subcommand}")
        print("Available: chat, plan, code, next")
        return False


# ============ CHAT SESSION ============

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
            if _is_mode_switch_shortcut(command_text):
                _handle_mode_command("/mode next")
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

            if command_name in {"help", "pomoc", "?"}:
                show_help()
                continue
            if command_name in {"exit", "quit"}:
                print("Exited chat mode.")
                break
            if command_name == "status":
                show_status()
                continue
            if command_name == "catalog":
                show_download_catalog()
                continue
            if command_name == "ide":
                _handle_ide_command(command_text)
                continue
            if command_name == "go":
                _handle_go_command()
                continue
            if command_name == "swap":
                _handle_swap_command(command_text)
                continue
            if command_name == "models":
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
            if command_name == "mode":
                _handle_mode_command(command_text)
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


# ============ MAIN ============

def main():
    global http_server, loader, HAS_AI_ENGINE, LAST_UPDATE_STATUS, HTTP_PORT, TERMINAL_CHAT_HISTORY, ide_integration, code_file_manager, INPUT_AREA_START_ROW, INPUT_AREA_CLEAR_LINES

    loader = SimpleGGUFLoader()
    ide_integration = IDEIntegration()
    code_file_manager = CodeFileManager()
    install_global_launcher(silent=True)

    try:
        import llama_cpp
        HAS_AI_ENGINE = True
    except ImportError:
        HAS_AI_ENGINE = False
        print("Warning: AI engine is not installed. Run 'install' to install it.")

    if HAS_AI_ENGINE and sys.version_info >= (3, 13):
        print("Warning: Python 3.13 + llama-cpp-python can be unstable for some GGUF models.")
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
        INPUT_AREA_CLEAR_LINES = min(10, max(6, get_terminal_height() - 3))

    try:
        main_chat_paused = False
        while True:
            try:
                # Jeśli model załadowany - od razu czat
                if loader and loader.current_model:
                    run_terminal_chat_session()
                    continue
                
                raw_command = _read_terminal_line(get_mode_prompt()).strip()
                
                if raw_command == "__TAB__":
                    _handle_mode_command("/mode next")
                    continue
                if raw_command == "\x1b":
                    continue
                    
                if not raw_command:
                    continue

                if not raw_command.startswith("/"):
                    print("ERROR: Use /command (np. /load, /help, /ide).")
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

                if command_name in ('exit', 'quit', 'wyjdz'):
                    confirm = _read_arrow_choice("Exit CMDAI?", [("yes", "Yes"), ("no", "No")], default_idx=1)
                    if confirm == 'yes':
                        print("Goodbye!")
                        break

                elif command_name in ('help', 'pomoc', '?'):
                    show_help()

                elif command_name in ('launcher', 'install-launcher'):
                    install_global_launcher(silent=False)

                elif command_name == 'mode':
                    if len(raw_command.split()) == 1:
                        show_mode_help()
                    else:
                        _handle_mode_command(raw_command)

                elif command_name == 'models':
                    models = loader.list_models() if loader else []
                    if not models:
                        print("ERROR: NO GGUF MODELS IN FOLDER")
                        continue
                    choice = _pick_model_name(models, "/load")
                    if not choice:
                        print("INFO: Load cancelled.")
                        continue
                    loader.load(choice, show_try_errors=True)

                elif command_name == 'load':
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

                elif command_name == 'swap':
                    _handle_swap_command(raw_command)

                elif command_name == 'ide':
                    _handle_ide_command(raw_command)

                elif command_name == 'go':
                    _handle_go_command()

                elif command_name == 'm':
                    _handle_mode_command("/mode next")

                elif command_name == 'catalog':
                    show_download_catalog()

                elif command_name in ('download', 'pull'):
                    parts = raw_command.split(maxsplit=2)
                    source = parts[1].strip() if len(parts) > 1 else ""
                    output_name = parts[2].strip() if len(parts) > 2 else None
                    
                    if not source:
                        print("Usage: /download <alias|url> [file.gguf]")
                        show_download_catalog()
                        continue
                    
                    if not loader:
                        print("ERROR: Loader not initialized")
                        continue

                    try:
                        result = loader.download_model(source, output_name=output_name, overwrite=False)
                        if result['status'] == 'already_exists':
                            print(f"Model already exists: {result['name']}")
                            print(f"   Path: {result['path']}")
                            print(f"   Size: {result['size']} B")
                        else:
                            print(f"Model downloaded: {result['name']}")
                            print(f"   Path: {result['path']}")
                            print(f"   Size: {result['size']} B")
                    except ValueError as e:
                        print(f"Invalid source: {e}")
                        print("Try 'catalog' to see available models")
                    except RuntimeError as e:
                        print(f"Download failed: {e}")
                    except Exception as e:
                        print(f"Error: {e}")

                elif command_name in ('unload',):
                    if loader.unload():
                        print("SUCCESS: Model unloaded from memory")
                        TERMINAL_CHAT_HISTORY.clear()
                    else:
                        print("ERROR: Failed to unload model")

                elif command_name == 'chat':
                    message = raw_command[4:].strip()
                    if not message:
                        run_terminal_chat_session()
                    else:
                        if main_chat_paused:
                            print("INFO: Chat is paused. Use '/pause' to resume.")
                        else:
                            send_terminal_prompt(message)

                elif command_name == 'ai':
                    run_terminal_chat_session()

                elif command_name == 'status':
                    show_status()

                elif command_name in ('clear', 'cls', 'wyczysc'):
                    clear_screen()
                    TERMINAL_CHAT_HISTORY.clear()
                    print("INFO: Chat history cleared.")

                elif command_name == 'version':
                    if HAS_AI_ENGINE:
                        import llama_cpp
                        print(f"llama-cpp-python: {llama_cpp.__version__}")
                    else:
                        print("ERROR: AI engine is not installed")

                elif command_name == 'update' and HAS_AI_ENGINE:
                    print(f"Updating runtime packages: {', '.join(RECOMMENDED_RUNTIME_PACKAGES)} ...")
                    try:
                        import subprocess
                        subprocess.check_call(
                            [sys.executable, "-m", "pip", "install", "--upgrade", *RECOMMENDED_RUNTIME_PACKAGES]
                        )
                        print("SUCCESS: Updated. Restart the app.")
                        break
                    except Exception as e:
                        print(f"ERROR: Update failed: {e}")

                elif command_name == 'install' and not HAS_AI_ENGINE:
                    print(f"Installing runtime packages: {', '.join(RECOMMENDED_RUNTIME_PACKAGES)} ...")
                    try:
                        import subprocess
                        subprocess.check_call([sys.executable, "-m", "pip", "install", *RECOMMENDED_RUNTIME_PACKAGES])
                        print("SUCCESS: Installed. Restart the app.")
                        break
                    except Exception as e:
                        print(f"ERROR: Install failed: {e}")

                else:
                    if loader and loader.current_model:
                        if main_chat_paused:
                            print("INFO: Chat is paused. Use '/pause' to resume.")
                        else:
                            send_terminal_prompt(raw_command)
                    else:
                        print("ERROR: Unknown command. Type '/help' to show available commands")

            except KeyboardInterrupt:
                print("")
            except EOFError:
                print("\nInput stream is closed. Keeping terminal open...")
                time.sleep(0.5)
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
    """Obsługuje komendę 'CMDAI launch' - uruchamia aplikację"""
    args = sys.argv[1:]
    
    # Jeśli brak argumentów lub 'launch' - uruchom aplikację
    if len(args) == 0 or args[0].lower() in ('launch', 'start', 'run'):
        main()
        return
    
    # Jeśli 'help' lub '-h' lub '--help' - pokaż pomoc launchera
    if args[0].lower() in ('help', '-h', '--help', '/?'):
        print("CMDAI Launcher")
        print("=" * 40)
        print("Usage: CMDAI [command]")
        print()
        print("Commands:")
        print("  launch    Launch CMDAI application (default)")
        print("  help      Show this help message")
        print()
        print("Examples:")
        print("  CMDAI           # Launch application")
        print("  CMDAI launch    # Launch application")
        print("  python run.py   # Launch application")
        return
    
    # Nieznana komenda
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
