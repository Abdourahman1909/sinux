"""One search across the desktop, like KRunner: apps, their quick actions, System Settings pages,
files (by name, and by content through KDE's Baloo index when it is running) and open windows.

KRunner's own D-Bus interface only opens its window, so the same sources are read directly:
.desktop entries (apps, [Desktop Action] sections, kcm_*.desktop settings pages), baloosearch6
and the KWin window list. Results get short ids; open_result(id) opens one.
"""
import configparser
from pathlib import Path
import shlex
import shutil
import subprocess

from tools import APP_DIRS, HOME, ToolError, find_files

SETTINGS_COMMANDS = ('systemsettings', 'kcmshell6', 'kcmshell5')


def _entries():
    """(path, parser) for every .desktop file, user entries overriding system ones."""
    seen = {}
    for folder in APP_DIRS:
        for path in sorted(folder.glob('*.desktop')) if folder.is_dir() else []:
            if path.name in seen: continue
            parser = configparser.RawConfigParser(strict=False, interpolation=None)
            try: parser.read(path, encoding='utf-8')
            except (configparser.Error, UnicodeError): continue
            if parser.has_section('Desktop Entry'):
                seen[path.name] = (path, parser)
    return seen.values()


def _score(query, *fields):
    """Every query word must appear somewhere; earlier fields (the name) count more."""
    words = [w for w in query.lower().split() if len(w) > 1] or [query.lower()]
    text = [str(f or '').lower() for f in fields]
    matched = sum(any(w in t for t in text) for w in words)
    # All words, or all but one of a longer query ("keyboard shortcuts" finds "Shortcuts").
    if matched < max(1, len(words) - (1 if len(words) > 1 else 0)): return 0
    score = 0
    for weight, t in zip((10, 4, 3, 2, 1), text):
        score += weight * sum(w in t for w in words)
        if t == query.lower(): score += 20
    return score


class Discover:
    def __init__(self, desktop):
        self.desktop = desktop
        self.results = {}

    def search(self, query, limit=12):
        query = str(query or '').strip()
        if not query: raise ToolError('Say what to look for.')
        found = []
        for path, parser in _entries():
            entry = parser['Desktop Entry']
            if entry.get('Type') != 'Application' or not entry.get('Exec'): continue
            command = shlex.split(entry['Exec'])[0] if entry['Exec'].strip() else ''
            name = entry.get('Name', path.stem)
            keywords = ' '.join(filter(None, (entry.get('Keywords'), entry.get('X-KDE-Keywords'))))
            if Path(command).name in SETTINGS_COMMANDS:
                score = _score(query, name, keywords, entry.get('Comment'), entry.get('GenericName'), path.stem)
                if score: found.append((score + 2, {'kind': 'setting', 'name': name, 'detail': entry.get('Comment', ''),
                                                    'exec': entry['Exec']}))
                continue
            if entry.get('NoDisplay', 'false') == 'true': continue
            score = _score(query, name, entry.get('GenericName'), keywords, entry.get('Comment'), path.stem)
            if score: found.append((score, {'kind': 'app', 'name': name, 'detail': entry.get('GenericName') or entry.get('Comment', ''),
                                            'app': path.stem}))
            for action in (entry.get('Actions') or '').split(';'):
                section = 'Desktop Action %s' % action.strip()
                if action.strip() and parser.has_section(section) and parser[section].get('Exec'):
                    label = parser[section].get('Name', action)
                    score = _score(query, label, name)
                    if score: found.append((score - 1, {'kind': 'app action', 'name': '%s: %s' % (name, label),
                                                        'exec': parser[section]['Exec']}))
        try:
            for w in self.desktop.windows():
                score = _score(query, w['title'], w['app'])
                if score: found.append((score + 1, {'kind': 'window', 'name': w['title'], 'window': w['id']}))
        except ToolError:
            pass
        for item in self._files(query):
            found.append(item)
        found.sort(key=lambda pair: -pair[0])
        self.results = {}
        out = []
        for _, item in found[:limit]:
            ident = 'r%d' % (len(self.results) + 1)
            self.results[ident] = item
            out.append({'id': ident, 'kind': item['kind'], 'name': item['name'],
                        **({'detail': item['detail'][:80]} if item.get('detail') else {}),
                        **({'path': item['path']} if item.get('path') else {})})
        return {'query': query, 'results': out, 'note': 'Open one with open_result(id).' if out else 'Nothing found.'}

    def _files(self, query):
        """Baloo (content and names, when indexing runs) first, then a quick name search of the home folder."""
        paths = []
        if shutil.which('baloosearch6'):
            try:
                done = subprocess.run(['baloosearch6', '-l', '8', query], capture_output=True, text=True, timeout=4)
                paths = [p for p in done.stdout.splitlines() if p.startswith(str(HOME)) and Path(p).exists()]
            except (OSError, subprocess.TimeoutExpired):
                pass
        items = [(6, {'kind': 'file (content match)' if query.lower() not in Path(p).name.lower() else 'file',
                      'name': Path(p).name, 'path': p}) for p in paths]
        known = set(paths)
        for hit in find_files(query, limit=6):
            if hit['path'] not in known:
                items.append((5, {'kind': hit['type'], 'name': Path(hit['path']).name, 'path': hit['path']}))
        return items

    def open(self, ident):
        item = self.results.get(str(ident))
        if not item: raise ToolError('Unknown result %s; call search_desktop again.' % ident)
        if item['kind'] == 'app':
            return self.desktop.open_application(item['app'])
        if item['kind'] == 'window':
            return {'focused': item['name'], **self.desktop.window_action(item['window'], 'focus')}
        if item.get('path'):
            return self.desktop.open_path(item['path'])
        command = [p for p in shlex.split(item['exec']) if not (len(p) == 2 and p.startswith('%'))]
        if command and Path(command[0]).name in SETTINGS_COMMANDS and not shutil.which(command[0]):
            # Same page through whichever settings launcher this system has.
            alternative = next((c for c in SETTINGS_COMMANDS if shutil.which(c)), None)
            if not alternative: raise ToolError('System Settings is not installed here.')
            command = [alternative] + command[1:]
        before = {w['id'] for w in self.desktop.windows()}
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(HOME))
        window = self.desktop.wait_for_window(before, '')
        return {'opened': item['name'], 'verified': bool(window), **({'window': window} if window else {})}
