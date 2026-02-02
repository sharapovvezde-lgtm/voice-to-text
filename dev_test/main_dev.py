# main_dev.py - Whisper Quick-Type DEV VERSION
# С поддержкой записи встреч

import sys
import os
import time
from datetime import datetime
from pathlib import Path

# Путь к корневой папке проекта
DEV_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DEV_DIR)
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, DEV_DIR)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QCheckBox, QPushButton, QSystemTrayIcon,
    QMenu, QGroupBox, QProgressBar, QTextEdit, QTabWidget,
    QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
    QSplitter
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QIcon, QCursor, QPixmap, QPainter, QColor, QTextCursor

# Основные модули из root
from recorder import AudioRecorder
from transcriber import get_transcriber
from hotkeys import get_hotkey_listener, MODIFIER_LIST, KEY_LIST
from utils import (
    scan_whisper_models, get_available_model_sizes,
    set_autostart, is_autostart_enabled,
    save_settings, load_settings
)

# DEV модули
from recorder_v2 import MeetingRecorder
from transcriber_v2 import MeetingTranscriber


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
        self._is_meeting = False  # Зелёный для встреч, красный для голоса
    
    def set_meeting_mode(self, is_meeting: bool):
        self._is_meeting = is_meeting
    
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        size = 18 + self._pulse
        off = (30 - size) // 2
        
        p.setBrush(QColor(0, 0, 0, 50))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(off + 2, off + 2, size, size)
        
        # Зелёный для встреч, красный для голосового ввода
        color = QColor(50, 200, 50) if self._is_meeting else QColor(255, 50, 50)
        p.setBrush(color)
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


class MeetingTranscribeWorker(QThread):
    """Воркер для транскрибации встреч"""
    finished = pyqtSignal(dict)
    progress = pyqtSignal(str)
    
    def __init__(self, transcriber: MeetingTranscriber, mic_path: str, sys_path: str, output_dir: str):
        super().__init__()
        self.transcriber = transcriber
        self.mic_path = mic_path
        self.sys_path = sys_path
        self.output_dir = output_dir
    
    def run(self):
        try:
            self.progress.emit("Загрузка модели Whisper...")
            self.transcriber.load_model()
            
            self.progress.emit("Транскрибация аудио...")
            result = self.transcriber.transcribe_meeting(
                mic_audio_path=self.mic_path,
                sys_audio_path=self.sys_path
            )
            
            self.progress.emit("Сохранение отчёта...")
            # Определяем путь для отчёта
            if self.mic_path:
                base = Path(self.mic_path).stem.replace("_mic", "")
                report_path = str(Path(self.output_dir) / f"{base}_transcript.txt")
            else:
                from datetime import datetime
                report_path = str(Path(self.output_dir) / f"Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
            
            report_path = self.transcriber.save_report(result, output_path=report_path)
            result["report_path"] = report_path
            
            self.finished.emit(result)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished.emit({"error": str(e)})


class Signals(QObject):
    start_rec = pyqtSignal()
    stop_rec = pyqtSignal()
    log = pyqtSignal(str)


class MainWindow(QMainWindow):
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Whisper Quick-Type [DEV]")
        self.setFixedSize(580, 680)
        
        # Основные компоненты
        self.recorder = AudioRecorder()
        self.transcriber = get_transcriber()
        self.hotkey = get_hotkey_listener()
        self.indicator = RecordingIndicator()
        self.signals = Signals()
        self.settings = load_settings()
        
        # DEV: Meeting Recorder
        self.meeting_recorder = MeetingRecorder(
            output_dir=os.path.join(DEV_DIR, "temp_records")
        )
        self.meeting_transcriber = MeetingTranscriber(model_name="base")
        
        self._recording = False
        self._processing = False
        self._meeting_recording = False
        self._last_recording = None  # Результат последней записи встречи
        
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
        
        self._log(f"🚀 [DEV] Готов! Горячие клавиши: {self.hotkey.get_hotkey_string()}")
        QTimer.singleShot(300, self._load_model)
    
    def _init_ui(self):
        w = QWidget()
        self.setCentralWidget(w)
        main_lay = QVBoxLayout(w)
        main_lay.setSpacing(8)
        main_lay.setContentsMargins(12, 12, 12, 12)
        
        # Заголовок
        title = QLabel("🎤 Whisper Quick-Type [DEV]")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #FF9800;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_lay.addWidget(title)
        
        # === TABS ===
        self.tabs = QTabWidget()
        main_lay.addWidget(self.tabs)
        
        # --- TAB 1: Голосовой ввод ---
        voice_tab = QWidget()
        voice_lay = QVBoxLayout(voice_tab)
        self._build_voice_tab(voice_lay)
        self.tabs.addTab(voice_tab, "🎤 Голос")
        
        # --- TAB 2: Встречи ---
        meeting_tab = QWidget()
        meeting_lay = QVBoxLayout(meeting_tab)
        self._build_meeting_tab(meeting_lay)
        self.tabs.addTab(meeting_tab, "📹 Встречи")
        
        # === ОБЩИЙ ЛОГ ===
        log_group = QGroupBox("📋 Лог")
        log_lay = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)
        self.log_text.setStyleSheet("""
            background: #1a1a1a; color: #0f0;
            font-family: Consolas; font-size: 10px;
        """)
        log_lay.addWidget(self.log_text)
        main_lay.addWidget(log_group)
        
        # === КНОПКИ ===
        btn_lay = QHBoxLayout()
        
        btn_hide = QPushButton("В трей")
        btn_hide.clicked.connect(self.hide)
        btn_lay.addWidget(btn_hide)
        
        btn_quit = QPushButton("Выход")
        btn_quit.setStyleSheet("background: #c00;")
        btn_quit.clicked.connect(self._quit)
        btn_lay.addWidget(btn_quit)
        
        main_lay.addLayout(btn_lay)
    
    def _build_voice_tab(self, lay):
        """Вкладка голосового ввода (оригинальный функционал)"""
        
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
        
        # === АВТОЗАПУСК ===
        self.autostart_cb = QCheckBox("Автозапуск с Windows")
        self.autostart_cb.stateChanged.connect(self._on_autostart)
        lay.addWidget(self.autostart_cb)
        
        lay.addStretch()
        
        # Заполняем
        self._refresh_models()
        self._refresh_mics()
    
    def _build_meeting_tab(self, lay):
        """Вкладка записи встреч"""
        
        # Хранение выбранной области
        self._selected_region = None
        
        # === ВЫБОР ОБЛАСТИ ===
        area_group = QGroupBox("🎯 Область записи")
        area_lay = QVBoxLayout(area_group)
        
        # Кнопка выбора области
        btn_select_region = QPushButton("📐 Выбрать область экрана")
        btn_select_region.setStyleSheet("background: #9C27B0; font-size: 13px; padding: 10px;")
        btn_select_region.clicked.connect(self._select_screen_region)
        area_lay.addWidget(btn_select_region)
        
        # Метка с выбранной областью
        self.region_label = QLabel("Область не выбрана (будет записан весь монитор)")
        self.region_label.setStyleSheet("color: #666; padding: 5px;")
        self.region_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area_lay.addWidget(self.region_label)
        
        # Монитор (запасной вариант)
        mon_lay = QHBoxLayout()
        mon_lay.addWidget(QLabel("Или монитор:"))
        self.monitor_combo = QComboBox()
        self._refresh_monitors()
        mon_lay.addWidget(self.monitor_combo, 1)
        area_lay.addLayout(mon_lay)
        
        lay.addWidget(area_group)
        
        # === АУДИО ===
        audio_group = QGroupBox("🎤 Источники звука")
        audio_lay = QVBoxLayout(audio_group)
        
        # Микрофон для встреч
        mic_lay = QHBoxLayout()
        mic_lay.addWidget(QLabel("Микрофон (Я):"))
        self.meeting_mic_combo = QComboBox()
        self._refresh_meeting_mics()
        mic_lay.addWidget(self.meeting_mic_combo, 1)
        audio_lay.addLayout(mic_lay)
        
        # Системный звук
        sys_lay = QHBoxLayout()
        self.sys_audio_cb = QCheckBox("Системный звук (Собеседник)")
        self.sys_audio_cb.setChecked(True)
        sys_lay.addWidget(self.sys_audio_cb)
        
        loopback = self.meeting_recorder.get_loopback_device()
        if loopback:
            loopback_name = loopback.name[:25] + "..." if len(loopback.name) > 25 else loopback.name
            sys_lay.addWidget(QLabel(f"✓ {loopback_name}"))
        else:
            self.sys_audio_cb.setEnabled(False)
            sys_lay.addWidget(QLabel("❌ Loopback не найден"))
        
        audio_lay.addLayout(sys_lay)
        lay.addWidget(audio_group)
        
        # === КНОПКИ УПРАВЛЕНИЯ ===
        ctrl_group = QGroupBox("⏯️ Управление")
        ctrl_lay = QHBoxLayout(ctrl_group)
        
        self.btn_start_meeting = QPushButton("▶️ Начать запись")
        self.btn_start_meeting.setStyleSheet("background: #4CAF50; font-size: 14px; padding: 10px;")
        self.btn_start_meeting.clicked.connect(self._start_meeting_recording)
        ctrl_lay.addWidget(self.btn_start_meeting)
        
        self.btn_stop_meeting = QPushButton("⏹️ Остановить")
        self.btn_stop_meeting.setStyleSheet("background: #f44336; font-size: 14px; padding: 10px;")
        self.btn_stop_meeting.clicked.connect(self._stop_meeting_recording)
        self.btn_stop_meeting.setEnabled(False)
        ctrl_lay.addWidget(self.btn_stop_meeting)
        
        lay.addWidget(ctrl_group)
        
        # === СТАТУС ===
        self.meeting_status = QLabel("⏸️ Готов к записи")
        self.meeting_status.setStyleSheet("font-size: 13px; padding: 8px; background: #333; color: #fff; border-radius: 4px;")
        self.meeting_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.meeting_status)
        
        # === ТАЙМЕР ===
        self.meeting_timer_label = QLabel("00:00:00")
        self.meeting_timer_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #1976D2;")
        self.meeting_timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.meeting_timer_label)
        
        self._meeting_timer = QTimer()
        self._meeting_timer.timeout.connect(self._update_meeting_timer)
        self._meeting_start_time = None
        
        # === ПОСЛЕДНИЕ ЗАПИСИ ===
        rec_group = QGroupBox("📁 Последние записи")
        rec_lay = QVBoxLayout(rec_group)
        
        self.recordings_list = QListWidget()
        self.recordings_list.setMaximumHeight(100)
        self.recordings_list.itemDoubleClicked.connect(self._open_recording)
        rec_lay.addWidget(self.recordings_list)
        
        rec_btn_lay = QHBoxLayout()
        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_recordings)
        rec_btn_lay.addWidget(btn_refresh)
        
        btn_transcribe = QPushButton("📝 Транскрибировать")
        btn_transcribe.clicked.connect(self._transcribe_selected)
        rec_btn_lay.addWidget(btn_transcribe)
        
        btn_open_folder = QPushButton("📂 Открыть папку")
        btn_open_folder.clicked.connect(self._open_records_folder)
        rec_btn_lay.addWidget(btn_open_folder)
        
        rec_lay.addLayout(rec_btn_lay)
        lay.addWidget(rec_group)
        
        self._refresh_recordings()
    
    def _init_tray(self):
        pix = QPixmap(24, 24)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Оранжевый для DEV версии
        p.setBrush(QColor(255, 152, 0))
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
    
    # === МЕТОДЫ ВКЛАДКИ ГОЛОС ===
    
    def _update_hk_label(self):
        self.hk_label.setText(f"💡 Удерживайте {self.hotkey.get_hotkey_string()} для записи")
        self.tray.setToolTip(f"Whisper [DEV]: {self.hotkey.get_hotkey_string()}")
    
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
        m = self.settings.get('model', 'base')
        for i in range(self.model_combo.count()):
            if self.model_combo.itemData(i) == m:
                self.model_combo.setCurrentIndex(i)
                break
        
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
    
    # === ЗАПИСЬ ГОЛОСА ===
    
    def _start_recording(self):
        if self._recording or self._meeting_recording:
            return
        if not self.transcriber.is_model_loaded():
            self._log("⚠️ Модель не загружена")
            return
        
        self.recorder.set_device(self.mic_combo.currentData())
        if self.recorder.start_recording():
            self._recording = True
            self.indicator.set_meeting_mode(False)
            self.indicator.start()
            self._log("🔴 Запись голоса...")
    
    def _stop_recording(self):
        if not self._recording:
            return
        
        self._recording = False
        self.indicator.stop()
        
        if self._processing:
            return
        self._processing = True
        
        audio = self.recorder.stop_recording()
        if audio is None or len(audio) == 0:
            self._log("⚠️ Нет аудио")
            self._processing = False
            return
        
        dur = self.recorder.get_audio_duration(audio)
        self._log(f"⏹️ {dur:.1f} сек")
        
        if dur < 0.4:
            self._log("⚠️ Слишком коротко")
            self._processing = False
            return
        
        self._log("🔄 Распознавание...")
        self._worker = TranscribeWorker(self.transcriber, audio)
        self._worker.finished.connect(self._on_transcribed, Qt.ConnectionType.SingleShotConnection)
        self._worker.start()
    
    def _on_transcribed(self, text):
        self._processing = False
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
            
            pyperclip.copy(text)
            time.sleep(0.15)
            
            user32 = ctypes.windll.user32
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
    
    # === МЕТОДЫ ВКЛАДКИ ВСТРЕЧИ ===
    
    def _refresh_monitors(self):
        self.monitor_combo.clear()
        for mon in self.meeting_recorder.get_monitors():
            self.monitor_combo.addItem(
                f"{mon['name']} ({mon['width']}x{mon['height']})",
                mon['id']
            )
    
    def _refresh_meeting_mics(self):
        self.meeting_mic_combo.clear()
        for mic in self.meeting_recorder.get_microphones():
            default = " ✓" if mic['is_default'] else ""
            self.meeting_mic_combo.addItem(f"{mic['name']}{default}", mic['id'])
    
    def _refresh_recordings(self):
        self.recordings_list.clear()
        records_dir = Path(DEV_DIR) / "temp_records"
        
        if records_dir.exists():
            # Ищем AVI файлы (основные видеозаписи)
            files = sorted(records_dir.glob("Meeting_*.avi"), key=os.path.getmtime, reverse=True)
            
            for f in files[:10]:  # Последние 10
                base_name = f.stem  # Meeting_20260202_123456
                
                # Проверяем наличие аудиофайлов
                mic_exists = (records_dir / f"{base_name}_mic.wav").exists()
                sys_exists = (records_dir / f"{base_name}_sys.wav").exists()
                
                # Формируем метку
                audio_status = ""
                if mic_exists and sys_exists:
                    audio_status = " 🎤🔊"
                elif mic_exists:
                    audio_status = " 🎤"
                elif sys_exists:
                    audio_status = " 🔊"
                
                item = QListWidgetItem(f"📹 {f.name}{audio_status}")
                # Сохраняем base_name для поиска связанных файлов
                item.setData(Qt.ItemDataRole.UserRole, base_name)
                self.recordings_list.addItem(item)
    
    def _select_screen_region(self):
        """Показать диалог выбора области экрана"""
        from recorder_v2 import ScreenRegionSelector
        
        self._log("🎯 Выберите область экрана мышкой...")
        self.hide()  # Скрываем окно
        
        # Небольшая задержка чтобы окно успело скрыться
        QApplication.processEvents()
        time.sleep(0.3)
        
        def on_region_selected(region):
            self._selected_region = region
            self.show()
            
            if region:
                self.region_label.setText(
                    f"✅ Выбрано: {region['width']}x{region['height']} "
                    f"(x:{region['left']}, y:{region['top']})"
                )
                self.region_label.setStyleSheet("color: #4CAF50; font-weight: bold; padding: 5px;")
                self._log(f"✅ Область выбрана: {region['width']}x{region['height']}")
            else:
                self.region_label.setText("❌ Выбор отменён")
                self.region_label.setStyleSheet("color: #f44; padding: 5px;")
                self._log("❌ Выбор области отменён")
        
        selector = ScreenRegionSelector(callback=on_region_selected)
        selector.show()
    
    def _start_meeting_recording(self):
        if self._meeting_recording or self._recording:
            return
        
        mic_id = self.meeting_mic_combo.currentData()
        record_system = self.sys_audio_cb.isChecked()
        
        # Используем выбранную область или монитор
        if self._selected_region:
            region = self._selected_region
            self._log(f"📹 Запись области {region['width']}x{region['height']}...")
            success = self.meeting_recorder.start(
                region=region,
                mic_device=mic_id,
                record_system=record_system
            )
        else:
            monitor_id = self.monitor_combo.currentData() or 1
            self._log(f"📹 Запись монитора {monitor_id}...")
            success = self.meeting_recorder.start(
                monitor_id=monitor_id,
                mic_device=mic_id,
                record_system=record_system
            )
        
        if success:
            self._meeting_recording = True
            self._meeting_start_time = time.time()
            self._meeting_timer.start(1000)
            
            self.indicator.set_meeting_mode(True)
            self.indicator.start()
            
            self.meeting_status.setText("🔴 ЗАПИСЬ ИДЁТ")
            self.meeting_status.setStyleSheet("font-size: 13px; padding: 8px; background: #c00; color: #fff; border-radius: 4px;")
            
            self.btn_start_meeting.setEnabled(False)
            self.btn_stop_meeting.setEnabled(True)
            
            self._log("✅ Запись встречи начата!")
        else:
            self._log("❌ Не удалось начать запись")
    
    def _stop_meeting_recording(self):
        if not self._meeting_recording:
            return
        
        self._log("⏹️ Останавливаю запись встречи...")
        
        self._meeting_recording = False
        self._meeting_timer.stop()
        self.indicator.stop()
        
        # stop() теперь возвращает dict
        result = self.meeting_recorder.stop()
        
        self.meeting_status.setText("⏸️ Готов к записи")
        self.meeting_status.setStyleSheet("font-size: 13px; padding: 8px; background: #333; color: #fff; border-radius: 4px;")
        
        self.btn_start_meeting.setEnabled(True)
        self.btn_stop_meeting.setEnabled(False)
        
        # Сохраняем результат для транскрибации
        self._last_recording = result
        
        if result and result.get("video"):
            self._log(f"✅ Видео: {os.path.basename(result['video'])}")
            if result.get("mic_audio"):
                self._log(f"✅ Микрофон: {os.path.basename(result['mic_audio'])}")
            if result.get("sys_audio"):
                self._log(f"✅ Сист.звук: {os.path.basename(result['sys_audio'])}")
            self._refresh_recordings()
        else:
            self._log("❌ Ошибка сохранения")
    
    def _update_meeting_timer(self):
        if self._meeting_start_time:
            elapsed = int(time.time() - self._meeting_start_time)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self.meeting_timer_label.setText(f"{h:02d}:{m:02d}:{s:02d}")
    
    def _open_recording(self, item):
        base_name = item.data(Qt.ItemDataRole.UserRole)
        records_dir = Path(DEV_DIR) / "temp_records"
        video_path = records_dir / f"{base_name}.avi"
        
        if video_path.exists():
            os.startfile(str(video_path))
    
    def _transcribe_selected(self):
        item = self.recordings_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Ошибка", "Выберите запись для транскрибации")
            return
        
        base_name = item.data(Qt.ItemDataRole.UserRole)
        records_dir = Path(DEV_DIR) / "temp_records"
        
        # Пути к аудиофайлам
        mic_path = records_dir / f"{base_name}_mic.wav"
        sys_path = records_dir / f"{base_name}_sys.wav"
        
        mic_path_str = str(mic_path) if mic_path.exists() else None
        sys_path_str = str(sys_path) if sys_path.exists() else None
        
        if not mic_path_str and not sys_path_str:
            QMessageBox.warning(
                self, "Ошибка", 
                f"Аудиофайлы не найдены для записи {base_name}\n\n"
                "Убедитесь, что запись содержит аудио."
            )
            return
        
        found_files = []
        if mic_path_str:
            found_files.append("Микрофон (Я)")
        if sys_path_str:
            found_files.append("Системный звук (Собеседник)")
        
        self._log(f"📝 Транскрибирую: {base_name}")
        self._log(f"   Найдено: {', '.join(found_files)}")
        self.meeting_status.setText("⏳ Транскрибация...")
        
        self._transcribe_worker = MeetingTranscribeWorker(
            self.meeting_transcriber,
            mic_path=mic_path_str,
            sys_path=sys_path_str,
            output_dir=str(records_dir)
        )
        self._transcribe_worker.progress.connect(lambda s: self._log(f"   {s}"))
        self._transcribe_worker.finished.connect(self._on_meeting_transcribed)
        self._transcribe_worker.start()
    
    def _on_meeting_transcribed(self, result):
        self.meeting_status.setText("⏸️ Готов к записи")
        
        if "error" in result:
            self._log(f"❌ Ошибка: {result['error']}")
            QMessageBox.critical(self, "Ошибка", result['error'])
            return
        
        report_path = result.get("report_path", "")
        self._log(f"✅ Отчёт: {report_path}")
        
        # Показать результат
        QMessageBox.information(
            self, 
            "Транскрибация завершена",
            f"Отчёт сохранён:\n{report_path}\n\n"
            f"Сегментов: {len(result.get('segments', []))}"
        )
        
        # Открыть отчёт
        if report_path and os.path.exists(report_path):
            os.startfile(report_path)
    
    def _open_records_folder(self):
        folder = Path(DEV_DIR) / "temp_records"
        folder.mkdir(exist_ok=True)
        os.startfile(str(folder))
    
    def _quit(self):
        if self._meeting_recording:
            self._stop_meeting_recording()
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
        QPushButton:disabled { background: #999; }
        QComboBox { padding: 4px; border: 1px solid #ccc; border-radius: 3px; }
        QTabWidget::pane { border: 1px solid #ccc; border-radius: 4px; }
        QTabBar::tab { background: #ddd; padding: 8px 16px; margin-right: 2px; border-radius: 4px 4px 0 0; }
        QTabBar::tab:selected { background: #1976D2; color: white; }
    """)
    
    win = MainWindow()
    win.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
