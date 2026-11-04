"""Host-side key-holding proxy for the Sinux agent (like a tiny local worker).

The network-disabled desktop container sends chat-completion bodies over a
private Unix socket. This process adds the model and API key, forwards the
request to Meta and returns the JSON response. The key never enters the
container. A per-session request budget guards against runaway loops.
"""
import hmac
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import threading
import time

HOST, PATH = 'api.meta.ai', '/v1/chat/completions'
ALLOWED = {'messages', 'tools', 'tool_choice', 'parallel_tool_calls', 'max_completion_tokens',
           'reasoning_effort', 'temperature', 'response_format'}
REQUEST_LIMIT = 12 * 1024 * 1024


def forward(key, body, timeout=90, path=PATH, limit=4 * 1024 * 1024):
    began = time.monotonic()
    connection = http.client.HTTPSConnection(HOST, timeout=timeout, context=ssl.create_default_context())
    try:
        connection.request('POST', path, json.dumps(body, ensure_ascii=False).encode(),
            {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
             'Accept': 'application/json', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        raw = response.read(limit)
        seconds = round(time.monotonic() - began, 2)
        if response.status != 200:
            try: message = json.loads(raw).get('error', {}).get('message', '')
            except (ValueError, AttributeError): message = ''
            return {'ok': False, 'status': response.status, 'error': str(message)[:500], 'seconds': seconds}
        return {'ok': True, 'response': json.loads(raw), 'seconds': seconds}
    except (OSError, ValueError, http.client.HTTPException) as error:
        return {'ok': False, 'status': 0, 'error': type(error).__name__, 'seconds': round(time.monotonic() - began, 2)}
    finally:
        connection.close()


def forward_stream(key, body, on_delta, timeout=90):
    """Like forward, but streams: reply text goes to on_delta as it arrives; returns the assembled reply.
    Falls back to an ordinary request if streaming fails before any text was delivered."""
    began, delivered = time.monotonic(), False
    connection = http.client.HTTPSConnection(HOST, timeout=timeout, context=ssl.create_default_context())
    try:
        connection.request('POST', PATH, json.dumps(dict(body, stream=True), ensure_ascii=False).encode(),
            {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
             'Accept': 'text/event-stream', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        if response.status != 200:
            raw = response.read(1024 * 1024)
            try: message = json.loads(raw).get('error', {}).get('message', '')
            except (ValueError, AttributeError): message = ''
            if response.status in (401, 403, 429):
                return {'ok': False, 'status': response.status, 'error': str(message)[:500],
                        'seconds': round(time.monotonic() - began, 2)}
            raise ValueError('stream refused: HTTP %s %s' % (response.status, message))
        content, calls, finish, usage = [], {}, None, {}
        for raw in response:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'): continue
            data = line[5:].strip()
            if data == '[DONE]': break
            chunk = json.loads(data)
            usage = chunk.get('usage') or usage
            for choice in chunk.get('choices') or []:
                delta = choice.get('delta') or {}
                if delta.get('content'):
                    content.append(delta['content']); on_delta(delta['content']); delivered = True
                for part in delta.get('tool_calls') or []:
                    slot = calls.setdefault(part.get('index', len(calls)),
                                            {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    slot['id'] = part.get('id') or slot['id']
                    function = part.get('function') or {}
                    slot['function']['name'] += function.get('name') or ''
                    slot['function']['arguments'] += function.get('arguments') or ''
                finish = choice.get('finish_reason') or finish
        message = {'role': 'assistant', 'content': ''.join(content)}
        if calls:
            message['tool_calls'] = [calls[i] for i in sorted(calls)]
        return {'ok': True, 'response': {'choices': [{'message': message, 'finish_reason': finish}], 'usage': usage},
                'seconds': round(time.monotonic() - began, 2), 'streamed': True}
    except (OSError, ValueError, http.client.HTTPException) as error:
        if delivered:
            return {'ok': False, 'status': 0, 'error': 'stream interrupted: %s' % type(error).__name__,
                    'seconds': round(time.monotonic() - began, 2)}
        print('bridge: streaming unavailable (%s); using a normal request' % error, flush=True)
        return forward(key, body, timeout=timeout)
    finally:
        connection.close()


HEDGE_AFTER = (4.0, 8.0)     # seconds: send the same request again if Meta has not answered yet
CACHE_KEY = 'sinux-desktop-agent'


def hedged(key, body, transport=None, delays=HEDGE_AFTER):
    """Meta's latency varies a lot (2.4 s and 11.6 s for the same request): when an answer is slow, send the
    same request again and use whichever answers first. Costs extra tokens, saves seconds (D72)."""
    import queue
    transport = transport or forward
    results, began, started, failed, waits = queue.Queue(), time.monotonic(), 0, 0, list(delays)
    def launch():
        nonlocal started
        started += 1
        threading.Thread(target=lambda: results.put(transport(key, body)), daemon=True).start()
    launch()
    while True:
        try:
            timeout = max(0.0, waits[0] - (time.monotonic() - began)) if waits else None
            reply = results.get(timeout=timeout)
        except queue.Empty:
            waits.pop(0); launch(); continue
        if reply.get('ok'):
            if started > 1: print('bridge: answered after %.1fs (%d parallel requests)' % (time.monotonic() - began, started), flush=True)
            reply['seconds'] = round(time.monotonic() - began, 2)
            return reply
        failed += 1
        if reply.get('status') in (400, 401, 403, 404, 413, 422):
            return reply                       # the same request would fail again
        if failed >= started:
            if not waits: return reply
            waits.pop(0); launch()             # all failed so far: retry right away


class ChatBridge:
    def __init__(self, directory, key, *, model=None, limit=200, transport=forward):
        from desktop.providers.meta import credentials
        self.settings = credentials.load_settings()
        if transport is scripted_transport and not key:
            key = 'offline-test-model'     # the scripted model needs no Meta key (same as native_bridge)
        self.directory, self.key = Path(directory), (key or None)
        self.model = model or self.settings['model']
        self.tokens = 0
        self.limit, self.used, self.transport = limit, 0, transport
        self.lock = threading.Lock()
        self.speaker = None
        self.voice_enabled = transport is forward or os.environ.get('SINUX_AGENT_VOICE_TEST') == '1'
        self.token = secrets.token_hex(32)
        self.stop = threading.Event()
        self.directory.mkdir(mode=0o700, exist_ok=False)
        (self.directory / 'capability').write_text(self.token)
        os.chmod(self.directory / 'capability', 0o600)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.directory / 'bridge.sock'))
        os.chmod(self.directory / 'bridge.sock', 0o600)
        self.socket.listen(4)
        self.socket.settimeout(.1)
        self.thread = threading.Thread(target=self.serve, name='sinux-agent-bridge', daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.socket.close()
        if self.speaker: self.speaker.close()
        self.thread.join(timeout=5)
        for name in ('bridge.sock', 'capability'):
            (self.directory / name).unlink(missing_ok=True)
        try: self.directory.rmdir()
        except OSError: pass
        self.key = None

    def serve(self):
        while not self.stop.is_set():
            try: peer, _ = self.socket.accept()
            except socket.timeout: continue
            except OSError: return
            threading.Thread(target=self.handle, args=(peer,), daemon=True).start()

    def handle(self, peer):
        with peer:
            try:
                peer.settimeout(30)
                raw = bytearray()
                while not raw.endswith(b'\n'):
                    chunk = peer.recv(65536)
                    if not chunk or len(raw) + len(chunk) > REQUEST_LIMIT: return
                    raw.extend(chunk)
                value = json.loads(raw)
                if not hmac.compare_digest(str(value.get('token', '')), self.token): return
                if value.get('op') in CLOUD_OPS:
                    reply = self.cloud(value)
                    peer.settimeout(120)
                    return peer.sendall((json.dumps(reply) + '\n').encode())
                if value.get('op'):
                    return self.voice(peer, value)
                body = value.get('body')
                if not isinstance(body, dict) or not set(body) <= ALLOWED or 'messages' not in body:
                    reply = {'ok': False, 'status': 0, 'error': 'bridge_protocol'}
                else:
                    with self.lock:
                        allowed = self.used < self.limit and bool(self.key)
                        if allowed: self.used += 1
                    if not self.key:
                        reply = {'ok': False, 'status': 0, 'error': 'not_connected'}
                    elif not allowed:
                        reply = {'ok': False, 'status': 0, 'error': 'session_request_limit',
                                 'limit': self.limit}
                    else:
                        body = dict(body, model=self.model, stream=False, store=False, prompt_cache_key=CACHE_KEY)
                        if value.get('stream') and self.transport is forward and os.environ.get('SINUX_STREAM') == '1':
                            def delta(text):
                                peer.sendall((json.dumps({'delta': text}) + '\n').encode())
                            peer.settimeout(120)
                            reply = forward_stream(self.key, body, delta)
                        elif self.transport is forward:
                            reply = hedged(self.key, body)
                        else:
                            reply = self.transport(self.key, body)
                        usage = (reply.get('response') or {}).get('usage') or {}
                        self.tokens += int(usage.get('total_tokens') or 0)
                        reply['used'], reply['limit'] = self.used, self.limit
                peer.settimeout(120)
                peer.sendall((json.dumps(reply) + '\n').encode())
            except (OSError, ValueError, TypeError):
                return


def _voice(self, peer, value):
    """Voice operations: listen (streamed events), speak, stop_speaking, status."""
    from desktop.providers.meta.voice import Listening, Speaker
    op = value['op']
    if self.speaker is None: self.speaker = Speaker()
    def send(event):
        try: peer.sendall((json.dumps(event) + '\n').encode())
        except OSError: pass
    if op in CONTROL_OPS:
        return send(_control(self, op, value))
    if op == 'status':
        return send({'ok': True, 'listen': self.voice_enabled, 'speak': self.voice_enabled and self.speaker.available()})
    if op == 'subscribe':
        # Long-lived: push speaking/quiet events until the desktop disconnects.
        gone = threading.Event()
        def push(event):
            try: peer.sendall((json.dumps(event) + '\n').encode())
            except OSError: gone.set(); raise
        self.speaker.listeners.add(push)
        send({'type': 'subscribed'})
        peer.settimeout(1)
        try:
            while not gone.is_set() and not self.stop.is_set():
                try:
                    if not peer.recv(64): break
                except socket.timeout: continue
        except OSError: pass
        finally: self.speaker.listeners.discard(push)
        return
    if op == 'speak':
        if not self.voice_enabled or not self.settings.get('speak', True): return send({'ok': False})
        return send({'ok': self.speaker.speak(str(value.get('text', ''))[:2000], wait=bool(value.get('wait')),
                                              speed=float(self.settings.get('voiceSpeed', 1.0)))})
    if op == 'stop_speaking':
        self.speaker.stop(); return send({'ok': True})
    if op == 'cue':
        # "I heard you": a short soft sound the moment the talk key is released.
        if self.voice_enabled and self.settings.get('speak', True): self.speaker.cue(str(value.get('kind') or 'heard'))
        return send({'ok': True})
    if op != 'listen':
        return send({'ok': False, 'error': 'unknown voice operation'})
    if not self.voice_enabled:
        send({'type': 'error', 'text': 'Voice input needs the live Meta connection.'})
        return send({'type': 'done', 'text': ''})
    self.speaker.stop()
    finished = threading.Event()
    def emit(event):
        send(event)
        if event['type'] == 'done': finished.set()
    if not self.key:
        send({'type': 'error', 'text': "Sinux isn't connected yet. Open Settings to add your Meta API key."})
        return send({'type': 'done', 'text': ''})
    turn = Listening(self.key, emit, language_bias=self.settings.get('languages') or None).start()
    # The desktop sends one line ("stop") to end the turn, or disconnects to cancel it.
    peer.settimeout(.2)
    pending = b''
    while not finished.is_set() and not self.stop.is_set():
        try: chunk = peer.recv(256)
        except socket.timeout: continue
        except OSError: chunk = b''
        if not chunk or b'\n' in pending + chunk:
            turn.stop(); break
        pending += chunk
    finished.wait(timeout=30)


ChatBridge.voice = _voice
CLOUD_OPS = {'web_search'}
SEARCH_MODEL = 'muse-spark-1.3'


def _cloud(self, value):
    """Meta's search grounding, which Chat Completions lacks (Responses API).
    Each counts as one request against the session budget."""
    op = value['op']
    with self.lock:
        allowed = self.used < self.limit and bool(self.key)
        if allowed: self.used += 1
    if not self.key:
        return {'ok': False, 'error': 'not_connected'}
    if not allowed:
        return {'ok': False, 'error': 'session_request_limit', 'limit': self.limit}
    if op == 'web_search':
        query = str(value.get('query', '')).strip()[:500]
        if not query: return {'ok': False, 'error': 'empty query'}
        body = {'model': SEARCH_MODEL, 'store': False, 'tools': [{'type': 'web_search'}],
                'include': ['web_search_call.results'],
                'input': 'Search the web and answer concisely with the key facts and dates. Question: ' + query}
        if self.transport is not forward:
            return scripted_search(query)
        reply = forward(self.key, body, timeout=90, path='/v1/responses')
        return search_result(reply) if reply.get('ok') else reply
    return {'ok': False, 'error': 'unknown operation'}


def search_result(reply):
    """Responses API output -> answer text, cited sources and raw results."""
    answer, cited, results = [], [], []
    for item in reply['response'].get('output') or []:
        if item.get('type') == 'web_search_call':
            for r in ((item.get('action') or {}).get('sources') or item.get('results') or []):
                if isinstance(r, dict) and r.get('url'):
                    results.append({'title': r.get('title', ''), 'url': r['url'], 'snippet': str(r.get('snippet', ''))[:300]})
        if item.get('type') == 'message':
            for part in item.get('content') or []:
                if part.get('type') == 'output_text':
                    answer.append(part.get('text', ''))
                    for note in part.get('annotations') or []:
                        if note.get('type') == 'url_citation' and note.get('url') not in [c['url'] for c in cited]:
                            cited.append({'title': note.get('title', ''), 'url': note['url']})
    return {'ok': True, 'answer': '\n'.join(answer).strip(), 'sources': cited[:10], 'results': results[:8],
            'searched': bool(results) or bool(cited), 'seconds': reply['seconds']}


def scripted_search(query):
    return {'ok': True, 'answer': 'Scripted answer for: %s' % query, 'searched': True,
            'sources': [{'title': 'Example source', 'url': 'https://example.org/'}], 'results': []}


ChatBridge.cloud = _cloud
CONTROL_OPS = {'config_get', 'set_key', 'forget_key', 'settings_set'}


def _control(self, op, value):
    """Sinux Settings: connection, model, preferences. The key never leaves this process
    except to Meta, and is written to disk only when the user asks to remember it."""
    from desktop.providers.meta import credentials
    if op == 'set_key':
        key = credentials.clean_key(value.get('key', ''))
        if not key or len(key) > 4096 or any(not 33 <= ord(c) <= 126 for c in key):
            return {'ok': False, 'error': 'That does not look like an API key (it contains characters keys never have).'}
        check = (forward if self.transport is forward else self.transport)(key, {
            'model': self.model, 'stream': False, 'store': False, 'max_completion_tokens': 16,
            'messages': [{'role': 'user', 'content': 'Reply with OK.'}]})
        if not check.get('ok'):
            return {'ok': False, 'error': {401: 'Meta rejected this key.', 403: 'This key has no access to the model.',
                                           402: 'Billing is required for this key.'}.get(check.get('status'),
                                           ('Could not reach Meta (%s). If this Wi-Fi has a login page, open Firefox, sign in '
                                            'there, then try again.' % (check.get('error') or 'no connection')) if not check.get('status')
                                           else 'Meta answered HTTP %s: %s' % (check['status'], check.get('error') or 'no detail'))}
        self.key = key
        if value.get('remember'): credentials.save_key(key)
        else: credentials.forget_key()
        return dict(_config(self), ok=True)
    if op == 'forget_key':
        credentials.forget_key()
        if value.get('disconnect'): self.key = None
        return dict(_config(self), ok=True)
    if op == 'settings_set':
        self.settings = credentials.save_settings({**self.settings, **(value.get('settings') or {})})
        self.model = self.settings['model']
        if not self.settings.get('speak', True) and self.speaker: self.speaker.stop()
        return dict(_config(self), ok=True)
    return dict(_config(self), ok=True)


def _config(self):
    from desktop.providers.meta import credentials
    return {'connected': bool(self.key), 'remembered': credentials.load_key() is not None,
            'model': self.model, 'models': credentials.MODELS, 'used': self.used, 'limit': self.limit,
            'tokens': self.tokens, 'settings': self.settings,
            'voice': {'listen': self.voice_enabled, 'speak': self.voice_enabled and bool(self.speaker and self.speaker.available())}}


def scripted_transport(key, body):
    """Offline stand-in for development tests: keyword-driven tool calls, no network."""
    messages = body['messages']
    last = messages[-1]
    def text_of(message):
        content = message.get('content')
        if isinstance(content, list):
            return ' '.join(p.get('text', '') for p in content if p.get('type') == 'text')
        return content or ''
    def call(name, arguments):
        return {'ok': True, 'seconds': 0.01, 'response': {'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call_' + secrets.token_hex(4), 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}]}}
    def say(text):
        return {'ok': True, 'seconds': 0.01, 'response': {'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': text}}]}}
    users = [m for m in messages if m['role'] == 'user' and not text_of(m).startswith('Screen after')]
    request = text_of(users[-1]).split('\n\n[')[0].lower() if users else ''
    last = max(i for i, m in enumerate(messages) if m is users[-1]) if users else 0
    answered = [m for m in messages[last:] if m['role'] == 'tool'] if users else []
    if answered:
        return say('(scripted) Finished: ' + answered[-1]['content'][:4000])
    if request.startswith('open '):
        return call('open_application', {'name': request[5:].strip()})
    if request.startswith('labels'):
        def n(x, y): return round(x / 1440 * 1000), round(y / 900 * 1000)
        marks = [('circle', (255, 225), (330, 258), 'Back / forward'), ('box', (455, 222), (850, 256), 'Location bar'),
                 ('box', (858, 222), (952, 256), 'Split search menu'), ('box', (250, 270), (452, 505), 'Places sidebar'),
                 ('box', (458, 262), (958, 600), 'Main view')]
        calls = []
        for shape, a, b, label in marks:
            (x1, y1), (x2, y2) = n(*a), n(*b)
            calls.append({'id': 'call_' + secrets.token_hex(4), 'type': 'function', 'function': {'name': 'annotate',
                          'arguments': json.dumps({'shape': shape, 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'label': label})}})
        return {'ok': True, 'seconds': 0.01, 'response': {'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'content': '', 'tool_calls': calls}}]}}
    if request.startswith('els'):
        return call('ui_elements', {'query': request[3:].strip()} if request[3:].strip() else {})
    if request.startswith('act '):
        parts = request.split()
        return call('act_on_element', {'element': parts[1], 'action': parts[2] if len(parts) > 2 else 'press'})
    if request.startswith('mark '):
        return call('annotate', {'shape': 'box', 'element': request.split()[1], 'label': 'Exactly here'})
    if request.startswith('ptr '):
        return call('point_at', {'element': request.split()[1], 'label': 'This one'})
    if request.startswith('openurl'):
        return call('open_path', {'path': 'https://duckduckgo.com/?q=Meta+Muse+Spark'})
    if request.startswith('readwin'):
        return call('read_window_text', {})
    if request.startswith('markwords'):
        return call('mark_text', {'find': 's', 'match': 'contains', 'shape': 'underline', 'say': 'Every word with an s.'})
    if request.startswith('websearch'):
        return call('web_search', {'query': 'latest KDE Plasma release'})
    if request.startswith('volume'):
        return call('system_control', {'setting': 'volume', 'value': '30'})
    if request.startswith('darkmode'):
        return call('system_control', {'setting': 'appearance', 'value': 'dark'})
    if request.startswith('look'):
        return call('look_at_screen', {})
    if request.startswith('clip'):
        return call('read_clipboard', {})
    if request.startswith('remind'):
        return call('remind_me', {'minutes': 0.1, 'message': 'stretch your legs'})
    if request.startswith('bgjob'):
        return call('run_command', {'command': 'sleep 3; echo background done', 'background': True})
    if request.startswith('term'):
        return call('run_in_terminal', {'command': 'python3 -c "print(sum(range(101)))"'})
    if request.startswith('close '):
        return call('window_action', {'window_id': request[6:].strip(), 'action': 'close'})
    if request.startswith('circle'):
        return call('annotate', {'shape': 'circle', 'x1': 280, 'y1': 140, 'x2': 420, 'y2': 200, 'label': 'This area'})
    if request.startswith('arrow'):
        return call('annotate', {'shape': 'arrow', 'x1': 600, 'y1': 600, 'x2': 30, 'y2': 40, 'label': 'Apps live here'})
    if request.startswith('underline'):
        return call('annotate', {'shape': 'underline', 'x1': 300, 'y1': 300, 'x2': 600, 'y2': 330})
    if request.startswith('corner'):
        return call('point_at', {'x': 16, 'y': 30, 'label': 'App launcher'})
    if request.startswith('point'):
        return call('point_at', {'x': 500, 'y': 150, 'label': 'Right here'})
    if request.startswith('windows'):
        return call('list_windows', {})
    if request.startswith('find '):
        return call('find_files', {'query': request[5:].strip()})
    if request.startswith('type '):
        return call('type_text', {'text': request[5:]})
    if request.startswith('click'):
        return call('click', {'x': 500, 'y': 500})
    if request.startswith('side'):
        return call('run_command', {'command': 'echo side'})
    return say('(scripted) You said: ' + request[:200])
