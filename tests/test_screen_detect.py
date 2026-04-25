from __future__ import annotations

from pathlib import Path

from quorum.signals.screen import ScreenDetectSignal, _parse_json
from quorum.signals.base import SignalContext


class TestParseJson:
    def test_valid_json(self) -> None:
        result = _parse_json('{"category": "camera", "confidence": 0.9}')
        assert result is not None
        assert result["category"] == "camera"

    def test_json_with_surrounding_text(self) -> None:
        result = _parse_json('Here is the result: {"category": "gaming", "confidence": 0.8} done')
        assert result is not None
        assert result["category"] == "gaming"

    def test_invalid_json(self) -> None:
        assert _parse_json("no json here") is None

    def test_empty_string(self) -> None:
        assert _parse_json("") is None


class TestScreenDetectSignal:
    def test_no_ollama_returns_empty(self) -> None:
        signal = ScreenDetectSignal(ollama_client=None)
        ctx = SignalContext(video=Path("/test.mkv"), keyframes=[Path("/frame.jpg")])
        result = signal.run(ctx)
        assert result == []

    def test_no_keyframes_returns_empty(self) -> None:
        signal = ScreenDetectSignal(ollama_client="fake")
        ctx = SignalContext(video=Path("/test.mkv"), keyframes=[])
        result = signal.run(ctx)
        assert result == []

    def test_signal_name(self) -> None:
        signal = ScreenDetectSignal()
        assert signal.name == "screen_detect"

    def test_with_mock_ollama(self) -> None:
        class MockOllama:
            def generate(self, model, prompt, images=None):
                return '{"category": "screen_recording", "confidence": 0.85, "reasoning": "UI elements visible"}'

        signal = ScreenDetectSignal(ollama_client=MockOllama())
        ctx = SignalContext(video=Path("/test.mkv"), keyframes=[Path("/frame.jpg")])
        result = signal.run(ctx)
        assert len(result) == 1
        assert result[0].title == "screen_recording"
        assert result[0].confidence == 0.85
        assert result[0].source == "screen_detect"

    def test_handles_ollama_error(self) -> None:
        class FailingOllama:
            def generate(self, model, prompt, images=None):
                raise RuntimeError("connection refused")

        signal = ScreenDetectSignal(ollama_client=FailingOllama())
        ctx = SignalContext(video=Path("/test.mkv"), keyframes=[Path("/frame.jpg")])
        result = signal.run(ctx)
        assert result == []
