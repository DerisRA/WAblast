import base64
import csv
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import (ttk, filedialog, messagebox)
from PIL import (Image, ImageDraw, ImageTk)
APP = 'WAblast'
MAX_TARGETS = 50
BATCH_REST_INTERVAL = 10
BATCH_REST_DELAY = 300
DELAY = 60
DELAY_JITTER_MIN = 0
DELAY_JITTER_MAX = 120
MAX_IMAGE_BYTES = 16 * 1024 * 1024
IMAGE_MIME_BY_FORMAT = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}
RANDOM_TEMPLATE_OPTION = 'Template Acak (per nomor)'
DATA = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming')) / APP
DATA.mkdir(parents=True, exist_ok=True)


def apply_data_directory_acl():
    if os.name != 'nt':
        return
    identity = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True, timeout=10, check=True)
    rows = list(csv.reader(identity.stdout.splitlines()))
    user_sid = next((row[1].strip() for row in rows if len(row) > 1 and row[1].strip().startswith('S-1-')), None)
    if not user_sid:
        raise RuntimeError('SID akun Windows saat ini tidak dapat ditemukan.')
    marker = DATA / '.ntfs_acl_owner'
    try:
        already_hardened = marker.read_text(encoding='ascii').strip() == user_sid
    except (OSError, UnicodeError):
        already_hardened = False
    recursive = [] if already_hardened else ['/T']
    reset = subprocess.run(['icacls.exe', str(DATA), '/reset', *recursive], capture_output=True, text=True, timeout=60)
    if reset.returncode:
        detail = (reset.stderr or reset.stdout).strip()
        raise RuntimeError(detail or f'icacls reset keluar dengan kode {reset.returncode}.')
    command = [
        'icacls.exe', str(DATA), '/inheritance:r', '/grant:r',
        f'*{user_sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', *recursive
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f'icacls keluar dengan kode {result.returncode}.')
    marker.write_text(user_sid, encoding='ascii')


DATA_ACL_ERROR = None
try:
    apply_data_directory_acl()
except Exception as exc:
    DATA_ACL_ERROR = str(exc)

SETTINGS = DATA / 'settings.json'
HISTORY = DATA / 'history.jsonl'
DEFAULTS = [{'name': f'Template {i + 1}', 'text': '', 'image': ''} for i in range(5)]
C_BASE = '#0f1013'
C_SIDEBAR = '#14151a'
C_SIDEBAR_BORDER = '#1f2129'
C_CARD = '#191a21'
C_CARD_BORDER = '#252733'
C_CARD_INNER = '#121318'
C_HOVER = '#232430'
C_ACTIVE_PILL = '#272835'
C_BORDER_SUBTLE = '#282a38'
C_PURPLE = '#7059f6'
C_PURPLE_HOVER = '#836dfa'
C_PURPLE_MUTED = '#2d284a'
C_TEXT_WHITE = '#ffffff'
C_TEXT_PRIMARY = '#e3e5ed'
C_TEXT_MUTED = '#878a9c'
C_TEXT_DIM = '#535667'
C_GREEN = '#10b981'
C_AMBER = '#f59e0b'
C_RED = '#ef4444'

def load_settings():
    data = {'country': '62', 'templates': DEFAULTS, 'optout': []}
    try:
        data.update(json.loads(SETTINGS.read_text(encoding='utf-8')))
    except (OSError, json.JSONDecodeError):
        pass
    items = data.get('templates')
    if not isinstance(items, list):
        items = []
    data['templates'] = (items + DEFAULTS)[:5]
    data['optout'] = data.get('optout', [])
    return data

def save_settings(data):
    temp = SETTINGS.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(SETTINGS)

@dataclass
class Target:
    phone: str
    name: str = ''

class DirectWhatsAppError(RuntimeError):
    pass


def get_app_dir():
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / 'whatsapp_bridge.js').is_file():
            return exe_dir
        if hasattr(sys, '_MEIPASS') and (Path(sys._MEIPASS) / 'whatsapp_bridge.js').is_file():
            return Path(sys._MEIPASS)
        return exe_dir
    return Path(__file__).resolve().parent


def kill_session_browsers(force_wmi=False):
    if sys.platform != 'win32':
        return
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        pid_file = DATA / 'whatsapp_auth' / 'browser.pid'
        if pid_file.is_file():
            raw_pid = pid_file.read_text(encoding='utf-8').strip()
            if raw_pid.isdigit():
                subprocess.run(['taskkill.exe', '/PID', raw_pid, '/T', '/F'], capture_output=True, timeout=4, creationflags=flags)
            pid_file.unlink(missing_ok=True)
    except Exception:
        pass
    locks_cleared = True
    try:
        session_dir = DATA / 'whatsapp_auth' / 'session-wablast'
        for f in ['lockfile', 'DevToolsActivePort']:
            target = session_dir / f
            if target.is_file():
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    if f == 'lockfile':
                        locks_cleared = False
    except Exception:
        pass
    if not locks_cleared or force_wmi:
        try:
            ps_script = (
                "$s = [wmisearcher]\"SELECT ProcessId FROM Win32_Process WHERE (Name='msedge.exe' OR Name='chrome.exe') AND CommandLine LIKE '%session-wablast%'\"\n"
                "foreach ($p in $s.Get()) {\n"
                "    & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null\n"
                "}\n"
            )
            b64 = base64.b64encode(ps_script.encode('utf-16le')).decode('ascii')
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', b64], capture_output=True, timeout=8, creationflags=flags)
            time.sleep(0.25)
        except Exception:
            pass
        try:
            session_dir = DATA / 'whatsapp_auth' / 'session-wablast'
            for f in ['lockfile', 'DevToolsActivePort']:
                target = session_dir / f
                if target.is_file():
                    target.unlink(missing_ok=True)
        except Exception:
            pass


class DirectWhatsApp:
    '''Owns a local whatsapp-web.js process and exchanges JSON-lines RPC.'''

    def __init__(self, events):
        self.events = events
        self.process = None
        self.closing = False
        self.pending = {}
        self.sequence = 0
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.start_lock = threading.Lock()
        self.stderr_tail = []

    def _ensure_process(self):
        with self.start_lock:
            if self.process is not None and self.process.poll() is None:
                return
            self.closing = False
            base = get_app_dir()
            bundled_node = base / 'node.exe'
            if not bundled_node.is_file():
                bundled_node = base / 'node' / 'node.exe'
            if bundled_node.is_file():
                node = str(bundled_node)
            else:
                node = shutil.which('node') or shutil.which('node.exe')
            if not node:
                raise DirectWhatsAppError('Node.js 22.12+ tidak ditemukan. Pasang Node.js lalu jalankan install_dependencies.bat.')
            try:
                version_text = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=5, check=True).stdout.strip()
                match = re.search('(\\d+)\\.(\\d+)', version_text)
                if not match or tuple(map(int, match.groups())) < (22, 12):
                    raise DirectWhatsAppError('WAblast memerlukan Node.js 22.12 atau lebih baru.')
            except (OSError, subprocess.SubprocessError) as exc:
                raise DirectWhatsAppError(f'Tidak dapat memeriksa versi Node.js: {exc}') from exc
            bridge = base / 'whatsapp_bridge.js'
            modules = base / 'node_modules'
            if not (bridge.is_file() and (modules / 'whatsapp-web.js').is_dir()):
                raise DirectWhatsAppError('Library WhatsApp belum dipasang. Jalankan install_dependencies.bat di folder aplikasi.')
            kill_session_browsers()
            env = os.environ.copy()
            env['WA_BLAST_DATA_DIR'] = str(DATA)
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            try:
                self.process = subprocess.Popen([node, str(bridge)], cwd=str(base), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1, creationflags=flags)
            except OSError as exc:
                raise DirectWhatsAppError(f'Tidak dapat membuka WhatsApp Web lokal: {exc}') from exc
            threading.Thread(target=self._read_stdout, daemon=True).start()
            threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        process = self.process
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if 'event' in message:
                    kind = message['event']
                    if kind == 'qr':
                        self.events.put(('qr', message.get('data', '')))
                    elif kind == 'status':
                        status = message.get('status', 'UNKNOWN')
                        detail = message.get('detail')
                        account = message.get('account')
                        if status == 'FAILED' and detail:
                            self.events.put(('error', detail))
                        self.events.put(('status', (status, detail, account)))
                    elif kind == 'error':
                        self.events.put(('error', message.get('message', 'Kesalahan WhatsApp Web')))
                    continue
                request_id = message.get('id')
                with self.lock:
                    waiter = self.pending.get(request_id)
                if waiter:
                    waiter['message'] = message
                    waiter['event'].set()
        finally:
            with self.lock:
                waiters = list(self.pending.values())
            for waiter in waiters:
                waiter['message'] = {'ok': False, 'error': 'Proses WhatsApp Web terputus.'}
                waiter['event'].set()
            if not self.closing:
                self.events.put(('status', 'FAILED'))

    def _read_stderr(self):
        process = self.process
        try:
            for line in process.stderr:
                self.stderr_tail.append(line.strip())
                self.stderr_tail = self.stderr_tail[-20:]
        except Exception:
            pass

    def _request(self, command, args=None, timeout=90):
        self._ensure_process()
        with self.lock:
            self.sequence += 1
            request_id = self.sequence
            waiter = {'event': threading.Event(), 'message': None}
            self.pending[request_id] = waiter
        message = json.dumps({'id': request_id, 'cmd': command, 'args': args or {}}, ensure_ascii=False)
        try:
            with self.write_lock:
                if self.process.poll() is not None:
                    raise DirectWhatsAppError('Proses WhatsApp Web berhenti.')
                self.process.stdin.write(message + '\n')
                self.process.stdin.flush()
            if not waiter['event'].wait(timeout):
                raise DirectWhatsAppError(f"WhatsApp Web tidak merespons perintah '{command}'.")
            response = waiter['message'] or {}
            if not response.get('ok'):
                raise DirectWhatsAppError(response.get('error') or 'Perintah WhatsApp Web gagal.')
            return response.get('result')
        except (BrokenPipeError, OSError) as exc:
            raise DirectWhatsAppError(f'Koneksi lokal WhatsApp Web terputus: {exc}') from exc
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def connect(self):
        return self._request('connect', timeout=20)

    def get_session(self):
        result = self._request('status', timeout=8) or {}
        return {'status': result.get('status', 'UNKNOWN'), 'account': result.get('account')}

    def qr(self):
        result = self._request('qr', timeout=8)
        try:
            return base64.b64decode(result['data'])
        except Exception as exc:
            raise DirectWhatsAppError('Data QR WhatsApp tidak valid.') from exc

    def check_exists(self, phone):
        return self._request('check_exists', {'phone': phone}, timeout=8)
        return self._request('check_exists', {'phone': phone}, timeout=6)

    def send(self, target, text, image, chat_id=None, image_mime=None):
        return self._request('send', {'phone': target.phone, 'chatId': chat_id, 'text': text, 'image': image, 'imageMime': image_mime}, timeout=45)
    def send(self, target, text, image, chat_id=None, image_mime=None, check_first=False):
        return self._request('send', {'phone': target.phone, 'chatId': chat_id, 'text': text, 'image': image, 'imageMime': image_mime, 'checkFirst': bool(check_first)}, timeout=25)

    def logout(self):
        return self._request('logout', timeout=15)

    def wipe_session(self):
        if self.process is None or self.process.poll() is not None:
            kill_session_browsers()
            session_path = DATA / 'whatsapp_auth' / 'session-wablast'
            had_session = session_path.exists()
            try:
                shutil.rmtree(session_path, ignore_errors=True)
            except Exception:
                pass
            return {'status': 'STOPPED', 'wiped': True, 'remoteLogout': not had_session}
        try:
            return self._request('wipe_session', timeout=15)
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            kill_session_browsers()
            session_path = DATA / 'whatsapp_auth' / 'session-wablast'
            if session_path.exists():
                shutil.rmtree(session_path, ignore_errors=True)
            return {'status': 'STOPPED', 'wiped': True, 'remoteLogout': False, 'fallback': True}

    def close(self):
        self.closing = True
        if self.process is None or self.process.poll() is not None:
            return
        try:
            self._request('shutdown', timeout=5)
        except Exception:
            pass
        if self.process and self.process.poll() is None:
            if sys.platform == 'win32':
                try:
                    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                    subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'], capture_output=True, creationflags=flags)
                except Exception:
                    try:
                        self.process.terminate()
                    except OSError:
                        pass
            else:
                try:
                    self.process.terminate()
                except OSError:
                    pass


def mask_account_phone(raw):
    if not raw:
        return 'Belum terhubung'
    digits = re.sub('\\D', '', str(raw))
    if len(digits) < 4:
        return 'XXXX'
    last4 = digits[-4:]
    if digits.startswith('62'):
        return f'+62 XXXX-XXXX-{last4}'
    return f'XXXX-XXXX-{last4}'


def phone_number(raw, country):
    value = raw.strip()
    digits = re.sub('\\D', '', value)
    country_code = re.sub('\\D', '', country or '62')
    international = value.startswith('+')
    if value.startswith('00'):
        digits = digits[2:]
        international = True
    if not international:
        if digits.startswith('0'):
            digits = country_code + digits[1:]
        elif country_code and not digits.startswith(country_code):
            digits = country_code + digits
    if not 8 <= len(digits) <= 15:
        raise ValueError('nomor harus 8-15 digit termasuk kode negara')
    return digits


def validate_image(path):
    image_path = Path(path)
    try:
        if not image_path.is_file():
            raise ValueError('File gambar tidak ditemukan.')
        if image_path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError('Ukuran gambar maksimal 16 MB.')
        with Image.open(image_path) as image:
            image_format = image.format
            if image_format not in IMAGE_MIME_BY_FORMAT:
                raise ValueError('Format gambar harus JPEG, PNG, atau WebP.')
            image.verify()
        with Image.open(image_path) as image:
            image.load()
        return IMAGE_MIME_BY_FORMAT[image_format]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('File bukan gambar JPEG, PNG, atau WebP yang valid.') from exc

def parse_targets(text, country, blocked):
    targets, errors, seen = [], [], set()
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = next(csv.reader([line]))
            phone = phone_number(row[0], country)
            name = row[1].strip() if len(row) > 1 else ''
            if phone in seen:
                errors.append(f'Baris {line_no}: duplikat dilewati')
            elif phone in blocked:
                errors.append(f'Baris {line_no}: opt-out dilewati')
            else:
                seen.add(phone)
                targets.append(Target(phone, name))
        except (ValueError, csv.Error, IndexError) as exc:
            errors.append(f'Baris {line_no}: {exc}')
            continue
    return targets, errors

ICON_CACHE = {}

def get_icon(name, color=C_TEXT_MUTED, accent='#00d2ff', bg=C_SIDEBAR, size=22, active=False):
    key = name, color, accent, bg, size, active
    if key in ICON_CACHE:
        return ICON_CACHE[key]
    scale = 4
    S = size * scale
    im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw = int(1.8 * scale)
    if name == 'send':
        x0, y0, x1, y1 = 2.5 * scale, 4.5 * scale, 19.5 * scale, 17.5 * scale
        r = 3.5 * scale
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=color, width=lw)
        d.line([(x0 + 2 * scale, y0 + 2 * scale), (11 * scale, 11 * scale), (x1 - 2 * scale, y0 + 2 * scale)], fill=color, width=lw, joint='round')
        bx, by, br = 17.5 * scale, 5 * scale, 3.2 * scale
        d.ellipse([bx - br, by - br, bx + br, by + br], fill=accent, outline=bg, width=int(1.2 * scale))
        d.ellipse([bx - 0.7 * scale, by - 0.7 * scale, bx + 0.7 * scale, by + 0.7 * scale], fill='#ffffff')
    elif name in ('template', 'templates'):
        pts = [(3.5 * scale, 2.5 * scale), (13 * scale, 2.5 * scale), (18.5 * scale, 8 * scale), (18.5 * scale, 19.5 * scale), (3.5 * scale, 19.5 * scale)]
        d.polygon(pts, outline=color, fill=bg, width=lw)
        d.line([(13 * scale, 2.5 * scale), (13 * scale, 8 * scale), (18.5 * scale, 8 * scale)], fill=color, width=lw, joint='round')
        d.line([(6.5 * scale, 11.5 * scale), (15.5 * scale, 11.5 * scale)], fill=color, width=int(1.5 * scale))
        d.line([(6.5 * scale, 14.5 * scale), (15.5 * scale, 14.5 * scale)], fill=color, width=int(1.5 * scale))
        d.line([(6.5 * scale, 17.5 * scale), (11.5 * scale, 17.5 * scale)], fill=accent if active else color, width=int(1.5 * scale))
    elif name == 'connection':
        cx, cy = 11 * scale, 17 * scale
        dot_c = accent if active else color
        d.ellipse([cx - 1.8 * scale, cy - 1.8 * scale, cx + 1.8 * scale, cy + 1.8 * scale], fill=dot_c)
        d.arc([cx - 5.5 * scale, cy - 6 * scale, cx + 5.5 * scale, cy + 5 * scale], start=215, end=325, fill=color, width=lw)
        d.arc([cx - 9.5 * scale, cy - 10 * scale, cx + 9.5 * scale, cy + 9 * scale], start=215, end=325, fill=color, width=lw)
    elif name == 'optout':
        cx, cy, hr = 9.5 * scale, 7 * scale, 3.2 * scale
        d.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], outline=color, width=lw)
        d.arc([3 * scale, 11 * scale, 16 * scale, 21 * scale], start=195, end=345, fill=color, width=lw)
        bx, by, br = 16.5 * scale, 15 * scale, 3.8 * scale
        badge_bg = '#ef4444' if not active else '#f87171'
        d.ellipse([bx - br, by - br, bx + br, by + br], fill=badge_bg, outline=bg, width=int(1.2 * scale))
        d.line([(bx - 1.8 * scale, by), (bx + 1.8 * scale, by)], fill='#ffffff', width=int(1.5 * scale))
    elif name == 'run':
        pts = [(11.5 * scale, 2 * scale), (16 * scale, 2 * scale), (10.5 * scale, 9 * scale), (15.5 * scale, 9 * scale), (6 * scale, 20 * scale), (9 * scale, 12 * scale), (6 * scale, 12 * scale)]
        d.polygon(pts, fill=color, outline=color)
    resized = im.resize((size, size), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(resized)
    ICON_CACHE[key] = photo
    return photo

def draw_vector_icon(canvas, icon_name, color=C_TEXT_MUTED, bg=C_SIDEBAR, active=False, size=22):
    photo = get_icon(icon_name, color=color, accent='#00d2ff', bg=bg, size=size, active=active)
    canvas.delete('all')
    canvas.configure(bg=bg)
    canvas.create_image(size // 2, size // 2, image=photo)
    canvas.image = photo

class App:
    def __init__(self, root):
        kill_session_browsers()
        self.root = root
        root.title('WAblast - WhatsApp Batch Sender')
        icon_path = get_app_dir() / 'app.ico'
        if not icon_path.is_file() and hasattr(sys, '_MEIPASS'):
            icon_path = Path(sys._MEIPASS) / 'app.ico'
        if icon_path.is_file():
            try:
                root.iconbitmap(str(icon_path))
            except Exception:
                pass
        root.geometry('1180x800')
        root.minsize(980, 680)
        root.configure(bg=C_BASE)
        self.data = load_settings()
        self.events = queue.Queue()
        self.editor_index = 0
        self.template_image = None
        self.qr_image = None
        self.client = None
        self.logout_busy = False
        self.connect_busy = False
        self.status = 'UNKNOWN'
        self.account_info = None
        self.account_phone_var = tk.StringVar(value='Belum terhubung')
        self.last_qr = 0
        self.next_poll = 0
        self.poll_busy = False
        self.cancel = threading.Event()
        self.resume = threading.Event()
        self.resume.set()
        self.worker = None
        self.rows = {}
        self.combo_template_map = {}
        self.active_tab = 'send'
        self.sidebar_expanded = True
        self.nav_expanded_widgets = {}
        self.nav_collapsed_widgets = {}
        self.setup_styles()
        self.build()
        self.load_template(0)
        self.load_optout()
        root.after(200, self.drain)
        root.after(500, self.poll_tick)
        root.after(800, self.auto_connect_if_saved_session)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Dark.Treeview', background=C_CARD_INNER, foreground=C_TEXT_PRIMARY, fieldbackground=C_CARD_INNER, rowheight=28, borderwidth=0, font=('Segoe UI', 9))
        style.configure('Dark.Treeview.Heading', background='#1d1f2a', foreground=C_TEXT_WHITE, relief='flat', font=('Segoe UI Semibold', 9))
        style.map('Dark.Treeview', background=[('selected', '#322d56')], foreground=[('selected', C_TEXT_WHITE)])
        style.map('Dark.Treeview.Heading', background=[('active', '#252735')])
        style.configure('Dark.TCombobox', fieldbackground=C_CARD_INNER, background=C_ACTIVE_PILL, foreground=C_TEXT_PRIMARY, arrowcolor=C_TEXT_MUTED, bordercolor=C_BORDER_SUBTLE, lightcolor=C_BORDER_SUBTLE, darkcolor=C_BORDER_SUBTLE, padding=6)
        style.map('Dark.TCombobox', fieldbackground=[('readonly', C_CARD_INNER)], selectbackground=[('readonly', '#322d56')], selectforeground=[('readonly', C_TEXT_WHITE)])
        style.configure('Dark.TCheckbutton', background=C_CARD, foreground=C_TEXT_PRIMARY, font=('Segoe UI', 9))
        style.map('Dark.TCheckbutton', background=[('active', C_CARD)], foreground=[('active', C_TEXT_WHITE)])

    def build(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.sidebar = tk.Frame(self.root, bg=C_SIDEBAR, width=230, highlightbackground=C_SIDEBAR_BORDER, highlightthickness=1)
        self.sidebar.grid(row=0, column=0, sticky='nsew')
        self.sidebar.grid_propagate(False)
        self.canvas = tk.Frame(self.root, bg=C_BASE)
        self.canvas.grid(row=0, column=1, sticky='nsew')
        self.canvas.columnconfigure(0, weight=1)
        self.canvas.rowconfigure(1, weight=1)
        self.build_sidebar()
        self.build_canvas_header()
        self.build_tab_frames()

    def build_sidebar(self):
        self.sidebar_expanded_frame = tk.Frame(self.sidebar, bg=C_SIDEBAR)
        self.sidebar_expanded_frame.pack(fill='both', expand=True)
        self.sidebar_collapsed_frame = tk.Frame(self.sidebar, bg=C_SIDEBAR)
        self.head_exp = tk.Frame(self.sidebar_expanded_frame, bg=C_SIDEBAR, padx=14, pady=16, cursor='hand2')
        self.head_exp.pack(fill='x')
        self.logo_box = tk.Frame(self.head_exp, bg=C_PURPLE, width=34, height=34, highlightbackground=C_PURPLE_HOVER, highlightthickness=1, cursor='hand2')
        self.logo_box.pack(side='left', padx=(0, 10))
        self.logo_box.pack_propagate(False)
        lbl_w = tk.Label(self.logo_box, text='WA', bg=C_PURPLE, fg=C_TEXT_WHITE, font=('Segoe UI Semibold', 10), cursor='hand2')
        lbl_w.pack(expand=True)
        self.info_box = tk.Frame(self.head_exp, bg=C_SIDEBAR, cursor='hand2')
        self.info_box.pack(side='left', fill='both')
        lbl_title = tk.Label(self.info_box, text='WAblast', font=('Segoe UI Semibold', 12), fg=C_TEXT_WHITE, bg=C_SIDEBAR, cursor='hand2')
        lbl_title.pack(anchor='w')
        lbl_sub = tk.Label(self.info_box, text='Untuk Oja Manis', font=('Segoe UI', 8), fg=C_TEXT_MUTED, bg=C_SIDEBAR, cursor='hand2')
        lbl_sub.pack(anchor='w')
        for w in self.head_exp, self.logo_box, lbl_w, self.info_box, lbl_title, lbl_sub:
            w.bind('<Button-1>', (lambda e: self.toggle_sidebar()))
        cta_wrap = tk.Frame(self.sidebar_expanded_frame, bg=C_SIDEBAR, padx=14, pady=8)
        cta_wrap.pack(fill='x')
        self.cta_btn = tk.Button(cta_wrap, text='Mulai Run Batch', bg=C_PURPLE, fg=C_TEXT_WHITE, activebackground=C_PURPLE_HOVER, activeforeground=C_TEXT_WHITE, relief='flat', cursor='hand2', font=('Segoe UI Semibold', 9), pady=7, bd=0, command=self.on_cta_click)
        self.cta_btn.pack(fill='x')
        tk.Label(self.sidebar_expanded_frame, text='NAVIGASI', font=('Segoe UI Bold', 8), fg=C_TEXT_DIM, bg=C_SIDEBAR, padx=16, pady=6).pack(anchor='w', pady=(8, 2))
        self.nav_exp_container = tk.Frame(self.sidebar_expanded_frame, bg=C_SIDEBAR, padx=10)
        self.nav_exp_container.pack(fill='x')
        nav_items_data = [('send', 'Kirim Pesan'), ('templates', 'Template Pesan'), ('connection', 'WhatsApp Web'), ('optout', 'Daftar Opt-out')]
        for key, label in nav_items_data:
            row = tk.Frame(self.nav_exp_container, bg=C_SIDEBAR, padx=10, pady=8, cursor='hand2')
            row.pack(fill='x', pady=2)
            icon_cv = tk.Canvas(row, width=22, height=22, bg=C_SIDEBAR, highlightthickness=0)
            icon_cv.pack(side='left', padx=(0, 10))
            draw_vector_icon(icon_cv, key, C_TEXT_MUTED, bg=C_SIDEBAR, active=key == self.active_tab)
            lbl = tk.Label(row, text=label, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_SIDEBAR)
            lbl.pack(side='left')
            for widget in row, icon_cv, lbl:
                widget.bind('<Button-1>', (lambda e, k=key: self.switch_tab(k)))
                widget.bind('<Enter>', (lambda e, k=key: self.on_exp_nav_hover(k, True)))
                widget.bind('<Leave>', (lambda e, k=key: self.on_exp_nav_hover(k, False)))
            self.nav_expanded_widgets[key] = {'row': row, 'icon': icon_cv, 'lbl': lbl}
            continue
        terms_wrap = tk.Frame(self.sidebar_expanded_frame, bg=C_SIDEBAR, padx=12, pady=14)
        terms_wrap.pack(side='bottom', fill='x')
        self.terms_sidebar_button = self.create_btn(terms_wrap, 'Syarat & Ketentuan', command=self.show_terms, padx=8, pady=6)
        self.terms_sidebar_button.pack(fill='x')
        self.head_col = tk.Frame(self.sidebar_collapsed_frame, bg=C_SIDEBAR, pady=18, cursor='hand2')
        self.head_col.pack(fill='x')
        logo_col = tk.Frame(self.head_col, bg=C_PURPLE, width=34, height=34, highlightbackground=C_PURPLE_HOVER, highlightthickness=1, cursor='hand2')
        logo_col.pack(anchor='center')
        logo_col.pack_propagate(False)
        lbl_logo = tk.Label(logo_col, text='WA', bg=C_PURPLE, fg=C_TEXT_WHITE, font=('Segoe UI Semibold', 10), cursor='hand2')
        lbl_logo.pack(expand=True)
        for w in self.head_col, logo_col, lbl_logo, self.sidebar_collapsed_frame:
            w.bind('<Button-1>', (lambda e: self.toggle_sidebar()))
        cta_col_wrap = tk.Frame(self.sidebar_collapsed_frame, bg=C_SIDEBAR, pady=6)
        cta_col_wrap.pack(fill='x')
        self.cta_col_btn = tk.Frame(cta_col_wrap, bg=C_PURPLE, width=38, height=38, cursor='hand2', highlightbackground=C_PURPLE_HOVER, highlightthickness=1)
        self.cta_col_btn.pack(anchor='center')
        self.cta_col_btn.pack_propagate(False)
        cta_col_icon = tk.Canvas(self.cta_col_btn, width=22, height=22, bg=C_PURPLE, highlightthickness=0)
        cta_col_icon.pack(expand=True)
        draw_vector_icon(cta_col_icon, 'run', C_TEXT_WHITE, bg=C_PURPLE, active=True, size=22)
        self.cta_col_btn.bind('<Button-1>', (lambda e: self.on_cta_click()))
        cta_col_icon.bind('<Button-1>', (lambda e: self.on_cta_click()))
        div = tk.Frame(self.sidebar_collapsed_frame, bg=C_CARD_BORDER, height=1)
        div.pack(fill='x', padx=12, pady=10)
        self.nav_col_container = tk.Frame(self.sidebar_collapsed_frame, bg=C_SIDEBAR)
        self.nav_col_container.pack(fill='x')
        for key, label in nav_items_data:
            btn_frame = tk.Frame(self.nav_col_container, bg=C_SIDEBAR, width=44, height=40, cursor='hand2', highlightthickness=0)
            btn_frame.pack(anchor='center', pady=3)
            btn_frame.pack_propagate(False)
            cv = tk.Canvas(btn_frame, width=22, height=22, bg=C_SIDEBAR, highlightthickness=0)
            cv.pack(expand=True)
            draw_vector_icon(cv, key, C_TEXT_MUTED, bg=C_SIDEBAR, active=key == self.active_tab)
            for w in btn_frame, cv:
                w.bind('<Button-1>', (lambda e, k=key: self.switch_tab(k)))
                w.bind('<Enter>', (lambda e, k=key: self.on_col_nav_hover(k, True)))
                w.bind('<Leave>', (lambda e, k=key: self.on_col_nav_hover(k, False)))
            self.nav_collapsed_widgets[key] = {'frame': btn_frame, 'icon': cv}
            continue
        terms_col_wrap = tk.Frame(self.sidebar_collapsed_frame, bg=C_SIDEBAR, pady=16)
        terms_col_wrap.pack(side='bottom', fill='x')
        self.terms_collapsed_button = tk.Button(terms_col_wrap, text='T&C', command=self.show_terms, bg=C_ACTIVE_PILL, fg=C_TEXT_PRIMARY, activebackground=C_HOVER, activeforeground=C_TEXT_WHITE, relief='flat', cursor='hand2', font=('Segoe UI Semibold', 8), bd=0, padx=4, pady=5)
        self.terms_collapsed_button.pack(anchor='center')

    def toggle_sidebar(self):
        if self.sidebar_expanded:
            self.sidebar_expanded = False
            self.sidebar.configure(width=68)
            self.sidebar_expanded_frame.pack_forget()
            self.sidebar_collapsed_frame.pack(fill='both', expand=True)
        else:
            self.sidebar_expanded = True
            self.sidebar.configure(width=230)
            self.sidebar_collapsed_frame.pack_forget()
            self.sidebar_expanded_frame.pack(fill='both', expand=True)
        self.update_nav_highlights()

    def build_canvas_header(self):
        header_wrap = tk.Frame(self.canvas, bg=C_BASE, padx=24, pady=18)
        header_wrap.grid(row=0, column=0, sticky='ew')
        header_wrap.columnconfigure(0, weight=1)
        left_h = tk.Frame(header_wrap, bg=C_BASE)
        left_h.pack(side='left')
        self.title_var = tk.StringVar(value='Kirim Pesan')
        self.subtitle_var = tk.StringVar(value='')
        self.title_label = tk.Label(left_h, textvariable=self.title_var, font=('Segoe UI Semibold', 18), fg=C_TEXT_WHITE, bg=C_BASE)
        self.title_label.pack(anchor='w')
        self.subtitle_label = tk.Label(left_h, textvariable=self.subtitle_var, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_BASE)
        self.subtitle_label.pack(anchor='w', pady=(2, 0))
        self.status_badge = tk.Frame(header_wrap, bg=C_CARD, padx=12, pady=6, highlightbackground=C_CARD_BORDER, highlightthickness=1)
        self.status_badge.pack(side='right')
        self.top_dot_canvas = tk.Canvas(self.status_badge, width=10, height=10, bg=C_CARD, highlightthickness=0)
        self.top_dot_canvas.pack(side='left', padx=(0, 6))
        self.top_dot = self.top_dot_canvas.create_oval(1, 1, 9, 9, fill=C_AMBER, outline='')
        self.top_status = tk.StringVar(value='WhatsApp belum dihubungkan')
        self.top_label = tk.Label(self.status_badge, textvariable=self.top_status, fg=C_TEXT_PRIMARY, bg=C_CARD, font=('Segoe UI Semibold', 9))
        self.top_label.pack(side='left')
    def build_tab_frames(self):
        self.views_container = tk.Frame(self.canvas, bg=C_BASE, padx=24)
        self.views_container.grid(row=1, column=0, sticky='nsew', pady=(0, 20))
        self.views_container.columnconfigure(0, weight=1)
        self.views_container.rowconfigure(0, weight=1)
        self.tab_send = tk.Frame(self.views_container, bg=C_BASE)
        self.tab_templates = tk.Frame(self.views_container, bg=C_BASE)
        self.tab_connection = tk.Frame(self.views_container, bg=C_BASE)
        self.tab_optout = tk.Frame(self.views_container, bg=C_BASE)
        for f in self.tab_send, self.tab_templates, self.tab_connection, self.tab_optout:
            f.grid(row=0, column=0, sticky='nsew')
        self.build_send()
        self.build_templates()
        self.build_connection()
        self.build_optout()
        self.switch_tab('send')

    def card(self, parent, title='', subtitle=''):
        f = tk.Frame(parent, bg=C_CARD, padx=16, pady=16, highlightbackground=C_CARD_BORDER, highlightthickness=1)
        if title:
            h_row = tk.Frame(f, bg=C_CARD)
            h_row.pack(fill='x', pady=(0, 10))
            tk.Label(h_row, text=title, font=('Segoe UI Semibold', 11), fg=C_TEXT_WHITE, bg=C_CARD).pack(side='left')
            if subtitle:
                tk.Label(h_row, text=subtitle, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='left', padx=10)
        return f

    def create_btn(self, parent, text, command=None, variant='secondary', padx=14, pady=6):
        if variant == 'danger':
            bg, fg, hover, border = '#3b2026', '#fecaca', '#51262d', '#7f1d1d'
        else:
            bg = C_PURPLE if variant == 'accent' else C_ACTIVE_PILL
            fg = C_TEXT_WHITE if variant == 'accent' else C_TEXT_PRIMARY
            hover = C_PURPLE_HOVER if variant == 'accent' else C_HOVER
            border = C_PURPLE if variant == 'accent' else C_CARD_BORDER
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=hover, activeforeground=C_TEXT_WHITE, relief='flat', cursor='hand2', bd=0, padx=padx, pady=pady, font=('Segoe UI Semibold', 9), highlightbackground=border, highlightthickness=1)
        b.bind('<Enter>', (lambda e: None))
        b.bind('<Leave>', (lambda e: None))
        return b

    def show_terms(self):
        window = tk.Toplevel(self.root)
        window.title('Syarat & Ketentuan WAblast')
        window.geometry('620x470')
        window.minsize(520, 400)
        window.configure(bg=C_BASE)
        window.transient(self.root)

        content = tk.Frame(window, bg=C_CARD, padx=18, pady=16, highlightbackground=C_CARD_BORDER, highlightthickness=1)
        content.pack(fill='both', expand=True, padx=16, pady=16)
        tk.Label(content, text='Syarat & Ketentuan Penggunaan WhatsApp', font=('Segoe UI Semibold', 13), fg=C_TEXT_WHITE, bg=C_CARD).pack(anchor='w', pady=(0, 10))
        terms_text = (
            'WAblast mengakses WhatsApp Web melalui whatsapp-web.js, pustaka komunitas yang tidak resmi. '
            'Perubahan pada WhatsApp dapat memutus koneksi ini, dan penggunaannya dapat menyebabkan akun dibatasi. '
            'Jeda pengiriman tidak menjadikan integrasi ini resmi atau menjamin akun bebas dari pembatasan.\n\n'
            'Dengan menggunakan fitur ini, Anda bertanggung jawab memastikan penerima menyetujui pesan, menghormati '
            'permintaan opt-out, dan mengikuti aturan WhatsApp serta hukum yang berlaku.\n\n'
            'Baca ketentuan dan kebijakan WhatsApp berikut sebelum mengirim pesan:'
        )
        body = tk.Text(content, wrap='word', height=9, font=('Segoe UI', 9), fg=C_TEXT_PRIMARY, bg=C_CARD_INNER, relief='flat', padx=10, pady=10, spacing1=2, spacing3=5)
        body.pack(fill='both', expand=True, pady=(0, 10))
        body.insert('1.0', terms_text)
        body.configure(state='disabled')

        links = (
            ('Ketentuan WhatsApp', 'https://www.whatsapp.com/legal/terms-of-service?pix=6f_0_0'),
            ('Panduan penggunaan bertanggung jawab', 'https://faq.whatsapp.com/361005896189245/?eea=0&locale=en_US'),
            ('Kebijakan Berkirim Pesan WhatsApp Business', 'https://business.whatsapp.com/policy/preview?lang=id_ID'),
        )
        links_frame = tk.Frame(content, bg=C_CARD)
        links_frame.pack(fill='x', pady=(0, 12))
        for label, url in links:
            link = tk.Label(links_frame, text=label, font=('Segoe UI', 9, 'underline'), fg=C_PURPLE_HOVER, bg=C_CARD, cursor='hand2')
            link.pack(anchor='w', pady=2)
            link.bind('<Button-1>', lambda _event, target=url: webbrowser.open(target))

        self.create_btn(content, 'Tutup', command=window.destroy, variant='accent').pack(anchor='e')
        window.grab_set()

    def build_send(self):
        self.tab_send.columnconfigure(0, weight=3)
        self.tab_send.columnconfigure(1, weight=2)
        self.tab_send.rowconfigure(1, weight=1)
        c_target = self.card(self.tab_send, 'Nomor Tujuan', f'Maks. {MAX_TARGETS} nomor per run')
        c_target.grid(row=0, column=0, sticky='nsew', padx=(0, 10), pady=(0, 12))
        lbl = tk.Label(c_target, text='Satu nomor per baris; opsional: tambahkan nama setelah koma (cth: 08123456789, Andi).', font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD)
        lbl.pack(anchor='w', pady=(0, 8))
        self.targets_box = tk.Text(c_target, height=7, font=('Consolas', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, selectbackground='#322d56', relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, padx=8, pady=8)
        self.targets_box.pack(fill='both', expand=True)
        t_row = tk.Frame(c_target, bg=C_CARD)
        t_row.pack(fill='x', pady=(10, 0))
        self.create_btn(t_row, 'Impor CSV...', command=self.import_csv).pack(side='left')
        self.create_btn(t_row, 'Kosongkan', command=(lambda: self.targets_box.delete('1.0', 'end'))).pack(side='left', padx=8)
        self.target_count_var = tk.StringVar(value='0 nomor terdeteksi')
        tk.Label(t_row, textvariable=self.target_count_var, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='right')
        self.targets_box.bind('<KeyRelease>', self.update_target_counter)
        c_msg = self.card(self.tab_send, 'Pesan & Pengiriman', 'Tinjau template terpilih')
        c_msg.grid(row=0, column=1, sticky='nsew', padx=(10, 0), pady=(0, 12))
        tk.Label(c_msg, text='Pilih Template', font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(anchor='w', pady=(0, 4))
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(c_msg, textvariable=self.choice, state='readonly', style='Dark.TCombobox')
        self.combo.pack(fill='x', pady=(0, 8))
        self.combo.bind('<<ComboboxSelected>>', self.preview_message)
        self.preview = tk.Text(c_msg, height=5, wrap='word', font=('Segoe UI', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, selectbackground='#322d56', relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightthickness=1, padx=8, pady=8, state='disabled')
        self.preview.pack(fill='both', expand=True)
        self.image_note = tk.StringVar(value='Tanpa gambar')
        tk.Label(c_msg, textvariable=self.image_note, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(anchor='w', pady=(6, 4))
        self.consent = tk.BooleanVar(value=False)
        self.chk_consent = ttk.Checkbutton(c_msg, text='Saya memastikan semua penerima menyetujui pesan ini.', variable=self.consent, style='Dark.TCheckbutton')
        self.chk_consent.pack(anchor='w', pady=(0, 8))
        tk.Label(c_msg, text='Persetujuan wajib dikonfirmasi setiap run; daftar opt-out selalu diperiksa.', font=('Segoe UI', 8), fg=C_TEXT_MUTED, bg=C_CARD, wraplength=360, justify='left').pack(anchor='w', pady=(0, 8))
        self.start_button = tk.Button(c_msg, text=f'Mulai Run Batch (maks. {MAX_TARGETS} nomor)', bg=C_PURPLE, fg=C_TEXT_WHITE, activebackground=C_PURPLE_HOVER, activeforeground=C_TEXT_WHITE, relief='flat', cursor='hand2', font=('Segoe UI Semibold', 10), pady=8, bd=0, command=self.start_run)
        self.start_button.pack(fill='x')
        c_prog = self.card(self.tab_send, 'Progres & Hasil Pengiriman', 'Hasil per nomor & riwayat antrean')
        c_prog.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(10, 0))
        stat_row = tk.Frame(c_prog, bg=C_CARD)
        stat_row.pack(fill='x', pady=(0, 8))
        self.run_status = tk.StringVar(value='Hubungkan WhatsApp untuk memulai.')
        tk.Label(stat_row, textvariable=self.run_status, font=('Segoe UI Semibold', 9), fg=C_TEXT_PRIMARY, bg=C_CARD).pack(side='left')
        tree_frame = tk.Frame(c_prog, bg=C_CARD_INNER, highlightbackground=C_BORDER_SUBTLE, highlightthickness=1)
        tree_frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=('phone', 'status', 'detail'), show='headings', style='Dark.Treeview', height=7)
        for col, title, width in (('phone', 'Nomor', 190), ('status', 'Status', 150), ('detail', 'Keterangan', 540)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor='w')
        self.tree.pack(fill='both', expand=True)
        btn_row = tk.Frame(c_prog, bg=C_CARD)
        btn_row.pack(fill='x', pady=(10, 0))
        self.pause_button = self.create_btn(btn_row, 'Jeda', command=self.toggle_pause)
        self.pause_button.pack(side='left')
        self.pause_button.configure(state='disabled')
        self.stop_button = self.create_btn(btn_row, 'Hentikan', command=self.stop_run)
        self.stop_button.pack(side='left', padx=8)
        self.stop_button.configure(state='disabled')
        tk.Label(btn_row, text='Jeda acak 1-3 mnt & jeda 5 mnt tiap 10 nomor', font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='right')

    def build_templates(self):
        self.tab_templates.columnconfigure(0, weight=1)
        self.tab_templates.columnconfigure(1, weight=3)
        self.tab_templates.rowconfigure(0, weight=1)
        c_list = self.card(self.tab_templates, 'Pilih Template', '5 slot template tetap')
        c_list.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        self.template_list = tk.Listbox(c_list, height=12, exportselection=False, bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, selectbackground='#322d56', selectforeground=C_TEXT_WHITE, relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, font=('Segoe UI', 10), bd=0)
        self.template_list.pack(fill='both', expand=True, pady=(0, 8))
        for i in range(5):
            self.template_list.insert('end', f'Template {i + 1}')
        self.template_list.selection_set(0)
        self.template_list.bind('<<ListboxSelect>>', self.select_template)
        c_edit = self.card(self.tab_templates, 'Editor Template', 'Sesuaikan teks dan gambar pendukung')
        c_edit.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        tk.Label(c_edit, text='Nama Template', font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(anchor='w', pady=(0, 4))
        self.name = tk.StringVar()
        name_entry = tk.Entry(c_edit, textvariable=self.name, font=('Segoe UI', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, bd=0)
        name_entry.pack(fill='x', ipady=6, pady=(0, 10))
        var_row = tk.Frame(c_edit, bg=C_CARD)
        var_row.pack(fill='x', pady=(0, 4))
        tk.Label(var_row, text='Isi Pesan', font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='left')
        tk.Label(var_row, text='Variabel tersedia: {{nama}} dan {{nomor}}', font=('Segoe UI', 8), fg=C_PURPLE, bg=C_CARD).pack(side='right')
        self.body = tk.Text(c_edit, height=9, wrap='word', font=('Segoe UI', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, selectbackground='#322d56', selectforeground=C_TEXT_WHITE, relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, padx=10, pady=10)
        self.body.pack(fill='both', expand=True, pady=(0, 10))
        img_ctrl = tk.Frame(c_edit, bg=C_CARD)
        img_ctrl.pack(fill='x', pady=(0, 10))
        self.image_path = tk.StringVar()
        self.create_btn(img_ctrl, 'Pilih Gambar...', command=self.choose_image).pack(side='left')
        self.create_btn(img_ctrl, 'Hapus Gambar', command=self.remove_image).pack(side='left', padx=8)
        self.image_lbl = tk.Label(img_ctrl, textvariable=self.image_path, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD, anchor='w')
        self.image_lbl.pack(side='left', padx=8, fill='x', expand=True)
        self.thumb_card = tk.Frame(c_edit, bg=C_CARD_INNER, highlightbackground=C_BORDER_SUBTLE, highlightthickness=1, padx=10, pady=8)
        self.thumb_card.pack(fill='x', pady=(0, 12))
        self.thumb = tk.Label(self.thumb_card, text='Pratinjau gambar akan tampil di sini', font=('Segoe UI', 9), fg=C_TEXT_DIM, bg=C_CARD_INNER)
        self.thumb.pack(anchor='w')
        save_row = tk.Frame(c_edit, bg=C_CARD)
        save_row.pack(fill='x')
        self.save_btn = self.create_btn(save_row, 'Simpan Perubahan Template', command=self.save_template, variant='accent')
        self.save_btn.pack(side='right')

    def build_connection(self):
        self.tab_connection.columnconfigure(0, weight=1)
        self.tab_connection.columnconfigure(1, weight=1)
        self.tab_connection.rowconfigure(0, weight=1)
        c_conf = self.card(self.tab_connection, 'Koneksi Langsung WhatsApp Web', 'Sesi QR lokal; koneksi berjalan di komputer ini')
        c_conf.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        tk.Label(c_conf, text='Aplikasi membuka sesi WhatsApp Web lokal. Node.js dan dependensi perlu dipasang satu kali.', font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD, wraplength=360, justify='left').pack(anchor='w', pady=(0, 18))
        acl_note = ('ACL NTFS membatasi folder data ke akun Windows ini dan SYSTEM. Jika akun Windows dipakai bersama, tekan Logout WhatsApp setelah selesai.' if not DATA_ACL_ERROR else f'PERINGATAN: Proteksi folder data gagal; koneksi WhatsApp diblokir. {DATA_ACL_ERROR[:140]}')
        acl_color = C_TEXT_MUTED if not DATA_ACL_ERROR else C_RED
        tk.Label(c_conf, text=acl_note, font=('Segoe UI', 8), fg=acl_color, bg=C_CARD, wraplength=360, justify='left').pack(anchor='w', pady=(0, 12))
        self.country = tk.StringVar(value=self.data.get('country', '62'))
        tk.Label(c_conf, text='Kode Negara Default', font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(anchor='w', pady=(0, 3))
        entry = tk.Entry(c_conf, textvariable=self.country, font=('Segoe UI', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, bd=0)
        entry.pack(fill='x', ipady=5, pady=(0, 10))
        row_btn = tk.Frame(c_conf, bg=C_CARD)
        row_btn.pack(fill='x', pady=10)
        self.create_btn(row_btn, 'Hubungkan / Minta QR', command=self.connect, variant='accent').pack(side='left')
        self.create_btn(row_btn, 'Segarkan QR', command=self.refresh_qr).pack(side='left', padx=8)
        self.logout_btn = self.create_btn(row_btn, 'Logout WhatsApp', command=self.logout_whatsapp, variant='danger')
        self.logout_btn.pack(side='left')
        self.connection_label = tk.StringVar(value='Belum terhubung')
        tk.Label(c_conf, textvariable=self.connection_label, font=('Segoe UI Semibold', 10), fg=C_PURPLE, bg=C_CARD).pack(anchor='w', pady=(14, 4))
        self.account_frame = tk.Frame(c_conf, bg=C_CARD_INNER, highlightbackground=C_BORDER_SUBTLE, highlightthickness=1, padx=12, pady=10)
        self.account_frame.pack(fill='x', pady=(8, 8))
        tk.Label(self.account_frame, text='Nomor WhatsApp Terhubung:', font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD_INNER).pack(anchor='w')
        self.account_phone_lbl = tk.Label(self.account_frame, textvariable=self.account_phone_var, font=('Consolas', 11, 'bold'), fg=C_TEXT_DIM, bg=C_CARD_INNER)
        self.account_phone_lbl.pack(anchor='w', pady=(3, 0))
        tk.Label(c_conf, text='Sesi tertaut disimpan lokal untuk login berikutnya.', font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD, wraplength=360, justify='left').pack(anchor='w', pady=(4, 0))
        c_qr = self.card(self.tab_connection, 'Autentikasi WhatsApp (QR)', 'Pindai menggunakan aplikasi WhatsApp ponsel')
        c_qr.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self.qr_box = tk.Frame(c_qr, bg=C_CARD_INNER, highlightbackground=C_BORDER_SUBTLE, highlightthickness=1, pady=20)
        self.qr_box.pack(fill='both', expand=True, pady=(0, 10))
        self.qr = tk.Label(self.qr_box, text='QR Code akan muncul saat sesi meminta pemindaian.', font=('Segoe UI', 10), fg=C_TEXT_DIM, bg=C_CARD_INNER)
        self.qr.pack(expand=True)
        guide = tk.Label(c_qr, text='WhatsApp -> Perangkat Tertaut -> Tautkan Perangkat -> Pindai QR.', font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD, justify='center')
        guide.pack(anchor='center')

    def build_optout(self):
        c_opt = self.card(self.tab_optout, 'Daftar Nomor Opt-out (Blokir Otomatis)', 'Filter selalu aktif; nomor di sini otomatis dilewati dan tidak dapat dikirimi')
        c_opt.pack(fill='both', expand=True)
        self.optbox = tk.Text(c_opt, height=18, font=('Consolas', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, selectbackground='#322d56', selectforeground=C_TEXT_WHITE, relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, padx=12, pady=12)
        self.optbox.pack(fill='both', expand=True, pady=(0, 12))
        self.optbox.bind('<KeyRelease>', (lambda _e: self.root.after(250, self.save_optout)))
        row = tk.Frame(c_opt, bg=C_CARD)
        row.pack(fill='x')
        self.opt_count_var = tk.StringVar(value='0 nomor diblokir')
        tk.Label(row, textvariable=self.opt_count_var, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='left')
        self.create_btn(row, 'Simpan Daftar Opt-out', command=self.save_optout, variant='accent').pack(side='right')

    def switch_tab(self, key):
        self.active_tab = key
        frames = {'send': self.tab_send, 'templates': self.tab_templates, 'connection': self.tab_connection, 'optout': self.tab_optout}
        for k, f in frames.items():
            if k == key:
                f.lift()
            else:
                f.lower()
        titles = {'send': ('Kirim Pesan', ''), 'templates': ('Template Pesan', 'Kelola 5 template pesan dinamis dan gambar lampiran'), 'connection': ('Koneksi WhatsApp Web', 'Hubungkan WhatsApp Web langsung dengan QR'), 'optout': ('Daftar Opt-out', 'Pengecualian nomor pelanggan agar tidak menerima pengiriman')}
        t, sub = titles.get(key, ('WAblast', ''))
        self.title_var.set(t)
        self.subtitle_var.set(sub)
        if sub:
            self.subtitle_label.pack(anchor='w', pady=(2, 0))
        else:
            self.subtitle_label.pack_forget()
        self.update_nav_highlights()

    def update_nav_highlights(self):
        for k, item in self.nav_expanded_widgets.items():
            is_active = k == self.active_tab
            bg = C_ACTIVE_PILL if is_active else C_SIDEBAR
            fg = C_TEXT_WHITE if is_active else C_TEXT_MUTED
            icon_fg = C_TEXT_WHITE if is_active else C_TEXT_MUTED
            item['row'].configure(bg=bg)
            item['icon'].configure(bg=bg)
            item['lbl'].configure(bg=bg, fg=fg, font=('Segoe UI Semibold' if is_active else 'Segoe UI', 9))
            draw_vector_icon(item['icon'], k, icon_fg, bg=bg, active=is_active)
        for k, item in self.nav_collapsed_widgets.items():
            is_active = k == self.active_tab
            bg = C_ACTIVE_PILL if is_active else C_SIDEBAR
            icon_fg = C_TEXT_WHITE if is_active else C_TEXT_MUTED
            item['frame'].configure(bg=bg, highlightbackground=C_PURPLE if is_active else C_SIDEBAR, highlightthickness=1 if is_active else 0)
            item['icon'].configure(bg=bg)
            draw_vector_icon(item['icon'], k, icon_fg, bg=bg, active=is_active)

    def on_exp_nav_hover(self, key, is_hover):
        if key != self.active_tab and key in self.nav_expanded_widgets:
            item = self.nav_expanded_widgets[key]
            bg = C_HOVER if is_hover else C_SIDEBAR
            fg = C_TEXT_PRIMARY if is_hover else C_TEXT_MUTED
            item['row'].configure(bg=bg)
            item['icon'].configure(bg=bg)
            item['lbl'].configure(bg=bg, fg=fg)
            draw_vector_icon(item['icon'], key, fg, bg=bg, active=False)

    def on_col_nav_hover(self, key, is_hover):
        if key != self.active_tab and key in self.nav_collapsed_widgets:
            item = self.nav_collapsed_widgets[key]
            bg = C_HOVER if is_hover else C_SIDEBAR
            fg = C_TEXT_PRIMARY if is_hover else C_TEXT_MUTED
            item['frame'].configure(bg=bg)
            item['icon'].configure(bg=bg)
            draw_vector_icon(item['icon'], key, fg, bg=bg, active=False)

    def on_cta_click(self):
        self.switch_tab('send')
        self.targets_box.focus_set()

    def update_target_counter(self, _e=None):
        txt = self.targets_box.get('1.0', 'end-1c')
        lines = [line.strip() for line in txt.splitlines() if line.strip()]
        self.target_count_var.set(f'{len(lines)} nomor dimasukkan (maks. {MAX_TARGETS})')

    def templates(self):
        items = self.data.get('templates', DEFAULTS)
        while len(items) < 5:
            items.append({'name': f'Template {len(items) + 1}', 'text': '', 'image': ''})
        self.data['templates'] = items[:5]
        return self.data['templates']

    def persist(self):
        if hasattr(self, 'country'):
            self.data['country'] = self.country.get().strip()
            self.data.pop('base_url', None)
            self.data.pop('session', None)
            self.data.pop('api_key', None)
        save_settings(self.data)

    def store_editor(self):
        if not hasattr(self, 'body'):
            return
        item = self.templates()[self.editor_index]
        item.update(name=self.name.get().strip() or f'Template {self.editor_index + 1}', text=self.body.get('1.0', 'end-1c'), image=self.image_path.get().strip())
        self.persist()

    def load_template(self, index):
        self.editor_index = index
        item = self.templates()[index]
        self.name.set(item.get('name') or f'Template {index + 1}')
        self.body.delete('1.0', 'end')
        self.body.insert('1.0', item.get('text', ''))
        self.image_path.set(item.get('image', ''))
        self.show_thumbnail(item.get('image', ''))
        self.refresh_names()

    def refresh_names(self):
        names = [x.get('name') or f'Template {i + 1}' for i, x in enumerate(self.templates())]
        if hasattr(self, 'template_list'):
            self.template_list.delete(0, 'end')
            for n in names:
                self.template_list.insert('end', n)
            self.template_list.selection_clear(0, 'end')
            self.template_list.selection_set(self.editor_index)
        if hasattr(self, 'combo'):
            display_names = []
            self.combo_template_map = {}
            for i, name in enumerate(names):
                label = name
                if name == RANDOM_TEMPLATE_OPTION or names.count(name) > 1:
                    label = f'{name} (Template {i + 1})'
                display_names.append(label)
                self.combo_template_map[label] = i
            options = [RANDOM_TEMPLATE_OPTION, *display_names]
            selected = self.choice.get()
            self.combo.configure(values=options)
            if selected not in options:
                self.choice.set(display_names[self.editor_index])
        self.preview_message()

    def select_template(self, _event=None):
        selected = self.template_list.curselection()
        if selected and selected[0] != self.editor_index:
            self.store_editor()
            self.load_template(selected[0])

    def save_template(self):
        self.store_editor()
        self.refresh_names()
        self.run_status.set('Template tersimpan.')

    def choose_image(self):
        path = filedialog.askopenfilename(filetypes=[('Gambar JPEG, PNG, WebP', '*.jpg *.jpeg *.png *.webp'), ('Semua file', '*.*')])
        if path:
            try:
                validate_image(path)
            except ValueError as exc:
                messagebox.showwarning('Gambar tidak valid', str(exc))
                return
            self.image_path.set(path)
            self.show_thumbnail(path)
            self.store_editor()
            self.preview_message()

    def remove_image(self):
        self.image_path.set('')
        self.show_thumbnail('')
        self.store_editor()
        self.preview_message()

    def show_thumbnail(self, path):
        self.template_image = None
        if not path:
            self.thumb.configure(text='Tanpa gambar lampiran', image='')
            return
        if not Path(path).is_file():
            self.thumb.configure(text=f'File tak ditemukan: {Path(path).name}', image='')
            return
        try:
            from PIL import Image, ImageTk
            with Image.open(path) as img:
                image = img.copy()
            image.thumbnail((500, 160))
            self.template_image = ImageTk.PhotoImage(image)
            self.thumb.configure(text=f'  {Path(path).name}', image=self.template_image, compound='left')
        except ImportError:
            self.thumb.configure(text=f'{Path(path).name} (Pillow opsional untuk thumbnail)', image='')
            return
        except Exception:
            self.thumb.configure(text=Path(path).name, image='')
            return

    def preview_message(self, _event=None):
        if not hasattr(self, 'preview'):
            return
        title = self.choice.get()
        templates = self.templates()
        if title == RANDOM_TEMPLATE_OPTION:
            eligible = [(i, item) for i, item in enumerate(templates) if item.get('text', '').strip()]
            self.preview.configure(state='normal')
            self.preview.delete('1.0', 'end')
            if eligible:
                names = [item.get('name') or f'Template {i + 1}' for i, item in eligible]
                self.preview.insert('1.0', 'Setiap nomor mendapat template acak dari yang terisi:\n' + '\n'.join(f'* {name}' for name in names))
                image_count = sum(bool(item.get('image')) for _, item in eligible)
                image_text = f'{image_count} template memiliki gambar' if image_count else 'Tidak ada gambar pada template terisi'
                self.image_note.set(f'Lampiran mengikuti template acak | {image_text}')
            else:
                self.preview.insert('1.0', 'Belum ada template berisi pesan. Isi teks pada menu Template terlebih dahulu.')
                self.image_note.set('Tidak ada template yang dapat dipilih')
            self.preview.configure(state='disabled')
            return
        index = self.combo_template_map.get(title)
        item = templates[index] if index is not None else templates[self.editor_index]
        text = item.get('text') or 'Isi pesan kosong. Buka menu Template untuk mengisinya.'
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        self.preview.insert('1.0', text)
        self.preview.configure(state='disabled')
        if item.get('image'):
            self.image_note.set('Gambar: ' + Path(item['image']).name)
            return
        self.image_note.set('Tanpa gambar lampiran')

    def load_optout(self):
        self.optbox.delete('1.0', 'end')
        opt_list = self.data.get('optout', [])
        self.optbox.insert('1.0', '\n'.join(opt_list))
        self.opt_count_var.set(f'{len(opt_list)} nomor terdaftar opt-out')

    def save_optout(self):
        numbers = []
        for line in self.optbox.get('1.0', 'end-1c').splitlines():
            if not line.strip():
                continue
            try:
                numbers.append(phone_number(line, self.country.get()))
            except ValueError:
                continue
        self.data['optout'] = sorted(set(numbers))
        self.opt_count_var.set(f"{len(self.data['optout'])} nomor terdaftar opt-out")
        self.persist()

    def import_csv(self):
        filename = filedialog.askopenfilename(filetypes=[('CSV', '*.csv'), ('All files', '*.*')])
        if not filename:
            return
        try:
            with open(filename, encoding='utf-8-sig', newline='') as f:
                rows = [row for row in csv.reader(f) if row and row[0].strip()]
        except (OSError, csv.Error) as exc:
            messagebox.showerror('Gagal impor CSV', str(exc))
            return
        try:
            rows = [r for r in rows if r[0].strip().lower() not in {'nomor', 'phone', 'nomor telepon'}]
            sanitized_rows = []
            for r in rows:
                phone = r[0].strip()
                name = r[1].strip() if len(r) > 1 else ''
                if name and name[0] in ('=', '+', '-', '@'):
                    name = "'" + name
                sanitized_rows.append((phone, name) if name else (phone,))
            existing = self.targets_box.get('1.0', 'end-1c').strip()
            if existing:
                self.targets_box.insert('end', '\n')
            self.targets_box.insert('end', '\n'.join((','.join(r) for r in sanitized_rows)))
            self.update_target_counter()
        except Exception as exc:
            messagebox.showerror('Gagal impor CSV', str(exc))
            return

    def make_client(self):
        return DirectWhatsApp(self.events)

    def has_saved_session(self):
        session_path = DATA / 'whatsapp_auth' / 'session-wablast'
        if not session_path.is_dir():
            return False
        idb_path = session_path / 'Default' / 'IndexedDB' / 'https_web.whatsapp.com_0.indexeddb.leveldb'
        if idb_path.is_dir():
            try:
                files = [f for f in idb_path.iterdir() if f.is_file() and f.suffix in {'.ldb', '.log'}]
                return len(files) > 0 and sum(f.stat().st_size for f in files) > 50000
            except OSError:
                return False
        return False

    def auto_connect_if_saved_session(self):
        if self.has_saved_session() and self.status not in {'WORKING', 'STARTING'}:
            self.connection_label.set('Memulihkan sesi WhatsApp tersimpan...')
            self.top_status.set('Menghubungkan...')
            self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
            self.connect()

    def connect(self):
        if self.logout_busy or self.connect_busy:
            return
        if DATA_ACL_ERROR:
            messagebox.showerror('Proteksi penyimpanan gagal', 'Sesi WhatsApp tidak dijalankan karena folder data belum berhasil dibatasi dengan ACL NTFS. Periksa konfigurasi keamanan Windows, lalu buka ulang WAblast.\n\n' + DATA_ACL_ERROR)
            return
        if self.status == 'WORKING':
            messagebox.showinfo('WhatsApp Terhubung', 'WhatsApp Web sudah terhubung dan siap digunakan.')
            return
        if self.status == 'STARTING':
            self.connection_label.set('WhatsApp Web sedang memulai sesi, mohon tunggu...')
            return
        self.connect_busy = True
        if self.client is None:
            self.client = self.make_client()
        self.persist()
        self.connection_label.set('Membuka WhatsApp Web lokal...')
        self.status = 'STARTING'
        self.top_status.set('Menghubungkan...')
        self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
        threading.Thread(target=self.connect_worker, args=(self.client,), daemon=True).start()

    def connect_worker(self, client):
        try:
            client.connect()
            self.events.put(('label', 'WhatsApp Web berjalan. Menunggu QR atau koneksi...'))
        except Exception as exc:
            self.events.put(('error', str(exc)))
        finally:
            self.connect_busy = False

    def refresh_qr(self):
        if self.logout_busy:
            return
        if DATA_ACL_ERROR:
            messagebox.showerror('Proteksi penyimpanan gagal', 'Sesi WhatsApp tidak dijalankan karena folder data belum berhasil dibatasi dengan ACL NTFS.\n\n' + DATA_ACL_ERROR)
            return
        if self.client is None:
            self.client = self.make_client()
        self.connection_label.set('Meminta QR...')
        threading.Thread(target=self.qr_worker, args=(self.client,), daemon=True).start()

    def qr_worker(self, client):
        try:
            self.events.put(('qr', base64.b64encode(client.qr()).decode('ascii')))
        except Exception as exc:
            self.events.put(('error', str(exc)))
            return

    def logout_whatsapp(self):
        if self.logout_busy:
            return
        if self.worker and self.worker.is_alive():
            messagebox.showwarning('Run masih berlangsung', 'Hentikan atau tunggu antrean selesai sebelum logout.')
            return
        if not messagebox.askyesno('Logout WhatsApp', 'Apakah Anda yakin ingin logout dari akun WhatsApp?'):
            return
        if self.client is None:
            self.client = self.make_client()
        self.logout_busy = True
        self.logout_btn.configure(state='disabled')
        self.connection_label.set('Memproses logout WhatsApp...')
        self.root.after(150, self.start_logout_when_poll_idle)

    def start_logout_when_poll_idle(self):
        if not self.logout_busy:
            return
        if not hasattr(self, '_logout_wait_count'):
            self._logout_wait_count = 0
        if self.poll_busy and self._logout_wait_count < 5:
            self._logout_wait_count += 1
            self.root.after(200, self.start_logout_when_poll_idle)
            return
        self._logout_wait_count = 0
        self.poll_busy = False
        threading.Thread(target=self.logout_worker, args=(self.client,), daemon=True).start()

    def logout_worker(self, client):
        try:
            result = client.logout() if client else {}
            self.events.put(('logout_done', result or {}))
        except Exception as exc:
            try:
                if client:
                    client.close()
            except Exception:
                pass
            try:
                kill_session_browsers(force_wmi=True)
                session_path = DATA / 'whatsapp_auth' / 'session-wablast'
                if session_path.exists():
                    shutil.rmtree(session_path, ignore_errors=True)
            except Exception:
                pass
            self.events.put(('logout_done', {'fallback': True, 'error': str(exc)}))

    def poll_tick(self):
        now = time.monotonic()
        sending_active = bool(self.worker and self.worker.is_alive())
        if self.client and not self.logout_busy and not self.connect_busy and not sending_active and self.status != 'STOPPED' and not self.poll_busy and now >= self.next_poll:
            self.poll_busy = True
            self.next_poll = now + 4
            threading.Thread(target=self.poll_worker, args=(self.client,), daemon=True).start()
        self.root.after(500, self.poll_tick)

    def poll_worker(self, client):
        try:
            info = client.get_session()
            state = (info or {}).get('status', 'UNKNOWN') if isinstance(info, dict) else 'UNKNOWN'
            account = (info or {}).get('account') if isinstance(info, dict) else None
            now = time.monotonic()
            if state in {'SCAN_QR', 'SCAN_QR_CODE'} and now - self.last_qr > 15:
                try:
                    image = base64.b64encode(client.qr()).decode('ascii')
                    self.last_qr = now
                    if image:
                        self.events.put(('qr', image))
                except Exception:
                    pass
            self.events.put(('status', (state, None, account)))
        except Exception as exc:
            self.events.put(('poll_error', str(exc)))
        self.events.put(('poll_done', None))

    def drain(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'label':
                    self.connection_label.set(value)
                elif kind == 'error':
                    self.connection_label.set('Koneksi gagal')
                    self.top_status.set('Periksa WhatsApp Web')
                    self.top_dot_canvas.itemconfig(self.top_dot, fill=C_RED)
                    messagebox.showerror('Operasi WhatsApp Web gagal', value)
                elif kind == 'logout_done':
                    self.logout_busy = False
                    self.logout_btn.configure(state='normal')
                    self.account_info = None
                    self.apply_status('STOPPED', 'Telah logout dari WhatsApp')
                    self.top_status.set('WhatsApp Logout')
                    self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
                    self.qr.configure(text='Sesi telah logout. Klik Hubungkan untuk menghubungkan akun baru.', image='')
                    self.qr_image = None
                    if isinstance(value, dict) and value.get('fallback'):
                        self.client = None
                    messagebox.showinfo('Logout WhatsApp', 'Anda telah berhasil logout dari akun WhatsApp.')
                elif kind == 'logout_error':
                    self.logout_busy = False
                    self.logout_btn.configure(state='normal')
                    self.connection_label.set('Gagal logout')
                    messagebox.showerror('Gagal logout', f'Tidak dapat logout dari WhatsApp: {value}')
                elif kind == 'poll_error':
                    self.status = 'UNKNOWN'
                    self.top_status.set('WhatsApp Web offline')
                    self.top_dot_canvas.itemconfig(self.top_dot, fill=C_RED)
                    self.connection_label.set(value)
                elif kind == 'poll_done':
                    self.poll_busy = False
                elif kind == 'status':
                    if isinstance(value, tuple):
                        self.apply_status(*value)
                    else:
                        self.apply_status(value)
                elif kind == 'qr':
                    self.show_qr(value)
                elif kind == 'row':
                    self.set_row(*value)
                elif kind == 'run_status':
                    self.run_status.set(value)
                elif kind == 'run_done':
                    self.run_finished(value)
                elif kind == 'countdown':
                    self.run_status.set(value)
                try:
                    if kind == 'label':
                        self.connection_label.set(value)
                    elif kind == 'error':
                        self.connection_label.set('Koneksi gagal')
                        self.top_status.set('Periksa WhatsApp Web')
                        self.top_dot_canvas.itemconfig(self.top_dot, fill=C_RED)
                        messagebox.showerror('Operasi WhatsApp Web gagal', value)
                    elif kind == 'logout_done':
                        self.logout_busy = False
                        self.logout_btn.configure(state='normal')
                        self.account_info = None
                        self.apply_status('STOPPED', 'Telah logout dari WhatsApp')
                        self.top_status.set('WhatsApp Logout')
                        self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
                        self.qr.configure(text='Sesi telah logout. Klik Hubungkan untuk menghubungkan akun baru.', image='')
                        self.qr_image = None
                        if isinstance(value, dict) and value.get('fallback'):
                            self.client = None
                        messagebox.showinfo('Logout WhatsApp', 'Anda telah berhasil logout dari akun WhatsApp.')
                    elif kind == 'logout_error':
                        self.logout_busy = False
                        self.logout_btn.configure(state='normal')
                        self.connection_label.set('Gagal logout')
                        messagebox.showerror('Gagal logout', f'Tidak dapat logout dari WhatsApp: {value}')
                    elif kind == 'poll_error':
                        self.status = 'UNKNOWN'
                        self.top_status.set('WhatsApp Web offline')
                        self.top_dot_canvas.itemconfig(self.top_dot, fill=C_RED)
                        self.connection_label.set(value)
                    elif kind == 'poll_done':
                        self.poll_busy = False
                    elif kind == 'status':
                        if isinstance(value, tuple):
                            self.apply_status(*value)
                        else:
                            self.apply_status(value)
                    elif kind == 'qr':
                        self.show_qr(value)
                    elif kind == 'row':
                        self.set_row(*value)
                    elif kind == 'run_status':
                        self.run_status.set(value)
                    elif kind == 'run_done':
                        self.run_finished(value)
                    elif kind == 'countdown':
                        self.run_status.set(value)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(200, self.drain)
        finally:
            self.root.after(150, self.drain)

    def apply_status(self, state, detail=None, account=None):
        self.status = state
        if account:
            self.account_info = account
        labels = {'WORKING': 'Terhubung - WhatsApp siap', 'SCAN_QR_CODE': 'Menunggu pemindaian QR', 'SCAN_QR': 'Menunggu pemindaian QR', 'STARTING': 'WhatsApp Web memulai sesi...', 'STOPPED': 'Sesi berhenti', 'FAILED': 'Sesi gagal; coba hubungkan ulang', 'UNKNOWN': 'Status tidak diketahui'}
        label = detail if detail else labels.get(state, f'Status sesi: {state}')
        self.connection_label.set(label)
        if state == 'WORKING':
            self.top_status.set('WhatsApp Siap')
            self.top_dot_canvas.itemconfig(self.top_dot, fill=C_GREEN)
            self.qr.configure(text='WhatsApp terhubung dan siap digunakan.', image='')
            self.qr_image = None
            phone = (self.account_info or {}).get('phone') if isinstance(self.account_info, dict) else None
            pushname = (self.account_info or {}).get('pushname') if isinstance(self.account_info, dict) else None
            if phone:
                masked = mask_account_phone(phone)
                if pushname:
                    masked += f' ({pushname})'
                self.account_phone_var.set(masked)
                self.account_phone_lbl.configure(fg=C_EMERALD)
            return
        if state in {'SCAN_QR', 'SCAN_QR_CODE'}:
            self.top_status.set('Scan QR WhatsApp')
            self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
            self.account_phone_var.set('Menunggu scan QR')
            self.account_phone_lbl.configure(fg=C_AMBER)
            if not self.qr_image:
                self.connection_label.set('Memuat QR Code...')
            return
        if state == 'STARTING':
            top_text = detail if detail else 'Memulai sesi...'
            self.top_status.set(top_text)
            self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
            if detail:
                self.connection_label.set(detail)
            else:
                self.connection_label.set('WhatsApp Web memulai sesi...')
            self.account_phone_var.set('Menghubungkan...')
            self.account_phone_lbl.configure(fg=C_AMBER)
            return
        self.top_status.set(label)
        self.top_dot_canvas.itemconfig(self.top_dot, fill=C_RED)
        self.account_phone_var.set('Belum terhubung')
        self.account_phone_lbl.configure(fg=C_TEXT_DIM)
        if state not in {'SCAN_QR', 'STARTING', 'SCAN_QR_CODE'}:
            self.qr.configure(text='QR Code akan tampil di sini.', image='')
            self.qr_image = None

    def show_qr(self, encoded):
        try:
            raw = base64.b64decode(encoded)
            try:
                from PIL import Image, ImageTk
                import io
                image = Image.open(io.BytesIO(raw)).convert('RGB')
                image.thumbnail((260, 260))
                self.qr_image = ImageTk.PhotoImage(image)
            except ImportError:
                self.qr_image = tk.PhotoImage(data=encoded)
            self.qr.configure(image=self.qr_image, text='')
            self.connection_label.set('Silakan pindai QR Code di sebelah kanan melalui WhatsApp di HP')
        except Exception as exc:
            self.connection_label.set(f'QR tidak dapat ditampilkan: {exc}')
            return

    def start_run(self):
        if self.worker and self.worker.is_alive():
            return
        if self.logout_busy:
            messagebox.showwarning('Logout berlangsung', 'Tunggu hingga proses logout selesai.')
            return
        if self.status not in {'WORKING', 'STARTING'}:
            messagebox.showwarning('Belum terhubung', 'Hubungkan WhatsApp Web dan pindai QR terlebih dahulu.')
            self.switch_tab('connection')
            return
        if not self.consent.get():
            messagebox.showwarning('Persetujuan', 'Pastikan penerima menyetujui pesan ini.')
            return
        self.store_editor()
        invalid_optout = []
        for line_number, line in enumerate(self.optbox.get('1.0', 'end-1c').splitlines(), 1):
            if not line.strip():
                continue
            try:
                phone_number(line, self.country.get())
            except ValueError:
                invalid_optout.append(line_number)
        if invalid_optout:
            lines = ', '.join(map(str, invalid_optout[:10]))
            messagebox.showwarning('Nomor opt-out tidak valid', f'Perbaiki nomor pada baris {lines} di Daftar Opt-out sebelum menjalankan batch. Tidak ada pesan yang dikirim.')
            self.switch_tab('optout')
            return
        self.save_optout()
        targets, errors = parse_targets(self.targets_box.get('1.0', 'end-1c'), self.country.get(), set(self.data.get('optout', [])))
        if not targets:
            messagebox.showwarning('Nomor tujuan', 'Masukkan setidaknya satu nomor valid.')
            return
        if len(targets) > MAX_TARGETS:
            messagebox.showwarning('Batas run', f'Maksimal {MAX_TARGETS} nomor per run.')
            return
        all_templates = self.templates()
        random_mode = self.choice.get() == RANDOM_TEMPLATE_OPTION
        if random_mode:
            selected_templates = [item for item in all_templates if item.get('text', '').strip()]
            if not selected_templates:
                messagebox.showwarning('Template kosong', 'Isi setidaknya satu template. Template kosong tidak akan dipilih.')
                return
        else:
            template_index = self.combo_template_map.get(self.choice.get())
            if template_index is None:
                messagebox.showwarning('Template kosong', 'Pilih template yang valid.')
                return
            selected_templates = [all_templates[template_index]]
            if not selected_templates[0].get('text', '').strip():
                messagebox.showwarning('Template kosong', 'Isi template sebelum mengirim.')
                return
        for item in selected_templates:
            if item.get('image'):
                try:
                    validate_image(item['image'])
                except ValueError as exc:
                    template_name = item.get('name') or 'Template'
                    messagebox.showwarning('Gambar tidak valid', f'{template_name}: {exc}\nPilih gambar yang valid pada menu Template.')
                    return
        if random_mode:
            names = ', '.join(item.get('name') or f'Template {all_templates.index(item) + 1}' for item in selected_templates)
            prompt = f'Kirim ke {len(targets)} nomor dengan template acak per nomor?\n\nTemplate yang dapat dipilih: {names}\nJeda acak 1-3 menit antar pesan & istirahat 5 menit tiap 10 nomor.'
        else:
            prompt = f"Kirim '{selected_templates[0].get('name')}' ke {len(targets)} nomor?\n\nJeda acak 1-3 menit antar pesan & istirahat 5 menit tiap 10 nomor."
        if errors:
            prompt += '\n\nCatatan: Nomor salah, duplikat, atau opt-out otomatis dilewati.'
        if not messagebox.askyesno('Konfirmasi run', prompt):
            return
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.rows = {i: self.tree.insert('', 'end', values=(t.phone, 'Menunggu', '')) for i, t in enumerate(targets)}
        self.rows = {}
        for i, t in enumerate(targets):
            if i == 0:
                self.rows[i] = self.tree.insert('', 'end', values=(t.phone, 'Mengirim', 'Mengirim pesan langsung...'))
            else:
                self.rows[i] = self.tree.insert('', 'end', values=(t.phone, 'Menunggu', 'Menunggu giliran (disiapkan saat jeda)'))
        self.cancel.clear()
        self.resume.set()
        self.start_button.configure(state='disabled')
        self.pause_button.configure(state='normal', text='Jeda')
        self.stop_button.configure(state='normal')
        self.run_status.set(f'Menyiapkan {len(targets)} pesan...')
        self.run_status.set(f'Mengirim pesan 1 dari {len(targets)} ke {targets[0].phone}...')
        self.root.update_idletasks()
        self.worker = threading.Thread(target=self.send_batch, args=(self.client, targets, [dict(item) for item in selected_templates], random_mode), daemon=True)
        self.worker.start()

    def render_text(self, text, target):
        mapping = {
            'nama': target.name or '',
            'name': target.name or '',
            'nomor': target.phone,
            'phone': target.phone
        }
        return re.sub(r'\{\{(nama|name|nomor|phone)\}\}', lambda m: mapping[m.group(1)], text)

    def send_batch(self, client, targets, template_options, random_mode=False):
        outcomes = []
        used_templates = [''] * len(targets)

        def ensure_session_ready():
            try:
                info = client.get_session()
                state = (info or {}).get('status') if isinstance(info, dict) else None
                state_error = None
            except Exception as exc:
                state = None
                state_error = str(exc)[:160]
            if state == 'STARTING':
                wait_sec = 0
                max_wait = 300
                while state == 'STARTING' and wait_sec < max_wait and not self.cancel.is_set():
                    rem = max_wait - wait_sec
                    rem_str = f'{rem // 60:02d}:{rem % 60:02d}'
                    self.events.put(('countdown', f'Menunggu kesiapan WhatsApp ({rem_str})...'))
                    self.events.put(('run_status', f'Menyiapkan sesi WhatsApp ({rem_str} tersisa, maks 5 menit)...'))
                    for _ in range(10):
                        if self.cancel.wait(0.1):
                            break
                    wait_sec += 1
                    try:
                        info = client.get_session()
                        state = (info or {}).get('status') if isinstance(info, dict) else None
                    except Exception:
                        pass
            if state == 'WORKING':
                return True, None
            if self.cancel.is_set():
                detail = 'Dihentikan pengguna'
            elif state == 'STARTING':
                detail = 'Sesi WhatsApp Web masih proses sinkronisasi setelah menunggu 5 menit; antrean dihentikan.'
            else:
                detail = f"Sesi WhatsApp Web {state or 'tidak diketahui'}; antrean dihentikan."
            if state_error:
                detail += f' ({state_error})'
            return False, detail

        def prepare_target_during_delay(idx, target):
            template = random.choice(template_options) if random_mode else template_options[0]
            template_name = template.get('name') or 'Template'
            rendered_text = self.render_text(template['text'], target)
            image_path = template.get('image', '')
            image_mime = validate_image(image_path) if image_path else None
            try:
                exists = client.check_exists(target.phone)
            except Exception:
                exists = {'numberExists': True, 'chatId': f"{target.phone}@c.us", 'fallback': True}
            if isinstance(exists, dict) and exists.get('numberExists') is False:
                return {'skip': True, 'template_name': template_name}
            chat_id = exists.get('chatId') if isinstance(exists, dict) else f"{target.phone}@c.us"
            return {
                'skip': False,
                'chat_id': chat_id,
                'template_name': template_name,
                'text': rendered_text,
                'image_path': image_path,
                'image_mime': image_mime
            }

        try:
            for i, target in enumerate(targets):
                if self.cancel.is_set():
                    outcomes.append('Dibatalkan')
                    self.events.put(('row', (i, 'Dibatalkan', 'Dihentikan pengguna')))
                    continue
                if i:
                    if i % BATCH_REST_INTERVAL == 0:
                        left = BATCH_REST_DELAY
                        is_batch_rest = True

                if i == 0:
                    # Pesan pertama: langsung kirim tanpa jeda
                    ok_ready, err_detail = ensure_session_ready()
                    if not ok_ready:
                        for j in range(i, len(targets)):
                            outcomes.append('Dibatalkan')
                            self.events.put(('row', (j, 'Dibatalkan', err_detail)))
                        break
                    try:
                        template = random.choice(template_options) if random_mode else template_options[0]
                        template_name = template.get('name') or 'Template'
                        used_templates[i] = template_name
                        rendered_text = self.render_text(template['text'], target)
                        image_path = template.get('image', '')
                        image_mime = validate_image(image_path) if image_path else None
                        self.events.put(('row', (i, 'Mengirim', f'Mengirim langsung ({template_name})...')))
                        self.events.put(('run_status', f'Mengirim pesan 1 dari {len(targets)} ke {target.phone}...'))
                        client.send(target, rendered_text, image_path, None, image_mime, check_first=True)
                        status, detail = 'Dikirim', f'Terkirim ke WhatsApp server | {template_name}'
                    except Exception as exc:
                        err_msg = str(exc)[:220]
                        if re.search(r'not found|not registered|tidak terdaftar|invalid wid', err_msg, re.IGNORECASE):
                            status, detail = 'Dilewati', 'Nomor tidak terdaftar di WhatsApp'
                        else:
                            status, detail = 'Gagal', err_msg
                    outcomes.append(status)
                    self.events.put(('row', (i, status, detail)))
                    self.events.put(('run_status', f'Selesai 1 dari {len(targets)} - {target.phone} ({status})'))
                    continue

                # Pesan ke-2 dst: siapkan pesan & chat SELAMA jeda berlangsung, lalu langsung kirim saat jeda habis
                if i % BATCH_REST_INTERVAL == 0:
                    left = BATCH_REST_DELAY
                    is_batch_rest = True
                else:
                    left = DELAY + random.randint(DELAY_JITTER_MIN, DELAY_JITTER_MAX)
                    is_batch_rest = False

                prep_holder = {'done': False, 'data': None}
                self.events.put(('row', (i, 'Menyiapkan', 'Menyiapkan pesan & chat selama waktu jeda...')))

                def _bg_prepare(idx=i, tgt=target, holder=prep_holder):
                    try:
                        res = prepare_target_during_delay(idx, tgt)
                        holder['data'] = res
                    except Exception as exc:
                        tpl = random.choice(template_options) if random_mode else template_options[0]
                        tpl_name = tpl.get('name') or 'Template'
                        img_p = tpl.get('image', '')
                        holder['data'] = {
                            'skip': False,
                            'chat_id': f'{tgt.phone}@c.us',
                            'template_name': tpl_name,
                            'text': self.render_text(tpl['text'], tgt),
                            'image_path': img_p,
                            'image_mime': validate_image(img_p) if img_p else None
                        }
                    holder['done'] = True
                    if not self.cancel.is_set():
                        if holder['data'].get('skip'):
                            self.events.put(('row', (idx, 'Tidak Terdaftar', 'Nomor tidak terdaftar di WhatsApp (akan dilewati)')))
                        else:
                            tname = holder['data'].get('template_name') or 'Template'
                            self.events.put(('row', (idx, 'Siap Kirim', f'Pesan ({tname}) siap, langsung terkirim saat jeda selesai')))

                prep_thread = threading.Thread(target=_bg_prepare, daemon=True)
                prep_thread.start()

                while left > 0:
                    if self.cancel.is_set():
                        break
                    if not self.resume.is_set():
                        self.resume.wait(0.25)
                        continue
                    time_str = f'{left // 60:02d}:{left % 60:02d}'
                    prefix = f'Istirahat batch ({i} selesai)' if is_batch_rest else 'Jeda acak'
                    if prep_holder['done']:
                        self.events.put(('countdown', f'{prefix} ({time_str}) - Pesan ke {target.phone} SIAP, langsung kirim saat jeda habis'))
                    else:
                        left = DELAY + random.randint(DELAY_JITTER_MIN, DELAY_JITTER_MAX)
                        is_batch_rest = False
                    while left > 0:
                        if self.cancel.is_set():
                            break
                        if not self.resume.is_set():
                            self.resume.wait(0.25)
                            continue
                        if is_batch_rest:
                            self.events.put(('countdown', f'Istirahat batch ({i} nomor selesai) - lanjut dalam {left // 60:02d}:{left % 60:02d}'))
                        else:
                            self.events.put(('countdown', f'Jeda acak - pesan berikutnya dalam {left // 60:02d}:{left % 60:02d}'))
                        step = min(1, left)
                        if self.cancel.wait(step):
                            break
                        left -= step
                        self.events.put(('countdown', f'{prefix} ({time_str}) - Menyiapkan pesan ke {target.phone}...'))
                    step = min(1, left)
                    if self.cancel.wait(step):
                        break
                    left -= step

                if self.cancel.is_set():
                    outcomes.append('Dibatalkan')
                    self.events.put(('row', (i, 'Dibatalkan', 'Dihentikan pengguna')))
                    continue
                self.events.put(('run_status', f'Menyiapkan pengiriman {i + 1} dari {len(targets)} ({target.phone})...'))
                try:
                    info = client.get_session()
                    state = (info or {}).get('status') if isinstance(info, dict) else None
                except Exception as exc:
                    state = None
                    state_error = str(exc)[:160]
                else:
                    state_error = None
                # Toleransi tunggu maksimal 300 detik (5 menit) jika WhatsApp masih proses sinkronisasi chat awal
                if state == 'STARTING':
                    wait_sec = 0
                    max_wait = 300
                    while state == 'STARTING' and wait_sec < max_wait and not self.cancel.is_set():
                        rem = max_wait - wait_sec
                        rem_str = f'{rem // 60:02d}:{rem % 60:02d}'
                        self.events.put(('countdown', f'Menunggu kesiapan WhatsApp ({rem_str})...'))
                        self.events.put(('run_status', f'Menyiapkan sesi WhatsApp ({rem_str} tersisa, maks 5 menit)...'))
                        for _ in range(10):
                            if self.cancel.wait(0.1):
                                break
                        wait_sec += 1
                        try:
                            info = client.get_session()
                            state = (info or {}).get('status') if isinstance(info, dict) else None
                        except Exception:
                            pass

                if state != 'WORKING':
                    if self.cancel.is_set():
                        detail = 'Dihentikan pengguna'
                    elif state == 'STARTING':
                        detail = 'Sesi WhatsApp Web masih proses sinkronisasi setelah menunggu 5 menit; antrean dihentikan.'
                    else:
                        detail = f"Sesi WhatsApp Web {state or 'tidak diketahui'}; antrean dihentikan."
                    if state_error:
                        detail += f' ({state_error})'
                if not prep_holder['done']:
                    prep_thread.join(timeout=3.0)

                ok_ready, err_detail = ensure_session_ready()
                if not ok_ready:
                    for j in range(i, len(targets)):
                        outcomes.append('Dibatalkan')
                        self.events.put(('row', (j, 'Dibatalkan', detail)))
                        self.events.put(('row', (j, 'Dibatalkan', err_detail)))
                    break

                prep = prep_holder['data'] or prepare_target_during_delay(i, target)
                if prep.get('skip'):
                    status, detail = 'Dilewati', 'Nomor tidak terdaftar di WhatsApp'
                    outcomes.append(status)
                    self.events.put(('row', (i, status, detail)))
                    self.events.put(('run_status', f'Dilewati nomor {i + 1} dari {len(targets)} - {target.phone}'))
                    continue

                # Jeda habis -> langsung kirim pesan yang sudah disiapkan!
                try:
                    self.events.put(('row', (i, 'Memeriksa', 'Memvalidasi nomor WhatsApp...')))
                    self.events.put(('run_status', f'Memeriksa nomor {i + 1} dari {len(targets)} ({target.phone})...'))
                    exists = None
                    try:
                        exists = client.check_exists(target.phone)
                    except Exception:
                        # Tolerir jika WhatsApp Web lambat memeriksa keberadaan nomor
                        # Tetap coba kirim langsung menggunakan ID standar agar pesan tetap tersampaikan
                        exists = {'numberExists': True, 'chatId': f"{target.phone}@c.us", 'fallback': True}

                    if isinstance(exists, dict) and exists.get('numberExists') is False:
                        status, detail = 'Dilewati', 'Nomor tidak terdaftar di WhatsApp'
                        outcomes.append(status)
                        self.events.put(('row', (i, status, detail)))
                        self.events.put(('run_status', f'Dilewati nomor {i + 1} dari {len(targets)} - {target.phone}'))
                        continue

                    chat_id = exists.get('chatId') if isinstance(exists, dict) else f"{target.phone}@c.us"
                    template = random.choice(template_options) if random_mode else template_options[0]
                    template_name = template.get('name') or 'Template'
                    template_name = prep['template_name']
                    used_templates[i] = template_name
                    self.events.put(('row', (i, 'Mengirim', f'Mengirim {template_name}...')))
                    self.events.put(('run_status', f'Mengirim pesan {i + 1} dari {len(targets)} ke {target.phone}...'))
                    image_path = template.get('image', '')
                    image_mime = validate_image(image_path) if image_path else None
                    send_res = client.send(target, self.render_text(template['text'], target),
                                           image_path, chat_id, image_mime)
                    client.send(target, prep['text'], prep['image_path'], prep['chat_id'], prep['image_mime'], check_first=False)
                    status, detail = 'Dikirim', f'Terkirim ke WhatsApp server | {template_name}'
                except Exception as exc:
                    err_msg = str(exc)[:220]
                    if re.search(r'not found|not registered|tidak terdaftar|invalid wid', err_msg, re.IGNORECASE):
                        status, detail = 'Dilewati', 'Nomor tidak terdaftar di WhatsApp'
                    else:
                        status, detail = 'Gagal', err_msg
                outcomes.append(status)
                self.events.put(('row', (i, status, detail)))
                self.events.put(('run_status', f'Selesai {i + 1} dari {len(targets)} - {target.phone}'))
                self.events.put(('run_status', f'Selesai {i + 1} dari {len(targets)} - {target.phone} ({status})'))
        finally:
            while len(outcomes) < len(targets):
                idx = len(outcomes)
                outcomes.append('Dibatalkan')
                self.events.put(('row', (idx, 'Dibatalkan', 'Dihentikan')))
            template_label = 'Template Acak' if random_mode else (template_options[0].get('name') or 'Template')
            try:
                self.write_history(targets, template_label, outcomes, used_templates)
            except Exception:
                pass
            self.events.put(('run_done', outcomes))

    def write_history(self, targets, template_label, outcomes, used_templates=None):
        used_templates = used_templates or []
        results = []
        for i, target in enumerate(targets):
            result = {'phone': target.phone, 'status': outcomes[i] if i < len(outcomes) else 'Dibatalkan'}
            if i < len(used_templates) and used_templates[i]:
                result['template'] = used_templates[i]
            results.append(result)
        entry = {'time': datetime.now().astimezone().isoformat(timespec='seconds'), 'template': template_label, 'results': results}
        try:
            with HISTORY.open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            return

    def set_row(self, index, status, detail):
        iid = self.rows.get(index)
        if iid:
            values = list(self.tree.item(iid, 'values'))
            values[1] = status
            values[2] = detail
            self.tree.item(iid, values=values)
            try:
                values = list(self.tree.item(iid, 'values'))
                values[1] = status
                values[2] = detail
                self.tree.item(iid, values=values)
            except tk.TclError:
                pass

    def run_finished(self, outcomes):
        self.start_button.configure(state='normal')
        self.pause_button.configure(state='disabled', text='Jeda')
        self.stop_button.configure(state='disabled')
        if self.cancel.is_set():
            self.run_status.set('Run dihentikan. Pesan yang sudah terkirim tidak dapat ditarik.')
            return
        self.run_status.set(f"Run selesai - {outcomes.count('Dikirim')} terkirim - {outcomes.count('Dilewati')} tidak terdaftar - {outcomes.count('Gagal')} gagal - {outcomes.count('Dibatalkan')} dibatalkan")

    def toggle_pause(self):
        if self.resume.is_set():
            self.resume.clear()
            self.pause_button.configure(text='Lanjutkan')
            self.run_status.set('Run dijeda sementara.')
            return
        self.resume.set()
        self.pause_button.configure(text='Jeda')

    def stop_run(self):
        self.cancel.set()
        self.resume.set()
        self.run_status.set('Menghentikan antrean...')

    def close(self):
        if self.logout_busy:
            messagebox.showwarning('Logout berlangsung', 'Tunggu hingga proses logout selesai.')
            return
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno('Run berlangsung', 'Tutup aplikasi dan hentikan antrean?'):
                return
            self.cancel.set()
            self.resume.set()
        self.store_editor()
        self.save_optout()
        self.persist()
        if self.client:
            self.client.close()
        kill_session_browsers()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()

if __name__ == '__main__':
    main()
