"""
store.py - The settings file, safe to share between several computers.

Several SheetMakers (on different computers) can use ONE settings file in a shared folder
(a network share, NAS, or a OneDrive / Dropbox / Google Drive folder):

  * Saving never overwrites what another computer changed. Each save reads the file as it
    is right now and applies only what changed HERE since this computer last read it
    (a three-way merge). Scraps are matched by their id; a changed "How many" is applied
    as a difference, so two computers using the same scrap size both count.
  * The file is written to a temporary file first and then swapped in, so nobody ever reads
    a half-written file. A small .lock file keeps two saves from running at the same moment.
  * poll() is called every few seconds: when the file changed on disk it is read again and
    the app shows the new scrap list.
  * If the file can't be reached (network down), changes are kept and saved as soon as it
    is back. A file that exists but can't be read is never overwritten.

Things that only make sense on one computer (the last folder used, where the shared file
is) live in a small local file instead: %LOCALAPPDATA%\\SheetMaker\\local.json.
"""

import copy
import json
import os
import sys
import time

LOCAL_KEYS = ("last_folder",)            # kept per computer, never shared
LOCK_STALE_S = 15                        # a lock older than this was left by a crash
LOCK_WAIT_S = 4


def local_dir():
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    base = base or os.environ.get("SHEETMAKER_LOCAL") or os.path.join(os.path.expanduser("~"), ".sheetmaker")
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = os.path.join(base, "SheetMaker")
    return base


def local_path():
    return os.path.join(local_dir(), "local.json")


def load_local():
    try:
        with open(local_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_local(d):
    try:
        os.makedirs(local_dir(), exist_ok=True)
        tmp = local_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, local_path())
    except Exception:
        pass


class Unreachable(Exception):
    pass


def _same(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def merge_scraps(base, mine, theirs):
    """Three-way merge of scrap lists by id.
    base   = the list as this computer last read it from the file
    mine   = the list as this computer has it now
    theirs = the list in the file right now (may hold other computers' changes)"""
    b = {s["id"]: s for s in base or []}
    m = {s["id"]: s for s in mine or []}
    out = [dict(s) for s in theirs or []]
    t = {s["id"]: s for s in out}
    for sid, ms in m.items():
        if sid not in b:                              # added here
            if sid not in t:
                out.append(dict(ms))
                t[sid] = out[-1]
            continue
        bs = b[sid]
        if _same(bs, ms):
            continue
        ts = t.get(sid)
        if ts is None:                                # changed here but removed elsewhere: stays removed
            continue
        for k, v in ms.items():
            if k == "qty":
                ts["qty"] = int(ts.get("qty", 0)) + int(v) - int(bs.get("qty", 0))
            elif not _same(v, bs.get(k)):
                ts[k] = v
    for sid in b:
        if sid not in m:                              # removed here
            t.pop(sid, None)
    out = [s for s in out if s["id"] in t and int(s.get("qty", 0)) > 0]
    return out


def merge(base, mine, theirs):
    out = copy.deepcopy(theirs)
    for k, v in mine.items():
        if k in LOCAL_KEYS:
            continue
        if k == "scraps":
            out["scraps"] = merge_scraps(base.get("scraps"), v, theirs.get("scraps"))
        elif k not in base or not _same(base[k], v):
            out[k] = copy.deepcopy(v)
    return out


class Store:
    def __init__(self, path, defaults, normalize=None):
        self.path = path
        self.defaults = defaults
        self.normalize = normalize or (lambda s: s)
        self.base = None            # what this computer last read from (or wrote to) the file
        self.sig = None             # (mtime, size) of the file when last read / written
        self.pending = None         # changes waiting for an unreachable file
        self.reachable = True
        self.problem = ""

    # ------------------------------------------------------------ file access
    def _sig(self):
        st = os.stat(self.path)
        return (st.st_mtime_ns, st.st_size)

    def _read(self):
        """The file as it is now. Missing file -> defaults. Unreadable -> Unreachable."""
        folder = os.path.dirname(self.path) or "."
        if not os.path.isdir(folder):
            raise Unreachable(f"The folder {folder} can't be reached.")
        last = None
        for _ in range(6):                  # another computer may be swapping the file in right now
            try:
                sig = self._sig()
            except FileNotFoundError:
                return dict(self.defaults), None
            except OSError as e:
                last = e
                time.sleep(0.15)
                continue
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                s = dict(self.defaults)
                s.update(data)
                return self.normalize(s), sig
            except (OSError, ValueError) as e:
                last = e
                time.sleep(0.15)
        raise Unreachable(f"The settings file can't be read: {last}")

    def _lock(self):
        lock = self.path + ".lock"
        t0 = time.time()
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.environ.get('COMPUTERNAME', '')} {time.time():.0f}".encode())
                os.close(fd)
                return lock
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(lock) > LOCK_STALE_S:
                        os.remove(lock)
                        continue
                except OSError:
                    pass
                if time.time() - t0 > LOCK_WAIT_S:
                    return None             # go ahead anyway; the merge still protects most changes
                time.sleep(0.1)
            except OSError as e:
                raise Unreachable(str(e))

    def _write(self, s):
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in s.items() if k not in LOCAL_KEYS}, f, indent=2)
        for i in range(10):                 # Windows refuses the swap while someone is reading
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.1 * (i + 1))
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ public
    def load(self):
        try:
            s, self.sig = self._read()
            self.reachable, self.problem = True, ""
        except Unreachable as e:
            s, self.sig = self.normalize(dict(self.defaults)), None
            self.reachable, self.problem = False, str(e)
        self.base = copy.deepcopy(s)
        loc = load_local()
        for k in LOCAL_KEYS:
            if k in loc:
                s[k] = loc[k]
        return s

    def save(self, mine):
        """Save this computer's settings, keeping other computers' changes. Returns the merged
        settings (what is in the file now), or `mine` when the file can't be reached."""
        loc = load_local()
        if any(loc.get(k) != mine.get(k) for k in LOCAL_KEYS if k in mine):
            loc.update({k: mine[k] for k in LOCAL_KEYS if k in mine})
            save_local(loc)
        try:
            lock = self._lock()
            try:
                theirs, _ = self._read()
                merged = merge(self.base or {}, mine, theirs)
                if not _same({k: v for k, v in merged.items() if k not in LOCAL_KEYS},
                             {k: v for k, v in theirs.items() if k not in LOCAL_KEYS}) or self.sig is None:
                    self._write(merged)
                self.sig = self._sig()
            finally:
                if lock:
                    try:
                        os.remove(lock)
                    except OSError:
                        pass
        except (Unreachable, OSError) as e:
            self.pending = copy.deepcopy(mine)
            self.reachable, self.problem = False, str(e)
            return mine
        self.pending = None
        self.reachable, self.problem = True, ""
        self.base = copy.deepcopy(merged)
        for k in LOCAL_KEYS:
            if k in mine:
                merged[k] = mine[k]
        return merged

    def replace(self, s):
        """Write s as the whole file (used when a shared file is first made). Raises OSError."""
        lock = self._lock()
        try:
            self._write(s)
            self.sig = self._sig()
        finally:
            if lock:
                try:
                    os.remove(lock)
                except OSError:
                    pass
        self.base = copy.deepcopy({k: v for k, v in s.items() if k not in LOCAL_KEYS})
        self.pending = None
        self.reachable, self.problem = True, ""

    def poll(self, current):
        """Call every few seconds with this computer's settings. Returns the new settings when
        the file changed (another computer saved, or the file came back), else None."""
        if self.pending is not None:
            before = self.reachable
            out = self.save(current)
            return out if self.reachable or before != self.reachable else None
        try:
            sig = self._sig()
        except FileNotFoundError:
            sig = None
        except OSError as e:
            if self.reachable:
                self.reachable, self.problem = False, str(e)
                return current
            return None
        if sig == self.sig and self.reachable:
            return None
        try:
            s, self.sig = self._read()
        except Unreachable as e:
            if self.reachable:
                self.reachable, self.problem = False, str(e)
                return current
            return None
        self.reachable, self.problem = True, ""
        # keep anything changed here that wasn't saved yet (normally nothing)
        merged = merge(self.base or {}, current, s)
        self.base = copy.deepcopy(s)
        for k in LOCAL_KEYS:
            if k in current:
                merged[k] = current[k]
        return merged
