"""Point d'entrée de l'application Windows (Cutline.exe).

`Cutline.exe --selftest <journal.txt>` : vérification automatique utilisée lors de la fabrication
de l'installateur (génère des rushes de démonstration, monte un teaser, contrôle les fichiers).
"""

import multiprocessing
import os
import sys


def _quiet_streams() -> None:
    # Application sans console : certaines bibliothèques écrivent sur stdout/stderr inexistants
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def selftest(log_path: str) -> int:
    import tempfile
    import traceback
    from pathlib import Path

    lines = []
    try:
        from cutline.app_runner import JobParams, run_job
        from cutline.core.media import ffmpeg_bin, ffprobe_bin
        from cutline.demo import generate
        lines += [f"ffmpeg: {ffmpeg_bin()}", f"ffprobe: {ffprobe_bin()}"]
        try:
            import faster_whisper  # noqa: F401
            from cutline.analysis.transcript import resolve_model
            from faster_whisper import WhisperModel
            WhisperModel(resolve_model("small"), device="cpu", compute_type="int8")  # charge les DLL
            lines.append(f"whisper: OK, modèle = {resolve_model('small')}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"whisper: INDISPONIBLE ({exc!r})")
            raise
        with tempfile.TemporaryDirectory() as tmp:
            rushes = generate(Path(tmp) / "rushes", takes=1)
            files = run_job(JobParams(mode="teaser", rushes=str(rushes), output_dir=str(Path(tmp) / "out"),
                                      script=str(Path(tmp) / "rushes_scenario.fountain"), title="Selftest",
                                      duration=12, transcribe=False, avid_version=True, width=640, height=360),
                            log=lines.append)
            ok = all(p.exists() and p.stat().st_size > 0 for p in files.values())
        lines.append("SELFTEST OK" if ok else "SELFTEST ÉCHEC : fichiers manquants")
        code = 0 if ok else 1
    except Exception:  # noqa: BLE001
        lines.append(traceback.format_exc())
        lines.append("SELFTEST ÉCHEC")
        code = 1
    Path(log_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _quiet_streams()
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    from cutline.gui import main

    main()
