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
from i18n import tr, set_language
from gateway_api import GatewayWhatsApp, CUSTOM_PROFILE_EXAMPLE, validate_profile
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
    if icon_name == 'api':
        icon_name = 'connection'
    photo = get_icon(icon_name, color=color, accent='#00d2ff', bg=bg, size=size, active=active)
    canvas.delete('all')
    canvas.configure(bg=bg)
    canvas.create_image(size // 2, size // 2, image=photo)
    canvas.image = photo

class App:
    def __init__(self, root):
        kill_session_browsers()
        self.root = root
        root.title(tr('WAblast - WhatsApp Batch Sender'))
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
        set_language(self.data.get('language', 'en'))
        self.connection_method = tk.StringVar(value=self.data.get('connection_method', 'WhatsApp Web'))
        self.gateway_kind = tk.StringVar(value=self.data.get('gateway_kind', 'WAHA'))
        self.gateway_url = tk.StringVar(value=self.data.get('gateway_url', 'http://localhost:3000'))
        self.gateway_session = tk.StringVar(value=self.data.get('gateway_session', 'default'))
        self.gateway_key = tk.StringVar()
        self.delay_mode = tk.StringVar(value=self.data.get('delay_mode', 'Random'))
        self.delay_seconds = tk.StringVar(value=str(self.data.get('delay_seconds', 60)))
        self.language_choice = tk.StringVar(value='Bahasa Indonesia' if self.data.get('language') == 'id' else 'English')
        self.events = queue.Queue()
        self.editor_index = 0
        self.template_image = None
        self.qr_image = None
        self.client = None
        self.logout_busy = False
        self.connect_busy = False
        self.status = 'UNKNOWN'
        self.account_info = None
        self.account_phone_var = tk.StringVar(value=tr('Belum terhubung'))
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
        style.configure('Dark.TFrame', background=C_CARD)
        style.configure('Dark.TLabel', background=C_CARD, foreground=C_TEXT_PRIMARY)
        style.configure('Dark.TEntry', fieldbackground=C_CARD_INNER, foreground=C_TEXT_PRIMARY, insertcolor=C_TEXT_PRIMARY)

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
        lbl_w = tk.Label(self.logo_box, text=tr('WA'), bg=C_PURPLE, fg=C_TEXT_WHITE, font=('Segoe UI Semibold', 10), cursor='hand2')
        lbl_w.pack(expand=True)
        self.info_box = tk.Frame(self.head_exp, bg=C_SIDEBAR, cursor='hand2')
        self.info_box.pack(side='left', fill='both')
        lbl_title = tk.Label(self.info_box, text=tr('WAblast'), font=('Segoe UI Semibold', 12), fg=C_TEXT_WHITE, bg=C_SIDEBAR, cursor='hand2')
        lbl_title.pack(anchor='w')
        lbl_sub = tk.Label(self.info_box, text=tr('By LilCat Studio'), font=('Segoe UI', 8), fg=C_TEXT_MUTED, bg=C_SIDEBAR, cursor='hand2')
        lbl_sub.pack(anchor='w')
        for w in self.head_exp, self.logo_box, lbl_w, self.info_box, lbl_title, lbl_sub:
            w.bind('<Button-1>', (lambda e: self.toggle_sidebar()))
        cta_wrap = tk.Frame(self.sidebar_expanded_frame, bg=C_SIDEBAR, padx=14, pady=8)
        cta_wrap.pack(fill='x')
        self.cta_btn = tk.Button(cta_wrap, text=tr('Mulai Run Batch'), bg=C_PURPLE, fg=C_TEXT_WHITE, activebackground=C_PURPLE_HOVER, activeforeground=C_TEXT_WHITE, relief='flat', cursor='hand2', font=('Segoe UI Semibold', 9), pady=7, bd=0, command=self.on_cta_click)
        self.cta_btn.pack(fill='x')
        tk.Label(self.sidebar_expanded_frame, text=tr('NAVIGASI'), font=('Segoe UI Bold', 8), fg=C_TEXT_DIM, bg=C_SIDEBAR, padx=16, pady=6).pack(anchor='w', pady=(8, 2))
        self.nav_exp_container = tk.Frame(self.sidebar_expanded_frame, bg=C_SIDEBAR, padx=10)
        self.nav_exp_container.pack(fill='x')
        nav_items_data = [('send', 'Kirim Pesan'), ('templates', 'Template Pesan'), ('connection', 'WhatsApp Web'), ('api', 'Custom API'), ('optout', 'Daftar Opt-out')]
        for key, label in nav_items_data:
            row = tk.Frame(self.nav_exp_container, bg=C_SIDEBAR, padx=10, pady=8, cursor='hand2')
            row.pack(fill='x', pady=2)
            icon_cv = tk.Canvas(row, width=22, height=22, bg=C_SIDEBAR, highlightthickness=0)
            icon_cv.pack(side='left', padx=(0, 10))
            draw_vector_icon(icon_cv, key, C_TEXT_MUTED, bg=C_SIDEBAR, active=key == self.active_tab)
            lbl = tk.Label(row, text=tr(label), font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_SIDEBAR)
            lbl.pack(side='left')
            for widget in row, icon_cv, lbl:
                widget.bind('<Button-1>', (lambda e, k=key: self.switch_tab(k)))
                widget.bind('<Enter>', (lambda e, k=key: self.on_exp_nav_hover(k, True)))
                widget.bind('<Leave>', (lambda e, k=key: self.on_exp_nav_hover(k, False)))
            self.nav_expanded_widgets[key] = {'row': row, 'icon': icon_cv, 'lbl': lbl}
            continue
        terms_wrap = tk.Frame(self.sidebar_expanded_frame, bg=C_SIDEBAR, padx=12, pady=14)
        terms_wrap.pack(side='bottom', fill='x')
        tk.Label(terms_wrap, text=tr('Language'), bg=C_SIDEBAR, fg=C_TEXT_MUTED, font=('Segoe UI', 9)).pack(anchor='w', pady=(0, 4))
        self.language_combo = ttk.Combobox(terms_wrap, textvariable=self.language_choice, values=['English', 'Bahasa Indonesia'], state='readonly', style='Dark.TCombobox')
        self.language_combo.pack(fill='x', pady=(0, 10))
        self.language_combo.bind('<<ComboboxSelected>>', self.change_language)
        self.terms_sidebar_button = self.create_btn(terms_wrap, 'Syarat & Ketentuan', command=self.show_terms, padx=8, pady=6)
        self.terms_sidebar_button.pack(fill='x')
        self.head_col = tk.Frame(self.sidebar_collapsed_frame, bg=C_SIDEBAR, pady=18, cursor='hand2')
        self.head_col.pack(fill='x')
        logo_col = tk.Frame(self.head_col, bg=C_PURPLE, width=34, height=34, highlightbackground=C_PURPLE_HOVER, highlightthickness=1, cursor='hand2')
        logo_col.pack(anchor='center')
        logo_col.pack_propagate(False)
        lbl_logo = tk.Label(logo_col, text=tr('WA'), bg=C_PURPLE, fg=C_TEXT_WHITE, font=('Segoe UI Semibold', 10), cursor='hand2')
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
        self.language_collapsed_combo = ttk.Combobox(terms_col_wrap, textvariable=self.language_choice, values=['English', 'Bahasa Indonesia'], state='readonly', width=4, style='Dark.TCombobox')
        self.language_collapsed_combo.pack(anchor='center', pady=(0, 10))
        self.language_collapsed_combo.bind('<<ComboboxSelected>>', self.change_language)
        self.terms_collapsed_button = tk.Button(terms_col_wrap, text=tr('T&C'), command=self.show_terms, bg=C_ACTIVE_PILL, fg=C_TEXT_PRIMARY, activebackground=C_HOVER, activeforeground=C_TEXT_WHITE, relief='flat', cursor='hand2', font=('Segoe UI Semibold', 8), bd=0, padx=4, pady=5)
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
        self.title_var = tk.StringVar(value=tr('Kirim Pesan'))
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
        self.top_status = tk.StringVar(value=tr('WhatsApp belum dihubungkan'))
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
        self.tab_api = tk.Frame(self.views_container, bg=C_BASE)
        self.tab_optout = tk.Frame(self.views_container, bg=C_BASE)
        for f in self.tab_send, self.tab_templates, self.tab_connection, self.tab_api, self.tab_optout:
            f.grid(row=0, column=0, sticky='nsew')
        self.build_send()
        self.build_templates()
        self.build_connection()
        self.build_gateway()
        self.build_optout()
        self.switch_tab('send')

    def card(self, parent, title='', subtitle=''):
        f = tk.Frame(parent, bg=C_CARD, padx=16, pady=16, highlightbackground=C_CARD_BORDER, highlightthickness=1)
        if title:
            h_row = tk.Frame(f, bg=C_CARD)
            h_row.pack(fill='x', pady=(0, 10))
            tk.Label(h_row, text=tr(title), font=('Segoe UI Semibold', 11), fg=C_TEXT_WHITE, bg=C_CARD).pack(side='left')
            if subtitle:
                tk.Label(h_row, text=tr(subtitle), font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='left', padx=10)
        return f

    def create_btn(self, parent, text, command=None, variant='secondary', padx=14, pady=6):
        if variant == 'danger':
            bg, fg, hover, border = '#3b2026', '#fecaca', '#51262d', '#7f1d1d'
        else:
            bg = C_PURPLE if variant == 'accent' else C_ACTIVE_PILL
            fg = C_TEXT_WHITE if variant == 'accent' else C_TEXT_PRIMARY
            hover = C_PURPLE_HOVER if variant == 'accent' else C_HOVER
            border = C_PURPLE if variant == 'accent' else C_CARD_BORDER
        b = tk.Button(parent, text=tr(text), command=command, bg=bg, fg=fg, activebackground=hover, activeforeground=C_TEXT_WHITE, relief='flat', cursor='hand2', bd=0, padx=padx, pady=pady, font=('Segoe UI Semibold', 9), highlightbackground=border, highlightthickness=1)
        b.bind('<Enter>', (lambda e: None))
        b.bind('<Leave>', (lambda e: None))
        return b

    def show_terms(self):
        window = tk.Toplevel(self.root)
        window.title(tr('Syarat & Ketentuan WAblast'))
        window.geometry('620x470')
        window.minsize(520, 400)
        window.configure(bg=C_BASE)
        window.transient(self.root)

        content = tk.Frame(window, bg=C_CARD, padx=18, pady=16, highlightbackground=C_CARD_BORDER, highlightthickness=1)
        content.pack(fill='both', expand=True, padx=16, pady=16)
        tk.Label(content, text=tr('Syarat & Ketentuan Penggunaan WhatsApp'), font=('Segoe UI Semibold', 13), fg=C_TEXT_WHITE, bg=C_CARD).pack(anchor='w', pady=(0, 10))
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
        body.insert('1.0', tr(terms_text))
        body.configure(state='disabled')

        links = (
            ('Ketentuan WhatsApp', 'https://www.whatsapp.com/legal/terms-of-service?pix=6f_0_0'),
            ('Panduan penggunaan bertanggung jawab', 'https://faq.whatsapp.com/361005896189245/?eea=0&locale=en_US'),
            ('Kebijakan Berkirim Pesan WhatsApp Business', 'https://business.whatsapp.com/policy/preview?lang=id_ID'),
        )
        links_frame = tk.Frame(content, bg=C_CARD)
        links_frame.pack(fill='x', pady=(0, 12))
        for label, url in links:
            link = tk.Label(links_frame, text=tr(label), font=('Segoe UI', 9, 'underline'), fg=C_PURPLE_HOVER, bg=C_CARD, cursor='hand2')
            link.pack(anchor='w', pady=2)
            link.bind('<Button-1>', lambda _event, target=url: webbrowser.open(target))

        self.create_btn(content, 'Tutup', command=window.destroy, variant='accent').pack(anchor='e')
        window.grab_set()

    def build_send(self):
        viewport = tk.Canvas(self.tab_send, bg=C_BASE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.tab_send, orient='vertical', command=viewport.yview)
        scrollbar.pack(side='right', fill='y')
        viewport.pack(side='left', fill='both', expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        pane = tk.Frame(viewport, bg=C_BASE)
        content_id = viewport.create_window((0, 0), window=pane, anchor='nw')
        viewport.bind('<Configure>', lambda event: viewport.itemconfigure(content_id, width=event.width))
        pane.bind('<Configure>', lambda event: viewport.configure(scrollregion=viewport.bbox('all')))
        self.send_viewport = viewport
        pane.columnconfigure(0, weight=1, uniform='send_columns')
        pane.columnconfigure(1, weight=1, uniform='send_columns')
        pane.rowconfigure(1, weight=1)
        c_target = self.card(pane, 'Nomor Tujuan', f'Maks. {MAX_TARGETS} nomor per run')
        c_target.grid(row=0, column=0, sticky='nsew', padx=(0, 10), pady=(0, 12))
        lbl = tk.Label(c_target, text=tr('Satu nomor per baris; opsional: tambahkan nama setelah koma (cth: 08123456789, Andi).'), font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD)
        lbl.pack(anchor='w', pady=(0, 8))
        lbl.configure(justify='left', anchor='w')
        c_target.bind('<Configure>', lambda event: lbl.configure(wraplength=max(80, event.width - 36)))
        self.targets_box = tk.Text(c_target, height=5, width=1, font=('Consolas', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, selectbackground='#322d56', relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, padx=8, pady=8)
        self.targets_box.pack(fill='both', expand=True)
        t_row = tk.Frame(c_target, bg=C_CARD)
        t_row.pack(fill='x', pady=(10, 0))
        self.create_btn(t_row, 'Impor CSV...', command=self.import_csv).pack(side='left')
        self.create_btn(t_row, 'Kosongkan', command=(lambda: self.targets_box.delete('1.0', 'end'))).pack(side='left', padx=8)
        self.target_count_var = tk.StringVar(value=tr('0 nomor terdeteksi'))
        tk.Label(t_row, textvariable=self.target_count_var, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='right')
        self.targets_box.bind('<KeyRelease>', self.update_target_counter)
        c_msg = self.card(pane, 'Pesan & Pengiriman', 'Tinjau template terpilih')
        c_msg.grid(row=0, column=1, sticky='nsew', padx=(10, 0), pady=(0, 12))
        tk.Label(c_msg, text=tr('Pilih Template'), font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(anchor='w', pady=(0, 4))
        self.choice = tk.StringVar()
        self.combo = ttk.Combobox(c_msg, textvariable=self.choice, state='readonly', style='Dark.TCombobox')
        self.combo.pack(fill='x', pady=(0, 8))
        self.combo.bind('<<ComboboxSelected>>', self.preview_message)
        self.preview = tk.Text(c_msg, height=3, width=1, wrap='word', font=('Segoe UI', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, selectbackground='#322d56', relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightthickness=1, padx=8, pady=8, state='disabled')
        self.preview.pack(fill='both', expand=True)
        self.image_note = tk.StringVar(value=tr('Tanpa gambar'))
        tk.Label(c_msg, textvariable=self.image_note, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(anchor='w', pady=(6, 4))
        self.consent = tk.BooleanVar(value=False)
        self.delivery_row = tk.Frame(c_msg, bg=C_CARD)
        self.delivery_row.pack(fill='x', pady=(0, 8))
        consent_box = tk.Frame(self.delivery_row, bg=C_CARD)
        consent_box.pack(side='left', fill='both', expand=True)
        self.chk_consent = ttk.Checkbutton(consent_box, variable=self.consent, style='Dark.TCheckbutton')
        self.chk_consent.pack(side='left', anchor='n')
        consent_text = tk.Label(consent_box, text=tr('Saya memastikan semua penerima menyetujui pesan ini.'), wraplength=160, justify='left', anchor='nw', font=('Segoe UI', 9), bg=C_CARD, fg=C_TEXT_PRIMARY)
        consent_text.pack(side='left', fill='both', expand=True)
        consent_text.bind('<Button-1>', lambda event: self.chk_consent.invoke())
        consent_box.bind('<Configure>', lambda event: consent_text.configure(wraplength=max(60, event.width - 28)))
        delay_box = tk.Frame(self.delivery_row, bg=C_CARD)
        delay_box.pack(side='right', padx=(10, 0))
        tk.Label(delay_box, text=tr('Delay'), bg=C_CARD, fg=C_TEXT_MUTED, font=('Segoe UI', 9)).pack(anchor='w')
        self.delay_display = tk.StringVar(value=tr(self.delay_mode.get()))
        self.delay_combo = ttk.Combobox(delay_box, textvariable=self.delay_display, values=[tr('Random'), tr('Manual')], width=9, state='readonly', style='Dark.TCombobox')
        self.delay_combo.pack(fill='x')
        self.delay_combo.bind('<<ComboboxSelected>>', self.update_delivery_settings)
        seconds_row = tk.Frame(delay_box, bg=C_CARD)
        seconds_row.pack(fill='x', pady=(4, 0))
        self.delay_entry = tk.Spinbox(seconds_row, textvariable=self.delay_seconds, from_=10, to=600, width=5, bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_TEXT_PRIMARY, buttonbackground=C_ACTIVE_PILL, relief='flat', command=self.update_delivery_settings)
        self.delay_entry.pack(side='left')
        self.delay_entry.bind('<FocusOut>', self.update_delivery_settings)
        tk.Label(seconds_row, text=tr('seconds'), bg=C_CARD, fg=C_TEXT_MUTED, font=('Segoe UI', 8)).pack(side='left', padx=(4, 0))
        tk.Label(delay_box, text='10–600', bg=C_CARD, fg=C_TEXT_MUTED, font=('Segoe UI', 8)).pack(anchor='w')
        self.delay_entry.configure(state='normal' if self.delay_mode.get() == 'Manual' else 'disabled')
        tk.Label(c_msg, text=tr('Persetujuan wajib dikonfirmasi setiap run; daftar opt-out selalu diperiksa.'), font=('Segoe UI', 8), fg=C_TEXT_MUTED, bg=C_CARD, wraplength=360, justify='left').pack(anchor='w', pady=(0, 8))
        self.start_button = tk.Button(c_msg, text=tr(f'Mulai Run Batch (maks. {MAX_TARGETS} nomor)'), bg=C_PURPLE, fg=C_TEXT_WHITE, activebackground=C_PURPLE_HOVER, activeforeground=C_TEXT_WHITE, relief='flat', cursor='hand2', font=('Segoe UI Semibold', 10), pady=8, bd=0, command=self.start_run)
        self.start_button.pack(fill='x')
        c_prog = self.card(pane, 'Progres & Hasil Pengiriman', 'Hasil per nomor & riwayat antrean')
        c_prog.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(10, 0))
        stat_row = tk.Frame(c_prog, bg=C_CARD)
        stat_row.pack(fill='x', pady=(0, 8))
        self.run_status = tk.StringVar(value=tr('Hubungkan WhatsApp untuk memulai.'))
        tk.Label(stat_row, textvariable=self.run_status, font=('Segoe UI Semibold', 9), fg=C_TEXT_PRIMARY, bg=C_CARD).pack(side='left')
        tree_frame = tk.Frame(c_prog, bg=C_CARD_INNER, highlightbackground=C_BORDER_SUBTLE, highlightthickness=1)
        tree_frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=('phone', 'status', 'detail'), show='headings', style='Dark.Treeview', height=7)
        for col, title, width in (('phone', 'Nomor', 190), ('status', 'Status', 150), ('detail', 'Keterangan', 540)):
            self.tree.heading(col, text=tr(title))
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
        self.delivery_note = tk.StringVar(value=tr('Jeda acak 1-3 mnt & jeda 5 mnt tiap 10 nomor'))
        tk.Label(btn_row, textvariable=self.delivery_note, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='right')
        def bind_scroll(widget):
            if not isinstance(widget, (tk.Text, tk.Spinbox, ttk.Combobox, ttk.Treeview)):
                widget.bind('<MouseWheel>', lambda event: viewport.yview_scroll(-int(event.delta / 120), 'units'))
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(pane)

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
        tk.Label(c_edit, text=tr('Nama Template'), font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(anchor='w', pady=(0, 4))
        self.name = tk.StringVar()
        name_entry = tk.Entry(c_edit, textvariable=self.name, font=('Segoe UI', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, bd=0)
        name_entry.pack(fill='x', ipady=6, pady=(0, 10))
        var_row = tk.Frame(c_edit, bg=C_CARD)
        var_row.pack(fill='x', pady=(0, 4))
        tk.Label(var_row, text=tr('Isi Pesan'), font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='left')
        tk.Label(var_row, text=tr('Variabel tersedia: {{nama}} dan {{nomor}}'), font=('Segoe UI', 8), fg=C_PURPLE, bg=C_CARD).pack(side='right')
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
        self.thumb = tk.Label(self.thumb_card, text=tr('Pratinjau gambar akan tampil di sini'), font=('Segoe UI', 9), fg=C_TEXT_DIM, bg=C_CARD_INNER)
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
        tk.Label(c_conf, text=tr('Aplikasi membuka sesi WhatsApp Web lokal. Node.js dan dependensi perlu dipasang satu kali.'), font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD, wraplength=360, justify='left').pack(anchor='w', pady=(0, 18))
        acl_note = ('ACL NTFS membatasi folder data ke akun Windows ini dan SYSTEM. Jika akun Windows dipakai bersama, tekan Logout WhatsApp setelah selesai.' if not DATA_ACL_ERROR else f'PERINGATAN: Proteksi folder data gagal; koneksi WhatsApp diblokir. {DATA_ACL_ERROR[:140]}')
        acl_color = C_TEXT_MUTED if not DATA_ACL_ERROR else C_RED
        tk.Label(c_conf, text=tr(acl_note), font=('Segoe UI', 8), fg=acl_color, bg=C_CARD, wraplength=360, justify='left').pack(anchor='w', pady=(0, 12))
        self.country = tk.StringVar(value=self.data.get('country', '62'))
        tk.Label(c_conf, text=tr('Kode Negara Default'), font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(anchor='w', pady=(0, 3))
        entry = tk.Entry(c_conf, textvariable=self.country, font=('Segoe UI', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, bd=0)
        entry.pack(fill='x', ipady=5, pady=(0, 10))
        row_btn = tk.Frame(c_conf, bg=C_CARD)
        row_btn.pack(fill='x', pady=10)
        self.create_btn(row_btn, 'Hubungkan / Minta QR', command=self.connect_direct, variant='accent').pack(side='left')
        self.create_btn(row_btn, 'Segarkan QR', command=self.refresh_qr).pack(side='left', padx=8)
        self.logout_btn = self.create_btn(row_btn, 'Logout WhatsApp', command=self.logout_whatsapp, variant='danger')
        self.logout_btn.pack(side='left')
        self.connection_label = tk.StringVar(value=tr('Belum terhubung'))
        tk.Label(c_conf, textvariable=self.connection_label, font=('Segoe UI Semibold', 10), fg=C_PURPLE, bg=C_CARD).pack(anchor='w', pady=(14, 4))
        self.account_frame = tk.Frame(c_conf, bg=C_CARD_INNER, highlightbackground=C_BORDER_SUBTLE, highlightthickness=1, padx=12, pady=10)
        self.account_frame.pack(fill='x', pady=(8, 8))
        tk.Label(self.account_frame, text=tr('Nomor WhatsApp Terhubung:'), font=('Segoe UI Semibold', 9), fg=C_TEXT_MUTED, bg=C_CARD_INNER).pack(anchor='w')
        self.account_phone_lbl = tk.Label(self.account_frame, textvariable=self.account_phone_var, font=('Consolas', 11, 'bold'), fg=C_TEXT_DIM, bg=C_CARD_INNER)
        self.account_phone_lbl.pack(anchor='w', pady=(3, 0))
        tk.Label(c_conf, text=tr('Sesi tertaut disimpan lokal untuk login berikutnya.'), font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD, wraplength=360, justify='left').pack(anchor='w', pady=(4, 0))
        c_qr = self.card(self.tab_connection, 'Autentikasi WhatsApp (QR)', 'Pindai menggunakan aplikasi WhatsApp ponsel')
        c_qr.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self.qr_box = tk.Frame(c_qr, bg=C_CARD_INNER, highlightbackground=C_BORDER_SUBTLE, highlightthickness=1, pady=20)
        self.qr_box.pack(fill='both', expand=True, pady=(0, 10))
        self.qr = tk.Label(self.qr_box, text=tr('QR Code akan muncul saat sesi meminta pemindaian.'), font=('Segoe UI', 10), fg=C_TEXT_DIM, bg=C_CARD_INNER)
        self.qr.pack(expand=True)
        guide = tk.Label(c_qr, text=tr('WhatsApp -> Perangkat Tertaut -> Tautkan Perangkat -> Pindai QR.'), font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD, justify='center')
        guide.pack(anchor='center')

    def build_optout(self):
        c_opt = self.card(self.tab_optout, 'Daftar Nomor Opt-out (Blokir Otomatis)', 'Filter selalu aktif; nomor di sini otomatis dilewati dan tidak dapat dikirimi')
        c_opt.pack(fill='both', expand=True)
        self.optbox = tk.Text(c_opt, height=18, font=('Consolas', 10), bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_PURPLE, selectbackground='#322d56', selectforeground=C_TEXT_WHITE, relief='flat', highlightbackground=C_BORDER_SUBTLE, highlightcolor=C_PURPLE, highlightthickness=1, padx=12, pady=12)
        self.optbox.pack(fill='both', expand=True, pady=(0, 12))
        self.optbox.bind('<KeyRelease>', (lambda _e: self.root.after(250, self.save_optout)))
        row = tk.Frame(c_opt, bg=C_CARD)
        row.pack(fill='x')
        self.opt_count_var = tk.StringVar(value=tr('0 nomor diblokir'))
        tk.Label(row, textvariable=self.opt_count_var, font=('Segoe UI', 9), fg=C_TEXT_MUTED, bg=C_CARD).pack(side='left')
        self.create_btn(row, 'Simpan Daftar Opt-out', command=self.save_optout, variant='accent').pack(side='right')

    def switch_tab(self, key):
        self.active_tab = key
        frames = {'send': self.tab_send, 'templates': self.tab_templates, 'connection': self.tab_connection, 'api': self.tab_api, 'optout': self.tab_optout}
        for k, f in frames.items():
            if k == key:
                f.lift()
            else:
                f.lower()
        titles = {'send': ('Kirim Pesan', ''), 'templates': ('Template Pesan', 'Kelola 5 template pesan dinamis dan gambar lampiran'), 'connection': ('Koneksi WhatsApp Web', 'Hubungkan WhatsApp Web langsung dengan QR'), 'optout': ('Daftar Opt-out', 'Pengecualian nomor pelanggan agar tidak menerima pengiriman')}
        titles['api'] = ('Custom API', 'WAHA / Custom gateway')
        t, sub = titles.get(key, ('WAblast', ''))
        self.title_var.set(tr(t))
        self.subtitle_var.set(tr(sub))
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
        self.target_count_var.set(tr(f'{len(lines)} nomor dimasukkan (maks. {MAX_TARGETS})'))

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
                if name == tr(RANDOM_TEMPLATE_OPTION) or names.count(name) > 1:
                    label = f'{name} (Template {i + 1})'
                display_names.append(label)
                self.combo_template_map[label] = i
            options = [tr(RANDOM_TEMPLATE_OPTION), *display_names]
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
        self.run_status.set(tr('Template tersimpan.'))

    def choose_image(self):
        path = filedialog.askopenfilename(filetypes=[('Gambar JPEG, PNG, WebP', '*.jpg *.jpeg *.png *.webp'), ('Semua file', '*.*')])
        if path:
            try:
                validate_image(path)
            except ValueError as exc:
                messagebox.showwarning(tr('Gambar tidak valid'), tr(str(exc)))
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
            self.thumb.configure(text=tr('Tanpa gambar lampiran'), image='')
            return
        if not Path(path).is_file():
            self.thumb.configure(text=tr(f'File tak ditemukan: {Path(path).name}'), image='')
            return
        try:
            from PIL import Image, ImageTk
            with Image.open(path) as img:
                image = img.copy()
            image.thumbnail((500, 160))
            self.template_image = ImageTk.PhotoImage(image)
            self.thumb.configure(text=tr(f'  {Path(path).name}'), image=self.template_image, compound='left')
        except ImportError:
            self.thumb.configure(text=tr(f'{Path(path).name} (Pillow opsional untuk thumbnail)'), image='')
            return
        except Exception:
            self.thumb.configure(text=tr(Path(path).name), image='')
            return

    def preview_message(self, _event=None):
        if not hasattr(self, 'preview'):
            return
        title = self.choice.get()
        templates = self.templates()
        if title == tr(RANDOM_TEMPLATE_OPTION):
            eligible = [(i, item) for i, item in enumerate(templates) if item.get('text', '').strip()]
            self.preview.configure(state='normal')
            self.preview.delete('1.0', 'end')
            if eligible:
                names = [item.get('name') or f'Template {i + 1}' for i, item in eligible]
                self.preview.insert('1.0', tr('Setiap nomor mendapat template acak dari yang terisi:\n') + '\n'.join(f'* {name}' for name in names))
                image_count = sum(bool(item.get('image')) for _, item in eligible)
                image_text = f'{image_count} template memiliki gambar' if image_count else 'Tidak ada gambar pada template terisi'
                self.image_note.set(tr(f'Lampiran mengikuti template acak | {image_text}'))
            else:
                self.preview.insert('1.0', tr('Belum ada template berisi pesan. Isi teks pada menu Template terlebih dahulu.'))
                self.image_note.set(tr('Tidak ada template yang dapat dipilih'))
            self.preview.configure(state='disabled')
            return
        index = self.combo_template_map.get(title)
        item = templates[index] if index is not None else templates[self.editor_index]
        text = item.get('text') or tr('Isi pesan kosong. Buka menu Template untuk mengisinya.')
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        self.preview.insert('1.0', text)
        self.preview.configure(state='disabled')
        if item.get('image'):
            self.image_note.set(tr('Gambar: ' + Path(item['image']).name))
            return
        self.image_note.set(tr('Tanpa gambar lampiran'))

    def load_optout(self):
        self.optbox.delete('1.0', 'end')
        opt_list = self.data.get('optout', [])
        self.optbox.insert('1.0', '\n'.join(opt_list))
        self.opt_count_var.set(tr(f'{len(opt_list)} nomor terdaftar opt-out'))

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
        self.opt_count_var.set(tr(f"{len(self.data['optout'])} nomor terdaftar opt-out"))
        self.persist()

    def import_csv(self):
        filename = filedialog.askopenfilename(filetypes=[('CSV', '*.csv'), ('All files', '*.*')])
        if not filename:
            return
        try:
            with open(filename, encoding='utf-8-sig', newline='') as f:
                rows = [row for row in csv.reader(f) if row and row[0].strip()]
        except (OSError, csv.Error) as exc:
            messagebox.showerror(tr('Gagal impor CSV'), tr(str(exc)))
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
            messagebox.showerror(tr('Gagal impor CSV'), tr(str(exc)))
            return

    def controls_busy(self):
        return self.connect_busy or self.logout_busy or self.poll_busy or bool(self.worker and self.worker.is_alive())

    def change_language(self, _event=None):
        code = 'id' if self.language_choice.get() == 'Bahasa Indonesia' else 'en'
        if self.controls_busy():
            self.language_choice.set('Bahasa Indonesia' if self.data.get('language') == 'id' else 'English')
            messagebox.showwarning(tr('Settings'), tr('Finish the active operation before changing settings.'))
            return
        self.data['language'] = code
        self.persist()
        messagebox.showinfo(tr('Language'), tr('Language applies after restarting WAblast.'))

    def update_delivery_settings(self, _event=None):
        if self.worker and self.worker.is_alive():
            return False
        mode = 'Manual' if self.delay_display.get() == tr('Manual') else 'Random'
        self.delay_mode.set(mode)
        self.delay_entry.configure(state='normal' if mode == 'Manual' else 'disabled')
        raw = self.delay_seconds.get().strip()
        if not raw.isascii() or not raw.isdigit() or not 10 <= int(raw) <= 600:
            messagebox.showwarning(tr('Delay'), tr('Manual delay must be a whole number from 10 to 600 seconds.'))
            return False
        self.data.update(delay_mode=mode, delay_seconds=int(raw))
        self.delivery_note.set(tr(f'Manual delay: {raw} seconds; batch rest: {max(300, int(raw))} seconds.') if mode == 'Manual' else tr('Jeda acak 1-3 mnt & jeda 5 mnt tiap 10 nomor'))
        self.persist()
        return True

    def set_delivery_controls(self, running):
        self.delay_combo.configure(state='disabled' if running else 'readonly')
        self.delay_entry.configure(state='disabled' if running or self.delay_mode.get() != 'Manual' else 'normal')
        self.language_combo.configure(state='disabled' if running else 'readonly')
        self.language_collapsed_combo.configure(state='disabled' if running else 'readonly')

    def connect_direct(self):
        if self.controls_busy():
            messagebox.showwarning(tr('Connection'), tr('Finish the active operation before changing settings.'))
            return
        if self.connection_method.get() != 'WhatsApp Web':
            if self.client:
                self.client.close()
            self.client = None
            self.connection_method.set('WhatsApp Web')
            self.data['connection_method'] = 'WhatsApp Web'
            self.apply_status('UNKNOWN')
        self.connect()

    def build_gateway(self):
        self.tab_api.columnconfigure(0, weight=1, uniform='api_columns')
        self.tab_api.columnconfigure(1, weight=1, uniform='api_columns')
        self.tab_api.rowconfigure(0, weight=1)
        win = self.card(self.tab_api, 'Gateway/API', 'WAHA / Custom gateway')
        win.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        profile_panel = self.card(self.tab_api, 'Custom API profile')
        profile_panel.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        fields = {}
        for label, variable, choices, secret in [
            ('Gateway profile', self.gateway_kind, ['WAHA', 'Custom'], False),
            ('Server URL', self.gateway_url, None, False),
            ('Session', self.gateway_session, None, False),
            ('API key (memory only)', self.gateway_key, None, True),
        ]:
            row = ttk.Frame(win, style='Dark.TFrame')
            row.pack(fill='x', pady=4)
            ttk.Label(row, text=tr(label), style='Dark.TLabel').pack(anchor='w', pady=(0, 3))
            fields[label] = tk.StringVar(value=variable.get())
            widget = (ttk.Combobox(row, textvariable=fields[label], values=choices, state='readonly', style='Dark.TCombobox')
                      if choices else ttk.Entry(row, textvariable=fields[label], show='*' if secret else '', style='Dark.TEntry'))
            widget.pack(fill='x')
        self.gateway_fields = fields
        tk.Label(profile_panel, text=tr('Custom API profile (JSON; use placeholders, never paste secrets):'), bg=C_CARD, fg=C_TEXT_MUTED, wraplength=300, justify='left').pack(anchor='w')
        editor = tk.Text(profile_panel, height=12, width=1, wrap='word', bg=C_CARD_INNER, fg=C_TEXT_PRIMARY, insertbackground=C_TEXT_PRIMARY, relief='flat', padx=8, pady=8)
        editor.pack(fill='both', expand=True, pady=6)
        self.gateway_editor = editor
        editor.insert('1.0', json.dumps(self.data.get('gateway_profile', CUSTOM_PROFILE_EXAMPLE), indent=2))
        profiles = dict(self.data.get('gateway_profiles', {}))
        profile_name = tk.StringVar(value='Custom gateway')
        profile_row = ttk.Frame(profile_panel, style='Dark.TFrame')
        profile_row.pack(fill='x')
        selector = ttk.Combobox(profile_row, textvariable=profile_name, values=list(profiles), style='Dark.TCombobox')
        selector.pack(fill='x', pady=(0, 6))
        def load_profile():
            profile = profiles.get(profile_name.get())
            if profile is not None:
                editor.delete('1.0', 'end')
                editor.insert('1.0', json.dumps(profile, indent=2))
                fields['Gateway profile'].set('Custom')
        def delete_profile():
            profiles.pop(profile_name.get(), None)
            selector.configure(values=list(profiles))
        ttk.Button(profile_row, text=tr('Load profile'), command=load_profile).pack(side='left')
        ttk.Button(profile_row, text=tr('Delete profile'), command=delete_profile).pack(side='left')

        def save(connect_after=False):
            if self.controls_busy():
                messagebox.showwarning(tr('Settings'), tr('Finish the active operation before changing settings.'))
                return
            try:
                method = 'Gateway/API'
                kind = fields['Gateway profile'].get()
                profile = None
                if kind == 'Custom':
                    profile = validate_profile(json.loads(editor.get('1.0', 'end-1c')))
                if method == 'Gateway/API':
                    GatewayWhatsApp(self.events, fields['Server URL'].get().strip(),
                                    fields['API key (memory only)'].get(), fields['Session'].get().strip(),
                                    profile if kind == 'Custom' else None)
            except (ValueError, TypeError, RuntimeError) as exc:
                messagebox.showerror(tr('Settings'), tr(str(exc)), parent=win)
                return
            if self.client:
                self.client.close()
                self.client = None
            for label, variable in [
                ('Gateway profile', self.gateway_kind),
                ('Server URL', self.gateway_url), ('Session', self.gateway_session),
                ('API key (memory only)', self.gateway_key)]:
                variable.set(fields[label].get())
            self.connection_method.set(method)
            self.data.update(connection_method=method, gateway_kind=kind,
                             gateway_url=self.gateway_url.get(), gateway_session=self.gateway_session.get())
            if kind == 'Custom':
                self.data['gateway_profile'] = profile
                profiles[profile_name.get().strip() or 'Custom gateway'] = profile
            self.data['gateway_profiles'] = profiles
            self.status = 'UNKNOWN'
            self.apply_status('UNKNOWN')
            self.persist()
            if connect_after:
                self.connect()
            else:
                messagebox.showinfo(tr('Settings'), tr('Gateway settings saved. Reconnect before sending.'))
        actions = tk.Frame(win, bg=C_CARD)
        actions.pack(fill='x', pady=(10, 6))
        self.create_btn(actions, 'Save', command=save).pack(side='left')
        self.create_btn(actions, 'Connect', command=lambda: save(True), variant='accent').pack(side='left', padx=8)
        self.create_btn(actions, 'Logout WhatsApp', command=self.logout_whatsapp, variant='danger').pack(side='left')
        tk.Label(win, textvariable=self.connection_label, bg=C_CARD, fg=C_TEXT_MUTED, wraplength=300, justify='left').pack(anchor='w', pady=8)
        self.gateway_qr = tk.Label(win, text=tr('QR Code akan muncul saat sesi meminta pemindaian.'), bg=C_CARD_INNER, fg=C_TEXT_MUTED, wraplength=300)
        self.gateway_qr.pack(fill='both', expand=True)

    def make_client(self):
        if self.connection_method.get() == 'Gateway/API':
            return GatewayWhatsApp(self.events, self.gateway_url.get().strip(), self.gateway_key.get(),
                                  self.gateway_session.get().strip(),
                                  self.data.get('gateway_profile') if self.gateway_kind.get() == 'Custom' else None)
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
        if self.connection_method.get() != 'WhatsApp Web':
            return
        if self.has_saved_session() and self.status not in {'WORKING', 'STARTING'}:
            self.connection_label.set(tr('Memulihkan sesi WhatsApp tersimpan...'))
            self.top_status.set(tr('Menghubungkan...'))
            self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
            self.connect()

    def connect(self):
        if self.logout_busy or self.connect_busy:
            return
        if DATA_ACL_ERROR:
            messagebox.showerror(tr('Proteksi penyimpanan gagal'), tr('Sesi WhatsApp tidak dijalankan karena folder data belum berhasil dibatasi dengan ACL NTFS. Periksa konfigurasi keamanan Windows, lalu buka ulang WAblast.\n\n' + DATA_ACL_ERROR))
            return
        if self.status == 'WORKING':
            messagebox.showinfo(tr('WhatsApp Terhubung'), tr('WhatsApp Web sudah terhubung dan siap digunakan.'))
            return
        if self.status == 'STARTING':
            self.connection_label.set(tr('WhatsApp Web sedang memulai sesi, mohon tunggu...'))
            return
        self.connect_busy = True
        if self.client is None:
            try:
                self.client = self.make_client()
            except (ValueError, RuntimeError) as exc:
                self.connect_busy = False
                messagebox.showerror(tr('Connection'), tr(str(exc)))
                return
        self.persist()
        self.connection_label.set(tr('Membuka WhatsApp Web lokal...'))
        self.status = 'STARTING'
        self.top_status.set(tr('Menghubungkan...'))
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
            messagebox.showerror(tr('Proteksi penyimpanan gagal'), tr('Sesi WhatsApp tidak dijalankan karena folder data belum berhasil dibatasi dengan ACL NTFS.\n\n' + DATA_ACL_ERROR))
            return
        if self.client is None:
            self.client = self.make_client()
        self.connection_label.set(tr('Meminta QR...'))
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
            messagebox.showwarning(tr('Run masih berlangsung'), tr('Hentikan atau tunggu antrean selesai sebelum logout.'))
            return
        if not messagebox.askyesno(tr('Logout WhatsApp'), tr('Apakah Anda yakin ingin logout dari akun WhatsApp?')):
            return
        if self.client is None:
            self.client = self.make_client()
        self.logout_busy = True
        self.logout_btn.configure(state='disabled')
        self.connection_label.set(tr('Memproses logout WhatsApp...'))
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
            if isinstance(client, GatewayWhatsApp):
                self.events.put(('logout_error', str(exc)))
                return
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
        if self.client and not getattr(self, 'settings_open', False) and not self.logout_busy and not self.connect_busy and not sending_active and self.status != 'STOPPED' and not self.poll_busy and now >= self.next_poll:
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
                    self.connection_label.set(tr(value))
                elif kind == 'error':
                    self.connection_label.set(tr('Koneksi gagal'))
                    self.top_status.set(tr('Periksa WhatsApp Web'))
                    self.top_dot_canvas.itemconfig(self.top_dot, fill=C_RED)
                    messagebox.showerror(tr('Operasi WhatsApp Web gagal'), tr(value))
                elif kind == 'logout_done':
                    if isinstance(value, dict) and value.get('gateway'):
                        self.client = None
                        self.gateway_key.set('')
                        self.gateway_fields['API key (memory only)'].set('')
                    self.logout_busy = False
                    self.logout_btn.configure(state='normal')
                    self.account_info = None
                    self.apply_status('STOPPED', 'Telah logout dari WhatsApp')
                    self.top_status.set(tr('WhatsApp Logout'))
                    self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
                    self.qr.configure(text=tr('Sesi telah logout. Klik Hubungkan untuk menghubungkan akun baru.'), image='')
                    self.qr_image = None
                    if isinstance(value, dict) and value.get('fallback'):
                        self.client = None
                    if isinstance(value, dict) and value.get('gateway') and not value.get('remoteLogout'):
                        messagebox.showinfo(tr('Gateway/API'), tr('Disconnected locally. The API key remains valid at your provider.'))
                    else:
                        messagebox.showinfo(tr('Logout WhatsApp'), tr('Anda telah berhasil logout dari akun WhatsApp.'))
                elif kind == 'logout_error':
                    self.logout_busy = False
                    self.logout_btn.configure(state='normal')
                    self.connection_label.set(tr('Gagal logout'))
                    messagebox.showerror(tr('Gagal logout'), tr(f'Tidak dapat logout dari WhatsApp: {value}'))
                elif kind == 'poll_error':
                    self.status = 'UNKNOWN'
                    self.top_status.set(tr('WhatsApp Web offline'))
                    self.top_dot_canvas.itemconfig(self.top_dot, fill=C_RED)
                    self.connection_label.set(tr(value))
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
                    self.run_status.set(tr(value))
                elif kind == 'run_done':
                    self.run_finished(value)
                elif kind == 'countdown':
                    self.run_status.set(tr(value))
        except queue.Empty:
            pass
        self.root.after(200, self.drain)

    def apply_status(self, state, detail=None, account=None):
        self.status = state
        if state not in {'SCAN_QR', 'SCAN_QR_CODE'}:
            self.gateway_qr.configure(image='', text=tr('WhatsApp terhubung dan siap digunakan.') if state == 'WORKING' else tr('QR Code akan muncul saat sesi meminta pemindaian.'))
        if account:
            self.account_info = account
        labels = {'WORKING': 'Terhubung - WhatsApp siap', 'SCAN_QR_CODE': 'Menunggu pemindaian QR', 'SCAN_QR': 'Menunggu pemindaian QR', 'STARTING': 'WhatsApp Web memulai sesi...', 'STOPPED': 'Sesi berhenti', 'FAILED': 'Sesi gagal; coba hubungkan ulang', 'UNKNOWN': 'Status tidak diketahui'}
        label = detail if detail else labels.get(state, f'Status sesi: {state}')
        self.connection_label.set(tr(label))
        if state == 'WORKING':
            self.top_status.set(tr('WhatsApp Siap'))
            self.top_dot_canvas.itemconfig(self.top_dot, fill=C_GREEN)
            self.qr.configure(text=tr('WhatsApp terhubung dan siap digunakan.'), image='')
            self.qr_image = None
            phone = (self.account_info or {}).get('phone') if isinstance(self.account_info, dict) else None
            pushname = (self.account_info or {}).get('pushname') if isinstance(self.account_info, dict) else None
            if phone:
                masked = mask_account_phone(phone)
                if pushname:
                    masked += f' ({pushname})'
                self.account_phone_var.set(tr(masked))
                self.account_phone_lbl.configure(fg=C_EMERALD)
            return
        if state in {'SCAN_QR', 'SCAN_QR_CODE'}:
            self.top_status.set(tr('Scan QR WhatsApp'))
            self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
            self.account_phone_var.set(tr('Menunggu scan QR'))
            self.account_phone_lbl.configure(fg=C_AMBER)
            if not self.qr_image:
                self.connection_label.set(tr('Memuat QR Code...'))
            return
        if state == 'STARTING':
            top_text = detail if detail else 'Memulai sesi...'
            self.top_status.set(tr(top_text))
            self.top_dot_canvas.itemconfig(self.top_dot, fill=C_AMBER)
            if detail:
                self.connection_label.set(tr(detail))
            else:
                self.connection_label.set(tr('WhatsApp Web memulai sesi...'))
            self.account_phone_var.set(tr('Menghubungkan...'))
            self.account_phone_lbl.configure(fg=C_AMBER)
            return
        self.top_status.set(tr(label))
        self.top_dot_canvas.itemconfig(self.top_dot, fill=C_RED)
        self.account_phone_var.set(tr('Belum terhubung'))
        self.account_phone_lbl.configure(fg=C_TEXT_DIM)
        if state not in {'SCAN_QR', 'STARTING', 'SCAN_QR_CODE'}:
            self.qr.configure(text=tr('QR Code akan tampil di sini.'), image='')
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
            self.qr.configure(image=self.qr_image, text=tr(''))
            self.gateway_qr.configure(image=self.qr_image, text='')
            self.connection_label.set(tr('Silakan pindai QR Code di sebelah kanan melalui WhatsApp di HP'))
        except Exception as exc:
            self.connection_label.set(tr(f'QR tidak dapat ditampilkan: {exc}'))
            return

    def start_run(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.update_delivery_settings():
            return
        try:
            value = self.delay_seconds.get().strip()
            if not value.isascii() or not value.isdigit() or not 10 <= int(value) <= 600:
                raise ValueError('Manual delay must be a whole number from 10 to 600 seconds.')
            self.run_delay = int(value) if self.delay_mode.get() == 'Manual' else None
        except ValueError as exc:
            messagebox.showwarning(tr('Delay'), tr(str(exc)))
            return
        if self.logout_busy:
            messagebox.showwarning(tr('Logout berlangsung'), tr('Tunggu hingga proses logout selesai.'))
            return
        if self.status not in {'WORKING', 'STARTING'}:
            messagebox.showwarning(tr('Belum terhubung'), tr('Hubungkan WhatsApp Web dan pindai QR terlebih dahulu.'))
            self.switch_tab('connection')
            return
        if not self.consent.get():
            messagebox.showwarning(tr('Persetujuan'), tr('Pastikan penerima menyetujui pesan ini.'))
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
            messagebox.showwarning(tr('Nomor opt-out tidak valid'), tr(f'Perbaiki nomor pada baris {lines} di Daftar Opt-out sebelum menjalankan batch. Tidak ada pesan yang dikirim.'))
            self.switch_tab('optout')
            return
        self.save_optout()
        targets, errors = parse_targets(self.targets_box.get('1.0', 'end-1c'), self.country.get(), set(self.data.get('optout', [])))
        if not targets:
            messagebox.showwarning(tr('Nomor tujuan'), tr('Masukkan setidaknya satu nomor valid.'))
            return
        if len(targets) > MAX_TARGETS:
            messagebox.showwarning(tr('Batas run'), tr(f'Maksimal {MAX_TARGETS} nomor per run.'))
            return
        all_templates = self.templates()
        random_mode = self.choice.get() == tr(RANDOM_TEMPLATE_OPTION)
        if random_mode:
            selected_templates = [item for item in all_templates if item.get('text', '').strip()]
            if not selected_templates:
                messagebox.showwarning(tr('Template kosong'), tr('Isi setidaknya satu template. Template kosong tidak akan dipilih.'))
                return
        else:
            template_index = self.combo_template_map.get(self.choice.get())
            if template_index is None:
                messagebox.showwarning(tr('Template kosong'), tr('Pilih template yang valid.'))
                return
            selected_templates = [all_templates[template_index]]
            if not selected_templates[0].get('text', '').strip():
                messagebox.showwarning(tr('Template kosong'), tr('Isi template sebelum mengirim.'))
                return
        for item in selected_templates:
            if item.get('image'):
                try:
                    validate_image(item['image'])
                except ValueError as exc:
                    template_name = item.get('name') or 'Template'
                    messagebox.showwarning(tr('Gambar tidak valid'), tr(f'{template_name}: {exc}\nPilih gambar yang valid pada menu Template.'))
                    return
        if isinstance(self.client, GatewayWhatsApp):
            try:
                self.client.validate_run(any(item.get('image') for item in selected_templates))
            except (ValueError, RuntimeError) as exc:
                messagebox.showerror(tr('Gateway/API'), tr(str(exc)))
                return
        if random_mode:
            names = ', '.join(item.get('name') or f'Template {all_templates.index(item) + 1}' for item in selected_templates)
            prompt = f'Kirim ke {len(targets)} nomor dengan template acak per nomor?\n\nTemplate yang dapat dipilih: {names}\nJeda acak 1-3 menit antar pesan & istirahat 5 menit tiap 10 nomor.'
        else:
            prompt = f"Kirim '{selected_templates[0].get('name')}' ke {len(targets)} nomor?\n\nJeda acak 1-3 menit antar pesan & istirahat 5 menit tiap 10 nomor."
        if errors:
            prompt += '\n\nCatatan: Nomor salah, duplikat, atau opt-out otomatis dilewati.'
        delay_summary = (f'Manual delay: {self.run_delay} seconds; batch rest: {max(300, self.run_delay)} seconds.'
                         if self.run_delay is not None else 'Random delay: 60–180 seconds; batch rest: 300 seconds.')
        prompt = prompt.replace('Jeda acak 1-3 menit antar pesan & istirahat 5 menit tiap 10 nomor.', tr(delay_summary))
        prompt += '\n\n' + self.connection_method.get()
        if not messagebox.askyesno(tr('Konfirmasi run'), tr(prompt)):
            return
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.rows = {}
        for i, t in enumerate(targets):
            if i == 0:
                self.rows[i] = self.tree.insert('', 'end', values=(t.phone, tr('Mengirim'), tr('Mengirim pesan langsung...')))
            else:
                self.rows[i] = self.tree.insert('', 'end', values=(t.phone, tr('Menunggu'), tr('Menunggu giliran (disiapkan saat jeda)')))
        self.cancel.clear()
        self.resume.set()
        self.start_button.configure(state='disabled')
        self.pause_button.configure(state='normal', text=tr('Jeda'))
        self.stop_button.configure(state='normal')
        self.run_status.set(tr(f'Menyiapkan {len(targets)} pesan...'))
        self.run_status.set(tr(f'Mengirim pesan 1 dari {len(targets)} ke {targets[0].phone}...'))
        self.root.update_idletasks()
        self.set_delivery_controls(True)
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
        manual_delay = getattr(self, 'run_delay', None)

        def wait_until_running():
            while not self.resume.is_set():
                if self.cancel.wait(0.1):
                    return False
            return not self.cancel.is_set()

        def cancel_remaining(start, detail):
            for index in range(start, len(targets)):
                outcomes.append('Dibatalkan')
                self.events.put(('row', (index, 'Dibatalkan', detail)))

        def ensure_session_ready():
            deadline = time.monotonic() + 300
            while True:
                if not wait_until_running():
                    return False, 'Dihentikan pengguna'
                try:
                    info = client.get_session()
                    state = info.get('status') if isinstance(info, dict) else None
                except Exception as exc:
                    return False, f'Koneksi WhatsApp gagal; antrean dihentikan. ({str(exc)[:160]})'
                if state == 'WORKING':
                    if self.cancel.is_set():
                        return False, 'Dihentikan pengguna'
                    return True, None
                if state != 'STARTING':
                    return False, f"Sesi WhatsApp {state or 'tidak diketahui'}; antrean dihentikan."
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, 'Sesi WhatsApp masih proses sinkronisasi setelah menunggu 5 menit; antrean dihentikan.'
                seconds = int(remaining + 0.999)
                self.events.put(('countdown', f'Menunggu kesiapan WhatsApp ({seconds // 60:02d}:{seconds % 60:02d})...'))
                self.events.put(('run_status', 'Menyiapkan sesi WhatsApp (maksimal 5 menit)...'))
                if self.cancel.wait(min(1, remaining)):
                    return False, 'Dihentikan pengguna'

        def prepare_target(target, template):
            image_path = template.get('image', '')
            image_mime = validate_image(image_path) if image_path else None
            try:
                exists = client.check_exists(target.phone)
            except Exception:
                if not isinstance(client, DirectWhatsApp):
                    raise
                exists = {'numberExists': True, 'chatId': f'{target.phone}@c.us'}
            return {
                'skip': isinstance(exists, dict) and exists.get('numberExists') is False,
                'chat_id': exists.get('chatId') if isinstance(exists, dict) else f'{target.phone}@c.us',
                'text': self.render_text(template['text'], target),
                'image_path': image_path,
                'image_mime': image_mime
            }

        def wait_delay(seconds, index, target, prepared):
            left = float(seconds)
            last_display = None
            while left > 0.000001:
                if not wait_until_running():
                    return False
                display = int(left + 0.999)
                if display != last_display:
                    prefix = f'Istirahat batch ({index} selesai)' if index % BATCH_REST_INTERVAL == 0 else ('Jeda manual' if manual_delay is not None else 'Jeda acak')
                    detail = 'Pesan siap' if prepared.is_set() else 'Menyiapkan pesan'
                    self.events.put(('countdown', f'{prefix} ({display // 60:02d}:{display % 60:02d}) - {detail} ke {target.phone}'))
                    last_display = display
                started = time.monotonic()
                if self.cancel.wait(min(0.1, left)):
                    return False
                if self.resume.is_set():
                    left -= time.monotonic() - started
            return wait_until_running()

        try:
            for i, target in enumerate(targets):
                if not wait_until_running():
                    cancel_remaining(i, 'Dihentikan pengguna')
                    break
                if i == 0:
                    ready, detail = ensure_session_ready()
                    if not ready:
                        cancel_remaining(i, detail)
                        break

                template = random.choice(template_options) if random_mode else template_options[0]
                template_name = template.get('name') or 'Template'
                used_templates[i] = template_name
                holder = {'data': None, 'error': None}
                prepared = threading.Event()

                def background_prepare(tgt=target, tpl=template, result=holder, done=prepared):
                    try:
                        if not self.cancel.is_set():
                            result['data'] = prepare_target(tgt, tpl)
                    except Exception as exc:
                        result['error'] = exc
                    finally:
                        done.set()

                self.events.put(('row', (i, 'Menyiapkan', f'Menyiapkan pesan ({template_name})...')))
                threading.Thread(target=background_prepare, daemon=True).start()
                if i:
                    if i % BATCH_REST_INTERVAL == 0:
                        delay = max(BATCH_REST_DELAY, manual_delay or 0)
                    else:
                        delay = manual_delay if manual_delay is not None else DELAY + random.randint(DELAY_JITTER_MIN, DELAY_JITTER_MAX)
                    if not wait_delay(delay, i, target, prepared):
                        cancel_remaining(i, 'Dihentikan pengguna')
                        break

                while not prepared.is_set():
                    if not wait_until_running() or self.cancel.wait(0.1):
                        break
                if not wait_until_running():
                    cancel_remaining(i, 'Dihentikan pengguna')
                    break
                ready, detail = ensure_session_ready()
                if not ready:
                    cancel_remaining(i, detail)
                    break
                if not wait_until_running():
                    cancel_remaining(i, 'Dihentikan pengguna')
                    break

                try:
                    if holder['error'] is not None:
                        raise holder['error']
                    prep = holder['data']
                    if prep['skip']:
                        status, detail = 'Dilewati', 'Nomor tidak terdaftar di WhatsApp'
                    else:
                        self.events.put(('countdown', ''))
                        self.events.put(('row', (i, 'Mengirim', f'Mengirim {template_name}...')))
                        self.events.put(('run_status', f'Mengirim pesan {i + 1} dari {len(targets)} ke {target.phone}...'))
                        client.send(target, prep['text'], prep['image_path'], prep['chat_id'], prep['image_mime'], check_first=False)
                        status, detail = 'Dikirim', f'Terkirim ke WhatsApp server | {template_name}'
                except Exception as exc:
                    err_msg = str(exc)[:220]
                    if isinstance(client, DirectWhatsApp) and re.search(r'not found|not registered|tidak terdaftar|invalid wid', err_msg, re.IGNORECASE):
                        status, detail = 'Dilewati', 'Nomor tidak terdaftar di WhatsApp'
                    else:
                        status, detail = 'Gagal', err_msg
                outcomes.append(status)
                self.events.put(('row', (i, status, detail)))
                self.events.put(('run_status', f'Selesai {i + 1} dari {len(targets)} - {target.phone} ({status})'))
        finally:
            cancel_remaining(len(outcomes), 'Dihentikan')
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
            values[1] = tr(status)
            values[2] = tr(detail)
            self.tree.item(iid, values=values)
            try:
                values = list(self.tree.item(iid, 'values'))
                values[1] = tr(status)
                values[2] = tr(detail)
                self.tree.item(iid, values=values)
            except tk.TclError:
                pass

    def run_finished(self, outcomes):
        self.set_delivery_controls(False)
        self.start_button.configure(state='normal')
        self.pause_button.configure(state='disabled', text=tr('Jeda'))
        self.stop_button.configure(state='disabled')
        if self.cancel.is_set():
            self.run_status.set(tr('Run dihentikan. Pesan yang sudah terkirim tidak dapat ditarik.'))
            return
        self.run_status.set(tr(f"Run selesai - {outcomes.count('Dikirim')} terkirim - {outcomes.count('Dilewati')} tidak terdaftar - {outcomes.count('Gagal')} gagal - {outcomes.count('Dibatalkan')} dibatalkan"))

    def toggle_pause(self):
        if self.resume.is_set():
            self.resume.clear()
            self.pause_button.configure(text=tr('Lanjutkan'))
            self.run_status.set(tr('Run dijeda sementara.'))
            return
        self.resume.set()
        self.pause_button.configure(text=tr('Jeda'))

    def stop_run(self):
        self.cancel.set()
        self.resume.set()
        self.run_status.set(tr('Menghentikan antrean...'))

    def close(self):
        if self.logout_busy:
            messagebox.showwarning(tr('Logout berlangsung'), tr('Tunggu hingga proses logout selesai.'))
            return
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(tr('Run berlangsung'), tr('Tutup aplikasi dan hentikan antrean?')):
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
