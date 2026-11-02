"""CPU streaming recognition with per-source state and conservative turn endings."""
from pathlib import Path

import numpy as np


def create_recognizer(model_path, threads):
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise RuntimeError("Die lokale Streaming-Erkennung fehlt. requirements-streaming.txt installieren.") from exc
    root = Path(model_path)
    required = ("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx")
    if not model_path or not all((root / name).is_file() for name in required):
        raise ValueError("Bitte den vollstaendigen Ordner des lokalen Nemotron-Streaming-Modells auswaehlen.")
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(root / "tokens.txt"), encoder=str(root / "encoder.int8.onnx"),
        decoder=str(root / "decoder.int8.onnx"), joiner=str(root / "joiner.int8.onnx"),
        num_threads=threads, feature_dim=128, sample_rate=16000, provider="cpu",
        enable_endpoint_detection=True, rule1_min_trailing_silence=2.4,
        rule2_min_trailing_silence=1.0, rule3_min_utterance_length=180,
    )


class ContinuousTranscript:
    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.position = 0
        self.last_voice = -3
        self.last_signal = -3
        self.previous_words = []
        self.stable = ""
        self.stream = recognizer.create_stream()
        self.stream.set_option("language", "de")
        # Prime the acoustic cache before the first actual word. This also
        # supplies the leading context after a deliberate utterance reset.
        self.stream.accept_waveform(16000, np.zeros(12800, dtype=np.float32))
        self._decode()
        self.last_text = self.recognizer.get_result(self.stream).strip()

    def _decode(self):
        decoded = False
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
            decoded = True
        return decoded

    def accept(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        self.position += len(samples) / 16000
        signal = bool(np.any(samples))
        if signal:
            self.last_signal = self.position
        elif self.position - self.last_signal >= 3 and not self.last_text:
            # A muted virtual cable can otherwise spend as much encoder time as
            # a second speaker. Skip only exact digital zero, never a quiet
            # nonzero sample. Keep a full tail after any signal so pending words
            # can finish, and never skip silence while text awaits an endpoint.
            return {"partial": "", "stable": "", "final": None}
        if len(samples) and float(np.sqrt(np.mean(samples * samples))) >= .008:
            self.last_voice = self.position
        self.stream.accept_waveform(16000, samples)
        decoded = self._decode()
        text = self.recognizer.get_result(self.stream).strip()
        self.last_text = text
        if decoded:
            words = text.split()
            count = 0
            for before, after in zip(self.previous_words, words):
                if before != after:
                    break
                count += 1
            # A last subword may still grow (e.g. "Fort" -> "Fortnite").
            if count == len(words) and words and not words[-1].endswith((".", "!", "?")):
                count -= 1
            self.stable = " ".join(words[:max(0, count)])
            self.previous_words = words
        # Endpoint detection alone can fire before a new word has finished.
        # Require actual quiet audio too, instead of cutting an active word.
        final = text if text and self.recognizer.is_endpoint(self.stream) and self.position - self.last_voice >= 1.2 else None
        return {"partial": text, "stable": self.stable, "final": final}
