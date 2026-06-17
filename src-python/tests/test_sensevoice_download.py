import json
import os
import shutil
import sys
import tempfile
import types
import unittest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.modules.setdefault(
    "numpy",
    types.SimpleNamespace(ndarray=object, float32=float, max=max, abs=abs),
)
sys.modules.setdefault("requests", types.SimpleNamespace())

import models.transcription.transcription_sensevoice as sensevoice


class FakeHuggingFaceHub:
    def __init__(self):
        self.calls = 0

    def snapshot_download(self, repo_id, local_dir, allow_patterns, local_dir_use_symlinks=False):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary network failure")
        os.makedirs(local_dir, exist_ok=True)
        for filename in allow_patterns:
            with open(os.path.join(local_dir, filename), "w", encoding="utf-8") as f:
                f.write("ok")


class SenseVoiceDownloadTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.original_hub = sensevoice.huggingface_hub
        self.original_hf_available = sensevoice._HF_AVAILABLE

    def tearDown(self):
        sensevoice.huggingface_hub = self.original_hub
        sensevoice._HF_AVAILABLE = self.original_hf_available
        shutil.rmtree(self.root, ignore_errors=True)

    def test_download_retries_transient_failure_and_marks_valid_model(self):
        fake_hub = FakeHuggingFaceHub()
        sensevoice.huggingface_hub = fake_hub
        sensevoice._HF_AVAILABLE = True
        progress = []
        finished = []

        result = sensevoice.downloadSenseVoiceWeight(
            self.root,
            "sensevoice-small-int8",
            callback=progress.append,
            end_callback=lambda: finished.append(True),
        )

        self.assertTrue(result)
        self.assertEqual(fake_hub.calls, 2)
        self.assertEqual(finished, [True])
        self.assertTrue(sensevoice.checkSenseVoiceWeight(self.root, "sensevoice-small-int8"))
        self.assertIn(1.0, progress)

        marker_path = os.path.join(
            self.root,
            "weights",
            "sensevoice",
            "sensevoice-small-int8",
            "downloaded.json",
        )
        with open(marker_path, encoding="utf-8") as f:
            marker = json.load(f)
        self.assertEqual(marker["backend"], "sherpa-onnx")


if __name__ == "__main__":
    unittest.main()
