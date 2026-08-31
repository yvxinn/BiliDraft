from types import SimpleNamespace
import pytest
from bilidraft import transcribe, media


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": 0, "start": 0, "end": 0, "text": "zero"}],
        [{"id": 0, "start": 2, "end": 1, "text": "reverse"}],
        [{"id": 0, "start": 0, "end": 1, "text": "a"}, {"id": 0, "start": 1, "end": 2, "text": "duplicate"}],
        [{"id": 0, "start": float("nan"), "end": 1, "text": "nan"}],
        [{"id": 0, "start": 1, "end": 2, "text": ""}],
    ],
)
def test_invalid_transcript_has_no_fake_timeline(rows):
    with pytest.raises(ValueError):
        transcribe.validate_transcript({"segments": rows})


def test_vad_milliseconds_are_preserved():
    tr = transcribe.sensevoice_result(
        [
            {
                "sentence_info": [
                    {"start": 1500, "end": 4200, "text": "<|zh|>你好"},
                    {"start": 5000, "end": 9000, "text": "完整句子"},
                ]
            }
        ]
    )
    assert tr["segments"][0] == {"id": 0, "start": 1.5, "end": 4.2, "text": "你好"}
    assert tr["segments"][1]["start"] == 5


def test_whole_transcription_without_timestamps_is_rejected():
    with pytest.raises(RuntimeError, match="时间戳"):
        transcribe.sensevoice_result([{"text": "整段，没有时间戳"}])


def test_no_subtitle_extracts_full_video_not_old_clip(cfg, tmp_path, monkeypatch, transcript):
    old = tmp_path / "audio_10min.wav"
    old.write_bytes(b"old")
    used = []

    def extract(video, target, config):
        used.append((video, target))
        return target

    monkeypatch.setattr(media, "extract_audio", extract)
    monkeypatch.setattr(transcribe, "_sensevoice", lambda *a: transcript)
    client = SimpleNamespace(fetch_subtitle=lambda *a: None)
    result = transcribe.get_transcript(client, "BV1234567890", 1, tmp_path, cfg, tmp_path / "video.mp4")
    assert result == transcript
    assert used == [(tmp_path / "video.mp4", tmp_path / "audio.wav")]


def test_available_subtitle_never_calls_asr(cfg, tmp_path, monkeypatch, transcript):
    monkeypatch.setattr(transcribe, "_sensevoice", lambda *a: pytest.fail("unnecessary ASR"))
    client = SimpleNamespace(fetch_subtitle=lambda *a: transcript)
    assert transcribe.get_transcript(client, "BV1234567890", 1, tmp_path, cfg) == transcript


def test_download_format_has_audio_and_ignores_user_global_config(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "find_ffmpeg", lambda *a: "/tools/ffmpeg")
    calls = []

    def execute(cmd, **kwargs):
        calls.append(cmd)
        (tmp_path / "video.mp4").write_bytes(b"video")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(media.subprocess, "run", execute)
    assert media.download_video("BV1234567890", tmp_path, cfg) == tmp_path / "video.mp4"
    cmd = calls[0]
    assert "--ignore-config" in cmd and "--remux-video" in cmd
    assert "+ba/" in cmd[cmd.index("-f") + 1]
    assert "video-only" not in " ".join(cmd)


def test_custom_ffmpeg_must_exist(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda *a: None)
    with pytest.raises(RuntimeError):
        media.find_ffmpeg("/missing/ffmpeg")
