#!/usr/bin/env python3
"""
ydisk-web-backup — выгрузка всего Яндекс Диска на свой компьютер через веб-версию.

Работает, когда обычные способы не работают: приложение Яндекс Диска ставить
не хочется, REST API отвечает 410 Gone, WebDAV только для платного тарифа,
а веб-версия отдаёт файлы только по одному. Скрипт делает то же, что делал бы
человек в браузере — обходит папки и жмёт «Скачать» на каждом файле, — но сам,
с докачкой, повторами и списком того, что Яндекс отдать не смог.

Как работает:
  * открывает отдельное окно Chrome (через Playwright) со своим профилем
    в папке chrome-profile/<имя> рядом со скриптом — в нём один раз входишь
    в аккаунт Яндекса, сессия сохраняется;
  * через внутренний API веб-версии (models-v2: mpfs/resources, mpfs/url)
    обходит всё дерево Диска, пишет manifest.json и получает подписанные
    ссылки на скачивание;
  * качает файлы сам, с докачкой (Range), повторами и списком ошибок.

Безопасно запускать повторно: уже скачанные файлы (совпадает размер)
пропускаются, ничего не удаляется ни локально, ни на Диске.

Использование (после ./setup.sh):
  .venv/bin/python ydisk-web-backup.py                       # всё в ~/YandexDisk-Backup/default
  .venv/bin/python ydisk-web-backup.py --profile masha --dest ~/Backups/Masha
  ... --scan-only          # только построить список файлов, ничего не качать
  ... --no-rescan          # использовать прошлый manifest.json (быстрее)
  ... --roots /disk        # без «безлимитных» фото с телефона (по умолчанию и /disk, и /photounlim)
  ... --workers 10         # параллельных загрузок (по умолчанию 6)
"""
import argparse, json, os, sys, threading, time, socket, queue, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Не установлен playwright. Запусти ./setup.sh (или: pip install playwright)")

VERSION = "1.0.1"
HERE = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------------------
# Браузер: отдельное окно Chrome со своим профилем, вызовы внутреннего API
# ----------------------------------------------------------------------------
JS_API = """
async ({method, params}) => {
  const m = document.documentElement.innerHTML.match(/"sk"\\s*:\\s*"([^"]+)"/);
  if (!m) return {error: 'no-sk'};
  const ac = new AbortController(); const t = setTimeout(() => ac.abort(), 45000);
  try {
    const r = await fetch('/models-v2?m=' + method, {method: 'POST', credentials: 'same-origin', signal: ac.signal,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sk: m[1], connection_id: String(Date.now()) + '0000', apiMethod: method, requestParams: params})});
    let j = null; try { j = await r.json(); } catch (e) {}
    return {status: r.status, json: j};
  } catch (e) { return {error: 'fetch: ' + String(e)}; }
  finally { clearTimeout(t); }
}
"""

class Disk:
    def __init__(self, profile_dir, browser="auto"):
        self.pw = sync_playwright().start()
        opts = dict(headless=False, viewport={"width": 1200, "height": 800},
                    args=["--disable-blink-features=AutomationControlled"])
        self.ctx = None
        tried = []
        for channel in (["chrome", "msedge", None] if browser == "auto" else [None if browser == "chromium" else browser]):
            try:
                self.ctx = self.pw.chromium.launch_persistent_context(profile_dir, channel=channel, **opts) if channel \
                    else self.pw.chromium.launch_persistent_context(profile_dir, **opts)
                print(f"Браузер: {channel or 'встроенный Chromium'}")
                break
            except Exception as e:
                tried.append(f"{channel or 'chromium'}: {str(e).splitlines()[0][:100]}")
        if not self.ctx:
            self.pw.stop()
            sys.exit("Не удалось запустить браузер:\n  " + "\n  ".join(tried) +
                     "\nПоставь Google Chrome или выполни: .venv/bin/python -m playwright install chromium")
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.queue = queue.Queue()
        self.debug = None

    def login(self, timeout_min=20):
        self.page.goto("https://disk.yandex.ru/client/disk", wait_until="domcontentloaded")
        t0 = time.time(); said = False
        while True:
            has_sk = False
            try:
                has_sk = self.page.evaluate("() => /\"sk\"\\s*:\\s*\"/.test(document.documentElement.innerHTML)")
            except Exception:
                pass
            if "disk.yandex.ru/client" in self.page.url and has_sk:
                r = self._call("mpfs/resources", {"sort": "name", "order": "1", "idContext": "/disk", "amount": 1, "offset": 0, "withParent": "1"})
                if r.get("status") == 200 and r.get("json") and "resources" in r["json"]:
                    break
            if not said:
                print("\nОткрылось окно браузера. Войди в нём в аккаунт Яндекса, чей Диск выгружаем.")
                print("Как только появится список файлов Диска, скрипт продолжит сам. Жду...", flush=True); said = True
            time.sleep(3)
            if time.time() - t0 > timeout_min * 60:
                raise SystemExit(f"Не дождался входа ({timeout_min} мин). Запусти ещё раз.")
        login = self.page.evaluate("() => { const m = document.documentElement.innerHTML.match(/\"login\"\\s*:\\s*\"([^\"]+)\"/); return m ? m[1] : '?'; }")
        print(f"Вошли как: {login}")

    def _call(self, method, params):
        t0 = time.time()
        try:
            res = self.page.evaluate(JS_API, {"method": method, "params": params})
        except Exception as e:
            res = {"error": str(e)[:200]}
        if self.debug:
            self.debug.write(f"{time.strftime('%H:%M:%S')} {method} {time.time()-t0:6.2f}s status={res.get('status')} err={res.get('error')} {str(params.get('path') or params.get('idContext'))[:60]}\n"); self.debug.flush()
        return res

    def api(self, method, params):
        # Playwright (sync) работает только в главном потоке: из других потоков
        # запрос кладётся в очередь, главный поток выполняет его в serve().
        if threading.current_thread() is threading.main_thread():
            return self._call(method, params)
        ev = threading.Event(); box = {}
        self.queue.put((method, params, ev, box))
        ev.wait()
        return box["res"]

    def serve(self, until):
        """Главный поток: обслуживать запросы потоков, пока until() не станет True."""
        while not until() or not self.queue.empty():
            try:
                method, params, ev, box = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            box["res"] = self._call(method, params); ev.set()

    def close(self):
        try: self.ctx.close(); self.pw.stop()
        except Exception: pass

disk = None

# ----------------------------------------------------------------------------
# Обход дерева
# ----------------------------------------------------------------------------
def list_dir(path, page=500):
    items, offset = [], 0
    while True:
        res = None
        for attempt in range(3):
            res = disk.api("mpfs/resources", {"sort": "name", "order": "1", "idContext": path, "amount": page, "offset": offset, "withParent": "1"})
            if res.get("status") == 200 and res.get("json") and "resources" in res["json"]: break
            time.sleep(3 * (attempt + 1))
        if not (res.get("status") == 200 and res.get("json") and "resources" in res["json"]):
            raise RuntimeError(f"list {path}: {json.dumps(res, ensure_ascii=False)[:300]}")
        rs = res["json"]["resources"]
        parent, children = rs[0], rs[1:]
        total = parent.get("meta", {}).get("total_results_count", len(children))
        items.extend(children)
        offset += len(children)
        if not children or offset >= total: break
    return items

def scan(roots, manifest_path):
    files, dirs, errors = [], [], []
    todo = list(roots)
    while todo:
        d = todo.pop(0)
        try:
            items = list_dir(d)
        except Exception as e:
            errors.append({"path": d, "error": str(e)}); print(f"\n  ! {d}: {e}"); continue
        dirs.append(d)
        for it in items:
            if it.get("type") == "dir":
                todo.append(it["path"])
            else:
                meta = it.get("meta", {})
                files.append({"path": it["path"], "size": meta.get("size", 0), "mtime": it.get("mtime"),
                              "file_id": meta.get("file_id"), "md5": meta.get("md5")})
        tot = sum(f["size"] for f in files)
        print(f"\r  папок: {len(dirs):>5}  файлов: {len(files):>6}  {tot/2**30:7.2f} GiB  ({d[:60]})      ", end="", flush=True)
    print()
    json.dump({"scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"), "roots": roots, "files": files,
               "dirs": dirs, "errors": errors}, open(manifest_path, "w"), ensure_ascii=False, indent=1)
    return files, errors

# ----------------------------------------------------------------------------
# Скачивание
# ----------------------------------------------------------------------------
UNSAFE_NAME = "небезопасное имя — пропущен"

def local_path(dest, remote):
    """/disk/Фото/a.jpg -> dest/disk/Фото/a.jpg ; /photounlim/x.jpg -> dest/photounlim/x.jpg.
    None, если с таким именем файл записался бы вне dest (имена в общих папках
    задают другие люди): '..', а на Windows ещё '\\' и ':' (C:..., потоки NTFS)
    и точка/пробел в конце имени (Windows их отбрасывает, '...' превращается в другое имя)."""
    parts = remote.lstrip("/").split("/")
    win = os.name == "nt"
    def unsafe(p):
        return (p in ("", ".", "..") or
                (win and (any(c in p for c in "\\:") or p != p.rstrip(". "))))
    if any(unsafe(p) for p in parts):
        return None
    lp = os.path.join(dest, *parts)
    root = os.path.join(os.path.abspath(dest), "")
    return lp if os.path.abspath(lp).startswith(root) else None

def already_have(dest, f):
    lp = local_path(dest, f["path"])
    return lp is not None and os.path.exists(lp) and os.path.getsize(lp) == f["size"]

def get_url(remote):
    res = None
    for attempt in range(3):
        res = disk.api("mpfs/url", {"path": remote})
        j = (res or {}).get("json") or {}
        if j.get("file") or j.get("digest"): break
        time.sleep(3 * (attempt + 1))
    j = (res or {}).get("json") or {}
    u = j.get("file") or j.get("digest")
    if not u:
        raise RuntimeError(f"нет ссылки: {json.dumps(res, ensure_ascii=False)[:200]}")
    if u.startswith("//"): u = "https:" + u
    return u

UNAVAILABLE = "timeout: хранилище Яндекса не отвечает (файл пока недоступен)"

def download_one(f, dest, log, retries=3, timeout=40):
    lp = local_path(dest, f["path"]); size = f["size"]
    if lp is None:
        log.write(f"{f['path']}\t{size}\t{UNSAFE_NAME}\n"); log.flush()
        return "fail", UNSAFE_NAME
    if os.path.exists(lp) and os.path.getsize(lp) == size:
        return "skip", None
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    part = lp + ".part"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            url = get_url(f["path"])
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have > size: os.remove(part); have = 0
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            if have: req.add_header("Range", f"bytes={have}-")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if have and r.status != 206: have = 0   # докачка не поддержана — с нуля
                with open(part, "ab" if have else "wb") as out:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk: break
                        out.write(chunk)
            got = os.path.getsize(part)
            if size and got != size:
                raise RuntimeError(f"размер {got} != ожидалось {size}")
            os.replace(part, lp)
            if f.get("mtime"):
                try: os.utime(lp, (f["mtime"], f["mtime"]))
                except Exception: pass
            return "ok", None
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (404, 410, 500, 502, 503, 504): break   # файл недоступен у Яндекса — ждать нет смысла
        except (socket.timeout, TimeoutError):
            last_err = UNAVAILABLE; break                           # висящие файлы не долбим — докачаем в следующий запуск
        except urllib.error.URLError as e:
            last_err = str(e.reason)[:200]
            if "timed out" in last_err: last_err = UNAVAILABLE; break
        except (ConnectionError, OSError, RuntimeError) as e:
            last_err = str(e)[:200]
        time.sleep(min(30, 5 * attempt))
    log.write(f"{f['path']}\t{size}\t{last_err}\n"); log.flush()
    return "fail", last_err

def main():
    ap = argparse.ArgumentParser(description="Выгрузка Яндекс Диска через веб-версию (см. README.md)")
    ap.add_argument("--profile", default="default", help="имя профиля браузера; отдельный на каждый аккаунт Яндекса (по умолчанию: default)")
    ap.add_argument("--dest", help="куда складывать файлы (по умолчанию: ~/YandexDisk-Backup/<профиль>)")
    ap.add_argument("--roots", nargs="+", default=["/disk", "/photounlim"], help="корни на Диске (по умолчанию: /disk и /photounlim — безлимитные фото)")
    ap.add_argument("--workers", type=int, default=6, help="параллельных загрузок (по умолчанию 6)")
    ap.add_argument("--browser", default="auto", choices=["auto", "chrome", "msedge", "chromium"], help="какой браузер запускать (auto: Chrome → Edge → встроенный Chromium)")
    ap.add_argument("--scan-only", action="store_true", help="только составить список файлов, ничего не качать")
    ap.add_argument("--no-rescan", action="store_true", help="не сканировать заново, взять прошлый manifest.json")
    ap.add_argument("--login-timeout", type=int, default=20, help="сколько минут ждать входа в аккаунт (по умолчанию 20)")
    ap.add_argument("--version", action="version", version=f"ydisk-web-backup {VERSION}")
    a = ap.parse_args()

    global disk
    dest = os.path.expanduser(a.dest) if a.dest else os.path.join(os.path.expanduser("~"), "YandexDisk-Backup", a.profile)
    state = os.path.join(dest, "_backup-state"); os.makedirs(state, exist_ok=True)
    profile_dir = os.path.join(HERE, "chrome-profile", a.profile)
    print(f"ydisk-web-backup {VERSION}\n  профиль: {a.profile}\n  папка:   {dest}")
    disk = Disk(profile_dir, a.browser)
    disk.debug = open(os.path.join(state, "api-calls.log"), "a")
    try:
        disk.login(a.login_timeout)
        manifest = os.path.join(state, "manifest.json")
        if a.no_rescan and os.path.exists(manifest):
            files = json.load(open(manifest))["files"]; errors = []
            print(f"Использую manifest.json: {len(files)} файлов")
        else:
            print(f"Сканирую дерево: {a.roots}")
            files, errors = scan(a.roots, manifest)
        total = sum(f["size"] for f in files)
        print(f"Итого: {len(files)} файлов, {total/2**30:.2f} GiB; ошибок листинга: {len(errors)}")
        print(f"Manifest: {manifest}")
        if a.scan_only: return

        todo = [f for f in files if not already_have(dest, f)]
        print(f"К скачиванию: {len(todo)} файлов, {sum(f['size'] for f in todo)/2**30:.2f} GiB  (уже есть: {len(files)-len(todo)})\n")
        ts = time.strftime("%Y%m%d-%H%M%S")
        failed_path = os.path.join(state, f"failed-{ts}.txt")
        log = open(failed_path, "w")
        counts = {"ok": 0, "skip": 0, "fail": 0}; done_bytes = 0; t0 = time.time(); lock = threading.Lock()

        def work(f):
            nonlocal done_bytes
            st, err = download_one(f, dest, log)
            with lock:
                counts[st] += 1
                if st == "ok": done_bytes += f["size"]
                n = counts["ok"] + counts["skip"] + counts["fail"]
                rate = done_bytes / max(1, time.time() - t0) / 2**20
                print(f"\r  {n}/{len(todo)}  ok={counts['ok']} fail={counts['fail']}  {done_bytes/2**30:.2f} GiB  {rate:.1f} MiB/s   ", end="", flush=True)
                if st == "fail": print(f"\n  ! {f['path']}: {err}")

        ex = ThreadPoolExecutor(max_workers=a.workers)
        futures = [ex.submit(work, f) for f in todo]
        disk.serve(until=lambda: all(fu.done() for fu in futures))
        ex.shutdown(wait=True)
        for fu in futures: fu.result()
        log.close()
        print("\n\n================================================================")
        print(f"Скачано: {counts['ok']}   не удалось: {counts['fail']}   уже было: {counts['skip']}")
        print(f"Время: {(time.time()-t0)/60:.1f} мин, {done_bytes/2**30:.2f} GiB")
        if counts["fail"]:
            print(f"Список ошибок: {failed_path}")
            print("Запусти ещё раз позже (через день-два) — докачает только их.")
        print("================================================================")
    finally:
        disk.close()

if __name__ == "__main__":
    main()
