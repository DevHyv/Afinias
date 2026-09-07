import math
import threading
import time
from collections import deque

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty
from kivy.uix.button import Button
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.utils import get_color_from_hex

# Android imports are kept optional so the project remains testable on desktop.
try:
    from android.permissions import Permission, check_permission, request_permissions
    ANDROID = True
except ImportError:
    Permission = None
    ANDROID = False

try:
    from jnius import autoclass
except ImportError:
    autoclass = None

Window.clearcolor = get_color_from_hex('#121212')

NOTES_ES = ["Do", "Do#", "Re", "Re#", "Mi", "Fa", "Fa#", "Sol", "Sol#", "La", "La#", "Si"]
NOTES_EN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

NOTE_MAP = {
    'C': 0, 'DO': 0,
    'C#': 1, 'DB': 1, 'DO#': 1, 'REB': 1,
    'D': 2, 'RE': 2,
    'D#': 3, 'EB': 3, 'RE#': 3, 'MIB': 3,
    'E': 4, 'MI': 4,
    'F': 5, 'FA': 5,
    'F#': 6, 'GB': 6, 'FA#': 6, 'SOLB': 6,
    'G': 7, 'SOL': 7,
    'G#': 8, 'AB': 8, 'SOL#': 8, 'LAB': 8,
    'A': 9, 'LA': 9,
    'A#': 10, 'BB': 10, 'LA#': 10, 'SIB': 10,
    'B': 11, 'SI': 11,
}

INSTRUMENTS = {
    'Guitarra (6 cuerdas)': [
        {'note': 'E', 'freq': 82.41, 'label': '6ª'},
        {'note': 'A', 'freq': 110.00, 'label': '5ª'},
        {'note': 'D', 'freq': 146.83, 'label': '4ª'},
        {'note': 'G', 'freq': 196.00, 'label': '3ª'},
        {'note': 'B', 'freq': 246.94, 'label': '2ª'},
        {'note': 'E', 'freq': 329.63, 'label': '1ª'},
    ],
    'Bajo': [
        {'note': 'E', 'freq': 41.20, 'label': '4ª'},
        {'note': 'A', 'freq': 55.00, 'label': '3ª'},
        {'note': 'D', 'freq': 73.42, 'label': '2ª'},
        {'note': 'G', 'freq': 98.00, 'label': '1ª'},
    ],
    'Ukelele': [
        {'note': 'G', 'freq': 392.00, 'label': '4ª'},
        {'note': 'C', 'freq': 261.63, 'label': '3ª'},
        {'note': 'E', 'freq': 329.63, 'label': '2ª'},
        {'note': 'A', 'freq': 440.00, 'label': '1ª'},
    ],
}


def nearest_note(freq):
    """Return musical note name and cents offset for a detected frequency."""
    if freq <= 0:
        return None, None, None
    midi = 69.0 + 12.0 * math.log2(freq / 440.0)
    midi_round = int(round(midi))
    target_freq = 440.0 * (2.0 ** ((midi_round - 69) / 12.0))
    cents = int(round(1200.0 * math.log2(freq / target_freq)))
    note = NOTES_EN[midi_round % 12]
    return note, cents, target_freq


def detect_pitch_autocorrelation(samples, sample_rate):
    """Detect monophonic pitch from PCM16 samples using normalized autocorrelation."""
    if samples is None or len(samples) < 2048:
        return None

    # Remove DC and normalize.
    signal = samples.astype('float32')
    signal -= signal.mean()
    rms = math.sqrt(float((signal * signal).mean()))
    if rms < 180.0:
        return None
    signal /= rms

    # Tuner range roughly covers low bass through ukulele.
    min_freq, max_freq = 35.0, 550.0
    min_lag = max(2, int(sample_rate / max_freq))
    max_lag = min(len(signal) - 2, int(sample_rate / min_freq))
    if max_lag <= min_lag:
        return None

    corr = []
    for lag in range(min_lag, max_lag + 1):
        a = signal[:-lag]
        b = signal[lag:]
        value = float((a * b).mean())
        corr.append(value)

    if not corr:
        return None

    peak_index = max(range(len(corr)), key=corr.__getitem__)
    peak_value = corr[peak_index]
    if peak_value < 0.20:
        return None

    lag = min_lag + peak_index

    # Parabolic interpolation for better cents accuracy.
    if 0 < peak_index < len(corr) - 1:
        y1, y2, y3 = corr[peak_index - 1], corr[peak_index], corr[peak_index + 1]
        denom = (y1 - 2.0 * y2 + y3)
        if abs(denom) > 1e-8:
            shift = 0.5 * (y1 - y3) / denom
            lag += max(-0.5, min(0.5, shift))

    if lag <= 0:
        return None
    return sample_rate / lag


class AndroidMicRecorder:
    """Small wrapper around Android AudioRecord using pyjnius."""

    SAMPLE_RATE = 44100
    CHANNEL_CONFIG = 16  # AudioFormat.CHANNEL_IN_MONO
    AUDIO_FORMAT = 2    # AudioFormat.ENCODING_PCM_16BIT

    def __init__(self, on_audio):
        self.on_audio = on_audio
        self.running = False
        self.thread = None
        self.audio_record = None
        self.buffer_size = 0

    def start(self):
        if self.running:
            return
        if not ANDROID or autoclass is None:
            raise RuntimeError('El micrófono Android solo está disponible dentro del APK.')

        AudioRecord = autoclass('android.media.AudioRecord')
        MediaRecorderAudioSource = autoclass('android.media.MediaRecorder$AudioSource')
        AudioFormat = autoclass('android.media.AudioFormat')

        source = MediaRecorderAudioSource.MIC
        channel_config = AudioFormat.CHANNEL_IN_MONO
        encoding = AudioFormat.ENCODING_PCM_16BIT
        self.buffer_size = AudioRecord.getMinBufferSize(self.SAMPLE_RATE, channel_config, encoding)
        if self.buffer_size <= 0:
            self.buffer_size = 4096
        self.buffer_size = max(self.buffer_size * 2, 4096)

        self.audio_record = AudioRecord(
            source,
            self.SAMPLE_RATE,
            channel_config,
            encoding,
            self.buffer_size,
        )
        self.audio_record.startRecording()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        import numpy as np

        # Overlapping windows give a smoother tuner display.
        window_size = 8192
        raw = bytearray()
        pcm = np.zeros(window_size, dtype=np.int16)

        while self.running and self.audio_record is not None:
            try:
                read_count = self.audio_record.read(pcm, 0, window_size)
                if read_count <= 0:
                    time.sleep(0.01)
                    continue
                samples = np.array(pcm[:read_count], dtype=np.int16, copy=True)
                frequency = detect_pitch_autocorrelation(samples, self.SAMPLE_RATE)
                self.on_audio(frequency)
            except Exception:
                break

    def stop(self):
        self.running = False
        record = self.audio_record
        self.audio_record = None
        if record is not None:
            try:
                record.stop()
            except Exception:
                pass
            try:
                record.release()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)
        self.thread = None


class TunerScreen(Screen):
    is_listening = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tuning_mode = 'auto'
        self.active_string_idx = 0
        self.current_strings = INSTRUMENTS['Guitarra (6 cuerdas)']
        self.string_buttons = []
        self.mic = None
        self.last_freq = None
        self.freq_history = deque(maxlen=5)
        self.target_freq = None

    def on_kv_post(self, base_widget):
        self.render_strings()
        self.set_mode('auto')

    def on_leave(self):
        self.stop_microphone()

    def render_strings(self):
        if 'strings_grid' not in self.ids:
            return
        grid = self.ids.strings_grid
        grid.clear_widgets()
        self.string_buttons = []
        for idx, string in enumerate(self.current_strings):
            btn = Button(
                text=(
                    f"[size=24sp][b]{string['note']}[/b][/size]\n"
                    f"[size=15sp][color=#A0A0A0]{string['label']} • {string['freq']:.2f} Hz[/color][/size]"
                ),
                markup=True,
                background_normal='',
                background_color=get_color_from_hex('#262638'),
                color=get_color_from_hex('#FFFFFF'),
                size_hint_y=None,
                height=dp(78),
            )
            btn.bind(on_release=lambda _btn, i=idx: self.select_string(i))
            self.string_buttons.append(btn)
            grid.add_widget(btn)
        self._refresh_selection()

    def _refresh_selection(self):
        for i, btn in enumerate(self.string_buttons):
            selected = self.tuning_mode == 'manual' and i == self.active_string_idx
            btn.background_color = get_color_from_hex('#5A4A7A' if selected else '#262638')

    def select_string(self, idx):
        if self.tuning_mode == 'manual':
            self.active_string_idx = idx
            self.target_freq = self.current_strings[idx]['freq']
            self._refresh_selection()

    def set_mode(self, mode):
        self.tuning_mode = 'manual' if mode == 'manual' else 'auto'
        if self.tuning_mode == 'manual':
            self.target_freq = self.current_strings[self.active_string_idx]['freq']
        self._refresh_selection()
        if 'btn_auto' in self.ids:
            self.ids.btn_auto.background_color = get_color_from_hex('#5A4A7A' if self.tuning_mode == 'auto' else '#262638')
            self.ids.btn_manual.background_color = get_color_from_hex('#5A4A7A' if self.tuning_mode == 'manual' else '#262638')

    def on_instrument_change(self, text):
        if text not in INSTRUMENTS:
            return
        was_listening = self.is_listening
        if was_listening:
            self.stop_microphone()
        self.current_strings = INSTRUMENTS[text]
        self.active_string_idx = 0
        self.target_freq = None
        self.reset_display()
        self.render_strings()
        if was_listening:
            self.start_microphone()

    def request_mic_permission(self):
        if not ANDROID:
            return True
        try:
            permission = Permission.RECORD_AUDIO
            if check_permission(permission):
                return True
            request_permissions([permission], self._on_permission_result)
            return False
        except Exception as exc:
            self._set_status(f'Permiso de micrófono: {exc}')
            return False

    def _on_permission_result(self, permissions, grants):
        granted = bool(grants) and all(bool(g) for g in grants)
        if granted:
            Clock.schedule_once(lambda _dt: self.start_microphone(), 0.1)
        else:
            Clock.schedule_once(lambda _dt: self._permission_denied(), 0.1)

    def _permission_denied(self):
        self.is_listening = False
        self.ids.mic_btn.text = 'Permitir micrófono'
        self._set_status('Android no concedió acceso al micrófono.')

    def toggle_microphone(self):
        if self.is_listening:
            self.stop_microphone()
        else:
            if ANDROID and not self.request_mic_permission():
                self.ids.mic_btn.text = 'Esperando permiso…'
                return
            self.start_microphone()

    def start_microphone(self):
        if self.is_listening:
            return
        try:
            self.mic = AndroidMicRecorder(self.on_audio_frequency)
            self.mic.start()
            self.is_listening = True
            self.ids.mic_btn.text = 'Detener'
            self.ids.mic_btn.background_color = get_color_from_hex('#6B3F48')
            self._set_status('Escuchando… toca una cuerda a la vez.')
        except Exception as exc:
            self.is_listening = False
            self.mic = None
            self.ids.mic_btn.text = 'Activar micrófono'
            self._set_status(f'No se pudo abrir el micrófono: {exc}')

    def stop_microphone(self):
        self.is_listening = False
        if self.mic:
            self.mic.stop()
            self.mic = None
        if 'mic_btn' in self.ids:
            self.ids.mic_btn.text = 'Activar micrófono'
            self.ids.mic_btn.background_color = get_color_from_hex('#262638')
        self.freq_history.clear()
        self.reset_display()

    def on_audio_frequency(self, frequency):
        Clock.schedule_once(lambda _dt, f=frequency: self.update_tuner(f), 0)

    def update_tuner(self, detected_freq):
        if not self.is_listening or detected_freq is None:
            return
        self.freq_history.append(detected_freq)
        freq = sum(self.freq_history) / len(self.freq_history)
        self.last_freq = freq

        if self.tuning_mode == 'manual':
            target = self.current_strings[self.active_string_idx]
        else:
            target = min(self.current_strings, key=lambda s: abs(s['freq'] - freq))

        self.target_freq = target['freq']
        cents = int(round(1200.0 * math.log2(freq / target['freq'])))
        cents_clamped = max(-50, min(50, cents))
        self.ids.lbl_note.text = target['note']
        self.ids.lbl_freq.text = f'{freq:.1f} Hz'
        self.ids.cent_bar.value = 50 + cents_clamped
        if abs(cents) <= 5:
            text = 'AFINADA'
            self.ids.lbl_cents.color = get_color_from_hex('#9BE564')
        elif cents < 0:
            text = f'{abs(cents)}¢ • SUBE'
            self.ids.lbl_cents.color = get_color_from_hex('#FFD166')
        else:
            text = f'{abs(cents)}¢ • BAJA'
            self.ids.lbl_cents.color = get_color_from_hex('#FF8FA3')
        self.ids.lbl_cents.text = text
        self.ids.target_lbl.text = f"Objetivo: {target['freq']:.2f} Hz • {target['label']}"

    def reset_display(self):
        if 'lbl_note' not in self.ids:
            return
        self.ids.lbl_note.text = '–'
        self.ids.lbl_freq.text = '0.0 Hz'
        self.ids.cent_bar.value = 50
        self.ids.lbl_cents.text = '—'
        self.ids.lbl_cents.color = get_color_from_hex('#FFFFFF')
        self.ids.target_lbl.text = 'Objetivo: —'

    def _set_status(self, message):
        if 'status_lbl' in self.ids:
            self.ids.status_lbl.text = message

    def go_transposer(self):
        self.stop_microphone()
        self.manager.current = 'transposer'


class TransposerScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.note_pairs = list(zip(NOTES_ES, NOTES_EN))

    def process_transpose(self):
        from_idx = self.parse_selector(self.ids.spin_from.text)
        to_idx = self.parse_selector(self.ids.spin_to.text)
        if from_idx is None or to_idx is None:
            self.ids.lbl_output.text = 'Selecciona las tonalidades.'
            return

        shift = (to_idx - from_idx) % 12
        output_in_spanish = self.ids.spin_format.text.startswith('Español')
        target_arr = NOTES_ES if output_in_spanish else NOTES_EN

        raw = self.ids.input_chords.text
        if not raw.strip():
            self.ids.lbl_output.text = 'Escribe acordes para transponer.'
            return

        # Conserva saltos de línea y espacios usando splitlines + whitespace tokens.
        lines = []
        for line in raw.splitlines() or ['']:
            tokens = line.split()
            converted = []
            for token in tokens:
                note_idx, suffix = self.parse_chord(token)
                if note_idx is None:
                    converted.append(token)
                else:
                    converted.append(target_arr[(note_idx + shift) % 12] + suffix)
            lines.append('  '.join(converted))

        self.ids.lbl_output.text = '\n'.join(lines)

    @staticmethod
    def parse_selector(text):
        if not text:
            return None
        inside = text.split('(')[-1].replace(')', '').strip().upper()
        return NOTE_MAP.get(inside)

    @staticmethod
    def parse_chord(chord):
        """Parse only a valid note prefix, preserving slash chords and suffixes."""
        chord = chord.strip()
        if not chord:
            return None, chord

        upper = chord.upper()
        # Spanish/English root names, longest first.
        candidates = sorted(NOTE_MAP.keys(), key=len, reverse=True)
        for name in candidates:
            if upper.startswith(name):
                # Avoid interpreting words like 'Solamente' as Sol chords.
                rest = chord[len(name):]
                if rest and rest[0].isalpha() and rest[0].upper() in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                    continue
                return NOTE_MAP[name], rest
        return None, chord

    def go_tuner(self):
        self.manager.current = 'tuner'


class AfiniaApp(App):
    def build(self):
        self.title = 'Afinia'
        Builder.load_file('afinia.kv')
        sm = ScreenManager()
        sm.add_widget(TunerScreen(name='tuner'))
        sm.add_widget(TransposerScreen(name='transposer'))
        return sm

    def on_stop(self):
        screen = self.root.get_screen('tuner') if self.root else None
        if screen:
            screen.stop_microphone()


if __name__ == '__main__':
    AfiniaApp().run()
