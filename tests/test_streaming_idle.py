"""A muted second source must not consume continuous encoder time or cut speech."""
import numpy as np

from companion.streaming_asr import ContinuousTranscript


class Stream:
    def __init__(self):
        self.ready = False
        self.received = []

    def set_option(self, *_):
        pass

    def accept_waveform(self, rate, samples):
        self.received.append(np.asarray(samples).copy())
        self.ready = True


class Recognizer:
    def __init__(self):
        self.decode_calls = 0
        self.text = ""
        self.endpoint = False

    def create_stream(self):
        return Stream()

    def is_ready(self, stream):
        return stream.ready

    def decode_stream(self, stream):
        self.decode_calls += 1
        stream.ready = False

    def get_result(self, stream):
        return self.text

    def is_endpoint(self, stream):
        return self.endpoint


def test_muted_source_avoids_encoder_work_after_initial_priming():
    recognizer = Recognizer()
    session = ContinuousTranscript(recognizer)
    priming_calls = recognizer.decode_calls
    for _ in range(600):
        result = session.accept(np.zeros(1600, dtype=np.float32))
        assert result == {"partial": "", "stable": "", "final": None}
    assert recognizer.decode_calls == priming_calls
    assert len(session.stream.received) == 1


def test_even_tiny_nonzero_speech_reaches_encoder_after_long_idle():
    recognizer = Recognizer()
    session = ContinuousTranscript(recognizer)
    for _ in range(100):
        session.accept(np.zeros(1600, dtype=np.float32))
    quiet = np.ones(1600, dtype=np.float32) * 1e-8
    recognizer.text = "leise gesprochen"
    result = session.accept(quiet)
    np.testing.assert_array_equal(session.stream.received[-1], quiet)
    assert result["partial"] == "leise gesprochen"
    assert recognizer.decode_calls == 2


def test_quiet_tail_is_preserved_even_before_first_word_appears():
    recognizer = Recognizer()
    session = ContinuousTranscript(recognizer)
    session.accept(np.ones(1600, dtype=np.float32) * .1)
    count = len(session.stream.received)
    for _ in range(25):
        session.accept(np.zeros(1600, dtype=np.float32))
    assert len(session.stream.received) == count + 25
    for _ in range(40):
        session.accept(np.zeros(1600, dtype=np.float32))
    assert len(session.stream.received) < count + 65


def test_pending_word_is_not_cut_by_idle_optimization():
    recognizer = Recognizer()
    session = ContinuousTranscript(recognizer)
    recognizer.text = "Zusammenhang bleibt erhalten."
    session.accept(np.ones(1600, dtype=np.float32) * .1)
    count = len(session.stream.received)
    for _ in range(50):
        result = session.accept(np.zeros(1600, dtype=np.float32))
        assert result["final"] is None
    assert len(session.stream.received) == count + 50
    recognizer.endpoint = True
    assert session.accept(np.zeros(1600, dtype=np.float32))["final"] == recognizer.text
