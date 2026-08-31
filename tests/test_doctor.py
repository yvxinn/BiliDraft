from types import SimpleNamespace

import pytest
from bilidraft import doctor


@pytest.mark.parametrize("duplicate,missing_api", [(True, False), (False, True), (False, False)])
def test_doctor_detects_opencv_installation_failures(cfg, monkeypatch, duplicate, missing_api):
    installed = {"opencv-python": "4.14.0"}
    if duplicate:
        installed["opencv-python-headless"] = "4.14.0"

    def version(name):
        if name not in installed:
            raise doctor.PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(doctor, "version", version)
    monkeypatch.setattr(doctor, "find_ffmpeg", lambda *a: "ffmpeg")
    cv2 = SimpleNamespace(VideoCapture=object, imdecode=object)
    if not missing_api:
        cv2.CascadeClassifier = object
    monkeypatch.setattr(doctor.importlib, "import_module", lambda *a: cv2)
    result = doctor.diagnose(cfg, offline=True)
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["opencv_distribution"]["ok"] is not duplicate
    assert checks["opencv_runtime"]["ok"] is not missing_api


@pytest.mark.parametrize("mode,required", [("representative", False), ("none", False), ("detailed", True)])
def test_ocr_dependency_only_required_when_used(cfg, monkeypatch, mode, required):
    cfg.slides.mode = mode
    original = doctor.importlib.util.find_spec
    monkeypatch.setattr(
        doctor.importlib.util,
        "find_spec",
        lambda name: None if name == "rapidocr_onnxruntime" else original(name),
    )
    monkeypatch.setattr(doctor, "find_ffmpeg", lambda *a: "ffmpeg")
    checks = {c["name"]: c for c in doctor.diagnose(cfg, offline=True)["checks"]}
    assert checks["ocr"]["required"] is required
    assert not checks["ocr"]["ok"]
