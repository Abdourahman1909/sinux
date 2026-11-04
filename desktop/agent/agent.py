"""Sinux agent loop: conversation → screen + desktop context → model with tools →
execute → verify → reply/point. Runs inside the disposable desktop session.

The model is reached through a host-side key-holding bridge (Unix socket).
"""
import base64
import os
import queue
import re
import json
from pathlib import Path
import socket
import threading
import time
import traceback

from elements import Elements, ElementError
from tools import (NATIVE, capture_image, screen_size, ToolError, to_pixels, to_model, screenshot_jpeg, find_files, list_folder,
                   read_file, write_file, run_command, run_admin, resolve_path, short_path, clipboard_read, clipboard_write,
                   notify, start_job, job_status, run_in_terminal)

BRIDGE = Path(os.environ.get('SINUX_BRIDGE_DIR', '/agent-bridge'))
MAX_STEPS = 24
KEEP_TURNS = 10          # earlier user turns kept in the model context
SCREEN_WORDS = re.compile(r"\b(where|this|that|these|those|here|screen|see|look|show|explain|point|click|press|"
                          r"button|menu|icon|tab|toolbar|sidebar|panel|field|dialog|what'?s on|on screen|read|which|how (do|can|to)|"
                          r"highlight|circle|draw|underline|arrow|find it|it says|error|message|open now|visible|layout|"
                          r"type|select|scroll|drag|zoom|picture|image|photo|color|colour)\b", re.I)
ABOUT_SINUX = '''Who we are, in one breath: Sinux is a Linux desktop with Meta's AI built into the operating system
itself, and Muse is its assistant - not a chatbot in a window, not a plugin, but part of the computer.
Mission: make Linux easy and welcoming for everyone. Muse sees the same screen you do, understands any app,
shows you how things work by pointing and drawing right on the real screen, does chores for you by voice, and
can be interrupted any time. People who never used Linux can just ask; experienced users get a fast,
hands-free co-pilot for the whole desktop.
Story: built for the Meta Global AI Developer Hackathon (2026) on Meta's Muse models through the Meta Model
API; the whole desktop, assistant included, can run straight from a USB stick on an ordinary laptop.
Technical brief (answer follow-up questions from this; if something is not here, say you are not sure):
- Layers: KDE Plasma 6 on Wayland (KWin compositor) -> the Sinux service (Python + Qt Quick), started with the
  desktop session -> the island (top centre, a layer-shell surface: presence, captions, a small living
  character) and a transparent overlay above all windows (pointer and hand-drawn marks) -> a small local
  bridge process that alone holds the Meta key and talks to the Meta Model API -> Piper speaking locally.
- Models: Meta Muse Spark 1.3 (default; Muse Wonder 1.0 selectable) with tool calling for thinking and acting;
  Meta Muse Voice Transcribe 1.0 (realtime, streamed while the talk key is held) for hearing; Meta search
  grounding (Responses API) for web answers. Piper, an open-source local voice, speaks (not a Meta model).
- Seeing: the window list through KWin scripting; screenshots through KWin's ScreenShot2 interface, which is
  granted only to Sinux's own program; every app's buttons, menus and text through Linux accessibility
  (AT-SPI), the same channel screen readers use; facts about the computer (live USB or installed, internet,
  install tools) checked at start and before each request.
- Acting (about 40 tools; a core of 22 always offered, the rest loaded on demand): windows (focus, tile, close
  via KWin), app controls by name (accessibility actions), a virtual keyboard/mouse as fallback, opening apps,
  a KRunner-style desktop search over apps, their actions, ~50 System Settings pages, files (incl. KDE's Baloo
  content index) and windows, files and a shell, administrator commands through the system's own password
  dialog (pkexec), sound/brightness/dark mode/Wi-Fi/media (PipeWire, KDE, NetworkManager, MPRIS), clipboard,
  notifications, reminders, web search.
- Showing: pointer, circle, box, underline, highlight and arrow marks with labels, drawn as animated
  hand-drawn strokes in sync with speech. Placement: exact from accessibility for controls and for words in
  text; for anything else (a picture, a map, a diagram) a second quick look at an enlarged crop finds the spot.
- Speed: one model request can plan several actions; Muse talks and thinks at the same time (narration runs on
  its own thread); marks are placed in parallel in the background; if Meta is slow, the same request is sent
  again after 4 seconds and the first answer wins; the stable prompt prefix is cached by Meta.
- Voice loop: hold Right Ctrl and speak (audio streams to Muse Voice Transcribe), release (a soft chime), Muse
  thinks and acts, answers sentence by sentence with Piper; holding Right Ctrl again interrupts it at once.
- Privacy and safety: the key lives only in the local bridge (on the USB it can be read from the stick at
  start); screenshots are sent to Meta only when a request is about the screen; the clipboard only when the
  user mentions it; administrator changes need the user's password; actions and the shell can be switched
  off in Sinux Settings; live from USB, the laptop's own disks are read-only and nothing is kept after shutdown.
- Delivery: a Debian 13 live image with KDE Plasma, built with Debian's live tools; written to USB with a
  full read-back check; Sinux updates arrive as GPG-signed packages on the stick's update partition and are
  installed at boot only if the signature verifies.
- Honest limits: thinking and hearing need the internet; each model step takes a few seconds; apps that expose
  little accessibility fall back to vision; some pop-up menus are invisible to accessibility; no memory between
  sessions yet; voice is English.
Loop per request: hear -> look (windows, text, screenshot when needed) -> think with tools -> act -> check
the result -> point/draw/explain -> speak.'''
# Tools that may run while earlier marks/sentences are still being shown and said.
OVERLAPS_NARRATION = {'mark_text', 'search_desktop', 'read_window_text', 'point_at', 'annotate', 'clear_annotations', 'ui_elements', 'list_windows',
                      'web_search', 'about_sinux', 'use_tools', 'find_files', 'read_file', 'list_folder',
                      'sinux_settings', 'list_applications', 'job_status', 'read_clipboard'}
VISUAL_ACTIONS = {'click', 'type_text', 'press_keys', 'scroll', 'look_at_screen'}
NEEDS_CURRENT_SCREEN = {'point_at', 'annotate', 'click', 'scroll'}     # coordinates must come from a current view
CHANGES_SCREEN = {'open_result', 'act_on_element', 'window_action', 'arrange_windows', 'open_application', 'open_path', 'click', 'type_text',
                  'press_keys', 'scroll', 'run_command', 'run_in_terminal', 'system_control'}

SYSTEM = '''You are Muse, the assistant built into Sinux: a Linux desktop (KDE Plasma 6) with Meta's AI inside the
operating system. You see the screen, read any app's controls and text, act across apps and the system, and show
things by drawing on the real screen. Files live under %s. The user talks to you by holding Right Ctrl (speech
is transcribed automatically and can be misheard) or types in the island at the top centre.

How to work:
1. Answer directly when no tool is needed. Talk like a warm, capable friend: one to three short sentences, plain text.
2. If a request is unclear or looks misheard, do not act: say what you heard and ask.
3. Do what is asked with tools, the most direct way (search_desktop, open_path, system_control, files, commands)
   rather than clicking through menus. Do it where the user is looking: if they name an app or want to see
   something, do it there (e.g. a web search: open_path with a search URL, read_window_text, mark the results).
4. To show or explain something (how to do it in an app, a picture, a document, a page), draw on the screen:
   send all marks of one explanation together in one reply, each with a short `say`. Use element ids from
   ui_elements for controls, mark_text for words, and `target` for parts of pictures. Never mark what is not
   visible yet (open the submenu first). When asked to show, show; click only when asked to do it.
5. Check results before saying something worked; if it failed, say so and try another way.
6. Work quietly: the island shows your progress, so do not narrate routine steps. End with a short reply.
7. Ask before anything risky or permanent (installing, deleting, sending). run_admin asks the user's permission.
8. About yourself (who you are, how you are built, why): call about_sinux, answer in two to four vivid sentences
   and offer to show it live. You are powered by Meta's Muse models; you are not Meta's "Meta AI" product.

Each message lists the open windows. Screen coordinates are 0-1000 across the whole screen. A screenshot comes
with requests about the screen; otherwise call look_at_screen. After clicks or typing a fresh one follows.'''


def fn(name, description, properties=None, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties or {}, 'required': list(required)}}}


S, N = {'type': 'string'}, {'type': 'number'}
TOOLS = [
    fn('point_at', 'Show the user where something is: animate the Sinux pointer to a control (element id from '
       'ui_elements, exact) or to a screen position.',
       {'element': dict(S, description='element id like e12 (preferred)'), 'x': N, 'y': N,
        'label': dict(S, description='2-5 word label shown beside the pointer'),
        'target': dict(S, description='the exact thing marked, e.g. "the heart in the diagram" (Sinux zooms in to place it)'),
        'say': dict(S, description='one short sentence spoken while pointing')}, ('label',)),
    fn('ui_elements', "List a window's controls by name with ids (buttons, menu items, tabs, fields, checkboxes) and "
       'their positions. Defaults to the active window. Optional query filters by words, e.g. "save".',
       {'window_id': S, 'query': S}),
    fn('act_on_element', 'Act on a control from ui_elements by id: press (buttons, menu items, tabs), toggle '
       '(checkboxes), open_menu (menu bar items), focus, or set_text (text fields).',
       {'element': S, 'action': {'type': 'string', 'enum': ['press', 'toggle', 'open_menu', 'focus', 'set_text']},
        'text': S}, ('element', 'action')),
    fn('annotate', 'Draw a hand-drawn mark: on a control by element id, or on the region (x1,y1)-(x2,y2); an arrow '
       'goes from (x1,y1) to (x2,y2) or to the element. Marks stay until the next request.',
       {'shape': {'type': 'string', 'enum': ['circle', 'box', 'underline', 'highlight', 'arrow']},
        'element': dict(S, description='element id like e12 (preferred)'),
        'x1': N, 'y1': N, 'x2': N, 'y2': N,
        'label': dict(S, description='optional 1-4 word label'),
        'target': dict(S, description='the exact thing marked, e.g. "the heart in the diagram" (Sinux zooms in to place it)'),
        'say': dict(S, description='one short sentence spoken while this mark is drawn')}, ('shape',)),
    fn('mark_text', 'Mark words or phrases in a window\'s text (document, page, terminal) at their exact positions; '
       'one call marks every visible match. Use this instead of annotate for text.',
       {'find': S, 'match': {'type': 'string', 'enum': ['contains', 'word', 'starts', 'phrase']},
        'shape': {'type': 'string', 'enum': ['underline', 'highlight', 'circle', 'box']},
        'window_id': dict(S, description='defaults to the active window'),
        'say': dict(S, description='one short sentence spoken as the marks appear')}, ('find',)),
    fn('clear_annotations', 'Remove all marks from the screen.'),
    fn('look_at_screen', 'Take a fresh screenshot (e.g. to check the result of an action or wait for something to load).'),
    fn('list_windows', 'List open windows: id, title, app, box [x1,y1,x2,y2] in normalised coordinates, active state.'),
    fn('window_action', 'Focus, close, minimize, maximize or restore a window by id.',
       {'window_id': S, 'action': {'type': 'string', 'enum': ['focus', 'close', 'minimize', 'maximize', 'restore']}},
       ('window_id', 'action')),
    fn('arrange_windows', 'Tile windows side by side or move one to a screen region.',
       {'layout': {'type': 'string', 'enum': ['side_by_side', 'left_half', 'right_half', 'center']},
        'window_ids': {'type': 'array', 'items': S, 'description': 'For side_by_side give two ids (left, right); otherwise one.'}},
       ('layout', 'window_ids')),
    fn('open_application', 'Launch an installed application by name (e.g. files, terminal, text editor, calculator, document viewer, image viewer).',
       {'name': S}, ('name',)),
    fn('list_applications', 'List installed applications (always current, including newly installed ones).'),
    fn('sinux_settings', 'Read your own Sinux settings: connection, model, talk key, voice, languages, permissions.'),
    fn('change_sinux_settings', 'Change your own Sinux preferences when the user asks. Only include fields to change.',
       {'talkKey': {'type': 'string', 'enum': ['Control_R', 'Alt_R', 'Super_R', 'Menu', 'F8', 'F9', 'F10', 'Pause', 'Scroll_Lock']},
        'speak': {'type': 'boolean', 'description': 'speak answers aloud'},
        'voiceSpeed': {'type': 'number', 'description': '0.7 to 1.5'},
        'languages': {'type': 'array', 'items': S, 'description': 'e.g. ["English","Turkish"]'},
        'model': {'type': 'string', 'enum': ['muse-spark-1.3', 'muse-wonder-1.0']},
        'reducedMotion': {'type': 'boolean'}}),
    fn('open_sinux_settings', 'Open the Sinux Settings app on a page.',
       {'page': {'type': 'string', 'enum': ['connection', 'voice', 'assistant', 'about']}}),
    fn('open_path', 'Open a file, a folder or a web address (https://...) in the right application; web addresses '
       'open in the browser.', {'path': dict(S, description='file or folder path, or a URL')}, ('path',)),
    fn('search_desktop', 'Search everything at once, like KRunner: installed apps and their quick actions, System '
       'Settings pages (e.g. "mouse", "display", "night light"), files by name or content, and open windows. Prefer '
       'this over clicking through menus to find a setting, app or file.', {'query': S}, ('query',)),
    fn('open_result', 'Open a search_desktop result by id (opens the app, settings page, file or window).',
       {'id': S}, ('id',)),
    fn('read_window_text', 'Read the text of a window through accessibility (a web page, document, terminal output, '
       'dialog), without a screenshot. Defaults to the active window.', {'window_id': S}),
    fn('find_files', 'Search file and folder names in the home folder (case-insensitive words).', {'query': S}, ('query',)),
    fn('list_folder', 'List the contents of a folder (default: home).', {'path': S}),
    fn('read_file', 'Read a text file.', {'path': S}, ('path',)),
    fn('write_file', 'Create or overwrite a text file in the home folder.', {'path': S, 'content': S}, ('path', 'content')),
    fn('click', 'Click at a screen position (normalised 0-1000). Give target = the exact name of the control you mean '
       '(e.g. "Create New"), or leave it out for an empty area; Sinux then aims at that control exactly.',
       {'x': N, 'y': N, 'target': S, 'button': {'type': 'string', 'enum': ['left', 'right', 'middle']}, 'double': {'type': 'boolean'}},
       ('x', 'y')),
    fn('type_text', 'Type text into the focused field or window.', {'text': S}, ('text',)),
    fn('press_keys', 'Press a key or shortcut, e.g. "ctrl+s", "Return", "alt+F4", "ctrl+shift+n".', {'keys': S}, ('keys',)),
    fn('scroll', 'Scroll at a screen position. Positive amount scrolls up, negative down.',
       {'x': N, 'y': N, 'amount': N}, ('x', 'y', 'amount')),
    fn('run_command', 'Run a bash command in the home folder as the user and get its output (python3 available). '
       'Long work: background=true, then job_status. Use run_in_terminal when the user should watch, run_admin for '
       'administrator commands.',
       {'command': S, 'timeout': dict(N, description='seconds, max 120 (default 20)'),
        'background': {'type': 'boolean'}}, ('command',)),
    fn('run_in_terminal', 'Run a command in a visible Konsole window so the user can watch; returns its output.',
       {'command': S}, ('command',)),
    fn('job_status', 'Check a background job started with run_command(background=true).', {'job': S}, ('job',)),
    fn('read_clipboard', 'Read the text currently on the clipboard.'),
    fn('copy_to_clipboard', 'Put text on the clipboard.', {'text': S}, ('text',)),
    fn('notify', 'Show a desktop notification.', {'title': S, 'body': S}, ('title', 'body')),
    fn('run_admin', 'Run an administrator command; the system shows its own password dialog and the user approves. '
       'Installing software (Debian): find the package first (run_command: apt-cache search), say in one sentence '
       'what you will install, then run_admin("apt-get update && apt-get install -y PKG"). Apps not in Debian need '
       'the vendor\'s repository: explain and ask first. Only for what the user asked for.',
       {'command': S, 'reason': dict(S, description='one short sentence: what this does, shown in the log')}, ('command',)),
    fn('web_search', 'Quick web answer with cited sources (Meta search grounding), for when the user just wants an '
       'answer and did not ask to see it in a browser. Mention where the answer comes from.',
       {'query': S}, ('query',)),
    fn('system_control', 'Change or read a system setting. volume: 0-100, +10, -10, up, down, mute, unmute, status. '
       'brightness: 0-100, +10, -10, status. appearance: dark or light. wifi: on, off, status, list, connect '
       '(with network and optional password). media: play_pause, play, pause, next, previous, stop.',
       {'setting': {'type': 'string', 'enum': ['volume', 'brightness', 'appearance', 'wifi', 'media']},
        'value': S, 'network': S, 'password': S}, ('setting',)),
    fn('about_sinux', 'Facts about how Sinux itself is built and integrated into this computer.'),
    fn('remind_me', 'Remind the user later: a notification plus a spoken reminder.',
       {'minutes': dict(N, description='from now, e.g. 0.5 or 10'), 'message': S}, ('minutes', 'message')),
]
# Tool groups: a small core is always offered; the rest load when the request needs them
# (keywords in the request, the model's use_tools call, or a call to a tool that is not loaded yet).
TOOL_GROUPS = {
    'files': ('list_folder', 'read_file', 'write_file', 'find_files'),
    'layout': ('arrange_windows', 'list_applications', 'list_windows'),
    'system': ('system_control', 'run_admin', 'notify', 'remind_me', 'read_clipboard', 'copy_to_clipboard'),
    'terminal': ('run_in_terminal', 'job_status'),
    'sinux': ('sinux_settings', 'change_sinux_settings', 'open_sinux_settings', 'about_sinux'),
}
GROUP_WORDS = {
    'files': r'file|folder|document|note|save|write|read|pdf|txt|report|essay|letter|list of',
    'layout': r'arrange|tile|side by side|split|left half|right half|organi[sz]e|layout|install|which apps|what apps',
    'system': r'volume|louder|quieter|loud|quiet|mute|sound|bright|dim|dark|light mode|theme|wi-?fi|internet|network|'
              r'music|play|pause|resume|next|previous|song|track|remind|later|minutes|alarm|timer|notify|clipboard|copy|paste|'
              r'install|uninstall|remove .{0,20}app|update|upgrade|package|admin|sudo|driver',
    'terminal': r'terminal|konsole|command|script|install|background|job|progress',
    'sinux': r'setting|preference|voice|speak|talk key|language|model|yourself|who are you|how do you|sinux|integrated|built',
}
GROUP_PATTERNS = {name: re.compile(words, re.I) for name, words in GROUP_WORDS.items()}
GROUPED = {tool for names in TOOL_GROUPS.values() for tool in names}
GROUP_OF = {tool: name for name, names in TOOL_GROUPS.items() for tool in names}
TOOLS.append(fn('use_tools', 'Load more tools when the request needs them. Groups: files (list/read/write files), layout (tile windows, list '
                'installed apps), system (volume, brightness, dark/light, Wi-Fi, media, reminders, notifications, '
                'clipboard, run_admin for installing software / administrator commands), terminal (visible Konsole, background jobs), sinux (your own settings and how you are '
                'built: about_sinux).',
                {'groups': {'type': 'array', 'items': {'type': 'string', 'enum': sorted(TOOL_GROUPS)}}}, ('groups',)))
ACTIONS = {'run_admin', 'open_result', 'system_control', 'act_on_element', 'window_action', 'arrange_windows', 'open_application', 'open_path', 'click', 'type_text',
           'press_keys', 'scroll', 'run_command', 'write_file', 'run_in_terminal', 'copy_to_clipboard'}


# Labels that describe an area rather than visible text: no zoomed lookup for these.
AREA_WORDS = re.compile(r'\b(here|there|space|area|empty|blank|region|sidebar|panel|pane|section|view|toolbar|'
                        r'menu ?bar|title ?bar|status ?bar|window|corner|top|bottom|left|right)\b', re.I)
# Requests about "this": attach the selection / focused text / control under the pointer automatically.
THIS_WORDS = re.compile(r"\b(this|that|these|those|here|selected|selection|highlighted|the error|on (my|the) screen|"
                        r"what am i looking at|what i'?m looking at)\b", re.I)
MENU_ROLES = {'menu item', 'check menu item', 'radio menu item', 'menu'}


FILLER = re.compile(r'\b(the|a|an|button|menu|item|icon|tab|option|field|link|entry|control)\b', re.I)


def names_match(name, want):
    """Is the control called `name` what a mark labelled `want` means? Exact first, then tolerant:
    "the Start Recording button" matches "Start Recording"."""
    name, want = plain(name), plain(want)
    if not name or not want: return 0
    if name == want: return 3
    core = ' '.join(FILLER.sub(' ', want).split())
    if core and core == name: return 2
    # A longer label containing the control's name ("the Start Recording button"); never the reverse, which is
    # ambiguous ("Folder" must not match "Open Folder With").
    if len(name) >= 4 and re.search(r'\b%s\b' % re.escape(name), core): return 1
    return 0


def plain(text):
    """Label/name for comparison: no step numbers, accelerators, ellipses or case."""
    import re
    text = re.sub(r'^\s*(step\s*)?\d+\s*[.):-]?\s*', '', str(text or ''), flags=re.I)
    return re.sub(r'[&…"“”]|\.\.\.', '', text).strip().strip(':').strip().lower()


def compact(w):
    """Window summary for the model: id, title, app, normalised box, state."""
    x1, y1 = to_model(w['x'], w['y'])
    x2, y2 = to_model(w['x'] + w['width'], w['y'] + w['height'])
    return {'id': w['id'], 'title': w['title'], 'app': w['app'], 'box': [x1, y1, x2, y2],
            **({'active': True} if w.get('active') else {}), **({'minimized': True} if w.get('minimized') else {})}


def compact_result(result):
    if isinstance(result.get('window'), dict):
        result = dict(result, window=compact(result['window']))
    return result


class BridgeError(Exception):
    pass


def cloud_call(op, timeout=120, **extra):
    """Meta server-side tools through the bridge (web search, image generation)."""
    token = (BRIDGE / 'capability').read_text().strip()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(timeout)
        peer.connect(str(BRIDGE / 'bridge.sock'))
        peer.sendall((json.dumps({'token': token, 'op': op, **extra}) + '\n').encode())
        raw = bytearray()
        while not raw.endswith(b'\n'):
            chunk = peer.recv(262144)
            if not chunk: break
            raw.extend(chunk)
    reply = json.loads(raw) if raw else {'ok': False, 'error': 'bridge closed'}
    if not reply.get('ok'):
        if reply.get('error') == 'not_connected':
            raise ToolError("Sinux isn't connected to Meta yet (add the key in Settings).")
        if reply.get('status') in (401, 403):
            raise ToolError('Meta rejected the API key (HTTP %s).' % reply.get('status'))
        raise ToolError('%s failed (HTTP %s): %s' % (op.replace('_', ' '), reply.get('status', '-'), reply.get('error') or 'no detail'))
    return reply


def call_model(body, timeout=120, on_text=None):
    """One model request through the bridge. With on_text, reply text is streamed to it as it arrives."""
    token = (BRIDGE / 'capability').read_text().strip()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(timeout)
        peer.connect(str(BRIDGE / 'bridge.sock'))
        peer.sendall((json.dumps({'token': token, 'body': body, 'stream': bool(on_text)}) + '\n').encode())
        buffer, raw = bytearray(), b''
        while not raw:
            chunk = peer.recv(65536)
            if not chunk: break
            buffer.extend(chunk)
            while b'\n' in buffer:
                line, _, rest = bytes(buffer).partition(b'\n')
                buffer = bytearray(rest)
                value = json.loads(line)
                if 'delta' in value:
                    if on_text: on_text(value['delta'])
                    continue
                raw = line
                break
    if not raw:
        raise BridgeError('The model bridge closed the connection.')
    reply = json.loads(raw)
    if not reply.get('ok'):
        if reply.get('error') == 'not_connected':
            raise BridgeError("Sinux isn't connected yet. Right-click the island to open Settings and add your Meta API key.")
        if reply.get('error') == 'session_request_limit':
            raise BridgeError('This session reached its limit of %s model requests.' % reply.get('limit'))
        if reply.get('status') in (401, 403):
            raise BridgeError('Meta rejected the API key (HTTP %s). Check the key in Sinux Settings.' % reply.get('status'))
        raise BridgeError('Model request failed (HTTP %s): %s' % (reply.get('status'), reply.get('error') or 'no detail'))
    return reply


def image_part(jpeg):
    return {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode(),
                                               'detail': 'high'}}


class Cancelled(Exception):
    pass


class Agent:
    """One conversation. `events` receives UI updates (thread-safe callback)."""

    def __init__(self, desktop, input_device, events, *, model_call=call_model, capture=screenshot_jpeg, narrate=None,
                 settings=None):
        self.desktop, self.input, self.events = desktop, input_device, events
        # narrate(text) speaks/captions one sentence and returns when it is finished.
        self._speak_line = narrate or (lambda text: time.sleep(min(5, 0.9 + 0.055 * len(text))))
        # Narration runs on its own thread: marks appear with their sentence, in order, while the next
        # model request is already being made. Tools that change the screen wait for it to finish.
        self.speech, self.pending, self.pending_lock = queue.Queue(), 0, threading.Lock()
        self.drained = threading.Event(); self.drained.set()
        self.visual_pending = 0
        self.visuals_done = threading.Event(); self.visuals_done.set()
        threading.Thread(target=self._narrator, name='sinux-narrator', daemon=True).start()
        # settings: object with get() -> dict, change(patch) -> dict, open(page)
        self.settings = settings
        self.model_call, self.capture = model_call, capture
        import environment
        try: computer = environment.facts()
        except Exception as error: computer = '- (could not check: %s)' % error
        self.messages = [{'role': 'system', 'content': SYSTEM % str(resolve_path('~')) + '\n\nThis computer (checked when '
                          'Sinux started):\n' + computer}]
        self.transcript = []   # what the panel shows
        self.cancel = threading.Event()
        self.busy = False
        self.lock = threading.Lock()
        self.screen_fresh = False   # True only while the latest screenshot still shows the screen
        self.disabled = set()       # tools switched off in Sinux Settings
        self.groups, self.used_groups = set(), set()   # tool groups offered this turn / used last turn
        self.elements = Elements()
        from discover import Discover
        self.discover = Discover(desktop)
        self.usage = {'requests': 0, 'seconds': 0.0, 'prompt': 0, 'completion': 0, 'images': 0, 'last': {}}

    # ------------------------------------------------------------ public
    def reset(self):
        self.cancel.set()
        with self.lock:
            self.messages = self.messages[:1]
            self.transcript = []
        self.events('reset', {})

    def interrupt(self):
        """The user spoke over Sinux: stop the current turn at the next safe point."""
        if self.busy:
            self.cancel.set()

    def send(self, text, spoken=False):
        if self.busy and self.cancel.is_set():
            # The user interrupted: start this request as soon as the stopped turn has wound down.
            def later():
                deadline = time.monotonic() + 15
                while self.busy and time.monotonic() < deadline:
                    time.sleep(0.1)
                if not self.send(text, spoken):
                    self._record('error', 'I was still finishing the previous request; please say that again.')
            threading.Thread(target=later, daemon=True).start()
            return True
        with self.lock:
            if self.busy: return False
            self.busy = True
        self.cancel.clear()
        threading.Thread(target=self._turn, args=(text, spoken), daemon=True).start()
        return True

    # ------------------------------------------------------------ narration
    def _narrator(self):
        while True:
            show, text = self.speech.get()
            try:
                if not self.cancel.is_set():
                    if show: show()
                    if text: self._speak_line(text)
            except Exception:
                traceback.print_exc()
            finally:
                with self.pending_lock:
                    self.pending -= 1
                    if show: self.visual_pending -= 1
                    if self.pending == 0: self.drained.set()
                    if self.visual_pending == 0: self.visuals_done.set()

    def _queue(self, show=None, text=''):
        """Show something (a mark or a pointer) and say its sentence, after what is queued."""
        with self.pending_lock:
            self.pending += 1
            self.drained.clear()
            if show:
                self.visual_pending += 1
                self.visuals_done.clear()
        self.speech.put((show, str(text or '')[:300]))

    def narrate(self, text):
        self._queue(None, text)

    def _drain(self, limit=90):
        """Wait until everything queued has been shown and said (skipped quickly after an interrupt)."""
        self.drained.wait(limit)

    def _stream_text(self, delta):
        """Streamed reply text: say each sentence as soon as it is complete."""
        self._partial += delta
        while True:
            end = re.search(r'[.!?…:](?=\s)|\n', self._partial)
            if not end: return
            sentence, self._partial = self._partial[:end.end()].strip(), self._partial[end.end():]
            if any(c.isalnum() for c in sentence):
                self.narrate(sentence); self._voiced = True

    def _flush_text(self):
        rest, self._partial = self._partial.strip(), ''
        if any(c.isalnum() for c in rest):
            self.narrate(rest); self._voiced = True

    # ------------------------------------------------------------ loop
    def _record(self, kind, text, **extra):
        entry = dict(kind=kind, text=text, at=time.time(), **extra)
        with self.lock:
            self.transcript.append(entry)
        self.events('entry', entry)

    def _status(self, text):
        self.events('status', {'text': text})

    def _strip_old_images(self):
        for message in self.messages:
            if isinstance(message.get('content'), list):
                message['content'] = [p if p.get('type') != 'image_url' else
                                      {'type': 'text', 'text': '[earlier screenshot omitted]'} for p in message['content']]

    def _screen_message(self, text, with_image=True):
        self._strip_old_images()
        if not with_image:
            return {'role': 'user', 'content': text}
        try:
            jpeg = self.capture()
            self.screen_fresh = True
            self.usage['images'] += 1
            return {'role': 'user', 'content': [{'type': 'text', 'text': text}, image_part(jpeg)]}
        except Exception as error:  # screenshot failure must not end the conversation
            print('screenshot failed:', repr(error), flush=True)
            return {'role': 'user', 'content': text + '\n(Screenshot unavailable: %s)' % error}

    def _trim(self):
        """Keep the system prompt and the last KEEP_TURNS user turns (cut only at user boundaries)."""
        starts = [i for i, m in enumerate(self.messages)
                  if m['role'] == 'user' and not str(m['content'] if isinstance(m['content'], str)
                                                     else m['content'][0].get('text', '')).startswith('Screen after')]
        if len(starts) > KEEP_TURNS:
            self.messages = self.messages[:1] + self.messages[starts[-KEEP_TURNS]:]
        # Earlier turns are only background: when the history grows large, drop window lists, long tool
        # results and long arguments in one go (rarely, so the cached prompt prefix stays the same between turns).
        if sum(len(json.dumps(m.get('content'), default=str)) for m in self.messages) < 30000:
            return
        for message in self.messages[1:]:
            content = message.get('content')
            if message['role'] == 'user' and isinstance(content, str) and '\n\n[' in content:
                message['content'] = content.split('\n\n[')[0]
            elif message['role'] == 'tool' and isinstance(content, str) and len(content) > 400:
                message['content'] = content[:400] + '…(trimmed)'
            for call in message.get('tool_calls') or []:
                arguments = call['function'].get('arguments') or ''
                if len(arguments) > 400:
                    try:
                        values = json.loads(arguments)
                        call['function']['arguments'] = json.dumps(
                            {k: (v[:200] + '…' if isinstance(v, str) and len(v) > 200 else v) for k, v in values.items()},
                            ensure_ascii=False)
                    except (ValueError, AttributeError):
                        call['function']['arguments'] = '{}'

    def _account(self, reply, began, had_image):
        usage = (reply.get('response') or {}).get('usage') or {}
        prompt, completion = int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0)
        self.usage['requests'] += 1
        self.usage['seconds'] += time.monotonic() - began
        self.usage['prompt'] += prompt
        self.usage['completion'] += completion
        self.usage['last'] = {'prompt': prompt, 'completion': completion, 'image': had_image,
                              'seconds': round(time.monotonic() - began, 2)}
        print('model request #%d: prompt=%d completion=%d image=%s %.2fs' % (
            self.usage['requests'], prompt, completion, had_image, time.monotonic() - began), flush=True)

    def _turn(self, text, spoken=False):
        try:
            self._record('user', text, spoken=spoken)
            self.events('clear_marks', {})
            visual = bool(SCREEN_WORDS.search(text))
            # Groups for this turn: what the request mentions, plus what the previous turn used (follow-ups).
            # Groups only grow during a session, so the tool list (an early part of every prompt) rarely changes.
            self.groups |= {name for name, pattern in GROUP_PATTERNS.items() if pattern.search(text)} | self.used_groups
            self.screen_fresh = False
            self._status('Looking at your screen…' if visual else 'Thinking…')
            context = self._context_note() + self._network_note() + (self._this_note(text) if THIS_WORDS.search(text) else '')
            self._trim()
            said = ('[Spoken request, transcribed automatically - it may be cut off or misheard]\n' + text) if spoken else text
            self.messages.append(self._screen_message(said + context, with_image=visual))
            for step in range(MAX_STEPS):
                if self.cancel.is_set(): raise Cancelled()
                self._status('Thinking…' if step == 0 else 'Checking the result…')
                began = time.monotonic()
                had_image = any(isinstance(m.get('content'), list) and any(p.get('type') == 'image_url' for p in m['content'])
                                for m in self.messages[-6:])
                offered = {n for g in self.groups for n in TOOL_GROUPS[g]}
                tools = [t for t in TOOLS if t['function']['name'] not in self.disabled
                         and (t['function']['name'] not in GROUPED or t['function']['name'] in offered)]
                body = {'messages': self.messages, 'tools': tools, 'tool_choice': 'auto', 'parallel_tool_calls': True,
                        'max_completion_tokens': 4096, 'reasoning_effort': 'minimal'}
                self._partial, self._voiced = '', False
                if self.model_call is call_model:
                    reply = self.model_call(body, on_text=self._stream_text)
                    self._flush_text()
                else:
                    reply = self.model_call(body)
                if not self._voiced and not reply['response']['choices'][0]['message'].get('tool_calls'):
                    content = (reply['response']['choices'][0]['message'].get('content') or '')
                    if content.strip():
                        self._stream_text(content); self._flush_text()
                self._account(reply, began, had_image)
                if self.cancel.is_set(): raise Cancelled()
                message = reply['response']['choices'][0]['message']
                calls = message.get('tool_calls') or []
                if reply['response']['choices'][0].get('finish_reason') == 'length':
                    print('reply cut off at the length limit (%d tool calls)' % len(calls), flush=True)
                    calls = [c for c in calls if self._valid_arguments(c)]
                    if not calls:
                        self.messages.append({'role': 'user', 'content': 'Your last reply was cut off (too long) and nothing '
                                              'was done. Reply again with fewer, shorter tool calls - for many words use one '
                                              'mark_text call.'})
                        continue
                assistant = {'role': 'assistant', 'content': message.get('content') or ''}
                if calls: assistant['tool_calls'] = calls
                self.messages.append(assistant)
                if message.get('content') and calls:
                    # Shown on the island while working; not spoken (the island's motion says "working").
                    self._record('assistant', message['content'].strip(), interim=True, voiced=True)
                if not calls:
                    final = (message.get('content') or '').strip() or 'Done.'
                    self._drain()
                    self._record('assistant', final, voiced=self._voiced)
                    return
                acted = False
                for call in calls:
                    if self.cancel.is_set(): raise Cancelled()
                    name = call['function']['name']
                    try: arguments = json.loads(call['function'].get('arguments') or '{}')
                    except ValueError: arguments = {}
                    if name in NEEDS_CURRENT_SCREEN and not self.screen_fresh and not arguments.get('element'):
                        # Never place marks or clicks from an old (or missing) view of the screen.
                        result = {'error': 'Not done: your view of the screen is out of date (it changed, or you have '
                                           'not seen it this turn). A fresh screenshot follows; call %s again using '
                                           'coordinates from it.' % name}
                        acted = True
                    else:
                        result = self._execute(name, arguments)
                        if name in CHANGES_SCREEN: self.screen_fresh = False
                        # Opening apps/files and arranging windows verify themselves in text;
                        # only direct UI input (and explicit looks) needs a new screenshot.
                        acted = acted or name in VISUAL_ACTIONS
                    self.messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                          'content': json.dumps(result, ensure_ascii=False, separators=(',', ':'))[:6000]})
                if acted:
                    time.sleep(.6)
                    self.messages.append(self._screen_message('Screen after your actions (verify the result):'))
            self._record('assistant', 'I stopped after %d steps so I don\'t run away with this. Tell me if I should keep going.' % MAX_STEPS)
        except Cancelled:
            self._record('notice', 'Stopped.')
            self._repair()
        except BridgeError as error:
            self._record('error', str(error))
            self._repair()
        except Exception as error:
            traceback.print_exc()
            self._record('error', 'Something went wrong: %s' % error)
            self._repair()
        finally:
            self._drain()
            with self.lock:
                self.busy = False
            self._status('')
            self.events('idle', {})

    def _repair(self):
        """Keep history valid: every tool call needs an answer, and end on a non-tool message."""
        answered = {m.get('tool_call_id') for m in self.messages if m.get('role') == 'tool'}
        for message in list(self.messages):
            for call in message.get('tool_calls') or []:
                if call['id'] not in answered:
                    self.messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': '{"error":"interrupted"}'})
        if self.messages[-1]['role'] != 'assistant':
            self.messages.append({'role': 'assistant', 'content': '(interrupted)'})

    def _context_note(self):
        try:
            windows = self.desktop.windows()
        except Exception:
            return ''
        if not windows:
            return '\n\n[Desktop: no application windows are open.]'
        lines = ['%s "%s" (%s)%s' % (w['id'], w['title'], w['app'], ' ← active' if w['active'] else '')
                 for w in windows[:12]]
        return '\n\n[Open windows, top first:\n' + '\n'.join(lines) + ']'

    @staticmethod
    def _valid_arguments(call):
        try:
            return isinstance(json.loads(call['function'].get('arguments') or '{}'), dict)
        except ValueError:
            return False

    def _network_note(self):
        if not NATIVE: return ''
        import environment
        state = environment.connectivity()
        return {'online': '\n[Internet: online]', 'login page': '\n[Internet: this Wi-Fi needs its login page first '
                '(captive portal) - suggest opening Firefox to sign in]', 'limited': '\n[Internet: limited]',
                'offline': '\n[Internet: offline]'}.get(state, '')

    def _this_note(self, request):
        """What "this" probably means: the selection or focused text in the active window, the control under
        the mouse pointer, and (only when asked about) the clipboard. Read locally, no screenshot needed."""
        lines = []
        try:
            active = next((w for w in self.desktop.windows() if w['active']), None)
            focus = self.elements.focused(active) if active else None
            if focus and focus.get('selected'):
                lines.append('Selected text in "%s": %s' % (active['title'], focus['selected'][:2500]))
            elif focus and focus.get('text'):
                lines.append('Focused %s in "%s"%s: %s' % (focus['role'], active['title'],
                             (' (%s)' % focus['name']) if focus['name'] else '', focus['text'][:2000]))
        except Exception as error:
            print('this: focus unavailable:', repr(error), flush=True)
        try:
            px, py = self.desktop.cursor()
            hit, _ = self._controls_at(px, py) if px >= 0 else (None, [])
            if hit:
                mx, my = to_model(px, py)
                lines.append('Under the mouse pointer (%d, %d): %s "%s"' % (mx, my, hit['role'], hit['name']))
        except Exception as error:
            print('this: pointer unavailable:', repr(error), flush=True)
        if re.search(r'clipboard|copied|paste', request, re.I):
            try: lines.append('Clipboard: %s' % (clipboard_read().get('text') or '(empty)')[:2000])
            except Exception: pass
        return ('\n\n[What "this" may refer to (read just now):\n' + '\n'.join(lines) + ']') if lines else ''

    # ------------------------------------------------------------ tools
    def _execute(self, name, a):
        if name not in OVERLAPS_NARRATION:
            # Do not act while marks/pointers are still being shown; plain speech may continue meanwhile.
            self.visuals_done.wait(90)
        label, detail = self._describe(name, a)
        self._status(label + (' ' + detail if detail else '') + '…')
        label = label + (' ' + detail if detail else '')
        shown = {k: (v if k not in ('password', 'text', 'markdown', 'key') else '<%d chars>' % len(str(v))) for k, v in a.items()}
        try:
            result = self._dispatch(name, a)
            self._record('tool', label, tool=name, ok=True)
            print('tool %s %s -> %s' % (name, json.dumps(shown, ensure_ascii=False)[:200],
                                        json.dumps(result, ensure_ascii=False, default=str)[:160]), flush=True)
            return result
        except ToolError as error:
            self._record('tool', '%s — %s' % (label, short_path(error)), tool=name, ok=False)
            print('tool %s %s -> ERROR %s' % (name, json.dumps(shown, ensure_ascii=False)[:200], str(error)[:200]), flush=True)
            return {'error': str(error)}
        except Exception as error:
            traceback.print_exc()
            self._record('tool', '%s — failed' % label, tool=name, ok=False)
            return {'error': '%s: %s' % (type(error).__name__, error)}

    def _remind(self, message):
        try: notify('Reminder', message, icon='appointment-reminder')
        except ToolError: pass
        self.events('announce', {'text': 'Reminder: ' + message})

    def _job_done(self, job):
        code = job['process'].returncode
        text = 'Background job %s finished%s: %s' % (job['id'], '' if code == 0 else ' with an error',
                                                     job['command'][:80])
        try: notify('Sinux', text, icon='dialog-information' if code == 0 else 'dialog-error')
        except ToolError: pass
        self.events('announce', {'text': text})

    def _paste(self, text):
        """Characters the virtual US keyboard cannot type go in through the clipboard."""
        clipboard_write(text)
        time.sleep(.1)
        self.input.press_keys('ctrl+v')
        time.sleep(.1)

    def _controls_at(self, px, py):
        """(control under the point or None, all positioned controls) for the window or menu there."""
        try:
            windows = self.desktop.windows()
            def inside(w): return w['x'] <= px < w['x'] + w['width'] and w['y'] <= py < w['y'] + w['height']
            order = sorted(windows, key=lambda w: (not w.get('active'), not inside(w)))
            for window in order[:2]:
                found = self.elements.probe(window, self.desktop.popups(window['pid']))
                hits = [e for e in found if e['rect'][0] <= px < e['rect'][0] + e['rect'][2] and e['rect'][1] <= py < e['rect'][1] + e['rect'][3]]
                hits.sort(key=lambda e: (not e['inMenu'], e['rect'][2] * e['rect'][3]))
                if hits or inside(window):
                    return (hits[0] if hits else None), found
        except Exception:
            traceback.print_exc()
        return None, []

    def _prefetch(self, work, fallback, exact=False):
        """Start placing a mark now (in parallel with the others); return a function that waits for the result."""
        if exact: return lambda: fallback
        box, done = [fallback], threading.Event()
        def run():
            try: box[0] = work()
            except Exception: traceback.print_exc()
            finally: done.set()
        threading.Thread(target=run, daemon=True).start()
        return lambda: (done.wait(10), box[0])[1]

    def _precheck(self, px, py, label):
        """Fast check before a mark is queued: refuse a mark that would land on a different menu item."""
        want = plain(label)
        if not want: return None
        hit, found = self._controls_at(px, py)
        if any(names_match(e['name'], label) for e in found): return None
        if hit and hit['role'] in MENU_ROLES and not names_match(hit['name'], label):
            raise ToolError('That spot is "%s", not "%s". If "%s" is inside a submenu, open that submenu first '
                            '(act_on_element), call ui_elements again, then use its element id.' % (hit['name'], label, label))
        return None

    def _aim(self, px, py, label, refuse=True, target=None):
        """Check a coordinate target against the real controls. Returns (px, py, rect or None, note).
        Snaps to the control the label names; refuses when a menu item would be missed."""
        want = plain(label)
        hit, found = self._controls_at(px, py)
        named = [e for e in found if want and names_match(e['name'], label)]
        named.sort(key=lambda e: (-names_match(e['name'], label), not e['inMenu'],
                                  abs(e['rect'][0] + e['rect'][2] / 2 - px) + abs(e['rect'][1] + e['rect'][3] / 2 - py)))
        if named:
            x, y, w, h = named[0]['rect']
            note = None if hit is named[0] or (hit and plain(hit['name']) == want) else 'snapped to "%s"' % named[0]['name']
            return x + w // 2, y + h // 2, named[0]['rect'], note
        if refuse and want and hit and hit['role'] in MENU_ROLES and not names_match(hit['name'], label):
            raise ToolError('That spot is "%s", not "%s". If "%s" is inside a submenu, open that submenu first '
                            '(act_on_element), call ui_elements again, then use its element id.' % (hit['name'], label, label))
        if target:
            # Anything on screen (part of a picture, diagram, map, chart, or a control): zoom in and find it.
            box = self._locate(px, py, str(target)[:120], wide=True)
            if box:
                x1, y1, x2, y2 = box
                return (x1 + x2) // 2, (y1 + y2) // 2, (x1, y1, x2 - x1, y2 - y1), 'located "%s"' % target
            return px, py, None, None
        if want and (not hit or not names_match(hit['name'], label)) and len(want) <= 60 and not AREA_WORDS.search(want):
            # Not in the control list (e.g. some pop-up menus): zoom in and locate the label on screen.
            box = self._locate(px, py, re.sub(r'^\s*(step\s*)?\d+\s*[.):-]?\s*', '', str(label), flags=re.I).strip())
            if box:
                x1, y1, x2, y2 = box
                return (x1 + x2) // 2, (y1 + y2) // 2, (x1, y1, x2 - x1, y2 - y1), 'located "%s" on screen' % label
        return px, py, None, ('on "%s"' % hit['name']) if hit else None

    def _locate(self, px, py, label, wide=False):
        """One small model request: find `label` in an enlarged crop around (px, py). Returns a pixel box or None."""
        import base64, io, re
        from PIL import Image
        try:
            screen = capture_image()
            sw, sh = screen_size()
            sx, sy = screen.width / sw, screen.height / sh
            half_w, half_h = (420, 260) if wide else (300, 170)    # pictures: the first estimate can be further off
            left, top = max(0, min(sw - 2 * half_w, px - half_w)), max(0, min(sh - 2 * half_h, py - half_h))
            crop = screen.crop((round(left * sx), round(top * sy), round((left + 2 * half_w) * sx), round((top + 2 * half_h) * sy)))
            crop = crop.resize((1200, round(1200 * half_h / half_w)), Image.LANCZOS)
            buffer = io.BytesIO(); crop.save(buffer, 'JPEG', quality=88)
            # Muse answers boxes on its native 0-1000 scale for both axes; asking for crop pixels gave
            # x in pixels but y on the 0-1000 scale (a row or two low). Reasoning tokens count against
            # max_completion_tokens (120 left no room for the answer), so keep reasoning minimal.
            what = ('the thing described as "%s": a user-interface element (its visible text may differ slightly), '
                    'text, or part of a picture, photo, diagram, map or chart' % label)
            prompt = ('This is an enlarged crop of a desktop screen. Find %s. Reply with JSON only: {"found": true, '
                      '"box": [x1, y1, x2, y2]} tightly covering it (a whole row for menu items), with coordinates normalised '
                      'from 0 to 1000 across this image (0,0 top-left, 1000,1000 bottom-right), or {"found": false}. If '
                      'several match, pick the one nearest the centre.' % what)
            reply = self.model_call({'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()}}]}],
                'max_completion_tokens': 800, 'reasoning_effort': 'minimal', 'temperature': 0})
            choice = reply['response']['choices'][0]
            text = choice['message'].get('content') or ''
            if isinstance(text, list): text = ' '.join(p.get('text', '') for p in text if isinstance(p, dict))
            found = re.search(r'\{.*\}', text, re.S)
            if not found:
                print('locate %r: no JSON (finish=%s): %r' % (label, choice.get('finish_reason'), text[:120]), flush=True)
                return None
            data = json.loads(found.group(0))
            if not data.get('found'):
                print('locate %r: not found' % label, flush=True)
                return None
            x1, y1, x2, y2 = [float(v) for v in data['box']]
            if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000) or (x2 - x1) * (y2 - y1) > 0.5 * 1000 * 1000:
                print('locate %r: unusable box %s' % (label, data['box']), flush=True)
                return None
            kx, ky = 2 * half_w / 1000, 2 * half_h / 1000
            box = round(left + x1 * kx), round(top + y1 * ky), round(left + x2 * kx), round(top + y2 * ky)
            print('locate %r near (%d, %d) -> %s' % (label, px, py, list(box)), flush=True)
            return box
        except Exception:
            traceback.print_exc()
            return None

    def _element_rect(self, ident):
        try:
            return self.elements.rect(str(ident))
        except ElementError as error:
            raise ToolError(str(error))

    def _describe(self, name, a):
        d = self.desktop
        if name == 'window_action':
            verb = {'focus': 'Switching to', 'close': 'Closing', 'minimize': 'Minimizing', 'maximize': 'Maximizing',
                    'restore': 'Restoring'}.get(a.get('action'), 'Arranging')
            return verb, d.title_of(a.get('window_id'))
        if name == 'arrange_windows':
            titles = [d.title_of(i) for i in a.get('window_ids') or []]
            if a.get('layout') == 'side_by_side': return 'Tiling', ' and '.join(titles)
            return 'Moving', ', '.join(titles)
        if name in ('open_path', 'read_file', 'write_file', 'list_folder'):
            return {'open_path': 'Opening', 'read_file': 'Reading', 'write_file': 'Writing',
                    'list_folder': 'Looking in'}[name], short_path(resolve_path(a.get('path') or '~'))
        if name == 'type_text':
            text = str(a.get('text', ''))
            return 'Typing', '“%s”' % (text[:40] + ('…' if len(text) > 40 else ''))
        if name == 'annotate':
            verb = {'circle': 'Circling', 'box': 'Boxing', 'underline': 'Underlining', 'highlight': 'Highlighting',
                    'arrow': 'Drawing an arrow'}.get(a.get('shape'), 'Drawing')
            return verb, str(a.get('label') or '')[:40]
        if name in ('sinux_settings', 'change_sinux_settings', 'open_sinux_settings'):
            return {'sinux_settings': 'Checking my settings', 'change_sinux_settings': 'Updating my settings',
                    'open_sinux_settings': 'Opening Sinux Settings'}[name], ''
        if name == 'ui_elements':
            return 'Reading controls', str(a.get('query') or '')[:40]
        if name == 'act_on_element':
            entry = self.elements.cache.get(str(a.get('element')))
            verb = {'press': 'Pressing', 'toggle': 'Toggling', 'open_menu': 'Opening menu', 'focus': 'Focusing',
                    'set_text': 'Filling in'}.get(a.get('action') or 'press', 'Using')
            return verb, ('“%s”' % entry[2]['name']) if entry else str(a.get('element'))
        if name == 'mark_text':
            return 'Marking', str(a.get('find') or '')[:40]
        if name == 'run_admin':
            return 'Waiting for your permission:', str(a.get('reason') or a.get('command') or '')[:60]
        if name in ('search_desktop', 'open_result', 'read_window_text'):
            entry = self.discover.results.get(str(a.get('id'))) if name == 'open_result' else None
            return {'search_desktop': 'Searching for', 'open_result': 'Opening',
                    'read_window_text': 'Reading the window'}[name], (a.get('query') or (entry or {}).get('name') or '')[:60]
        if name == 'use_tools':
            return 'Getting tools ready', ', '.join(a.get('groups') or [])
        if name == 'web_search':
            return 'Searching the web for', str(a.get('query') or '')[:60]
        if name == 'system_control':
            return {'volume': 'Setting the volume', 'brightness': 'Setting the brightness', 'appearance': 'Switching appearance',
                    'wifi': 'Wi-Fi', 'media': 'Media'}.get(a.get('setting'), 'System'), str(a.get('value') or '')[:30]
        if name == 'about_sinux':
            return 'Recalling how I work', ''
        if name in ('read_clipboard', 'copy_to_clipboard', 'notify', 'remind_me', 'job_status', 'run_in_terminal'):
            return {'read_clipboard': 'Reading the clipboard', 'copy_to_clipboard': 'Copying to the clipboard',
                    'notify': 'Notifying', 'remind_me': 'Setting a reminder', 'job_status': 'Checking job',
                    'run_in_terminal': 'Running in Konsole'}[name], str(a.get('command') or a.get('job') or '')[:60]
        label = {'point_at': 'Pointing at', 'clear_annotations': 'Clearing marks', 'look_at_screen': 'Looking at the screen', 'list_windows': 'Checking windows',
                 'open_application': 'Opening', 'find_files': 'Searching for', 'click': 'Clicking',
                 'press_keys': 'Pressing', 'scroll': 'Scrolling', 'run_command': 'Running',
                 'list_applications': 'Checking installed apps'}.get(name, name)
        detail = a.get('name') or a.get('query') or a.get('label') or a.get('keys') or a.get('command') or ''
        return label, str(detail)[:60]

    def _dispatch(self, name, a):
        d = self.desktop
        if name == 'use_tools':
            wanted = [g for g in a.get('groups') or [] if g in TOOL_GROUPS]
            self.groups.update(wanted); self.used_groups.update(wanted)
            return {'loaded': wanted, 'tools': sorted({t for g in wanted for t in TOOL_GROUPS[g]} - self.disabled)}
        if name in GROUP_OF:
            self.groups.add(GROUP_OF[name]); self.used_groups.add(GROUP_OF[name])
        if name in self.disabled:
            raise ToolError('The user turned this off in Sinux Settings. Explain or point instead.')
        if name == 'point_at':
            if a.get('element'):
                ex, ey, ew, eh = self._element_rect(a['element'])
                px, py = ex + ew // 2, ey + eh // 2
                note = None
            else:
                if a.get('x') is None or a.get('y') is None: raise ToolError('Give an element id or x and y.')
                px, py = to_pixels(a['x'], a['y'])
                note = self._precheck(px, py, a.get('label'))
            label = str(a.get('label', ''))[:60]
            aimed = self._prefetch(lambda: self._aim(px, py, a.get('label'), refuse=False, target=a.get('target'))[:2],
                                   (px, py), a.get('element'))
            def show():
                x, y = aimed()
                self.events('point', {'x': x, 'y': y, 'label': label})
            self._queue(show, a.get('say'))
            return {'pointing': True, **({'landed': note} if note else {})}
        if name == 'annotate':
            shape = a.get('shape') if a.get('shape') in ('circle', 'box', 'underline', 'highlight', 'arrow') else 'circle'
            if a.get('element'):
                ex, ey, ew, eh = self._element_rect(a['element'])
                if shape == 'arrow':
                    if a.get('x1') is not None and a.get('y1') is not None: x1, y1 = to_pixels(a['x1'], a['y1'])
                    else:
                        sw, sh = screen_size()
                        x1, y1 = min(sw - 40, ex + ew + 160), min(sh - 60, ey + eh + 120)
                    x2, y2 = ex + ew // 2, ey + eh // 2
                else:
                    x1, y1, x2, y2 = ex, ey, ex + ew, ey + eh
                note = None
            else:
                if any(a.get(k) is None for k in ('x1', 'y1', 'x2', 'y2')): raise ToolError('Give an element id or x1,y1,x2,y2.')
                x1, y1 = to_pixels(a['x1'], a['y1'])
                x2, y2 = to_pixels(a['x2'], a['y2'])
                target = (x2, y2) if shape == 'arrow' else ((x1 + x2) // 2, (y1 + y2) // 2)
                note = self._precheck(*target, a.get('label'))
            label = str(a.get('label') or '')[:40]
            def place(box=(x1, y1, x2, y2)):
                x1, y1, x2, y2 = box
                if shape == 'arrow':
                    x2, y2, _, _ = self._aim(x2, y2, a.get('label'), refuse=False, target=a.get('target'))
                else:
                    _, _, rect, _ = self._aim((x1 + x2) // 2, (y1 + y2) // 2, a.get('label'), refuse=False,
                                              target=a.get('target'))
                    if rect: x1, y1, x2, y2 = rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]
                return x1, y1, x2, y2
            aimed = self._prefetch(place, (x1, y1, x2, y2), a.get('element'))
            def show():
                x1, y1, x2, y2 = aimed()
                self.events('mark', {'shape': shape, 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'label': label})
            self._queue(show, a.get('say'))
            return {'drawn': shape, **({'landed': note} if note else {})}
        if name == 'ui_elements':
            windows = d.windows()
            if a.get('window_id'):
                window = next((w for w in windows if w['id'].startswith(str(a['window_id']).strip('{}').lower()[:8])), None)
            else:
                window = next((w for w in windows if w['active']), windows[0] if windows else None)
            if not window: raise ToolError('No such window. Call list_windows.')
            try:
                found = self.elements.snapshot(window, a.get('query'), popups=d.popups(window['pid']))
            except ElementError as error:
                raise ToolError(str(error))
            compact_list = []
            for e in found:
                item = {'id': e['id'], 'role': e['role'], 'name': e['name']}
                if e['rect']:
                    x, y, w_, h_ = e['rect']
                    item['box'] = list(to_model(x, y)) + list(to_model(x + w_, y + h_))
                if e['flags']: item['state'] = e['flags']
                if e['inMenu']: item['inOpenMenu'] = True
                compact_list.append(item)
            return {'window': window['title'], 'count': len(compact_list), 'elements': compact_list}
        if name == 'act_on_element':
            try:
                result = self.elements.act(str(a['element']), a.get('action') or 'press', a.get('text'),
                                           click=(lambda x, y: self.input.click(x, y)) if self.input else None)
            except ElementError as error:
                raise ToolError(str(error))
            time.sleep(0.35)
            return dict(result, note='Call ui_elements again to see the new state (ids change).')
        if name == 'clear_annotations':
            self._queue(lambda: self.events('clear_marks', {}))
            return {'cleared': True}
        if name == 'look_at_screen':
            return {'ok': True, 'note': 'A fresh screenshot follows.'}
        if name == 'list_windows':
            return {'windows': [compact(w) for w in d.windows()]}
        if name == 'window_action':
            d.window_action(a['window_id'], a['action'])
            time.sleep(.3)
            return {'done': a['action'], 'windows': [compact(w) for w in d.windows()]}
        if name == 'arrange_windows':
            ids, layout = list(a.get('window_ids') or []), a['layout']
            SCREEN_W, SCREEN_H = screen_size()
            half = SCREEN_W // 2
            regions = {'left_half': [{'x': 0, 'y': 0, 'width': half, 'height': SCREEN_H}],
                       'right_half': [{'x': half, 'y': 0, 'width': half, 'height': SCREEN_H}],
                       'center': [{'x': SCREEN_W // 6, 'y': SCREEN_H // 10, 'width': SCREEN_W * 2 // 3, 'height': SCREEN_H * 4 // 5}],
                       'side_by_side': [{'x': 0, 'y': 0, 'width': half, 'height': SCREEN_H},
                                        {'x': half, 'y': 0, 'width': half, 'height': SCREEN_H}]}[layout]
            if len(ids) < len(regions): raise ToolError('Give %d window id(s) for %s.' % (len(regions), layout))
            for window_id, region in zip(ids, regions):
                d.window_action(window_id, 'move', region)
            time.sleep(.3)
            return {'arranged': layout, 'windows': [compact(w) for w in d.windows()]}
        if name == 'open_application':
            return compact_result(d.open_application(a['name']))
        if name == 'list_applications':
            d._apps = None   # rescan: newly installed apps appear immediately
            return {'applications': sorted({x['name'] for x in d.applications().values()})}
        if name == 'sinux_settings':
            if not self.settings: raise ToolError('Settings are unavailable.')
            return self.settings.get()
        if name == 'change_sinux_settings':
            if not self.settings: raise ToolError('Settings are unavailable.')
            allowed = {'talkKey', 'speak', 'voiceSpeed', 'languages', 'model', 'reducedMotion'}
            patch = {k: v for k, v in a.items() if k in allowed}
            if not patch: raise ToolError('Nothing to change. Permissions can only be changed by the user in Settings.')
            return self.settings.change(patch)
        if name == 'open_sinux_settings':
            if not self.settings: raise ToolError('Settings are unavailable.')
            self.settings.open(a.get('page') or 'connection')
            return {'opened': 'Sinux Settings', 'page': a.get('page') or 'connection'}
        if name == 'open_path':
            return compact_result(d.open_path(a['path']))
        if name == 'find_files':
            found = find_files(a['query'])
            return {'results': found, 'count': len(found)}
        if name == 'list_folder':
            return {'items': list_folder(a.get('path') or '~')}
        if name == 'read_file':
            return read_file(a['path'])
        if name == 'write_file':
            return write_file(a['path'], a['content'])
        if name == 'click':
            px, py, _, note = self._aim(*to_pixels(a['x'], a['y']), a.get('target'))
            self.events('point', {'x': px, 'y': py, 'label': '', 'click': True})
            time.sleep(.35)
            button = {'left': 1, 'middle': 2, 'right': 3}.get(a.get('button') or 'left', 1)
            self.input.click(px, py, button, 2 if a.get('double') else 1)
            return {'clicked': list(to_model(px, py)), **({'landed': note} if note else {})}
        if name == 'type_text':
            self.events('yield_keyboard', {}); time.sleep(.35)
            d.ensure_app_focus()
            skipped = (self.input.type_text(str(a['text']), paste=self._paste) if NATIVE
                       else self.input.type_text(str(a['text'])))
            return {'typed': len(a['text']) - skipped, 'skippedCharacters': skipped}
        if name == 'press_keys':
            self.events('yield_keyboard', {}); time.sleep(.35)
            d.ensure_app_focus()
            self.input.press_keys(str(a['keys']))
            return {'pressed': a['keys']}
        if name == 'scroll':
            px, py = to_pixels(a['x'], a['y'])
            self.input.scroll(px, py, a['amount'])
            return {'scrolled': a['amount']}
        if name == 'run_command':
            if a.get('background'):
                return start_job(str(a['command']), on_done=self._job_done)
            return run_command(str(a['command']), a.get('timeout') or 20)
        if name == 'run_in_terminal':
            return run_in_terminal(d, str(a['command']))
        if name == 'job_status':
            return job_status(str(a['job']))
        if name == 'read_clipboard':
            return clipboard_read()
        if name == 'copy_to_clipboard':
            return clipboard_write(str(a['text']))
        if name == 'notify':
            return notify(a['title'], a['body'])
        if name == 'remind_me':
            minutes = max(0.05, min(24 * 60, float(a['minutes'])))
            message = str(a['message'])[:300]
            timer = threading.Timer(minutes * 60, self._remind, args=(message,))
            timer.daemon = True; timer.start()
            return {'scheduled': True, 'inMinutes': minutes, 'message': message}
        if name == 'web_search':
            reply = cloud_call('web_search', query=str(a['query'])[:500], timeout=120)
            return {k: reply.get(k) for k in ('answer', 'sources', 'results', 'searched')}
        if name == 'system_control':
            import system
            return system.control(a.get('setting'), a.get('value', ''), str(a.get('network') or ''), str(a.get('password') or ''))
        if name == 'mark_text':
            windows = d.windows()
            wanted = str(a.get('window_id') or '').strip('{}').lower()[:8]
            window = next((w for w in windows if wanted and w['id'].startswith(wanted)), None) or \
                     next((w for w in windows if w['active']), None)
            if not window: raise ToolError('No window is open.')
            match = a.get('match') if a.get('match') in ('contains', 'word', 'starts', 'phrase') else 'contains'
            shape = a.get('shape') if a.get('shape') in ('underline', 'highlight', 'circle', 'box') else 'underline'
            try: found = self.elements.text_boxes(window, a['find'], match)
            except ElementError as error: raise ToolError(str(error))
            if not found:
                raise ToolError('No visible text in "%s" matches %r (or the app does not expose its text). Use annotate with '
                                'coordinates from the screenshot instead.' % (window['title'], a['find']))
            found = [h for h in found if h['box']] + [h for h in found if not h['box']][:8]
            for index, hit in enumerate(found):
                if hit['box']:
                    x, y, w, h = hit['box']
                    aimed = lambda box=(x - 2, y, x + w + 2, y + h): box
                else:
                    # Position not reliable (terminals): find the word by looking, in parallel with the others.
                    ax, ay, aw, ah = hit['area']
                    aimed = self._prefetch(lambda word=hit['text'], cx=ax + aw // 2, cy=ay + ah // 2:
                                           self._locate(cx, cy, 'the word "%s" in the terminal text' % word, wide=True), None)
                def show(aimed=aimed):
                    box = aimed()
                    if box: self.events('mark', {'shape': shape, 'x1': box[0], 'y1': box[1], 'x2': box[2], 'y2': box[3], 'label': ''})
                self._queue(show, a.get('say') if index == 0 else '')
            return {'marked': len(found), 'in': window['title'], 'texts': [h['text'] for h in found][:30]}
        if name == 'run_admin':
            print('run_admin (%s): %s' % (str(a.get('reason') or '')[:100], str(a['command'])[:200]), flush=True)
            return run_admin(str(a['command']))
        if name == 'search_desktop':
            return self.discover.search(str(a['query'])[:120])
        if name == 'open_result':
            return self.discover.open(a['id'])
        if name == 'read_window_text':
            windows = d.windows()
            wanted = str(a.get('window_id') or '').strip('{}').lower()[:8]
            window = next((w for w in windows if wanted and w['id'].startswith(wanted)), None) or \
                     next((w for w in windows if w['active']), windows[0] if windows else None)
            if not window: raise ToolError('No window is open.')
            try: text = self.elements.window_text(window)
            except ElementError as error: raise ToolError(str(error))
            if not text:
                return {'window': window['title'], 'text': '', 'note': 'No readable text here; use look_at_screen.'}
            return {'window': window['title'], 'chars': len(text), 'text': text}
        if name == 'about_sinux':
            where = ('Right now: Debian 13 running from a live USB on this laptop; its own disk stays read-only and '
                     'Sinux updates arrive as signed packages on the stick.' if NATIVE else
                     'Right now: the development preview, an isolated container desktop shown in a browser.')
            return {'about': ABOUT_SINUX + '\n' + where}
        raise ToolError('Unknown tool %s.' % name)
