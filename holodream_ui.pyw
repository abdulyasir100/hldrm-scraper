"""Holodream Scraper - a four-step window over the extraction scripts.

Every script here needs the same three things right, and each one has bitten
already: the right Python (this folder's .venv when there is one, not whatever
venv a terminal has active), this folder as the working directory, and a UTF-8
console for the Japanese names. Running them from here gets all three right.

The same file serves two setups. Next to install_character.py and a
live2d-companion checkout it installs characters straight into the app; on its
own it exports them to a folder instead.

Launch with Holodream.bat.
"""
from __future__ import annotations

import csv
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent
# children need a console interpreter: pythonw gives them no stdout to stream
PY = str(Path(sys.executable).with_name("python.exe"))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

APP = HERE.parent / "live2d-companion"
INSTALL_MODE = (HERE / "install_character.py").exists() and APP.is_dir()
SUISEI = "00018"


def catalogue() -> set[str]:
    path = HERE / "octo_list.json"
    if not path.exists():
        return set()
    return {e["name"] for e in json.loads(path.read_text(encoding="utf-8"))["assetBundles"]}


def characters() -> list[str]:
    """'00018  すいせい' entries for the picker, named once names.py has run."""
    named = HERE / "previews" / "names.csv"
    if named.exists():
        with open(named, encoding="utf-8") as f:
            return [f"{r['character_id']}  {r['name'] or '?'}" for r in csv.DictReader(f)]
    ids = {n[len("live2d_mdl_"):].split("-")[0] for n in catalogue() if n.startswith("live2d_mdl_")}
    return [f"{i}  ?" for i in sorted(ids)]


def chibi_jobs(cid: str) -> list[list[str]]:
    """One build_chibi.py call per 3D outfit. An outfit without hair of its own
    wears the character's base hair, which build_chibi takes via --hair."""
    names = catalogue()
    prefix = f"mdl_chr_drs_{cid}-"
    outfits = sorted(n[len(prefix):-len("_body")] for n in names
                     if n.startswith(prefix) and n.endswith("_body"))
    jobs = []
    for outfit in outfits:
        cmd = ["build_chibi.py", cid, outfit]
        if f"{prefix}{outfit}_hair" not in names:
            if f"{prefix}base-0000-00_hair" not in names:
                continue  # no hair anywhere to put on it
            cmd += ["--hair", "base-0000-00"]
        jobs.append(cmd)
    return jobs


def toolkit_missing() -> bool:
    try:
        import holodori_asset_tools  # noqa: F401
        return False
    except ImportError:
        return True


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Holodream Scraper")
        self.geometry("780x660")
        self.minsize(640, 500)
        # output lines, plus one tuple per finished run - Tk may only be touched
        # from the main thread, so the worker reports back through here
        self.lines: queue.Queue[str | tuple] = queue.Queue()
        self.buttons: list[ttk.Button] = []
        self.build()
        self.reload_characters()
        self.after(80, self.drain)
        if toolkit_missing():
            self.after(200, self.explain_setup)

    # ---- layout ------------------------------------------------------------

    def step(self, parent, number: str, title: str, hint: str) -> ttk.Frame:
        box = ttk.LabelFrame(parent, text=f"  {number}  {title}  ", padding=10)
        box.pack(fill="x", pady=(0, 8))
        ttk.Label(box, text=hint, foreground="#555", wraplength=700).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 0))
        return row

    def button(self, parent, text: str, command) -> ttk.Button:
        b = ttk.Button(parent, text=text, command=command)
        b.pack(side="left", padx=(0, 6))
        self.buttons.append(b)
        return b

    def build(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")

        row = self.step(top, "1", "Game updated?",
                        "Run this after every hololive Dreams update. It fetches the newest "
                        "catalogue and re-maps what the game has already downloaded.")
        self.button(row, "Refresh catalogue", self.refresh)

        row = self.step(top, "2", "Who's who",
                        "Works out every character's name from the story and draws picture "
                        "sheets of all of them, graduated members included.")
        self.button(row, "Build names + previews", self.names)
        self.button(row, "Open previews", lambda: self.open(HERE / "previews"))

        row = self.step(top, "3", "Get a character",
                        "Pick someone. Live2D gives every outfit as ordinary Cubism files; "
                        "3D chibi gives their park model as a rigged .glb for Blender or three.js.")
        self.pick = ttk.Combobox(row, width=24, state="readonly")
        self.pick.pack(side="left", padx=(0, 6))
        self.pick.bind("<<ComboboxSelected>>", self.picked)
        self.button(row, "Live2D", self.download)
        self.button(row, "3D chibi", self.chibi)
        row = ttk.Frame(row.master)
        row.pack(fill="x", pady=(6, 0))
        self.button(row, "Open Live2D models", lambda: self.open(HERE / "live2d"))
        self.button(row, "Open 3D chibis", lambda: self.open(HERE / "out" / "chibi"))

        if INSTALL_MODE:
            row = self.step(top, "4", "Put into my companion app",
                            "Installs the picked character into live2d-companion with the latest "
                            "motions. A reinstall keeps any names you've already given them.")
            label = "Install"
        else:
            row = self.step(top, "4", "Export a ready-to-use folder",
                            "Packs the picked character - every outfit, the shared motions and a "
                            "voice clip - into out/<folder name>, ready for any Live2D app.")
            label = "Export"
        ttk.Label(row, text="folder name:").pack(side="left", padx=(0, 4))
        self.slug = ttk.Entry(row, width=14)
        self.slug.pack(side="left", padx=(0, 6))
        self.button(row, label, self.install)

        log = ttk.Frame(self, padding=(12, 0, 12, 12))
        log.pack(fill="both", expand=True)
        self.status = ttk.Label(log, text="Ready.")
        self.status.pack(anchor="w", pady=(0, 4))
        self.out = tk.Text(log, height=10, wrap="word", bg="#15171c", fg="#d6dae3",
                           insertbackground="#d6dae3", relief="flat", font=("Consolas", 9))
        self.out.pack(fill="both", expand=True)

    # ---- helpers -----------------------------------------------------------

    def explain_setup(self) -> None:
        messagebox.showwarning(
            "Holodream Scraper",
            "The holodori-asset-tools package isn't installed for this Python, so nothing "
            "will run yet. In a terminal:\n\n"
            "  git clone https://github.com/HolodoriDB/holodori-asset-tools\n"
            "  pip install -e ./holodori-asset-tools\n\n"
            f"Python in use:\n{sys.executable}",
        )

    def reload_characters(self) -> None:
        values = characters()
        self.pick["values"] = values
        if values and not self.pick.get():
            self.pick.set(next((v for v in values if v.startswith(SUISEI)), values[0]))
            self.picked()

    def picked(self, _event=None) -> None:
        cid = self.cid()
        self.slug.delete(0, "end")
        self.slug.insert(0, "suisei" if cid == SUISEI else cid)

    def cid(self) -> str:
        return self.pick.get().split()[0] if self.pick.get() else ""

    def needs_index(self) -> bool:
        if (HERE / "local_index.json").exists():
            return False
        messagebox.showinfo("Holodream Scraper", "Run step 1 first - it only takes a moment.")
        return True

    def open(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    # ---- running scripts ---------------------------------------------------

    def run(self, label: str, *commands: list[str], then=None) -> None:
        """Run commands one after another off the UI thread, streaming output."""
        for b in self.buttons:
            b.state(["disabled"])
        self.status.config(text=f"Working: {label}...")
        self.out.delete("1.0", "end")

        def work() -> None:
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                   "PYTHONWARNINGS": "ignore"}
            ok = True
            for cmd in commands:
                self.lines.put(f"\n> {' '.join(cmd)}\n")
                proc = subprocess.Popen(
                    [PY, *cmd], cwd=HERE, env=env, creationflags=NO_WINDOW,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )
                for line in proc.stdout:
                    self.lines.put(line)
                if proc.wait() != 0:
                    self.lines.put("\n[stopped - that step failed]\n")
                    ok = False
                    break
            self.lines.put(("finished", label, ok, then))

        threading.Thread(target=work, daemon=True).start()

    def finished(self, label: str, ok: bool, then) -> None:
        for b in self.buttons:
            b.state(["!disabled"])
        self.status.config(text=f"{'Done' if ok else 'Failed'}: {label}.")
        if ok and then:
            then()

    def drain(self) -> None:
        # progress bars rewrite one line with \r; keep only the latest state
        while not self.lines.empty():
            line = self.lines.get_nowait()
            if isinstance(line, tuple):
                self.finished(*line[1:])
                continue
            if "\r" in line:
                line = line.rsplit("\r", 1)[-1]
            self.out.insert("end", line)
            self.out.see("end")
        self.after(80, self.drain)

    # ---- the steps ---------------------------------------------------------

    def refresh(self) -> None:
        self.run("refresh catalogue", ["index_local.py"], then=self.reload_characters)

    def names(self) -> None:
        if self.needs_index():
            return
        self.run(
            "names + previews",
            ["pull.py", "--filter", r"^adv_(main|chr)_[0-9_.-]+$", "--fetch", "--no-extract"],
            ["names.py"],
            ["pull.py", "--filter", r"^img_(chr_icon_mini|cos_2d_thumb)_", "--fetch"],
            ["gather_previews.py"],
            then=self.reload_characters,
        )

    def download(self) -> None:
        cid = self.cid()
        if not cid or self.needs_index():
            return
        self.run(
            f"Live2D {self.pick.get().strip()}",
            ["pull.py", "--filter", f"live2d_.*_{cid}[-_]", "--fetch"],
            ["build_live2d.py", cid],
            then=lambda: self.open(HERE / "live2d" / cid),
        )

    def chibi(self) -> None:
        cid = self.cid()
        if not cid or self.needs_index():
            return
        jobs = chibi_jobs(cid)
        if not jobs:
            messagebox.showinfo("Holodream Scraper", f"{self.pick.get().strip()} has no 3D chibi in the game.")
            return
        self.run(
            f"3D chibi {self.pick.get().strip()} ({len(jobs)} outfits)",
            ["pull.py", "--filter", f"^mdl_chr_drs_{cid}-", "--fetch", "--no-extract"],
            *jobs,
            then=lambda: self.open(HERE / "out" / "chibi"),
        )

    def install(self) -> None:
        cid, slug = self.cid(), self.slug.get().strip()
        if not cid or not slug or self.needs_index():
            return
        name = self.pick.get().split(maxsplit=1)[-1].strip()
        name = "" if name == "?" else name
        # the motions are shared, but converting them needs one character's rig
        # on disk to resolve their hashed curve names - use the picked one
        prep = [["pull.py", "--filter", f"live2d_.*_{cid}[-_]", "--fetch"],
                ["pull.py", "--filter", r"^live2d_mot_", "--fetch"],
                ["build_motions.py", cid]]
        if INSTALL_MODE:
            last = ["install_character.py", cid, slug]
            # only name a fresh install; an existing one keeps the name you gave it
            if name and not (APP / "public" / "models" / slug / "character.json").exists():
                last += ["--name", name]
            self.run(f"install {slug}", *prep, last)
        else:
            dest = HERE / "out" / slug
            last = ["export_character.py", cid, "--out", str(dest)] + (["--name", name] if name else [])
            self.run(f"export {slug}", *prep, last, then=lambda: self.open(dest))


if __name__ == "__main__":
    App().mainloop()
