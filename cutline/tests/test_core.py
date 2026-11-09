import pytest

from cutline.core.editing import (EditError, extract, insert, lift, overwrite, razor, ripple_trim,
                                  roll_trim, slide, slip)
from cutline.core.model import Clip, Project, Timeline
from cutline.core.timecode import FrameRate, frames_to_tc, tc_to_frames


def test_timecode_25():
    r = FrameRate.parse("25")
    assert frames_to_tc(25 * 3600 + 26, r) == "01:00:01:01"
    assert tc_to_frames("10:00:00:00", r) == 900000


@pytest.mark.parametrize("frames", [0, 1, 1799, 1800, 17982, 17983, 107892, 863999])
def test_drop_frame_roundtrip(frames):
    r = FrameRate.parse(29.97)
    assert r.drop_frame_capable
    tc = frames_to_tc(frames, r)
    assert ";" in tc
    assert tc_to_frames(tc, r) == frames


def test_drop_frame_skips_labels():
    r = FrameRate.parse("30000/1001")
    assert frames_to_tc(1800, r) == "00:01:00;02"
    assert frames_to_tc(17982, r) == "00:10:00;00"


def _tl():
    tl = Timeline.standard("t", video_tracks=1, audio_tracks=1)
    for i in range(3):
        for t in tl.tracks:
            t.clips.append(Clip(record_in=i * 100, duration=100, asset_id="a", source_in=1000 + i * 200,
                                name=f"c{i}"))
    return tl


def test_razor_and_lift():
    tl = _tl()
    v = tl.track("V1")
    head, tail = razor(v, 150)
    assert head.duration == 50 and tail.record_in == 150 and tail.source_in == 1250
    lift(v, 0, 50)
    assert v.clips[0].record_in == 50 and tl.duration == 300


def test_insert_ripples_all_tracks():
    tl = _tl()
    insert(tl, tl.track("V1"), Clip(0, 40, asset_id="b"), at=150)
    assert tl.duration == 340
    assert [c.record_in for c in tl.track("A1").clips] == [0, 100, 190, 240]


def test_overwrite_keeps_duration():
    tl = _tl()
    overwrite(tl.track("V1"), Clip(0, 60, asset_id="b"), at=70)
    v = tl.track("V1")
    assert tl.duration == 300
    assert [(c.record_in, c.duration) for c in v.clips] == [(0, 70), (70, 60), (130, 70), (200, 100)]
    assert v.clips[2].source_in == 1230


def test_extract_closes_gap():
    tl = _tl()
    extract(tl, 100, 200)
    assert tl.duration == 200
    assert [c.name for c in tl.track("V1").clips] == ["c0", "c2"]


def test_ripple_roll_slip_slide():
    tl = _tl()
    v = tl.track("V1")
    ripple_trim(tl, v, v.clips[0], "tail", -20)
    assert v.clips[0].duration == 80 and v.clips[1].record_in == 80 and tl.track("A1").clips[1].record_in == 80
    ripple_trim(tl, v, v.clips[0], "head", 10)
    assert v.clips[0].source_in == 990 and v.clips[1].record_in == 90
    roll_trim(v, v.clips[0], v.clips[1], 5)
    assert v.clips[0].duration == 95 and v.clips[1].record_in == 95 and v.clips[1].source_in == 1205
    slip(v.clips[1], 30)
    assert v.clips[1].source_in == 1235 and v.clips[1].record_in == 95
    before = tl.track("V1").end
    slide(v, v.clips[1], 10)
    assert v.clips[1].record_in == 105 and v.clips[0].duration == 105 and v.clips[2].duration == 90
    assert tl.track("V1").end == before
    with pytest.raises(EditError):
        roll_trim(v, v.clips[0], v.clips[2], 1)


def test_locked_track():
    tl = _tl()
    tl.track("V1").locked = True
    with pytest.raises(EditError):
        razor(tl.track("V1"), 50)


def test_project_roundtrip(tmp_path):
    p = Project("x")
    p.add_timeline(_tl())
    p.save(tmp_path / "p.cutline")
    q = Project.load(tmp_path / "p.cutline")
    tl = next(iter(q.timelines.values()))
    assert tl.duration == 300 and tl.track("A1").clips[2].source_in == 1400
