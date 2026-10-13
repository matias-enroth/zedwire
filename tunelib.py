"""The tune library behind zxmusic.py: what is in the tunes folder, the
keyword each tune answers to, and the pages MUSIC shows on the ZX81.

The folder is scanned again on every request from the ZX81, so new,
renamed and deleted files show up without restarting anything (listing a
few hundred files takes milliseconds). What each file is - its title,
its songs, whether the AY can play it - is worked out once and cached by
size and modification time in ~/.cache/zedwire/library.json.

Keywords: every playable tune gets one, made from its file name (up to 8
letters and digits, so it is quick to type on the ZX81). music.conf is
optional; its entries add keywords of your own choosing and options
(scc=, ts=, drums=), which also apply when the tune is picked from the
browser. A song of a multi-song file is KEYWORD/N.
"""

import json, os, re, time

TUNE_EXTS = {'.ay', '.pt3', '.ym', '.vgm', '.vgz', '.psg'}
ZXTUNE_EXTS = {'.stc', '.stp', '.st1', '.st3', '.asc', '.as0', '.pt1', '.pt2', '.sqt', '.psc',
               '.ftc', '.gtr'}                     # Spectrum tracker formats zxtune123 converts
PAGE = 18                                           # rows on a ZX81 page (lines 2-19)
WIDTH = 32
KEYLEN = 8
MAX_KEYWORD = 27                                    # the most LOAD "MUSIC:..." can carry (see zxpand-serial.md)
CACHE_VERSION = 3
SETTLE = 60                                         # seconds: a file changed more recently may still be being copied
UNSUPPORTED = 'unsupported'                         # tunes the AY can never play are moved here, and ignored

# ZX81 character codes 0-63 (11 = ", 12 = pound)
ZX_CHARS = ' ??????????"£$:?()><=+-*/;,.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
ZX_SUBST = {'[': '(', ']': ')', '{': '(', '}': ')', '&': '+', '_': ' ', '!': '.', '#': '.',
            "'": '', '`': '', '|': '/', '\\': '/', '~': '-', '@': '.', '%': '.', '^': '.'}
PLAY = 0xFF                                          # the reply names a tune to play (a page starts with its row count, 0-18)


def zx_clean(text):
    """Text as the ZX81 will show it (upper case, substitutes), so that
    lengths and padding come out right."""
    return ''.join(ZX_SUBST.get(c, c) for c in text.upper())


def to_zx(text, inverse=False):
    out = bytearray()
    for ch in text.upper():
        ch = ZX_SUBST.get(ch, ch)
        for c in ch:
            code = 15 if c == '?' or c not in ZX_CHARS else ZX_CHARS.index(c)
            out.append(code | (0x80 if inverse else 0))
    return bytes(out)


def common_title(names):
    """'Bionic Commando - Stage 1', 'Bionic Commando - Stage 2' -> 'Bionic Commando'."""
    firsts = {n.split(' - ')[0].strip() for n in names if n}
    if len(firsts) == 1:
        return firsts.pop()
    p = os.path.commonprefix([n for n in names if n]).rstrip(' -(:')
    return p if len(p) >= 4 else ''


def describe(path, zxtune=None):
    """{'title', 'songs', 'play', 'why'} for a tune file, without playing it."""
    import zxmusic
    name = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1].lower()
    info = {'title': name, 'songs': [], 'play': True, 'why': ''}
    try:
        data = open(path, 'rb').read()
        if data[:8] == b'ZXAYEMUL':
            ay = zxmusic.AyFile(data)
            names = [ay.song(i)['name'] or f'Song {i + 1}' for i in range(ay.count)]
            why = {i: zxmusic.unplayable_song(data, ay, i) for i in range(ay.count)}
            hidden = [i for i, w in why.items() if w]
            shown = [nm for i, nm in enumerate(names) if i not in hidden]
            if len(hidden) == ay.count:
                kinds = sorted(set(why.values()))
                info.update(play=False, unsupported=True,
                            why='only ' + ' and '.join(kinds) + ' songs: nothing the AY can play')
            elif len(names) > 1:
                info['songs'] = names
                info['hidden'] = hidden                   # beeper and sampled songs of a file that also has real ones
                info['title'] = common_title(shown) or name
            else:
                info['title'] = names[0] if ay.song(0)['name'] else name
        elif data[:13] == b'ProTracker 3.' or data[:14] == b'Vortex Tracker':
            title = data[0x1E:0x3E].decode('latin-1').strip()
            author = data[0x42:0x62].decode('latin-1').strip()
            info['title'] = ' - '.join(x for x in (title, author) if x) or name
        elif data[:4] == b'Vgm ' or data[:2] == b'\x1f\x8b':
            _, title, _ = zxmusic.load_vgm(data)          # refuses VGMs without an AY
            info['title'] = title.split(' [')[0] or name
        elif data[:2] == b'YM' or (data[2:5] == b'-lh' and data[6:7] == b'-') or data[:4] == b'MIX1':
            _, title, _ = zxmusic.load_ym(data)           # refuses sampled (MIX1/YMT) files
            info['title'] = title or name
        elif data[:3] == b'PSG':
            pass
        elif ext in ZXTUNE_EXTS:
            if not (zxtune or zxmusic.shutil.which('zxtune123')):
                info.update(play=False, why='needs zxtune123')
        else:
            info.update(play=False, why='not a tune file')
    except zxmusic.Unsupported as e:                    # the AY can never play it
        info.update(play=False, unsupported=True, why=str(e)[:200])
    except Exception as e:                              # anything unreadable is just not playable
        info.update(play=False, why=str(e)[:200] or type(e).__name__)
    return info


def keyword_base(path):
    """A short keyword from a file name: 'Project AY_Spectrum_Games_BionicCommando.ay'
    -> BIONICCO, 'nq - synchronization (2015).pt3' -> SYNCHRON, 'VirtualEscape2.ym'
    -> VIRTUAL2."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = stem.rsplit('_', 1)[-1]                       # Project AY_Spectrum_Games_Name
    stem = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', stem)    # (2015), [Stage 1 ...]
    parts = [p for p in re.split(r'\s+-\s+', stem) if p.strip()]
    if len(parts) > 1 and len(parts[0].split()) <= 2 and not parts[0].strip()[:1].isdigit():
        parts = parts[1:]                                 # "Artist - Title"
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', ' '.join(parts)).replace("'", '')
    words = re.findall(r'[A-Za-z0-9]+', text)
    while len(words) > 1 and words[0].isdigit():         # track numbers
        words.pop(0)
    key = ''.join(words).upper()
    digits = re.search(r'\d+$', key)
    if digits and len(key) > KEYLEN and len(digits.group()) < KEYLEN:
        key = key[:KEYLEN - len(digits.group())] + digits.group()   # keep a trailing number
    return key[:KEYLEN] or 'TUNE'


def parse_entry(text):
    """'FILE[@SONG] [| opts]' -> (file, song index or None, opts)."""
    entry, _, opts = text.partition('|')
    entry = entry.strip()
    path, _, n = entry.rpartition('@')
    if not path or not n.isdigit():
        path, n = entry, ''
    return path, (int(n) - 1 if n else None), opts.strip()


class Library:
    def __init__(self, root, conf=None, zxtune=None, cache_dir=None, log=print):
        self.root = os.path.abspath(root)
        self.conf_path = conf
        self.zxtune = zxtune
        self.log = log
        self.cache_path = os.path.join(cache_dir, 'library.json') if cache_dir else None
        self.cache = {}
        if self.cache_path:
            try:
                c = json.load(open(self.cache_path))
                if c.get('version') == CACHE_VERSION:
                    self.cache = c['files']
            except (OSError, ValueError, KeyError):
                pass
        self.files = {}                 # rel path -> info, playable tunes only
        self.keys = {}                  # KEYWORD -> (abs path, song or None, opts)
        self.names = {}                 # (rel path, song or None) -> KEYWORD shown in the browser
        self.cwd, self.sel, self.open_file = '', 0, None
        self.signature = None
        self.scan()

    # ---- scanning

    def scan(self):
        """Look at the folder again; describe new or changed files."""
        found = []
        for dirpath, dirs, names in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith('.')
                       and not (dirpath == self.root and d == UNSUPPORTED)]
            for n in names:
                ext = os.path.splitext(n)[1].lower()
                if not n.startswith('.') and (ext in TUNE_EXTS or ext in ZXTUNE_EXTS):
                    found.append(os.path.join(dirpath, n))
        found.sort()
        found_set, conf_files = set(found), self._conf_files()
        new, files, cache_changed = 0, {}, False
        for p in found:
            try:
                st = os.stat(p)
            except OSError:
                continue
            stamp = [st.st_size, st.st_mtime_ns]
            c = self.cache.get(p)
            if not c or c['stamp'] != stamp:
                info = describe(p, self.zxtune)
                if info.get('unsupported') and p not in conf_files:       # a file music.conf names stays put
                    if time.time() - st.st_mtime < SETTLE:
                        continue                                          # maybe still being copied: look again later
                    if self._set_aside(p, info['why']):
                        self.cache.pop(p, None)
                        cache_changed = True
                        continue
                c = {'stamp': stamp, 'info': info}
                self.cache[p] = c
                cache_changed = True
                new += 1
            if c['info']['play']:
                files[os.path.relpath(p, self.root)] = c['info']
        gone = [p for p in self.cache if p.startswith(self.root + os.sep) and p not in found_set]
        for p in gone:
            del self.cache[p]
            cache_changed = True
        conf_stamp = None
        if self.conf_path and os.path.exists(self.conf_path):
            conf_stamp = os.stat(self.conf_path).st_mtime_ns
        signature = (tuple(sorted(files)), conf_stamp)
        if cache_changed and self.cache_path:
            try:
                os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
                with open(self.cache_path + '.tmp', 'w') as f:
                    json.dump({'version': CACHE_VERSION, 'files': self.cache}, f)
                os.replace(self.cache_path + '.tmp', self.cache_path)
            except OSError:
                pass
        if signature != self.signature:
            old = set(self.files)
            self.files = files
            self._make_keywords()
            if self.signature is not None:
                added, removed = len(set(files) - old), len(old - set(files))
                self.log(f'library: {len(files)} tunes ({added} new, {removed} gone)')
            self.signature = signature
            if self.cwd and not any(f.startswith(self.cwd + os.sep) for f in files):
                self.cwd, self.sel, self.open_file = '', 0, None
            if self.open_file and self.open_file not in files:
                self.open_file, self.sel = None, 0
        return new

    def _set_aside(self, path, why):
        """Move a tune the AY can never play into UNSUPPORTED/ in the tunes folder,
        where the library doesn't look. True if it moved."""
        rel = os.path.relpath(path, self.root)
        folder = os.path.join(self.root, UNSUPPORTED, os.path.dirname(rel))   # keeps its subfolder
        stem, ext = os.path.splitext(os.path.basename(path))
        dest, n = os.path.join(folder, stem + ext), 1
        try:
            os.makedirs(folder, exist_ok=True)
            while os.path.exists(dest):
                n += 1
                dest = os.path.join(folder, f'{stem} ({n}){ext}')
            os.rename(path, dest)
        except OSError as e:
            self.log(f'could not move {os.path.relpath(path, self.root)} to {UNSUPPORTED}/: {e.strerror}')
            return False
        self.log(f'moved {rel} to {os.path.relpath(dest, self.root)}: {why}')
        return True

    def _conf_files(self):
        """Absolute paths of the files music.conf names: never moved."""
        out = set()
        if self.conf_path and os.path.exists(self.conf_path):
            base = os.path.dirname(os.path.abspath(self.conf_path))
            try:
                lines = open(self.conf_path, encoding='utf-8', errors='replace').read().splitlines()
            except OSError:
                return out
            for raw in lines:
                key, sep, value = raw.strip().partition('=')
                if sep and not raw.lstrip().startswith('#'):
                    out.add(os.path.normpath(os.path.join(base, parse_entry(value)[0])))
        return out

    def _make_keywords(self):
        keys, names = {}, {}
        if self.conf_path and os.path.exists(self.conf_path):
            base = os.path.dirname(os.path.abspath(self.conf_path))
            for lineno, raw in enumerate(open(self.conf_path, encoding='utf-8', errors='replace'), 1):
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                key, sep, value = line.partition('=')
                key = key.strip().upper()
                if not sep or not key or not value.strip():
                    self.log(f'{self.conf_path}:{lineno}: skipping malformed line: {line}')
                    continue
                if len(key) > MAX_KEYWORD:          # can't be typed, and the browser would send it
                    self.log(f'{self.conf_path}:{lineno}: skipping {key}: {len(key)} characters, '
                             f'the ZX81 sends at most {MAX_KEYWORD}')
                    continue
                file, song, opts = parse_entry(value)
                path = os.path.normpath(os.path.join(base, file))
                keys[key] = (path, song, opts)
                if path.startswith(self.root + os.sep):
                    names.setdefault((os.path.relpath(path, self.root), song), key)
        taken = set(keys) | {'TEST'}
        for rel in sorted(self.files):
            if (rel, None) in names:                    # music.conf names the whole file
                continue
            base = keyword_base(rel)
            key, n = base, 1
            while key in taken:
                n += 1
                key = base[:KEYLEN - len(str(n))] + str(n)
            taken.add(key)
            keys[key] = (os.path.join(self.root, rel), None, '')
            names.setdefault((rel, None), key)
        self.keys, self.names = keys, names

    def options(self, path, song):
        """Options from music.conf for a file (and song), if it has an entry."""
        best = ''
        for p, s, opts in self.keys.values():
            if p == path and opts and (s == song or s is None):
                best = opts if s == song else best or opts
        return best

    def resolve(self, keyword):
        """KEYWORD or KEYWORD/N -> (path, song or None, opts), or None."""
        key, _, n = keyword.upper().partition('/')
        if key not in self.keys:
            return None
        path, song, opts = self.keys[key]
        if n:
            if not n.isdigit() or int(n) < 1:
                return None
            song = int(n) - 1
            opts = self.options(path, song) or opts
        elif not opts:
            opts = self.options(path, song)
        return path, song, opts

    def keyword_for(self, rel, song=None):
        if (rel, song) in self.names:
            return self.names[rel, song]
        key = self.names.get((rel, None))
        return f'{key}/{song + 1}' if song is not None else key

    # ---- the browser

    def _entries(self):
        if self.open_file:
            info = self.files[self.open_file]
            return [('song', i, name) for i, name in enumerate(info['songs']) if i not in info.get('hidden', ())]
        dirs, tunes = set(), []
        prefix = self.cwd + os.sep if self.cwd else ''
        for rel, info in self.files.items():
            if not rel.startswith(prefix):
                continue
            rest = rel[len(prefix):]
            if os.sep in rest:
                dirs.add(rest.split(os.sep)[0])
            else:
                tunes.append(rel)
        tunes.sort(key=lambda r: (re.sub(r'[^a-z0-9]', '', self.files[r]['title'].lower()), r))
        return ([('dir', d, d) for d in sorted(dirs, key=str.lower)] +
                [('tune', r, self.files[r]['title']) for r in tunes])

    def browse(self, cmd):
        """A browser command -> ('page', (header, rows, footer, selected row, pages))
        or ('play', keyword).

          ''   the page with the selection on it
          N    the next page (after the last: the first), selection at the top
          P    the previous page (before the first: the last), at the bottom
          0    back up a folder, or out of a song list
          T    the top folder
          k    entry k (1-18) of the current page: open it, or play it
        MUSIC moves the cursor within a page by itself and only asks for
        another page when it runs off the top or bottom."""
        self.scan()
        cmd = cmd.strip().upper()
        entries = self._entries()
        self.sel = min(getattr(self, 'sel', 0), max(0, len(entries) - 1))
        first = self.sel // PAGE * PAGE
        if cmd == 'N':
            self.sel = first + PAGE if first + PAGE < len(entries) else 0
        elif cmd == 'P':
            last_page = max(0, (len(entries) - 1) // PAGE * PAGE)
            start = first - PAGE if first >= PAGE else last_page
            self.sel = min(start + PAGE, len(entries)) - 1 if entries else 0
        elif cmd == '0':
            if self.open_file:
                came_from, self.open_file = self.open_file, None
            elif self.cwd:
                came_from, self.cwd = self.cwd, os.path.dirname(self.cwd)
            else:
                came_from = None
            entries = self._entries()
            self.sel = next((i for i, e in enumerate(entries)
                             if came_from and (e[1] == came_from or
                                               os.path.join(self.cwd, str(e[1])) == came_from)), 0)
        elif cmd == 'T':
            self.cwd, self.open_file, self.sel = '', None, 0
        elif cmd.isdigit() and 1 <= int(cmd) <= PAGE and first + int(cmd) - 1 < len(entries):
            self.sel = first + int(cmd) - 1
            kind, what, _ = entries[self.sel]
            if kind == 'dir':
                self.cwd, self.sel = os.path.join(self.cwd, what) if self.cwd else what, 0
            elif kind == 'song':
                return 'play', self.keyword_for(self.open_file, what)
            elif self.files[what]['songs']:
                self.open_file, self.sel = what, 0
            else:
                return 'play', self.keyword_for(what)
        return 'page', self.render(self._entries())

    def render(self, entries):
        pages = max(1, -(-len(entries) // PAGE))
        page = min(self.sel // PAGE, pages - 1)
        if self.open_file:
            where = self.files[self.open_file]['title']
        else:
            where = '/' + self.cwd.replace(os.sep, '/') if self.cwd else 'MUSIC BROWSER'
        count = f' {page + 1}/{pages}' if pages > 1 else ''
        header = (where[:WIDTH - len(count)].ljust(WIDTH - len(count)) + count).upper()
        rows = []
        for kind, what, label in entries[page * PAGE:(page + 1) * PAGE]:
            label = zx_clean(label)
            tag = ''
            if kind == 'dir':
                label += '/'
            elif kind == 'song':
                title = zx_clean(self.files[self.open_file]['title'])
                if label.startswith(title) and label != title:
                    label = label[len(title):].lstrip(' -:')
            elif self.files[what]['songs']:
                tag = f"({len(self.files[what]['songs']) - len(self.files[what].get('hidden', ()))})"
            room = WIDTH - len(tag) - (1 if tag else 0)
            rows.append(f'{label[:room]:<{room}} {tag}' if tag else label[:WIDTH])
        if not entries:
            rows.append(f'NO TUNES IN {os.path.basename(self.root)}'[:WIDTH])
        footer = ['6 7 MOVE  8 ENTER PLAY  5 0 BACK',
                  'Q QUIT']                      # MUSIC puts the sound chip on the right
        return header, rows, footer, (self.sel - page * PAGE) if entries else 0, pages


def page_bytes(header, rows, footer, selected, pages=1):
    """A page as MUSIC draws it: the number of rows, the selected row, the
    number of pages, then 32-character lines in ZX81 codes - header
    (inverse), rows, two footer lines (inverse)."""
    def line(text, inv=False):
        z = to_zx(text, inv)[:WIDTH]
        return z + bytes([0x80 if inv else 0]) * (WIDTH - len(z))
    return checksummed(bytes([len(rows), selected, min(pages, 255)]) + line(header, True)
                       + b''.join(line(r) for r in rows) + b''.join(line(f, True) for f in footer))


def play_bytes(keyword):
    return checksummed(bytes([PLAY]) + to_zx(keyword))


def checksummed(body):
    """A last byte that makes all of them add up to 0 (mod 256): MUSIC
    throws away a reply that doesn't, and asks again."""
    return body + bytes([-sum(body) & 0xFF])
