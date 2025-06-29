from __future__ import annotations
import configparser, hashlib, os, sys, time, typing as T
from pathlib import Path

import requests
import neocities
from alive_progress import alive_bar

VERSION = "2.2"


# ──── helpers ────────────────────────────────────────────────────────
def sha1_file(p: Path, buf: int = 128 * 1024) -> str:
    """
    Hex SHA-1 of *p*.  Uses hashlib.file_digest on 3.11+, else manual loop.
    """
    if hasattr(hashlib, "file_digest"):               # Python 3.11+
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


def create_default_config(path: Path) -> None:
    cfg = configparser.ConfigParser()
    cfg["DEFAULT"] = {
        "version": VERSION,
        "id": "your-neocities-id",
        "password": "your-neocities-password-or-key",
        "api_key": "",
        "include_files": "html,css,js,png,jpg,jpeg,gif,webp,json",
        "batch_size": "100",
    }
    with path.open("w", encoding="utf8") as fh:
        cfg.write(fh)
    print("Created template config.ini – fill in your credentials and rerun.")


def retry_on_rate_limit(fn, *a, **kw):
    """
    Call *fn* (no args expected) and back-off 60→120→240 s on HTTP 429/5xx.
    """
    delay = 60
    while True:
        try:
            return fn(*a, **kw)
        except requests.HTTPError as e:
            if e.response.status_code in (429, 500, 502, 503, 504):
                print(f"⚠  {e.response.status_code}; retrying in {delay}s…")
                time.sleep(delay)
                delay = min(delay * 2, 600)
            else:
                raise


# ──── main routine ──────────────────────────────────────────────────
def main(root_path = '.', skip_input:bool = False, _print: T.Callable[[str], None] = print) -> None:
    if skip_input:
        _input = lambda _: None  # type: ignore[assignment]
    else:
        _input = input
    
    cfg_path = Path("config.ini")
    if not cfg_path.exists():
        create_default_config(cfg_path)
        sys.exit()

    cfg = configparser.ConfigParser()
    cfg.read(cfg_path, encoding="utf8")
    c = cfg["DEFAULT"]

    # interactive credential prompt – ENTER keeps stored value
    site_id  = _input(f"Neocities ID [{c['id']}]: ") or c["id"]
    secret   = _input(f"Password / API key [{c['password']}]: ") or c["password"]
    batch_sz = int(c.get("batch_size", "100"))
    exts     = [e.strip().lower().lstrip(".") for e in c["include_files"].split(",")]

    # save back what user typed
    c.update({"id": site_id, "password": secret})
    with cfg_path.open("w", encoding="utf8") as fh:
        cfg.write(fh)

    # connect
    try:
        if c.get("api_key"):
            nc = neocities.NeoCities(api_key=c["api_key"])
        else:
            nc = neocities.NeoCities(site_id, secret)
    except Exception as e:
        sys.exit(f"✖  Could not create API client – {e}")

    # pull remote file list
    raw = retry_on_rate_limit(nc.listitems)              # GET /api/list
    if raw.get("result") != "success" or "files" not in raw:
        sys.exit(f"✖  API error: {raw.get('message', raw)}")

    remote_index = {f["path"]: (f["size"], f["sha1_hash"])
                    for f in raw["files"] if not f["is_directory"]}

    # scan local tree
    root = Path(root_path).resolve()
    local = [p for p in root.rglob("*")
             if p.is_file() and p.suffix.lstrip(".").lower() in exts]

    to_send: list[tuple[str, str]] = []
    for p in local:
        rel = p.relative_to(root).as_posix()
        abs = p.as_posix()
        size = p.stat().st_size
        r_size, r_sha = remote_index.get(rel, (None, None))
        if size != r_size or sha1_file(p) != r_sha:
            to_send.append((abs, rel))

    total = len(to_send)
    _print(f"→  {total} / {len(local)} files need upload/update.")
    if not skip_input and (not total or _input("Continue? [y/N] ").lower() != "y"):
        _input("✖  Press ENTER to exit.")
        return

    # batch upload
    sent = 0
    with alive_bar(total, title="Uploading", bar="classic") as bar:
        for chunk in chunked(to_send, batch_sz):
            retry_on_rate_limit(lambda: nc.upload(*chunk))
            bar(len(chunk))
            sent += len(chunk)
            if not skip_input:
                _print(f"  {sent} file(s) uploaded so far…")

    _print(f"✓  Finished – {sent} file(s) uploaded in "
          f"{(total - 1)//batch_sz + 1} request(s).")

    _input("Press ENTER to exit.")

if __name__ == "__main__":
    main()
