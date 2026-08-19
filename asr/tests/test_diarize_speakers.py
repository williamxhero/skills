from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "diarize_speakers.py"
SPEC = importlib.util.spec_from_file_location("diarize_speakers", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MIRROR = "https://hf-mirror.com"
OFFICIAL = "https://huggingface.co"


class FakeSegment:
    start = 0.0
    end = 1.0


class FakeAnnotation:
    def itertracks(self, yield_label: bool):
        assert yield_label
        yield FakeSegment(), None, "SPEAKER_00"


def args() -> argparse.Namespace:
    return argparse.Namespace(
        engine="auto",
        pyannote_model="fake-pyannote",
        pyannote_hf_endpoint=OFFICIAL,
        no_pyannote_fallback=False,
        num_speakers=1,
        min_speakers=1,
        max_speakers=2,
        ecapa_model="fake-ecapa",
        ecapa_hf_endpoint=OFFICIAL,
        allow_ecapa_hf_token=False,
        window_seconds=1.5,
        hop_seconds=0.75,
        min_window_seconds=0.75,
        embedding_batch_size=1,
        vad_threshold=0.5,
        vad_min_speech_ms=180,
        vad_min_silence_ms=180,
        force=True,
    )


class PyannoteEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("HF_ENDPOINT")
        os.environ["HF_ENDPOINT"] = MIRROR

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("HF_ENDPOINT", None)
        else:
            os.environ["HF_ENDPOINT"] = self.previous

    def fake_modules(self, fail: bool = False):
        observations: list[tuple[str, str | None]] = []

        class FakePipelineInstance:
            def to(self, device):
                observations.append(("to", os.environ.get("HF_ENDPOINT")))

            def __call__(self, source, **kwargs):
                observations.append(("call", os.environ.get("HF_ENDPOINT")))
                return types.SimpleNamespace(
                    exclusive_speaker_diarization=FakeAnnotation()
                )

        class FakePipeline:
            @classmethod
            def from_pretrained(cls, model, token=None):
                observations.append(
                    ("from_pretrained", os.environ.get("HF_ENDPOINT"))
                )
                if fail:
                    raise RuntimeError("fake model failure")
                return FakePipelineInstance()

        fake_audio = types.ModuleType("pyannote.audio")
        fake_audio.Pipeline = FakePipeline
        fake_package = types.ModuleType("pyannote")
        fake_package.audio = fake_audio
        fake_torch = types.ModuleType("torch")
        fake_torch.device = lambda value: value
        return observations, {
            "pyannote": fake_package,
            "pyannote.audio": fake_audio,
            "torch": fake_torch,
        }

    def test_context_restores_mirror_after_normal_and_exceptional_exit(self):
        with MODULE.pyannote_hf_environment(OFFICIAL):
            self.assertEqual(os.environ["HF_ENDPOINT"], OFFICIAL)
        self.assertEqual(os.environ["HF_ENDPOINT"], MIRROR)

        os.environ.pop("HF_ENDPOINT")
        with MODULE.pyannote_hf_environment(OFFICIAL):
            self.assertEqual(os.environ["HF_ENDPOINT"], OFFICIAL)
        self.assertNotIn("HF_ENDPOINT", os.environ)
        os.environ["HF_ENDPOINT"] = MIRROR

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with MODULE.pyannote_hf_environment(OFFICIAL):
                self.assertEqual(os.environ["HF_ENDPOINT"], OFFICIAL)
                raise RuntimeError("boom")
        self.assertEqual(os.environ["HF_ENDPOINT"], MIRROR)

    def test_pyannote_load_and_inference_use_official_then_restore(self):
        observations, modules = self.fake_modules()
        with mock.patch.dict(sys.modules, modules):
            turns, _, _ = MODULE.diarize_pyannote(Path("fake.m4a"), args(), "cpu")
        self.assertEqual(turns[0]["speaker"], "SPEAKER_00")
        self.assertEqual(
            observations,
            [
                ("from_pretrained", OFFICIAL),
                ("to", OFFICIAL),
                ("call", OFFICIAL),
            ],
        )
        self.assertEqual(os.environ["HF_ENDPOINT"], MIRROR)

    def test_pyannote_failure_falls_back_and_restores_mirror(self):
        observations, modules = self.fake_modules(fail=True)
        fallback_endpoints: list[str | None] = []
        fake_turns = [
            {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "evidence": []}
        ]

        def fake_ecapa(*unused):
            fallback_endpoints.append(os.environ.get("HF_ENDPOINT"))
            return fake_turns, {}, {"selected_speaker_count": 1}

        with tempfile.TemporaryDirectory() as temp_name:
            source = Path(temp_name) / "fake.m4a"
            source.write_bytes(b"not real media")
            with (
                mock.patch.dict(sys.modules, modules),
                mock.patch.object(MODULE, "probe_duration", return_value=1.0),
                mock.patch.object(MODULE, "decode_wav"),
                mock.patch.object(
                    MODULE,
                    "diarize_ecapa",
                    side_effect=fake_ecapa,
                ),
            ):
                payload = MODULE.process_one(
                    source, None, Path(temp_name) / "out.json", args(), "cpu"
                )
        self.assertEqual(observations, [("from_pretrained", OFFICIAL)])
        self.assertEqual(fallback_endpoints, [MIRROR])
        self.assertEqual(payload["engine"]["selected"], "ecapa")
        self.assertEqual(payload["engine"]["pyannote_hf_endpoint_used"], OFFICIAL)
        self.assertIn("fake model failure", payload["engine"]["pyannote_fallback_reason"])
        self.assertEqual(os.environ["HF_ENDPOINT"], MIRROR)


if __name__ == "__main__":
    unittest.main()
