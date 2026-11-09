import shutil

import pytest

from cutline.app_runner import JobParams, parse_speakers, run_job
from cutline.demo import generate

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg requis")


def test_parse_speakers():
    assert parse_speakers("ITW_maire = Anne Dupont | Maire\n\nmauvaise ligne") == {"ITW_maire": "Anne Dupont | Maire"}


def test_run_job_creates_avid_ready_files(tmp_path):
    rushes = generate(tmp_path / "rushes", takes=1)
    logs = []
    files = run_job(JobParams(mode="teaser", rushes=str(rushes), output_dir=str(tmp_path / "out"),
                              script=str(tmp_path / "rushes_scenario.fountain"),
                              music=str(tmp_path / "rushes_musique.wav"), title="Brume",
                              duration=15, transcribe=False, avid_version=True, width=640, height=360),
                    log=logs.append)
    assert {"video", "avid", "edl", "fcpxml", "readme"} <= set(files)
    assert all(p.exists() and p.stat().st_size > 0 for p in files.values())
    assert "Avid" in files["readme"].read_text(encoding="utf-8")
    assert logs[-1] == "Terminé !"


def test_run_job_errors_are_readable(tmp_path):
    with pytest.raises(ValueError, match="rushes"):
        run_job(JobParams(mode="trailer", rushes="", output_dir=str(tmp_path)))
