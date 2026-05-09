import gzip
import http.server
import logging
import os
import re
import struct
import threading

log = logging.getLogger('web')

BASE       = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE, 'output')

MAGIC = b'ANSI'


def _read_header(fh):
    raw = fh.read(16)
    if len(raw) < 16:
        raise ValueError('Truncated header')
    magic, fps_num, fps_den, total_frames = struct.unpack('>4sIII', raw)
    if magic != MAGIC:
        raise ValueError(f'Bad magic: {magic!r}')
    return fps_num, fps_den, total_frames


def _read_frame(fh):
    hdr = fh.read(12)
    if len(hdr) < 12:
        return None
    frame_no, flags, dlen = struct.unpack('>III', hdr)
    data = fh.read(dlen)
    if len(data) < dlen:
        return None
    return frame_no, flags, data

def _xterm256(n: int) -> str:
    if n < 16:
        _basic = [
            '#000000', '#800000', '#008000', '#808000',
            '#000080', '#800080', '#008080', '#c0c0c0',
            '#808080', '#ff0000', '#00ff00', '#ffff00',
            '#0000ff', '#ff00ff', '#00ffff', '#ffffff',
        ]
        return _basic[n]
    if n < 232:
        n -= 16
        b = n % 6;  n //= 6
        g = n % 6;  r = n // 6
        def v(x): return 0 if x == 0 else 55 + x * 40
        return f'#{v(r):02x}{v(g):02x}{v(b):02x}'
    grey = 8 + (n - 232) * 10
    return f'#{grey:02x}{grey:02x}{grey:02x}'


def ansi_to_html(data: bytes, cols: int = 80, rows: int = 24) -> str:
    EMPTY_CELL = (' ', None, None, False)
    grid = [[EMPTY_CELL] * cols for _ in range(rows)]

    text = data.decode('utf-8', errors='replace')

    fg: str | None = None
    bg: str | None = None
    bold = False
    cur_row = 0
    cur_col = 0

    def put(ch: str):
        nonlocal cur_col, cur_row
        if 0 <= cur_row < rows and 0 <= cur_col < cols:
            grid[cur_row][cur_col] = (ch, fg, bg, bold)
        cur_col += 1
        if cur_col >= cols:
            cur_col = 0
            cur_row = min(cur_row + 1, rows - 1)

    i = 0
    while i < len(text):
        ch = text[i]

        if ch == '\x1b' and i + 1 < len(text) and text[i + 1] == '[':
            j = i + 2
            while j < len(text) and (text[j].isdigit() or text[j] in ';?'):
                j += 1
            if j >= len(text):
                break
            terminator = text[j]
            param_str = text[i + 2:j]
            i = j + 1

            if terminator == 'm':
                # SGR
                params = [int(x) if x else 0 for x in param_str.split(';')]
                k = 0
                while k < len(params):
                    p = params[k]
                    if p == 0:
                        fg = bg = None; bold = False
                    elif p == 1:
                        bold = True
                    elif p == 22:
                        bold = False
                    elif p == 38 and k + 2 < len(params) and params[k + 1] == 5:
                        fg = _xterm256(params[k + 2]); k += 2
                    elif p == 48 and k + 2 < len(params) and params[k + 1] == 5:
                        bg = _xterm256(params[k + 2]); k += 2
                    elif 30 <= p <= 37:
                        _4bit = ['#000','#c00','#0c0','#cc0','#00c','#c0c','#0cc','#ccc']
                        fg = _4bit[p - 30]
                    elif 40 <= p <= 47:
                        _4bit = ['#000','#c00','#0c0','#cc0','#00c','#c0c','#0cc','#ccc']
                        bg = _4bit[p - 40]
                    elif 90 <= p <= 97:
                        _hi = ['#555','#f55','#5f5','#ff5','#55f','#f5f','#5ff','#fff']
                        fg = _hi[p - 90]
                    elif 100 <= p <= 107:
                        _hi = ['#555','#f55','#5f5','#ff5','#55f','#f5f','#5ff','#fff']
                        bg = _hi[p - 100]
                    elif p == 39:
                        fg = None
                    elif p == 49:
                        bg = None
                    k += 1

            elif terminator in ('H', 'f'):
                parts = param_str.split(';')
                r = int(parts[0]) - 1 if parts[0] else 0
                c = int(parts[1]) - 1 if len(parts) > 1 and parts[1] else 0
                cur_row = max(0, min(r, rows - 1))
                cur_col = max(0, min(c, cols - 1))

            elif terminator == 'J':
                p = int(param_str) if param_str else 0
                if p == 2:
                    grid = [[EMPTY_CELL] * cols for _ in range(rows)]
                    cur_row = cur_col = 0

            elif terminator == 'K':
                p = int(param_str) if param_str else 0
                if p in (0, 2) and 0 <= cur_row < rows:
                    start = 0 if p == 2 else cur_col
                    for c in range(start, cols):
                        grid[cur_row][c] = EMPTY_CELL

        elif ch == '\x1b':
            i += 1
            if i < len(text) and not text[i].isdigit():
                i += 1

        elif ch == '\r':
            cur_col = 0
            i += 1

        elif ch == '\n':
            cur_row = min(cur_row + 1, rows - 1)
            i += 1

        else:
            put(ch)
            i += 1

    parts: list[str] = []
    for row in grid:
        prev_fg = prev_bg = None
        prev_bold = False
        span_open = False
        for (ch, rfg, rbg, rbold) in row:
            if rfg != prev_fg or rbg != prev_bg or rbold != prev_bold:
                if span_open:
                    parts.append('</span>')
                    span_open = False
                if rfg or rbg or rbold:
                    style = ''
                    if rfg:
                        style += f'color:{rfg};'
                    if rbg:
                        style += f'background:{rbg};'
                    if rbold:
                        style += 'font-weight:bold;'
                    esc_style = style.replace('"', '&quot;')
                    parts.append(f'<span style="{esc_style}">')
                    span_open = True
                prev_fg, prev_bg, prev_bold = rfg, rbg, rbold
            if ch == '&':
                parts.append('&amp;')
            elif ch == '<':
                parts.append('&lt;')
            elif ch == '>':
                parts.append('&gt;')
            else:
                parts.append(ch)
        if span_open:
            parts.append('</span>')
        parts.append('\n')

    return ''.join(parts)

FLAG_KEYFRAME = 0x01


def extract_preview_frames(path: str, count: int = 5) -> list[str]:
    if not os.path.exists(path):
        return []
    try:
        with gzip.open(path, 'rb') as gz:
            fps_num, fps_den, total_frames = _read_header(gz)

            keyframes: list[tuple[int, bytes]] = []
            while True:
                frame = _read_frame(gz)
                if frame is None:
                    break
                frame_no, flags, data = frame
                if flags & FLAG_KEYFRAME:
                    keyframes.append((frame_no, data))

        if not keyframes:
            return []

        if len(keyframes) <= count:
            chosen = keyframes
        else:
            step = (len(keyframes) - 1) / (count - 1)
            chosen = [keyframes[round(i * step)] for i in range(count)]

        return [ansi_to_html(data) for _, data in chosen]

    except Exception as exc:
        log.warning(f'extract_preview_frames({path}): {exc}')
        return []

_BLOCK_FONT: dict[str, list[str]] = {
    'N': ['█   █', '██  █', '█ █ █', '█  ██', '█   █'],
    'I': [' ███ ', '  █  ', '  █  ', '  █  ', ' ███ '],
    'S': [' ████', '█    ', ' ███ ', '    █', '████ '],
    'E': ['█████', '█    ', '████ ', '█    ', '█████'],
    'M': ['█   █', '██ ██', '█ █ █', '█   █', '█   █'],
    'O': [' ███ ', '█   █', '█   █', '█   █', ' ███ '],
}

_WORD = 'NISEMONO'

def _block_art_html() -> str:
    lines = []
    for row in range(5):
        lines.append(' '.join(_BLOCK_FONT[c][row] for c in _WORD))
    return '\n'.join(lines)

def _build_page(frames: list[str]) -> str:
    banner_html = _block_art_html()

    frames_js = 'const FRAMES = [\n'
    for f in frames:
        escaped = f.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
        frames_js += f'`{escaped}`,\n'
    frames_js += '];'

    has_frames = 'true' if frames else 'false'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NISEMONOGATARI // TELNET</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --phosphor:   #cc44ff;
    --phosphor2:  #ff44cc;
    --green:      #39ff14;
    --amber:      #ffb300;
    --dim:        #666;
    --bg:         #050508;
    --bg2:        #0a0a10;
    --border:     #2a0a3a;
    --rule:       #3d1560;
    --glow:       0 0 6px #cc44ff88, 0 0 16px #cc44ff33;
    --glow-green: 0 0 6px #39ff1488, 0 0 14px #39ff1433;
  }}

  html, body {{
    height: 100%;
    background: var(--bg);
    color: var(--phosphor);
    font-family: "Courier New", Courier, monospace;
    font-size: 14px;
    line-height: 1.55;
    overflow-x: hidden;
  }}

  body::before {{
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      to bottom,
      transparent 0px,
      transparent 2px,
      rgba(0,0,0,0.18) 2px,
      rgba(0,0,0,0.18) 4px
    );
    pointer-events: none;
    z-index: 9999;
  }}

  body::after {{
    content: '';
    position: fixed;
    inset: 0;
    background: radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.65) 100%);
    pointer-events: none;
    z-index: 9998;
  }}

  .page {{
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }}

  .banner {{
    text-align: center;
    margin-bottom: 0.25rem;
  }}
  .banner pre {{
    display: inline-block;
    color: var(--phosphor);
    text-shadow: var(--glow);
    font-size: clamp(7px, 1.1vw, 13px);
    line-height: 1.3;
    letter-spacing: 0.05em;
    white-space: pre;
  }}
  .subtitle {{
    text-align: center;
    color: var(--phosphor2);
    font-size: 0.85rem;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    margin-bottom: 0.15rem;
  }}

  .rule {{
    border: none;
    border-top: 1px solid var(--rule);
    margin: 1.25rem 0;
    box-shadow: 0 1px 0 0 #1a0830;
  }}

  .terminal-wrap {{
    display: table;
    margin: 1.5rem auto;
    border: 1px solid var(--border);
    border-radius: 3px;
    background: #000;
    box-shadow: 0 0 0 1px #0d0d1a, 0 4px 32px rgba(0,0,0,0.8), var(--glow);
    overflow: hidden;
  }}
  .terminal-titlebar {{
    background: #110820;
    padding: 4px 10px;
    font-size: 0.7rem;
    color: var(--dim);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 6px;
    letter-spacing: 0.1em;
  }}
  .terminal-titlebar .dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--border);
    display: inline-block;
  }}
  .terminal-titlebar .dot.r {{ background: #5a1a1a; }}
  .terminal-titlebar .dot.y {{ background: #4a3a00; }}
  .terminal-titlebar .dot.g {{ background: #0a3a0a; }}
  .terminal-screen {{
    padding: 6px 8px;
    overflow: hidden;
  }}
  .terminal-screen pre {{
    font-family: "Courier New", Courier, monospace;
    font-size: 13px;
    line-height: 1.0;
    white-space: pre;
    overflow: hidden;
    transition: opacity 0.15s ease;
  }}
  .terminal-screen pre.fade-out {{ opacity: 0; transition: opacity 0.08s ease; }}
  .frame-counter {{
    background: #110820;
    padding: 3px 10px;
    font-size: 0.65rem;
    color: var(--dim);
    border-top: 1px solid var(--border);
    text-align: right;
    letter-spacing: 0.08em;
  }}

  .no-preview {{
    margin: 1.5rem 0;
    border: 1px solid var(--border);
    padding: 2rem;
    text-align: center;
    color: var(--dim);
    font-size: 0.8rem;
    letter-spacing: 0.15em;
  }}

  .connect-box {{
    background: var(--bg2);
    border: 1px solid var(--rule);
    padding: 1.25rem 1.5rem;
    margin: 1.25rem 0;
  }}
  .connect-box .cmd {{
    display: block;
    color: var(--green);
    text-shadow: var(--glow-green);
    font-size: 1.15rem;
    margin: 0.5rem 0 0.2rem;
    letter-spacing: 0.05em;
  }}
  .connect-box .cmd::before {{ content: '$ '; color: var(--dim); }}

  h2 {{
    color: var(--amber);
    font-size: 0.8rem;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    margin-bottom: 0.6rem;
  }}
  h2::before {{ content: '// '; color: var(--dim); }}

  p, li {{
    color: #c8a0e0;
    line-height: 1.65;
  }}
  ul {{
    list-style: none;
    padding: 0;
  }}
  ul li::before {{ content: '  » '; color: var(--phosphor2); }}

  .key-table {{
    width: 100%;
    border-collapse: collapse;
    margin: 0.75rem 0;
    font-size: 0.85rem;
  }}
  .key-table th {{
    text-align: left;
    color: var(--amber);
    border-bottom: 1px solid var(--rule);
    padding: 4px 12px 4px 0;
    letter-spacing: 0.15em;
    font-size: 0.7rem;
    text-transform: uppercase;
  }}
  .key-table td {{
    padding: 3px 12px 3px 0;
    color: #c8a0e0;
    vertical-align: top;
  }}
  .key-table td:first-child {{
    color: var(--green);
    text-shadow: var(--glow-green);
    white-space: nowrap;
    min-width: 120px;
  }}

  kbd {{
    background: #1a0a2a;
    border: 1px solid var(--rule);
    border-radius: 2px;
    padding: 1px 5px;
    font-family: inherit;
    font-size: 0.85em;
    color: var(--phosphor);
  }}

  a {{ color: var(--phosphor2); text-underline-offset: 3px; }}
  a:hover {{ color: var(--phosphor); text-shadow: var(--glow); }}

  .dimtext {{ color: var(--dim); font-size: 0.8rem; }}
  .warn {{ color: var(--amber); }}

  footer {{
    text-align: center;
    color: var(--dim);
    font-size: 0.7rem;
    letter-spacing: 0.2em;
    margin-top: 3rem;
    text-transform: uppercase;
  }}
</style>
</head>
<body>
<div class="page">

  <!-- Banner -->
  <div class="banner">
    <pre>{banner_html}</pre>
  </div>
  <p class="subtitle">N I S E M O N O G A T A R I &nbsp;·&nbsp; A N S I &nbsp; T E L N E T</p>
  <hr class="rule">

  <!-- ANSI Preview -->
  <div id="preview-container">
  </div>

  <hr class="rule">

  <!-- Connect -->
  <section>
    <h2>Connect</h2>
    <div class="connect-box">
      <span class="dimtext">Open a terminal and run:</span>
      <code class="cmd">telnet nisemonogatari.com</code>
      <span class="dimtext">or, for terminals that prefer explicit port:</span>
      <code class="cmd">telnet nisemonogatari.com 23</code>
    </div>

    <table class="key-table">
      <thead><tr><th>Key</th><th>Action</th></tr></thead>
      <tbody>
        <tr><td><kbd>1</kbd> – <kbd>9</kbd>, <kbd>A</kbd>, <kbd>B</kbd></td><td>Select episode</td></tr>
        <tr><td><kbd>Q</kbd></td><td>Disconnect / return to menu during playback</td></tr>
      </tbody>
    </table>
  </section>

  <hr class="rule">

  <!-- What is this -->
  <section>
    <h2>What is this?</h2>
    <p>
      A Telnet server that streams <em>Nisemonogatari</em> episodes as real-time ANSI art
      directly to your terminal.
    </p>
    <br>
    <p>
      Each frame is pre-rendered as VT100/ANSI escape sequences and stored in a custom
      binary container. The server reads, decompresses, and streams them at the original
      framerate over a plain Telnet connection.
    </p>
    <br>
    <table class="key-table">
      <thead><tr><th>Arc</th><th>Episodes</th></tr></thead>
      <tbody>
        <tr><td>Karen Bee</td><td>S02E01 – S02E08</td></tr>
        <tr><td>Tsukihi Phoenix</td><td>S02E09 – S02E11</td></tr>
      </tbody>
    </table>
  </section>

  <hr class="rule">

  <!-- Terminal requirements -->
  <section>
    <h2>Terminal Requirements</h2>
    <ul>
      <li>ANSI / VT100 escape sequence support</li>
      <li>UTF-8 character encoding</li>
      <li>Minimum <strong>80 × 24</strong> columns / rows</li>
    </ul>
    <br>
    <p class="warn">
      ⚠ Windows users: use <a href="https://putty.org" target="_blank" rel="noopener">PuTTY</a>
      or WSL. The built-in Windows Telnet client is not supported.
    </p>
    <br>
    <p class="dimtext">
      The server negotiates terminal size via NAWS and shows a capability check screen
      before the episode menu. If your terminal is too small or lacks colour support,
      the check screen will warn you before playback begins.
    </p>
  </section>

  <hr class="rule">

  <footer>
    nisemonogatari &nbsp;·&nbsp; ansi telnet stream &nbsp;·&nbsp;
    <a href="https://github.com/gage-marshall/Nisemonogatari-Telnet" target="_blank" rel="noopener">source</a>
  </footer>

</div>

<script>
  {frames_js}
  const HAS_FRAMES = {has_frames};

  const container = document.getElementById('preview-container');

  if (!HAS_FRAMES || FRAMES.length === 0) {{
    container.innerHTML = '<div class="no-preview">[ ANSI PREVIEW UNAVAILABLE ]</div>';
  }} else {{
    container.innerHTML = `
      <div class="terminal-wrap">
        <div class="terminal-titlebar">
          <span class="dot r"></span><span class="dot y"></span><span class="dot g"></span>
          &nbsp;nisemonogatari.com &mdash; telnet 23
        </div>
        <div class="terminal-screen" id="tscreen">
          <pre id="tframe"></pre>
        </div>
        <div class="frame-counter" id="fcounter">frame 1 / ${{FRAMES.length}}</div>
      </div>`;

    const pre     = document.getElementById('tframe');
    const counter = document.getElementById('fcounter');
    let idx = 0;

    function nextFrame() {{
      pre.classList.add('fade-out');
      setTimeout(() => {{
        idx = (idx + 1) % FRAMES.length;
        pre.innerHTML = FRAMES[idx];
        counter.textContent = `frame ${{idx + 1}} / ${{FRAMES.length}}`;
        pre.classList.remove('fade-out');
      }}, 90);
    }}

    pre.innerHTML = FRAMES[0];
    setInterval(nextFrame, 3500);
  }}
</script>
</body>
</html>'''

_PAGE_CACHE: str | None = None
_PAGE_LOCK = threading.Lock()


def _get_page() -> bytes:
    global _PAGE_CACHE
    with _PAGE_LOCK:
        if _PAGE_CACHE is None:
            frames: list[str] = []
            for ep in ('S02E01', 'S02E02', 'S02E03'):
                p = os.path.join(OUTPUT_DIR, f'{ep}.ansi.gz')
                frames = extract_preview_frames(p, count=5)
                if frames:
                    log.info(f'Loaded {len(frames)} preview frames from {ep}.ansi.gz')
                    break
            if not frames:
                log.warning('No preview frames available; serving page without ANSI widget')
            _PAGE_CACHE = _build_page(frames)
    return _PAGE_CACHE.encode('utf-8')


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(f'{self.address_string()} {fmt % args}')

    def do_GET(self):
        if self.path not in ('/', '/index.html'):
            self.send_response(301)
            self.send_header('Location', '/')
            self.end_headers()
            return
        body = _get_page()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        body = _get_page()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()

def start(host: str = '0.0.0.0', port: int = 2323) -> threading.Thread:
    server = http.server.HTTPServer((host, port), _Handler)
    t = threading.Thread(target=server.serve_forever, name='http', daemon=True)
    t.start()
    log.info(f'HTTP server listening on {host}:{port}')
    return t
