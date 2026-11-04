"""Precise actions by name: read an application's controls through AT-SPI (the
Linux accessibility layer) and act on them directly — press, toggle, focus,
open a menu, set text — instead of guessing pixels from a screenshot.

Under Wayland, AT-SPI reports positions relative to the window's client area;
KWin supplies where that client area is on screen, which together give exact
screen rectangles for pointing and drawing.
"""
import threading

INTERACTIVE = {
    'push button', 'toggle button', 'check box', 'radio button', 'menu item', 'check menu item',
    'radio menu item', 'menu', 'page tab', 'combo box', 'text', 'entry', 'password text', 'link',
    'spin button', 'slider', 'list item', 'tree item', 'table cell', 'icon', 'button',
}
ROLE_ALIASES = {'button': 'push button'}
ACTION_PREFERENCE = {
    'press': ['press', 'click', 'activate', 'showmenu', 'toggle', 'jump'],
    'toggle': ['toggle', 'press', 'click'],
    'open_menu': ['showmenu', 'press', 'click'],
    'focus': ['setfocus'],
}
MAX_NODES = 5000
MAX_ELEMENTS = 160


class ElementError(Exception):
    pass


class Elements:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}          # id -> (accessible, screen rect or None, info)
        self.Atspi = None

    def _atspi(self):
        if self.Atspi is None:
            import gi
            gi.require_version('Atspi', '2.0')
            from gi.repository import Atspi
            Atspi.set_timeout(400, 1200)
            self.Atspi = Atspi
        return self.Atspi

    # ------------------------------------------------------------ reading
    def snapshot(self, window, query=None, popups=(), keep=True):
        """window: KWin info with pid, title and client rect (cx, cy, cw, ch) in pixels.
        popups: KWin info for the app's open popup menus, used to place menu items on screen."""
        Atspi = self._atspi()
        with self.lock:
            desktop = Atspi.get_desktop(0)
            apps = [desktop.get_child_at_index(i) for i in range(min(desktop.get_child_count(), 80))]
            app = next((a for a in apps if a is not None and a.get_process_id() == window['pid']), None)
            if app is None:
                raise ElementError('This application does not expose accessibility information.')
            tops = [app.get_child_at_index(i) for i in range(min(app.get_child_count(), 20))]
            tops = [t for t in tops if t is not None]
            if not tops:
                raise ElementError('No accessible windows for this application.')
            main = self._match_window(tops, window)
            elements, popups_found, cache = [], [], {}
            for top in tops:
                showing = self._has(top, 'SHOWING')
                if top is main:
                    self._walk(top, window, True, elements, cache, main)
                elif showing and top.get_role_name() in ('menu', 'window', 'popup menu', 'dialog', 'tool tip'):
                    popups_found.append(top)
            for popup in popups_found:
                geometry = self._match_popup(popup, popups)
                self._walk(popup, geometry, geometry is not None, elements, cache, main)
            if keep:
                self.cache = cache     # the ids the model will use
            if query:
                words = [w for w in query.lower().split() if w]
                elements = [e for e in elements if all(w in (e['name'] + ' ' + e['role']).lower() for w in words)]
            return elements[:MAX_ELEMENTS]

    def probe(self, window, popups=()):
        """Positioned controls of a window and its open menus, without disturbing the ids the model holds."""
        return [e for e in self.snapshot(window, popups=popups, keep=False) if e['rect']]

    # ------------------------------------------------------------ text ("this", page/document content)
    TEXT_ROLES = {'text', 'paragraph', 'document text', 'document web', 'document frame', 'terminal', 'heading',
                  'entry', 'section', 'list item', 'label', 'static', 'article', 'block quote', 'table cell'}

    def _app_tops(self, window):
        Atspi = self._atspi()
        desktop = Atspi.get_desktop(0)
        apps = [desktop.get_child_at_index(i) for i in range(min(desktop.get_child_count(), 80))]
        app = next((a for a in apps if a is not None and a.get_process_id() == window['pid']), None)
        if app is None:
            raise ElementError('This application does not expose accessibility information.')
        tops = [t for t in (app.get_child_at_index(i) for i in range(min(app.get_child_count(), 20))) if t is not None]
        return self._match_window(tops, window) if tops else None

    def _text_of(self, node, limit):
        Atspi = self.Atspi
        iface = node.get_text_iface()
        if iface is None: return '', ''
        count = Atspi.Text.get_character_count(iface)
        selected = ''
        for i in range(min(Atspi.Text.get_n_selections(iface), 3)):
            r = Atspi.Text.get_selection(iface, i)
            selected += Atspi.Text.get_text(iface, r.start_offset, r.end_offset)
        if count <= limit:
            text = Atspi.Text.get_text(iface, 0, count)
        else:   # around the caret
            caret = max(0, Atspi.Text.get_caret_offset(iface))
            start = max(0, min(count - limit, caret - limit // 2))
            text = Atspi.Text.get_text(iface, start, start + limit)
        return text.replace('\ufffc', ' ').strip(), selected.replace('\ufffc', ' ').strip()

    def focused(self, window, limit=3000):
        """The focused control of a window: its role, name, selected text and the text around the caret."""
        Atspi = self._atspi()
        with self.lock:
            top = self._app_tops(window)
            if top is None: return None
            stack, visited = [top], 0
            focused_text = selection = focused_any = None
            while stack and visited < MAX_NODES:
                node = stack.pop(); visited += 1
                try: states = node.get_state_set()
                except Exception: continue
                if not states.contains(Atspi.StateType.SHOWING): continue
                text_iface = node.get_text_iface()
                if states.contains(Atspi.StateType.FOCUSED):
                    # Several controls can report focus; a text control (document, field, terminal) wins.
                    if text_iface is not None: focused_text = node
                    else: focused_any = focused_any or node
                elif text_iface is not None and selection is None:
                    try:
                        if Atspi.Text.get_n_selections(text_iface) > 0: selection = node
                    except Exception:
                        pass
                try: count = min(node.get_child_count(), 300)
                except Exception: count = 0
                stack.extend(c for c in (node.get_child_at_index(i) for i in range(count - 1, -1, -1)) if c is not None)
            best = focused_text or selection or focused_any
            if best is None: return None
            text, selected = self._text_of(best, limit)
            return {'role': best.get_role_name(), 'name': (best.get_name() or '')[:80],
                    **({'selected': selected[:limit]} if selected else {}), **({'text': text} if text else {})}

    def text_boxes(self, window, find, match='contains', limit=60):
        """Screen boxes of visible words/phrases in a window's text, straight from the app (exact positions).
        match: contains (words containing `find`), word (whole word), starts (words starting with it), phrase."""
        import re
        Atspi = self._atspi()
        want = str(find or '').lower()
        if not want: return []
        out = []
        with self.lock:
            top = self._app_tops(window)
            if top is None: return []
            stack, visited = [top], 0
            while stack and visited < MAX_NODES * 2 and len(out) < limit:
                node = stack.pop(); visited += 1
                try: states, role = node.get_state_set(), node.get_role_name()
                except Exception: continue
                if not states.contains(Atspi.StateType.SHOWING): continue
                iface = node.get_text_iface() if role in self.TEXT_ROLES else None
                if iface is not None and (node.get_child_count() == 0 or role in ('terminal', 'document text', 'text')):
                    text = Atspi.Text.get_text(iface, 0, min(Atspi.Text.get_character_count(iface), 20000))
                    lowered = text.lower()
                    if match == 'phrase':
                        spans = [(m.start(), m.end()) for m in re.finditer(re.escape(want), lowered)]
                    else:
                        test = {'word': lambda w: w == want, 'starts': lambda w: w.startswith(want)}.get(match, lambda w: want in w)
                        spans = [(m.start(), m.end()) for m in re.finditer(r"[\w'’-]+", lowered) if test(m.group(0).strip("'’-"))]
                    try:
                        area = node.get_extents(Atspi.CoordType.WINDOW)
                        area = (area.x, area.y, area.width, area.height)
                    except Exception:
                        area = None
                    for start, end in spans:
                        if role == 'terminal' and area:
                            # Terminals report unreliable text positions (Konsole): the agent places these by looking.
                            out.append({'text': text[start:end], 'box': None,
                                        'area': (window['cx'] + area[0], window['cy'] + area[1], area[2], area[3])})
                        else:
                            box = self._checked(self._range_box(iface, start, end, window), area, window)
                            if box: out.append({'text': text[start:end], 'box': box})
                        if len(out) >= limit: break
                    if role in ('terminal', 'document text', 'text'): continue
                try: count = min(node.get_child_count(), 400)
                except Exception: count = 0
                stack.extend(c for c in (node.get_child_at_index(i) for i in range(count - 1, -1, -1)) if c is not None)
        out.sort(key=lambda e: (e['box'][1] // 8, e['box'][0]) if e['box'] else (0, 0))     # reading order
        return out

    @staticmethod
    def _checked(box, area, window):
        """Drop a reported text position that falls outside the text's own area (a wrong report, not a mark)."""
        if not box or not area or area[2] <= 0 or area[3] <= 0:
            return box
        x, y, w, h = box
        ax, ay, aw, ah = window['cx'] + area[0], window['cy'] + area[1], area[2], area[3]
        return box if ax - 4 <= x and x + w <= ax + aw + 4 and ay - 4 <= y and y + h <= ay + ah + 4 else None

    def _range_box(self, iface, start, end, window):
        """Screen rectangle of characters [start, end) or None when off-screen / unknown."""
        Atspi = self.Atspi
        try:
            r = Atspi.Text.get_range_extents(iface, start, end, Atspi.CoordType.WINDOW)
            x, y, w, h = r.x, r.y, r.width, r.height
        except Exception:
            w = h = 0
        if w <= 0 or h <= 0:   # some toolkits only answer per character
            try:
                a = Atspi.Text.get_character_extents(iface, start, Atspi.CoordType.WINDOW)
                b = Atspi.Text.get_character_extents(iface, end - 1, Atspi.CoordType.WINDOW)
                x, y = min(a.x, b.x), min(a.y, b.y)
                w, h = max(a.x + a.width, b.x + b.width) - x, max(a.y + a.height, b.y + b.height) - y
            except Exception:
                return None
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x >= window['cw'] or y >= window['ch']:
            return None
        return (window['cx'] + x, window['cy'] + y, w, h)

    def window_text(self, window, limit=8000):
        """The readable text of a window in reading order (pages, documents, terminals, labels)."""
        Atspi = self._atspi()
        with self.lock:
            top = self._app_tops(window)
            if top is None: return ''
            parts, stack, visited, total = [], [top], 0, 0
            while stack and visited < MAX_NODES * 2 and total < limit:
                node = stack.pop(); visited += 1
                try:
                    states, role = node.get_state_set(), node.get_role_name()
                except Exception:
                    continue
                if not states.contains(Atspi.StateType.SHOWING): continue
                if role in self.TEXT_ROLES and node.get_text_iface() is not None:
                    text, _ = self._text_of(node, limit - total)
                    # Containers repeat their children's text: keep leaves, or large single documents.
                    if text and (node.get_child_count() == 0 or role in ('terminal', 'document text')):
                        parts.append(text); total += len(text)
                        if role in ('terminal', 'document text'): continue
                try: count = min(node.get_child_count(), 400)
                except Exception: count = 0
                stack.extend(c for c in (node.get_child_at_index(i) for i in range(count - 1, -1, -1)) if c is not None)
            seen, out = set(), []
            for part in parts:
                if part not in seen: seen.add(part); out.append(part)
            return '\n'.join(out)[:limit]

    def _match_window(self, tops, window):
        title = (window.get('title') or '').lower()
        for top in tops:
            name = (top.get_name() or '').lower()
            if name and (name in title or title.startswith(name)):
                return top
        for top in tops:
            try:
                r = top.get_extents(self.Atspi.CoordType.WINDOW)
                if abs(r.width - window.get('cw', 0)) <= 4 and abs(r.height - window.get('ch', 0)) <= 4:
                    return top
            except Exception:
                pass
        return next((t for t in tops if self._has(t, 'SHOWING')), tops[0])

    def _match_popup(self, node, popups):
        """Pair an accessible popup with KWin's popup window of the same size."""
        try:
            r = node.get_extents(self.Atspi.CoordType.WINDOW)
        except Exception:
            return None
        for w in popups:
            if abs(r.width - w.get('cw', 0)) <= 6 and abs(r.height - w.get('ch', 0)) <= 6:
                return w
        return popups[0] if len(popups) == 1 else None

    def _has(self, node, state):
        try:
            return node.get_state_set().contains(getattr(self.Atspi.StateType, state))
        except Exception:
            return False

    def _walk(self, top, window, positioned, out, cache, main):
        Atspi = self.Atspi
        stack, visited = [top], 0
        while stack and visited < MAX_NODES and len(out) < MAX_ELEMENTS * 2:
            node = stack.pop(); visited += 1
            try:
                role = node.get_role_name()
                states = node.get_state_set()
            except Exception:
                continue
            if not states.contains(Atspi.StateType.SHOWING):
                continue
            role = ROLE_ALIASES.get(role, role)
            name = (node.get_name() or node.get_description() or '').strip()
            if role in INTERACTIVE and (name or role in ('text', 'entry', 'combo box')):
                rect = None
                if positioned:
                    try:
                        r = node.get_extents(Atspi.CoordType.WINDOW)
                        if r.width > 0 and r.height > 0 and r.x < window['cw'] and r.y < window['ch']:
                            rect = (window['cx'] + r.x, window['cy'] + r.y, r.width, r.height)
                    except Exception:
                        rect = None
                if rect or not positioned:
                    flags = [label for label, s in (('checked', 'CHECKED'), ('focused', 'FOCUSED'), ('selected', 'SELECTED'),
                                                    ('expanded', 'EXPANDED')) if states.contains(getattr(Atspi.StateType, s))]
                    if not states.contains(Atspi.StateType.ENABLED) and not states.contains(Atspi.StateType.SENSITIVE):
                        flags.append('disabled')
                    ident = 'e%d' % (len(cache) + 1)
                    info = {'id': ident, 'role': role, 'name': name[:80], 'flags': flags, 'inMenu': top is not main}
                    if role in ('text', 'entry'):
                        info['editable'] = states.contains(Atspi.StateType.EDITABLE)
                    cache[ident] = (node, rect, info)
                    out.append(dict(info, rect=rect))
            try:
                count = min(node.get_child_count(), 300)
            except Exception:
                count = 0
            for i in range(count - 1, -1, -1):
                try:
                    child = node.get_child_at_index(i)
                except Exception:
                    child = None
                if child is not None:
                    stack.append(child)

    def rect(self, ident):
        entry = self.cache.get(ident)
        if not entry:
            raise ElementError('Unknown element %s; call ui_elements again.' % ident)
        if not entry[1]:
            raise ElementError('%s has no on-screen position (it is inside a menu).' % ident)
        return entry[1]

    # ------------------------------------------------------------ acting
    def act(self, ident, action='press', text=None, click=None):
        Atspi = self._atspi()
        entry = self.cache.get(ident)
        if not entry:
            raise ElementError('Unknown element %s; call ui_elements again.' % ident)
        node, rect, info = entry
        if click and rect and (action == 'open_menu' or (action == 'press' and info['role'] == 'menu item' and not info['inMenu'])):
            # Menu bar items: a real click gives a proper drop-down (an accessibility ShowMenu
            # under Wayland opens the menu as a separate decorated window).
            x, y, w, h = rect
            click(x + w // 2, y + h // 2)
            return {'done': True, 'element': info['name'], 'role': info['role'], 'action': 'open_menu'}
        with self.lock:
            if action == 'set_text':
                editable = node.get_editable_text_iface()
                if editable is None:
                    raise ElementError('%s "%s" is not editable text.' % (info['role'], info['name']))
                ok = Atspi.EditableText.set_text_contents(editable, text or '')
                return {'done': bool(ok), 'element': info['name'], 'action': 'set_text'}
            if action == 'focus':
                component = node.get_component_iface()
                if component is not None and Atspi.Component.grab_focus(component):
                    return {'done': True, 'element': info['name'], 'action': 'focus'}
            iface = node.get_action_iface()
            if iface is None:
                raise ElementError('%s "%s" has no actions.' % (info['role'], info['name']))
            names = [(Atspi.Action.get_action_name(iface, i) or '').lower() for i in range(Atspi.Action.get_n_actions(iface))]
            for wanted in ACTION_PREFERENCE.get(action, [action.lower()]):
                if wanted in names:
                    ok = Atspi.Action.do_action(iface, names.index(wanted))
                    return {'done': bool(ok), 'element': info['name'], 'role': info['role'], 'action': wanted}
            raise ElementError('"%s" cannot %s; it offers: %s' % (info['name'], action, ', '.join(names) or 'nothing'))
