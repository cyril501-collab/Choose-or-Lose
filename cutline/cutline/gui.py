"""Application graphique Cutline : montage automatique en quelques clics, sans terminal."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .app_runner import MODE_LABELS, JobParams, run_job

MODE_HELP = {
    "trailer": "Bande-annonce de film, série ou documentaire (1 à 2 minutes). "
               "Un scénario est fortement conseillé.",
    "teaser": "Petite bande-annonce rythmée de 15 à 30 secondes.",
    "previously": "Résumé « Précédemment dans… » : choisissez les rushes de l'épisode précédent "
                  "et le scénario de l'épisode à venir.",
    "coming_next": "Auto-promo d'antenne « À suivre » de 10 à 20 secondes avec horaire et chaîne.",
    "news": "Sujet de JT : images d'illustration, sonores d'interviews, synthés et voix off. "
            "Nommez vos interviews ITW_… (ex : ITW_maire.mp4).",
}
DEFAULT_DURATION = {"trailer": 90, "teaser": 25, "previously": 45, "coming_next": 15, "news": 90}

BG = "#1E1F24"
PANEL = "#2A2C33"
INK = "#F2F2F2"
MUTED = "#A9ACB6"
ACCENT = "#E8A33D"


def open_folder(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Cutline {__version__} — Montage automatique")
        self.configure(bg=BG)
        self.minsize(820, 640)
        self.geometry("980x780")
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self._style()

        self.mode = tk.StringVar(value="trailer")
        self.rushes = tk.StringVar()
        self.script = tk.StringVar()
        self.music = tk.StringVar()
        self.prev_rushes = tk.StringVar()
        self.prev_script = tk.StringVar()
        self.title_var = tk.StringVar()
        self.end_card = tk.StringVar()
        self.channel = tk.StringVar()
        self.cards = tk.StringVar()
        self.duration = tk.IntVar(value=90)
        self.rate = tk.StringVar(value="25")
        self.transcribe = tk.BooleanVar(value=True)
        self.avid = tk.BooleanVar(value=True)
        default_out = Path.home() / "Desktop" / "Cutline"
        if not default_out.parent.exists():
            default_out = Path.home() / "Cutline"
        self.out_dir = tk.StringVar(value=str(default_out))

        self._build()
        self._on_mode()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ style
    def _style(self) -> None:
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        base = ("Helvetica", 11)
        st.configure(".", background=BG, foreground=INK, font=base)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=INK)
        st.configure("Panel.TLabel", background=PANEL, foreground=INK)
        st.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Helvetica", 10))
        st.configure("H.TLabel", background=BG, foreground=ACCENT, font=("Helvetica", 13, "bold"))
        st.configure("Title.TLabel", background=BG, foreground=INK, font=("Helvetica", 20, "bold"))
        st.configure("TRadiobutton", background=PANEL, foreground=INK)
        st.map("TRadiobutton", background=[("active", PANEL)])
        st.configure("TCheckbutton", background=PANEL, foreground=INK)
        st.map("TCheckbutton", background=[("active", PANEL)])
        st.configure("TButton", padding=6)
        st.configure("Go.TButton", font=("Helvetica", 14, "bold"), padding=12, background=ACCENT,
                     foreground="#1E1F24")
        st.map("Go.TButton", background=[("active", "#F2B65A"), ("disabled", "#6B5A3E")])
        st.configure("TEntry", fieldbackground="#FFFFFF", foreground="#111111")
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL)

    # ------------------------------------------------------------------ layout
    def _section(self, parent: tk.Widget, title: str) -> ttk.Frame:
        ttk.Label(parent, text=title, style="H.TLabel").pack(anchor="w", pady=(14, 4))
        f = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        f.pack(fill="x")
        f.columnconfigure(1, weight=1)
        return f

    def _file_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, kind: str,
                  hint: str = "") -> list[tk.Widget]:
        lab = ttk.Label(parent, text=label, style="Panel.TLabel", width=26)
        lab.grid(row=row, column=0, sticky="w", pady=3)
        ent = ttk.Entry(parent, textvariable=var)
        ent.grid(row=row, column=1, sticky="ew", padx=6)
        btn = ttk.Button(parent, text="Choisir…", command=lambda: self._pick(var, kind))
        btn.grid(row=row, column=2)
        widgets: list[tk.Widget] = [lab, ent, btn]
        if hint:
            h = ttk.Label(parent, text=hint, style="Muted.TLabel")
            h.grid(row=row + 1, column=1, sticky="w", padx=6)
            widgets.append(h)
        return widgets

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=(18, 12))
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Cutline — montage automatique", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Choisissez vos fichiers, puis cliquez sur « Monter ». "
                  "Le résultat est prêt à importer dans Avid Media Composer.",
                  foreground=MUTED).pack(anchor="w")

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        bottom = ttk.Frame(outer)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        canvas.pack(side="left", fill="both", expand=True, pady=(8, 0))
        scroll.pack(side="right", fill="y", pady=(8, 0))

        # 1. Type de montage
        f = self._section(body, "1. Que voulez-vous monter ?")
        for i, (key, label) in enumerate(MODE_LABELS.items()):
            ttk.Radiobutton(f, text=label, value=key, variable=self.mode,
                            command=self._on_mode).grid(row=i, column=0, columnspan=3, sticky="w", pady=1)
        self.mode_help = ttk.Label(f, text="", style="Muted.TLabel", wraplength=760, justify="left")
        self.mode_help.grid(row=len(MODE_LABELS), column=0, columnspan=3, sticky="w", pady=(6, 0))

        # 2. Fichiers
        f = self._section(body, "2. Vos fichiers")
        self._file_row(f, 0, "Dossier des rushes *", self.rushes, "dir",
                       "Le dossier qui contient vos vidéos (MXF, MOV, MP4…).")
        self.script_label = self._file_row(f, 2, "Scénario (conseillé)", self.script, "script",
                                           "Fichier texte ou Fountain (.txt, .fountain). Facultatif.")[0]
        self._file_row(f, 4, "Musique (facultatif)", self.music, "audio",
                       "Les coupes seront calées sur la musique.")
        self.prev_widgets = (self._file_row(f, 6, "Rushes épisode précédent *", self.prev_rushes, "dir")
                             + self._file_row(f, 7, "Scénario épisode précédent", self.prev_script, "script"))

        # 3. Réglages
        f = self._section(body, "3. Réglages")
        rows = [("Titre du programme", self.title_var, "Ex : Les Disparus de Brume"),
                ("Carton final", self.end_card, "Ex : Le 12 mars · Ce soir 21h10 · Prochainement"),
                ("Chaîne", self.channel, "Ex : France 2 (pour « À suivre »)"),
                ("Cartons intermédiaires", self.cards, "Séparés par « / ». Ex : CET HIVER / UNE VILLE SE TAIT")]
        for i, (lab, var, hint) in enumerate(rows):
            ttk.Label(f, text=lab, style="Panel.TLabel", width=26).grid(row=2 * i, column=0, sticky="w", pady=3)
            ttk.Entry(f, textvariable=var).grid(row=2 * i, column=1, columnspan=2, sticky="ew", padx=6)
            ttk.Label(f, text=hint, style="Muted.TLabel").grid(row=2 * i + 1, column=1, sticky="w", padx=6)
        r = 2 * len(rows)
        ttk.Label(f, text="Durée visée (secondes)", style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(f, from_=5, to=600, textvariable=self.duration, width=8).grid(row=r, column=1, sticky="w", padx=6)
        ttk.Label(f, text="Images par seconde", style="Panel.TLabel").grid(row=r + 1, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=self.rate, values=["25", "50", "24", "23.976", "29.97", "30"],
                     width=8, state="readonly").grid(row=r + 1, column=1, sticky="w", padx=6)
        ttk.Checkbutton(f, text="Reconnaître automatiquement les dialogues (plus lent, recommandé)",
                        variable=self.transcribe).grid(row=r + 2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(f, text="Créer aussi une version pour Avid (DNxHR HQ .mov)",
                        variable=self.avid).grid(row=r + 3, column=0, columnspan=3, sticky="w")

        # 3b. JT
        self.news_frame = self._section(body, "Sujet JT : voix off et intervenants")
        self.news_title = body.winfo_children()[-2]
        ttk.Label(self.news_frame, text="Texte de la voix off (une ligne vide entre chaque bloc)",
                  style="Panel.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self.vo_text = tk.Text(self.news_frame, height=6, wrap="word", font=("Helvetica", 11))
        self.vo_text.grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(self.news_frame, text="Intervenants (une ligne chacun : nom_du_fichier = Prénom Nom | Fonction)",
                  style="Panel.TLabel").grid(row=2, column=0, columnspan=3, sticky="w")
        self.sp_text = tk.Text(self.news_frame, height=3, wrap="none", font=("Helvetica", 11))
        self.sp_text.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
        self.sp_text.insert("1.0", "ITW_maire = Anne Dupont | Maire")

        # 4. Sortie
        f = self._section(body, "4. Où enregistrer le résultat ?")
        self.out_title = body.winfo_children()[-2]
        self._file_row(f, 0, "Dossier de sortie", self.out_dir, "outdir")

        # Bas : bouton, progression, journal
        self.go = ttk.Button(bottom, text="▶  MONTER", style="Go.TButton", command=self._start)
        self.go.pack(fill="x")
        self.bar = ttk.Progressbar(bottom, maximum=1.0, mode="determinate")
        self.bar.pack(fill="x", pady=6)
        self.logbox = tk.Text(bottom, height=7, bg=PANEL, fg=INK, relief="flat", font=("Courier", 10),
                              state="disabled", wrap="word")
        self.logbox.pack(fill="x")

    # ------------------------------------------------------------------ interactions
    def _pick(self, var: tk.StringVar, kind: str) -> None:
        if kind in ("dir", "outdir"):
            path = filedialog.askdirectory(title="Choisir un dossier")
        elif kind == "script":
            path = filedialog.askopenfilename(title="Choisir le scénario", filetypes=[
                ("Scénario", "*.fountain *.txt *.spmd"), ("Tous les fichiers", "*.*")])
        else:
            path = filedialog.askopenfilename(title="Choisir la musique", filetypes=[
                ("Audio", "*.wav *.mp3 *.aif *.aiff *.m4a *.flac"), ("Tous les fichiers", "*.*")])
        if path:
            var.set(path)

    def _on_mode(self) -> None:
        mode = self.mode.get()
        self.mode_help.configure(text=MODE_HELP[mode])
        self.duration.set(DEFAULT_DURATION[mode])
        for w in self.prev_widgets:
            if mode == "previously":
                w.grid()
            else:
                w.grid_remove()
        self.script_label.configure(text="Scénario épisode à venir" if mode == "previously"
                                    else "Scénario (conseillé)")
        if mode == "news":
            self.news_title.pack(anchor="w", pady=(14, 4), before=self.out_title)
            self.news_frame.pack(fill="x", before=self.out_title)
        else:
            self.news_title.pack_forget()
            self.news_frame.pack_forget()

    def log(self, msg: str) -> None:
        self.q.put(("log", msg))

    def _write_log(self, msg: str) -> None:
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _params(self) -> JobParams:
        return JobParams(
            mode=self.mode.get(), rushes=self.rushes.get().strip(), output_dir=self.out_dir.get().strip(),
            script=self.script.get().strip(), music=self.music.get().strip(),
            previous_rushes=self.prev_rushes.get().strip(), previous_script=self.prev_script.get().strip(),
            title=self.title_var.get().strip(), end_card=self.end_card.get().strip(),
            channel=self.channel.get().strip(), duration=float(self.duration.get() or 0),
            rate=self.rate.get(), voiceover=self.vo_text.get("1.0", "end"),
            speakers=self.sp_text.get("1.0", "end"),
            cards=[c.strip() for c in self.cards.get().split("/") if c.strip()],
            transcribe=self.transcribe.get(), avid_version=self.avid.get())

    def _start(self) -> None:
        if self.running:
            return
        params = self._params()
        if not params.rushes:
            messagebox.showwarning("Cutline", "Choisissez d'abord le dossier de vos rushes.")
            return
        self.running = True
        self.go.configure(state="disabled", text="Montage en cours…")
        self.bar["value"] = 0
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

        def work() -> None:
            try:
                files = run_job(params, log=self.log, progress=lambda x: self.q.put(("progress", x)))
                self.q.put(("done", files))
            except Exception as exc:  # noqa: BLE001 — on affiche toute erreur à l'utilisateur
                self.q.put(("error", (str(exc), traceback.format_exc())))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._write_log(data)
                elif kind == "progress":
                    self.bar["value"] = data
                elif kind == "done":
                    self._finish()
                    out = Path(self.out_dir.get())
                    names = "\n".join(f"• {p.name}" for p in data.values())
                    messagebox.showinfo("Montage terminé",
                                        f"Les fichiers ont été créés dans :\n{out}\n\n{names}\n\n"
                                        "Pour Avid, lisez « LISEZMOI_Avid.txt ».")
                    open_folder(out)
                elif kind == "error":
                    self._finish()
                    msg, tb = data
                    self._write_log("ERREUR : " + msg)
                    self._write_log(tb)
                    messagebox.showerror("Cutline", f"Le montage n'a pas pu être réalisé :\n\n{msg}")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _finish(self) -> None:
        self.running = False
        self.go.configure(state="normal", text="▶  MONTER")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
