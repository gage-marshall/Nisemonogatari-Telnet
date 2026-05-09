import argparse
import gzip
import logging
import os
import select
import socket
import struct
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE, 'output')

EPISODES = [
    ('S02E01', 'Karen Bee,        Part 1'),
    ('S02E02', 'Karen Bee,        Part 2'),
    ('S02E03', 'Karen Bee,        Part 3'),
    ('S02E04', 'Karen Bee,        Part 4'),
    ('S02E05', 'Karen Bee,        Part 5'),
    ('S02E06', 'Karen Bee,        Part 6'),
    ('S02E07', 'Karen Bee,        Part 7'),
    ('S02E08', 'Karen Bee,        Part 8'),
    ('S02E09', 'Tsukihi Phoenix,  Part 1'),
    ('S02E10', 'Tsukihi Phoenix,  Part 2'),
    ('S02E11', 'Tsukihi Phoenix,  Part 3'),
]

KEY_MAP = {str(i + 1): EPISODES[i] for i in range(9)}
KEY_MAP['a'] = EPISODES[9]
KEY_MAP['b'] = EPISODES[10]

MAGIC        = b'ANSI'
FLAG_KEYFRAME = 0x01


def read_header(fh):
    raw = fh.read(16)
    if len(raw) < 16:
        raise ValueError('Truncated header')
    magic, fps_num, fps_den, total_frames = struct.unpack('>4sIII', raw)
    if magic != MAGIC:
        raise ValueError(f'Bad magic: {magic!r}')
    return fps_num, fps_den, total_frames


def read_frame(fh):
    hdr = fh.read(12)
    if len(hdr) < 12:
        return None
    frame_no, flags, dlen = struct.unpack('>III', hdr)
    data = fh.read(dlen)
    if len(data) < dlen:
        return None
    return frame_no, flags, data

IAC  = 255
WILL = 251
WONT = 252
DO   = 253
DONT = 254
SB   = 250
SE   = 240

OPT_ECHO = 1
OPT_SGA  = 3   # Suppress goahead
OPT_NAWS = 31  # Negotiate window size

NEGOTIATE = bytes([
    IAC, WILL, OPT_SGA,   # server: suppress go-ahead
    IAC, WILL, OPT_ECHO,  # server: will echo
    IAC, DO,   OPT_NAWS,  # server: request window size
])


def strip_iac(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b != IAC:
            out.append(b)
            i += 1
        else:
            i += 1
            if i >= len(data):
                break
            cmd = data[i]
            i += 1
            if cmd == IAC:
                out.append(0xFF)
            elif cmd in (WILL, WONT, DO, DONT):
                i += 1
            elif cmd == SB:
                while i < len(data) - 1:
                    if data[i] == IAC and data[i + 1] == SE:
                        i += 2
                        break
                    i += 1
    return bytes(out)


def parse_naws(data: bytes) -> tuple[int, int] | None:
    i = 0
    while i < len(data) - 8:
        if (data[i] == IAC and data[i+1] == SB and data[i+2] == OPT_NAWS):
            w = (data[i+3] << 8) | data[i+4]
            h = (data[i+5] << 8) | data[i+6]
            return w, h
        i += 1
    return None

_C_RESET  = b'\x1b[0m'
_C_TITLE  = b'\x1b[38;5;201m'   # magenta
_C_KEY    = b'\x1b[38;5;226m'   # yellow
_C_EP     = b'\x1b[38;5;255m'   # white
_C_DIM    = b'\x1b[38;5;244m'   # grey
_C_RULE   = b'\x1b[38;5;57m'    # purple

_BLOCK_FONT: dict[str, list[str]] = {
    'N': ['█   █', '██  █', '█ █ █', '█  ██', '█   █'],
    'I': [' ███ ', '  █  ', '  █  ', '  █  ', ' ███ '],
    'S': [' ████', '█    ', ' ███ ', '    █', '████ '],
    'E': ['█████', '█    ', '████ ', '█    ', '█████'],
    'M': ['█   █', '██ ██', '█ █ █', '█   █', '█   █'],
    'O': [' ███ ', '█   █', '█   █', '█   █', ' ███ '],
}

_WORD      = 'NISEMONO'
_ART_WIDTH = 5 * len(_WORD) + (len(_WORD) - 1)
_ART_PAD   = (80 - _ART_WIDTH) // 2

_BANNER_LINES: tuple[str, ...] = ('',) + tuple(
    ' ' * _ART_PAD + ' '.join(_BLOCK_FONT[c][r] for c in _WORD)
    for r in range(5)
) + ('', '')


def _block_art_banner(start_row: int = 2) -> bytes:
    buf = bytearray(_C_TITLE)
    for i, line in enumerate(_BANNER_LINES):
        buf.extend(f'\x1b[{start_row + i};1H'.encode())
        if line:
            buf.extend(line.encode('utf-8'))
    buf.extend(_C_RESET)
    return bytes(buf)


def build_menu() -> bytes:
    buf = bytearray()

    buf.extend(b'\x1b[2J\x1b[H\x1b[?7l\x1b[?25l')

    buf.extend(_block_art_banner(start_row=2))

    buf.extend(b'\x1b[10;1H')
    buf.extend(_C_RULE + ('─' * 80).encode('utf-8') + _C_RESET)

    buf.extend(b'\x1b[11;1H')
    buf.extend(_C_TITLE + 'N I S E M O N O G A T A R I'.center(80).encode() + _C_RESET)

    buf.extend(b'\x1b[12;1H')
    buf.extend(_C_RULE + ('─' * 80).encode('utf-8') + _C_RESET)

    for idx, (ep_id, title) in enumerate(EPISODES):
        key = str(idx + 1) if idx < 9 else ('A' if idx == 9 else 'B')
        row = 13 + idx
        buf.extend(f'\x1b[{row};1H'.encode())
        buf.extend(
            _C_KEY + f'  [{key}]'.encode() +
            _C_EP  + f'  {ep_id}  '.encode() +
            _C_DIM + f'\u2013  {title}'.encode('utf-8') +
            _C_RESET
        )

    buf.extend(b'\x1b[24;1H')
    buf.extend(_C_DIM + b'  Press [1-9 / A / B] to watch an episode.' + _C_RESET)

    buf.extend(b'\x1b[25;1H')
    buf.extend(_C_DIM + b'  [Q] Disconnect.' + _C_RESET)

    return bytes(buf)


_MENU_BYTES = build_menu()

_C_RED   = b'\x1b[38;5;196m'
_C_GREEN = b'\x1b[38;5;46m'

_ENC_BOX    = '┌──┐ │  │ └──┘ ─ │ █'
_ENC_COLORS = [
    (_C_TITLE,  b'MAGENTA'),
    (_C_KEY,    b'YELLOW '),
    (_C_EP,     b'WHITE  '),
    (_C_DIM,    b'GREY   '),
    (_C_GREEN,  b'GREEN  '),
]


def build_check_screen(w: int, h: int, naws_received: bool) -> bytes:
    buf = bytearray()
    buf.extend(b'\x1b[2J\x1b[H\x1b[?25l\x1b[?7l')

    rule = ('─' * 80).encode('utf-8')

    buf.extend(_C_RULE + b'  Encoding / terminal check' + _C_RESET + b'\r\n')
    buf.extend(_C_RULE + rule + _C_RESET + b'\r\n')

    buf.extend(
        _C_KEY + b'  TERMINAL CAPABILITY CHECK' + _C_RESET + b'\r\n\r\n'
        + b'  This system requires a modern terminal with support for:\r\n'
        + b'  * ANSI / VT100 Escape Sequences\r\n'
        + b'  * UTF-8 Character Encoding\r\n'
        + b'  * Minimum Size: 80x24 Columns/Rows\r\n\r\n'
        + _C_DIM + b'  Windows users: Use PuTTY or WSL. Microsoft Telnet is not supported.\r\n'
        + _C_RESET + b'\r\n'
    )
    buf.extend(_C_RULE + rule + _C_RESET + b'\r\n')

    buf.extend(_C_DIM + b'  Box chars : ' + _C_RESET)
    buf.extend(_ENC_BOX.encode('utf-8') + b'\r\n')

    buf.extend(_C_DIM + b'  Colours   : ' + _C_RESET)
    for colour, label in _ENC_COLORS:
        buf.extend(colour + b'[' + label + b'] ' + _C_RESET)
    buf.extend(b'\r\n')

    buf.extend(_C_RULE + rule + _C_RESET + b'\r\n')

    has_warning = False

    if not naws_received:
        has_warning = True
        buf.extend(
            _C_RED +
            b'  Warning: could not detect your terminal size.\r\n'
            b'           This server requires a minimum of 80x24 characters.\r\n'
            b'           Display may be corrupted if your terminal is too small.' +
            _C_RESET + b'\r\n'
        )
    elif w < 80 or h < 24:
        has_warning = True
        msg = f'  Warning: your terminal is {w}\xd7{h}. Minimum required is 80\xd724.'
        buf.extend(
            _C_RED + msg.encode('utf-8') + b'\r\n' +
            b'           Resize your terminal before continuing or display may be corrupted.' +
            _C_RESET + b'\r\n'
        )

    if not has_warning:
        buf.extend(_C_GREEN + b'  Terminal size looks good.' + _C_RESET + b'\r\n')

    buf.extend(b'\r\n')
    buf.extend(_C_DIM + b'  Press [ENTER] to continue...' + _C_RESET)

    return bytes(buf)


def _wait_for_enter(conn: socket.socket) -> bool:
    conn.settimeout(None)
    buf = b''
    while True:
        try:
            chunk = conn.recv(256)
        except OSError:
            return False
        if not chunk:
            return False
        buf += chunk
        clean = strip_iac(buf)
        if b'\r' in clean or b'\n' in clean:
            return True
        buf = b''


def play_episode(conn: socket.socket, ep_id: str) -> str:
    path = os.path.join(OUTPUT_DIR, f'{ep_id}.ansi.gz')
    if not os.path.exists(path):
        conn.sendall(f'\r\n  [Error: {ep_id}.ansi.gz not found]\r\n'.encode())
        time.sleep(2)
        return 'ended'

    try:
        conn.sendall(b'\x1b[2J\x1b[H\x1b[?25l\x1b[?7l')
    except OSError:
        return 'disconnected'

    with gzip.open(path, 'rb') as gz:
        fps_num, fps_den, _ = read_header(gz)
        frame_duration = fps_den / fps_num
        t_origin = None

        while True:
            frame = read_frame(gz)
            if frame is None:
                break
            frame_no, flags, data = frame

            if t_origin is None:
                t_origin = time.monotonic() - frame_no * frame_duration

            try:
                conn.sendall(data)
            except OSError:
                return 'disconnected'

            deadline = t_origin + (frame_no + 1) * frame_duration

            try:
                r, _, _ = select.select([conn], [], [], 0)
            except OSError:
                return 'disconnected'
            if r:
                try:
                    raw = conn.recv(256)
                except OSError:
                    return 'disconnected'
                if not raw:
                    return 'disconnected'
                if b'q' in strip_iac(raw).lower():
                    return 'quit'

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

    return 'ended'

def handle_client(conn: socket.socket, addr):
    log = logging.getLogger(f'client:{addr[0]}:{addr[1]}')
    log.info('connected')

    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.settimeout(30)

    try:
        conn.sendall(NEGOTIATE)

        time.sleep(0.15)
        conn.settimeout(0.1)
        init_data = b''
        try:
            while True:
                chunk = conn.recv(256)
                if not chunk:
                    break
                init_data += chunk
        except socket.timeout:
            pass

        size = parse_naws(init_data)
        naws_received = size is not None
        if naws_received:
            w, h = size
            log.info(f'terminal size: {w}×{h}')
        else:
            w, h = 0, 0
            log.info('terminal size: unknown (no NAWS)')

        conn.settimeout(None)

        try:
            conn.sendall(build_check_screen(w, h, naws_received))
        except OSError:
            return

        if not _wait_for_enter(conn):
            return

        while True:
            conn.sendall(_MENU_BYTES)

            key = _read_key(conn)
            if key is None:
                break

            key_lower = key.lower()

            if key_lower == 'q':
                conn.sendall(b'\r\n\x1b[0m  Goodbye.\r\n')
                break

            ep_info = KEY_MAP.get(key_lower)
            if ep_info is None:
                continue

            ep_id, title = ep_info
            log.info(f'playing {ep_id}')

            result = play_episode(conn, ep_id)
            log.info(f'{ep_id} ended: {result}')

            if result == 'disconnected':
                break

            if result == 'ended':
                try:
                    conn.sendall(
                        b'\x1b[2J\x1b[H\x1b[38;5;201m'
                        b'\r\n\r\n'
                        b'  Episode complete.  Returning to menu...\r\n'
                        b'\x1b[0m'
                    )
                    time.sleep(2)
                except OSError:
                    break

    except (OSError, ConnectionResetError, BrokenPipeError) as e:
        log.info(f'disconnected: {e}')
    except Exception as e:
        log.exception(f'unexpected error: {e}')
    finally:
        try:
            conn.sendall(b'\x1b[?25h\x1b[?7h\x1b[0m')
        except OSError:
            pass
        conn.close()
        log.info('session closed')


def _read_key(conn: socket.socket) -> str | None:

    buf = b''
    conn.settimeout(None)
    while True:
        try:
            chunk = conn.recv(256)
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
        clean = strip_iac(buf)
        for b in clean:
            ch = chr(b)
            if ch.isprintable() or ch in ('\r', '\n'):
                return ch if ch.strip() else None
        buf = b''

def run_server(host: str, port: int, web_port: int | None = 2323):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(name)s  %(message)s',
        datefmt='%H:%M:%S',
    )
    log = logging.getLogger('server')

    if web_port is not None:
        import web_server
        web_server.start(host, web_port)

    missing = [ep for ep, _ in EPISODES
               if not os.path.exists(os.path.join(OUTPUT_DIR, f'{ep}.ansi.gz'))]
    if missing:
        log.warning(f'Missing output files: {missing}')

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(10)
    log.info(f'Listening on {host}:{port}  ({len(EPISODES) - len(missing)}/{len(EPISODES)} episodes ready)')

    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        log.info('shutting down')
    finally:
        srv.close()


def main():
    parser = argparse.ArgumentParser(description='Nisemonogatari ANSI Telnet Server')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=23)
    parser.add_argument('--web-port', type=int, default=2323,
                        help='Port for the HTTP landing page (default: 2323, 0 to disable)')
    args = parser.parse_args()
    web_port = args.web_port if args.web_port else None
    run_server(args.host, args.port, web_port)


if __name__ == '__main__':
    main()
