"""Push-to-talk voice loop (upgrade roadmap "Next" phase): local STT
(faster-whisper) + local TTS (Piper), both fully on-device on mini - no
audio ever leaves the machine, only the already-transcribed text does
(through the same /query path, same assert_clean() redaction gate, as
typed questions). Deliberately push-to-talk, not always-listening/wake-
word: this app's whole privacy model is built around explicit,
user-initiated capture (per-Activity opt-in tracks, pause flag, no
ambient recording), and an always-on microphone would be a real
philosophy mismatch, not just a UX preference.

Voice-in implies voice-out: an answer synthesized from a voice-triggered
question gets spoken aloud via TTS; a typed question's answer does not -
matches ordinary voice-assistant UX (ask by voice, hear an answer) without
making every ordinary text interaction noisy.

Runs only on mini (needs real audio hardware - confirmed live 2026-09-10:
a working mic input and speaker outputs both present via PipeWire/ALSA).
Models are downloaded at install time (scripts/install_avatar.sh), not
vendored in git - see that script for the exact commands.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from PySide6.QtCore import QObject, Signal, Slot

WHISPER_SAMPLE_RATE = 16000  # what faster-whisper's models are trained on
# The mic's ALSA hardware path (via PortAudio) rejects 16kHz outright on
# this machine (PaErrorCode -9997 "Invalid sample rate", confirmed live
# 2026-09-10 running inside the actual systemd --user service context, not
# just an SSH shell) - PipeWire's usual resampling isn't in this path.
# Record at the device's own native rate instead and resample to 16kHz in
# Python before handing audio to whisper. Same story for playback (Piper
# outputs 22050Hz for the lessac-medium voice) - confirmed live that
# 48000 is accepted for both input and output on this hardware/hostapi
# combination, so standardize on it for the mic and resample TTS to it too.
RECORD_SAMPLE_RATE = 44100
PLAYBACK_SAMPLE_RATE = 48000
VOICE_MODELS_DIR = Path.home() / ".local" / "share" / "screen-context-assistant" / "voice_models"
PIPER_VOICE_PATH = VOICE_MODELS_DIR / "en_US-lessac-medium.onnx"


def _resample_linear(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Plain numpy linear-interpolation resampling - not audiophile-grade,
    but whisper is robust to minor resampling artifacts and this avoids
    pulling in scipy just for this one downsample."""
    if from_rate == to_rate or len(audio) == 0:
        return audio
    duration = len(audio) / from_rate
    old_x = np.linspace(0, duration, num=len(audio), endpoint=False)
    new_len = int(round(duration * to_rate))
    new_x = np.linspace(0, duration, num=new_len, endpoint=False)
    return np.interp(new_x, old_x, audio).astype(np.float32)


def _strip_markdown(text: str) -> str:
    """Answers are markdown-formatted for the panel (see MARKDOWN_INSTRUCTION
    in rag_service.py) - TTS would otherwise read '**bold**' as literal
    asterisks. Not a full markdown parser, just the syntax that actually
    shows up in these answers."""
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)  # code blocks
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]*)\*", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.strip()


class VoiceEngine(QObject):
    transcriptionReady = Signal(str)
    transcriptionFailed = Signal(str)
    recordingChanged = Signal(bool)
    speakingChanged = Signal(bool)

    def __init__(self, whisper_model_size: str = "base.en", parent=None):
        super().__init__(parent)
        self._whisper_model_size = whisper_model_size
        self._whisper: WhisperModel | None = None
        self._piper = None  # lazy PiperVoice, loaded on the same warm-up thread
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        self._recording = False
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _warm_up(self) -> None:
        """Load both models once at startup, off the Qt thread, so the
        first real use isn't stuck waiting on a multi-second model load."""
        try:
            self._whisper = WhisperModel(self._whisper_model_size, device="cpu", compute_type="int8")
        except Exception:  # noqa: BLE001
            pass  # transcribe() below handles _whisper still being None
        try:
            from piper import PiperVoice
            if PIPER_VOICE_PATH.exists():
                self._piper = PiperVoice.load(str(PIPER_VOICE_PATH))
        except Exception:  # noqa: BLE001
            pass  # speak() below handles _piper still being None

    @Slot()
    def startRecording(self) -> None:
        if self._recording:
            return
        self._frames = []
        self._recording = True
        self.recordingChanged.emit(True)

        def _callback(indata, frame_count, time_info, status):  # noqa: ARG001
            self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=RECORD_SAMPLE_RATE, channels=1, dtype="float32", callback=_callback,
        )
        self._stream.start()

    @Slot()
    def stopRecording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self.recordingChanged.emit(False)
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            self.transcriptionFailed.emit("No audio captured.")
            return
        audio = np.concatenate(self._frames, axis=0).flatten()
        audio = _resample_linear(audio, RECORD_SAMPLE_RATE, WHISPER_SAMPLE_RATE)
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio: np.ndarray) -> None:
        if self._whisper is None:
            self.transcriptionFailed.emit("Speech model still loading, try again in a moment.")
            return
        try:
            segments, _info = self._whisper.transcribe(audio, language="en")
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:  # noqa: BLE001
            self.transcriptionFailed.emit(str(exc))
            return
        if not text:
            self.transcriptionFailed.emit("Didn't catch that - no speech detected.")
            return
        self.transcriptionReady.emit(text)

    @Slot(str)
    def speak(self, text: str) -> None:
        text = _strip_markdown(text)
        if not text:
            return
        threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    @Slot()
    def stopSpeaking(self) -> None:
        # sd.stop() is safe to call from any thread - it just signals the
        # PortAudio stream _speak() is blocked on in sd.wait() to stop,
        # which then returns there and runs the speakingChanged(False)
        # cleanup in its own finally block same as a natural finish.
        sd.stop()

    def _speak(self, text: str) -> None:
        if self._piper is None:
            return  # voice output is a nice-to-have, not load-bearing - stay silent rather than error
        self.speakingChanged.emit(True)
        try:
            chunks = list(self._piper.synthesize(text))
            if chunks:
                audio = np.concatenate([c.audio_float_array for c in chunks])
                audio = _resample_linear(audio, chunks[0].sample_rate, PLAYBACK_SAMPLE_RATE)
                sd.play(audio, samplerate=PLAYBACK_SAMPLE_RATE)
                sd.wait()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.speakingChanged.emit(False)
