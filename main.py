# main.py - Whisper Quick-Type
# Голосовой ввод текста

import sys
import os
import time
from datetime import datetime

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QCheckBox, QPushButton, QSystemTrayIcon,
    QMenu, QGroupBox, QProgressBar, QTextEdit
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QIcon, QCursor, QPixmap, QPainter, QColor, QTextCursor

from recorder import AudioRecorder
from transcriber import get_transcriber
from hotkeys import get_hotkey_listener, MODIFIER_LIST, KEY_LIST
from utils import (
    scan_whisper_models, get_available_model_sizes,
    set_autostart, is_autostart_enabled,
    save_settings, load_settings
)


class RecordingIndicator(QWidget):
    """Красный индикатор записи у курсора"""
    
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(30, 30)
        
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update)
        self._pulse = 0
        self._dir = 1
    
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        size = 18 + self._pulse
        off = (30 - size) // 2
        
        p.setBrush(QColor(0, 0, 0, 50))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(off + 2, off + 2, size, size)
        
        p.setBrush(QColor(255, 50, 50))
        p.drawEllipse(off, off, size, size)
    
    def _update(self):
        self.move(QCursor.pos().x() + 15, QCursor.pos().y() + 15)
        self._pulse += self._dir
        if self._pulse >= 6 or self._pulse <= 0:
            self._dir *= -1
        self.update()
    
    def start(self):
        self._update()
        self.show()
        self._timer.start(40)
    
    def stop(self):
        self._timer.stop()
        self.hide()


class ModelLoader(QThread):
    finished = pyqtSignal(bool, str)
    
    def __init__(self, transcriber, model):
        super().__init__()
        self.transcriber = transcriber
        self.model = model
    
    def run(self):
        ok = self.transcriber.load_model(self.model)
        self.finished.emit(ok, self.model)


class TranscribeWorker(QThread):
    finished = pyqtSignal(str)
    
    def __init__(self, transcriber, audio):
        super().__init__()
        self.transcriber = transcriber
        self.audio = audio
    
    def run(self):
        text, _ = self.transcriber.transcribe(self.audio)
        self.finished.emit(text.strip())


class Signals(QObject):
    start_rec = pyqtSignal()
    stop_rec = pyqtSignal()
    log = pyqtSignal(str)


class MainWindow(QMainWindow):
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Whisper Quick-Type")
        self.setFixedSize(480, 580)
        
        self.recorder = AudioRecorder()
        self.transcriber = get_transcriber()
        self.hotkey = get_hotkey_listener()
        self.indicator = RecordingIndicator()
        self.signals = Signals()
        self.settings = load_settings()
        
        self._recording = False
        
        self._init_ui()
        self._init_tray()
        self._load_settings()
        
        self.signals.start_rec.connect(self._start_recording, Qt.ConnectionType.QueuedConnection)
        self.signals.stop_rec.connect(self._stop_recording, Qt.ConnectionType.QueuedConnection)
        self.signals.log.connect(self._log)
        
        self.hotkey.set_callbacks(
            on_press=lambda: self.signals.start_rec.emit(),
            on_release=lambda: self.signals.stop_rec.emit()
        )
        self.hotkey.start()
        
        self._log(f"🚀 Готов! Горячие клавиши: {self.hotkey.get_hotkey_string()}")
        QTimer.singleShot(300, self._load_model)
    
    def _init_ui(self):
        w = QWidget()
        self.setCentralWidget(w)
        lay = QVBoxLayout(w)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)
        
        # Заголовок
        title = QLabel("🎤 Whisper Quick-Type")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1976D2;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        
        # === ГОРЯЧИЕ КЛАВИШИ ===
        hk_group = QGroupBox("⌨️ Горячие клавиши")
        hk_lay = QHBoxLayout(hk_group)
        
        self.mod_combo = QComboBox()
        self.mod_combo.addItems(MODIFIER_LIST)
        hk_lay.addWidget(QLabel("Мод:"))
        hk_lay.addWidget(self.mod_combo)
        
        hk_lay.addWidget(QLabel("+"))
        
        self.key1_combo = QComboBox()
        self.key1_combo.addItems(KEY_LIST)
        hk_lay.addWidget(self.key1_combo)
        
        hk_lay.addWidget(QLabel("+"))
        
        self.key2_combo = QComboBox()
        self.key2_combo.addItems(KEY_LIST)
        hk_lay.addWidget(self.key2_combo)
        
        btn_apply = QPushButton("✓")
        btn_apply.setFixedWidth(40)
        btn_apply.clicked.connect(self._apply_hotkey)
        hk_lay.addWidget(btn_apply)
        
        lay.addWidget(hk_group)
        
        self.hk_label = QLabel()
        self.hk_label.setStyleSheet("color: #666;")
        self.hk_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.hk_label)
        
        # === МОДЕЛЬ ===
        m_group = QGroupBox("🧠 Модель Whisper")
        m_lay = QVBoxLayout(m_group)
        
        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(self._on_model_change)
        m_lay.addWidget(self.model_combo)
        
        self.model_status = QLabel("...")
        self.model_status.setStyleSheet("color: #888;")
        m_lay.addWidget(self.model_status)
        
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        m_lay.addWidget(self.progress)
        
        lay.addWidget(m_group)
        
        # === МИКРОФОН ===
        mic_group = QGroupBox("🎙️ Микрофон")
        mic_lay = QVBoxLayout(mic_group)
        self.mic_combo = QComboBox()
        self.mic_combo.currentIndexChanged.connect(self._on_mic_change)
        mic_lay.addWidget(self.mic_combo)
        lay.addWidget(mic_group)
        
        # === ЛОГ ===
        log_group = QGroupBox("📋 Лог")
        log_lay = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setStyleSheet("""
            background: #1a1a1a; color: #0f0;
            font-family: Consolas; font-size: 10px;
        """)
        log_lay.addWidget(self.log_text)
        lay.addWidget(log_group)
        
        # === НАСТРОЙКИ ===
        self.autostart_cb = QCheckBox("Автозапуск с Windows")
        self.autostart_cb.stateChanged.connect(self._on_autostart)
        lay.addWidget(self.autostart_cb)
        
        # === КНОПКИ ===
        btn_lay = QHBoxLayout()
        
        btn_hide = QPushButton("В трей")
        btn_hide.clicked.connect(self.hide)
        btn_lay.addWidget(btn_hide)
        
        btn_quit = QPushButton("Выход")
        btn_quit.setStyleSheet("background: #c00;")
        btn_quit.clicked.connect(self._quit)
        btn_lay.addWidget(btn_quit)
        
        lay.addLayout(btn_lay)
        
        # Заполняем
        self._refresh_models()
        self._refresh_mics()
    
    def _init_tray(self):
        pix = QPixmap(24, 24)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(25, 118, 210))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 20, 20)
        p.end()
        
        self.tray = QSystemTrayIcon(QIcon(pix), self)
        
        menu = QMenu()
        menu.addAction("Открыть", self.show)
        menu.addSeparator()
        menu.addAction("Выход", self._quit)
        
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show() if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
    
    def _update_hk_label(self):
        self.hk_label.setText(f"💡 Удерживайте {self.hotkey.get_hotkey_string()} для записи")
        self.tray.setToolTip(f"Whisper: {self.hotkey.get_hotkey_string()}")
    
    def _apply_hotkey(self):
        mod = self.mod_combo.currentText()
        k1 = self.key1_combo.currentText()
        k2 = self.key2_combo.currentText()
        
        if k1 == k2:
            self._log("⚠️ Клавиши должны быть разными!")
            return
        
        self.hotkey.stop()
        
        if self.hotkey.set_hotkey(mod, k1, k2):
            self.hotkey.set_callbacks(
                on_press=lambda: self.signals.start_rec.emit(),
                on_release=lambda: self.signals.stop_rec.emit()
            )
            self.hotkey.start()
            self._update_hk_label()
            
            self.settings['hotkey_mod'] = mod
            self.settings['hotkey_k1'] = k1
            self.settings['hotkey_k2'] = k2
            save_settings(self.settings)
            
            self._log(f"✅ Клавиши: {self.hotkey.get_hotkey_string()}")
            self.tray.showMessage("Whisper", f"Горячие клавиши: {self.hotkey.get_hotkey_string()}", 
                                  QSystemTrayIcon.MessageIcon.Information, 1500)
        else:
            self._log("❌ Ошибка настройки клавиш")
            self.hotkey.start()
    
    def _log(self, msg):
        t = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{t}] {msg}")
        c = self.log_text.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(c)
    
    def _refresh_models(self):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        local = scan_whisper_models()
        for s in get_available_model_sizes():
            mark = "✓" if s in local else "↓"
            self.model_combo.addItem(f"{mark} {s}", s)
        self.model_combo.blockSignals(False)
    
    def _refresh_mics(self):
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        for dev_id, name in AudioRecorder.get_microphones():
            self.mic_combo.addItem(name, dev_id)
        saved = self.settings.get('microphone')
        if saved is not None:
            for i in range(self.mic_combo.count()):
                if self.mic_combo.itemData(i) == saved:
                    self.mic_combo.setCurrentIndex(i)
                    break
        self.mic_combo.blockSignals(False)
    
    def _load_settings(self):
        # Модель
        m = self.settings.get('model', 'base')
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == m:
                self.model_combo.setCurrentIndex(i)
                break
        
        # Горячие клавиши
        mod = self.settings.get('hotkey_mod', 'CTRL')
        k1 = self.settings.get('hotkey_k1', 'Z')
        k2 = self.settings.get('hotkey_k2', 'X')
        
        if mod in MODIFIER_LIST:
            self.mod_combo.setCurrentText(mod)
        if k1 in KEY_LIST:
            self.key1_combo.setCurrentText(k1)
        if k2 in KEY_LIST:
            self.key2_combo.setCurrentText(k2)
        
        self.hotkey.set_hotkey(mod, k1, k2)
        self._update_hk_label()
        
        # Автозапуск
        self.autostart_cb.setChecked(is_autostart_enabled())
    
    def _load_model(self):
        model = self.model_combo.currentData() or "base"
        self.model_status.setText(f"⏳ Загрузка {model}...")
        self.progress.show()
        self._log(f"📥 Загрузка модели {model}...")
        
        self._loader = ModelLoader(self.transcriber, model)
        self._loader.finished.connect(self._on_model_loaded)
        self._loader.start()
    
    def _on_model_loaded(self, ok, name):
        self.progress.hide()
        if ok:
            self.model_status.setText(f"✓ {name} готова")
            self.model_status.setStyleSheet("color: #4CAF50;")
            self._log(f"✅ Модель {name} готова!")
        else:
            self.model_status.setText(f"✗ Ошибка")
            self.model_status.setStyleSheet("color: #f44;")
            self._log(f"❌ Ошибка загрузки модели")
    
    def _on_model_change(self):
        if hasattr(self, '_loader') and self._loader.isRunning():
            return
        model = self.model_combo.currentData()
        if model and model != self.transcriber.get_model_name():
            self.settings['model'] = model
            save_settings(self.settings)
            self._load_model()
    
    def _on_mic_change(self):
        mic = self.mic_combo.currentData()
        self.settings['microphone'] = mic
        save_settings(self.settings)
        self.recorder.set_device(mic)
    
    def _on_autostart(self, state):
        on = state == Qt.CheckState.Checked.value
        set_autostart(on)
    
    # === ЗАПИСЬ ===
    
    def _start_recording(self):
        if self._recording:
            return
        if not self.transcriber.is_model_loaded():
            self._log("⚠️ Модель не загружена")
            return
        
        self.recorder.set_device(self.mic_combo.currentData())
        if self.recorder.start_recording():
            self._recording = True
            self.indicator.start()
            self._log("🔴 Запись...")
    
    def _stop_recording(self):
        if not self._recording:
            return
        
        self._recording = False
        self.indicator.stop()
        
        audio = self.recorder.stop_recording()
        if audio is None or len(audio) == 0:
            self._log("⚠️ Нет аудио")
            return
        
        dur = self.recorder.get_audio_duration(audio)
        self._log(f"⏹️ {dur:.1f} сек")
        
        if dur < 0.4:
            self._log("⚠️ Слишком коротко")
            return
        
        self._log("🔄 Распознавание...")
        self._worker = TranscribeWorker(self.transcriber, audio)
        self._worker.finished.connect(self._on_transcribed)
        self._worker.start()
    
    def _on_transcribed(self, text):
        if text:
            self._log(f"📝 {text[:60]}...")
            self._insert(text)
        else:
            self._log("⚠️ Не распознано")
    
    def _insert(self, text):
        try:
            import pyperclip
            import ctypes
            
            self._log("⏳ Ожидание...")
            time.sleep(0.8)
            
            # Копируем в буфер
            pyperclip.copy(text)
            time.sleep(0.15)
            
            # Вставляем через Windows API (SendInput)
            user32 = ctypes.windll.user32
            
            # Структура INPUT
            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002
            VK_CONTROL = 0x11
            VK_V = 0x56
            
            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", ctypes.c_ushort),
                           ("wScan", ctypes.c_ushort),
                           ("dwFlags", ctypes.c_ulong),
                           ("time", ctypes.c_ulong),
                           ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
            
            class INPUT(ctypes.Structure):
                _fields_ = [("type", ctypes.c_ulong),
                           ("ki", KEYBDINPUT),
                           ("padding", ctypes.c_ubyte * 8)]
            
            def press_key(vk):
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.ki.wVk = vk
                inp.ki.dwFlags = 0
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            
            def release_key(vk):
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.ki.wVk = vk
                inp.ki.dwFlags = KEYEVENTF_KEYUP
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            
            # Ctrl+V
            press_key(VK_CONTROL)
            time.sleep(0.05)
            press_key(VK_V)
            time.sleep(0.05)
            release_key(VK_V)
            time.sleep(0.05)
            release_key(VK_CONTROL)
            
            self._log("✅ Вставлено!")
        except Exception as e:
            self._log(f"❌ {e}")
    
    def _quit(self):
        self.hotkey.stop()
        self.tray.hide()
        QApplication.quit()
    
    def closeEvent(self, e):
        e.ignore()
        self.hide()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle('Fusion')
    
    app.setStyleSheet("""
        QMainWindow, QWidget { background: #f5f5f5; }
        QGroupBox { font-weight: bold; border: 1px solid #ccc; border-radius: 5px; 
                    margin-top: 8px; padding-top: 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
        QPushButton { background: #1976D2; color: white; border: none; 
                      padding: 6px 12px; border-radius: 4px; }
        QPushButton:hover { background: #1565C0; }
        QComboBox { padding: 4px; border: 1px solid #ccc; border-radius: 3px; }
    """)
    
    win = MainWindow()
    win.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
