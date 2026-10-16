from __future__ import annotations
import configparser, hashlib, os, threading, time, typing as T
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import sys

import requests
import neocities

VERSION = "2.3"
CFG_PATH = Path("config.ini").resolve()


# ──── helpers ────────────────────────────────────────────────────────
def sha1_file(p: Path, buf: int = 128 * 1024) -> str:
    if hasattr(hashlib, "file_digest"):
        with p.open("rb") as fh:
            return hashlib.file_digest(fh, "sha1").hexdigest()
    h = hashlib.sha1()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def chunked(seq: list[T.Any], n: int) -> T.Iterator[list[T.Any]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def load_or_create_config(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not path.exists():
        cfg["DEFAULT"] = {
            "version": VERSION,
            "id": "your-neocities-id",
            "password": "your-neocities-password-or-key",
            "api_key": "",
            "target_dir": str(Path(".").resolve()),
            "include_files": "html,css,js,png,jpg,jpeg,gif,webp,json,eot,ttf,woff,woff2,svg,ico",
            "batch_size": "100",
        }
        with path.open("w", encoding="utf8") as fh:
            cfg.write(fh)
    else:
        cfg.read(path, encoding="utf8")
    return cfg

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)

# ──── GUI ────────────────────────────────────────────────────────────
class SyncApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Neocities Sync v{VERSION}")
        self.geometry("600x500")
        
        # ICON
        icon_file_path = resource_path("ico.ico")
        self.iconbitmap(icon_file_path)
        # app_icon = tk.PhotoImage(file=icon_file_path)
        # self.iconphoto(False, app_icon)

        self.cfg = load_or_create_config(CFG_PATH)
        self.c = self.cfg["DEFAULT"]
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(self, text="Settings", padding=10)
        frm.pack(fill="x", padx=10, pady=5)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Neocities ID:").grid(row=0, column=0, sticky="w", **pad)
        self.id_var = tk.StringVar(value=self.c.get("id", ""))
        ttk.Entry(frm, textvariable=self.id_var).grid(row=0, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(frm, text="Password:").grid(row=1, column=0, sticky="w", **pad)
        self.pw_var = tk.StringVar(value=self.c.get("password", ""))
        ttk.Entry(frm, textvariable=self.pw_var, show="*").grid(row=1, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(frm, text="API Key:").grid(row=2, column=0, sticky="w", **pad)
        self.key_var = tk.StringVar(value=self.c.get("api_key", ""))
        ttk.Entry(frm, textvariable=self.key_var, show="*").grid(row=2, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(frm, text="Target Folder:").grid(row=3, column=0, sticky="w", **pad)
        self.dir_var = tk.StringVar(value=self.c.get("target_dir", str(Path(".").resolve())))
        ttk.Entry(frm, textvariable=self.dir_var).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse...", command=self._browse).grid(row=3, column=2, **pad)

        ttk.Label(frm, text="Extensions:").grid(row=4, column=0, sticky="w", **pad)
        self.ext_var = tk.StringVar(value=self.c.get("include_files", ""))
        ttk.Entry(frm, textvariable=self.ext_var).grid(row=4, column=1, columnspan=2, sticky="ew", **pad)
        
        # Batch size setting
        ttk.Label(frm, text="Batch Size:").grid(row=5, column=0, sticky="w", **pad)
        self.batch_var = tk.StringVar(value=self.c.get("batch_size", "100"))
        ttk.Entry(frm, textvariable=self.batch_var).grid(row=5, column=1, sticky="ew", **pad)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=5)
        self.b_up = ttk.Button(btns, text="Upload (Local → Remote)", command=lambda: self.run("upload"))
        self.b_up.pack(side="left", expand=True, fill="x", padx=2)
        self.b_dn = ttk.Button(btns, text="Download (Remote → Local)", command=lambda: self.run("download"))
        self.b_dn.pack(side="left", expand=True, fill="x", padx=2)

        self.prog = ttk.Progressbar(self, mode="determinate")
        self.prog.pack(fill="x", padx=10, pady=5)

        self.log_box = scrolledtext.ScrolledText(self, height=12, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)

    def log(self, msg: str):
        def _w():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _w)

    def set_prog(self, val: int, total: int):
        self.after(0, lambda: self.prog.configure(maximum=max(total, 1), value=val))

    def confirm(self, msg: str) -> bool:
        res = False
        ev = threading.Event()
        def _ask():
            nonlocal res
            res = messagebox.askyesno("Confirm", msg)
            ev.set()
        self.after(0, _ask)
        ev.wait()
        return res

    def retry_on_rate_limit(self, fn, *a, **kw):
        delay = 60
        while True:
            try:
                return fn(*a, **kw)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (429, 500, 502, 503, 504):
                    self.log(f"⚠ {e.response.status_code}; retrying in {delay}s…")
                    time.sleep(delay)
                    delay = min(delay * 2, 600)
                else:
                    raise

    def run(self, mode: str):
        self.c.update({
            "id": self.id_var.get().strip(),
            "password": self.pw_var.get().strip(),
            "api_key": self.key_var.get().strip(),
            "target_dir": self.dir_var.get().strip(),
            "include_files": self.ext_var.get().strip(),
            "batch_size": self.batch_var.get().strip(),
        })
        with CFG_PATH.open("w", encoding="utf8") as fh:
            self.cfg.write(fh)

        self.b_up["state"] = self.b_dn["state"] = "disabled"
        threading.Thread(target=self._worker, args=(mode,), daemon=True).start()

    def _worker(self, mode: str):
        try:
            site_id = self.c["id"]
            secret = self.c["password"]
            batch_sz = int(self.c.get("batch_size", "100"))
            exts = [e.strip().lower().lstrip(".") for e in self.c["include_files"].split(",")]
            root = Path(self.c["target_dir"]).resolve()

            # Change working directory to target_dir so (rel, rel) uses pure POSIX paths
            os.chdir(root)

            self.log(f"Connecting to Neocities ({site_id})…")
            if self.c.get("api_key"):
                nc = neocities.NeoCities(api_key=self.c["api_key"])
            else:
                nc = neocities.NeoCities(site_id, secret)

            raw = self.retry_on_rate_limit(nc.listitems)
            if raw.get("result") != "success" or "files" not in raw:
                self.log(f"✖ API error: {raw.get('message', raw)}")
                return

            # Normalize any '\' in remote paths returned by API
            remote_index = {
                f["path"].replace("\\", ""): (f["size"], f["sha1_hash"])
                for f in raw["files"] if not f["is_directory"]
            }
            self.log(f"→ Found {len(remote_index)} files on Neocities.")

            # Scan local tree
            local = [
                p for p in root.rglob("*")
                if p.is_file()
                and p.suffix.lstrip(".").lower() in exts
                and p.resolve() != CFG_PATH
            ]

            local_index: dict[str, tuple[int, str]] = {}
            self.set_prog(0, len(local))
            with ThreadPoolExecutor() as executor:
                futures = {executor.submit(sha1_file, p): p for p in local}
                for i, future in enumerate(as_completed(futures), 1):
                    p = futures[future]
                    try:
                        sha = future.result()
                        rel = p.relative_to(root).as_posix()
                        local_index[rel] = (p.stat().st_size, sha)
                    except Exception as e:
                        self.log(f"✖ Error processing {p}: {e}")
                    self.set_prog(i, len(local))

            if mode == "upload":
                to_send = [
                    (rel, rel) for rel, (size, sha) in local_index.items()
                    if (size, sha) != remote_index.get(rel, (None, None))
                ]
                total = len(to_send)
                self.log(f"→ {total} / {len(local)} files need to be uploaded.")
                if not total or not self.confirm(f"Upload {total} file(s)?"):
                    return

                sent = 0
                self.set_prog(0, total)
                for chunk in chunked(to_send, batch_sz):
                    self.retry_on_rate_limit(lambda: nc.upload(*chunk))
                    sent += len(chunk)
                    self.set_prog(sent, total)
                self.log(f"✓ Finished – {sent} file(s) uploaded.")

            elif mode == "download":
                to_dl = [
                    rel for rel, (r_size, r_sha) in remote_index.items()
                    if Path(rel).suffix.lstrip(".").lower() in exts
                    and local_index.get(rel, (None, None)) != (r_size, r_sha)
                ]
                total = len(to_dl)
                self.log(f"→ {total} files need to be downloaded.")
                if not total or not self.confirm(f"Download {total} file(s)?"):
                    return

                done = 0
                self.set_prog(0, total)
                def _dl(rel: str):
                    url = f"https://{site_id}.neocities.org/{rel}"
                    r = self.retry_on_rate_limit(requests.get, url, timeout=30)
                    r.raise_for_status()
                    dest = root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(r.content)

                with ThreadPoolExecutor(max_workers=8) as ex:
                    futs = {ex.submit(_dl, rel): rel for rel in to_dl}
                    for fut in as_completed(futs):
                        try:
                            fut.result()
                            done += 1
                        except Exception as e:
                            self.log(f"✖ Failed {futs[fut]}: {e}")
                        self.set_prog(done, total)
                self.log(f"✓ Finished – {done}/{total} file(s) downloaded.")

        except Exception as e:
            self.log(f"✖ Error: {e}")
        finally:
            self.after(0, lambda: (self.b_up.configure(state="normal"), self.b_dn.configure(state="normal")))


if __name__ == "__main__":
    SyncApp().mainloop()