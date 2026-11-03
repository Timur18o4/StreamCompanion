"""Short-lived Piper worker: bounded CPU use and cancellable native synthesis."""
import json
from pathlib import Path
import sys
import wave
import time


def main():
    import onnxruntime as ort
    from piper import PiperVoice, SynthesisConfig
    from piper.config import PiperConfig

    voice, loaded = None, None
    def render(payload):
        nonlocal voice, loaded
        model = Path(payload["model"])
        if loaded != str(model):
            config = PiperConfig.from_dict(json.loads(Path(str(model) + ".json").read_text(encoding="utf-8")))
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            voice = PiperVoice(config=config, session=ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"]))
            loaded = str(model)
        with wave.open(payload["output"], "wb") as output:
            voice.synthesize_wav(payload["text"], output, syn_config=SynthesisConfig(length_scale=1 / payload["rate"]))

    if sys.argv[1] == "--server":
        folder = Path(sys.argv[2])
        last_job = time.monotonic()
        while folder.exists() and time.monotonic() - last_job < 300:
            request = folder / "request.json"
            if not request.exists():
                time.sleep(.03)
                continue
            try:
                payload = json.loads(request.read_text(encoding="utf-8"))
                request.unlink()
                render(payload)
                result = {"ok": True}
            except Exception:
                result = {"ok": False}
            temporary = folder / "response.tmp"
            temporary.write_text(json.dumps(result), encoding="utf-8")
            temporary.replace(folder / "response.json")
            last_job = time.monotonic()
    else:
        render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
