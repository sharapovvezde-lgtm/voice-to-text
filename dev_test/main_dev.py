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
    QListWidget, QListWidgetItem, QMessageBox, QFrame, QScrollArea,
    QSizePolicy, QSpacerItem
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QIcon, QCursor, QPixmap, QPainter, QColor, QTextCursor, QFont

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
from recorder_v2 import MeetingRecorder, ScreenRegionSelector
from transcriber_v2 import MeetingTranscriber


class RecordingIndicator(QWidget):
    """Индикатор записи"""
    
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
        self._is_meeting = False
    
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
            self.progress.emit("Загрузка Whisper...")
            self.transcriber.load_model()
            
            self.progress.emit("Транскрибация...")
            result = self.transcriber.transcribe_meeting(
                mic_audio_path=self.mic_path,
                sys_audio_path=self.sys_path
            )
            
            self.progress.emit("Сохранение...")
            if self.mic_path:
                base = Path(self.mic_path).stem.replace("_mic", "")
                report_path = str(Path(self.output_dir) / f"{base}_transcript.txt")
            else:
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
        self.setMinimumSize(550, 700)
        self.resize(550, 750)
        
        # Компоненты
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
        self._last_recording = None
        self._selected_region = None  # ОБЯЗАТЕЛЬНО для записи!
        self._region_selector = None  # Селектор области экрана
        
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
        
        self._log(f"🚀 [DEV] Горячие клавиши: {self.hotkey.get_hotkey_string()}")
        QTimer.singleShot(300, self._load_model)
    
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # Заголовок
        title = QLabel("🎤 Whisper Quick-Type [DEV]")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #FF9800; padding: 5px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)
        
        # Вкладки
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #ccc; border-radius: 5px; background: white; }
            QTabBar::tab { background: #e0e0e0; padding: 10px 20px; margin-right: 2px; 
                          border-radius: 5px 5px 0 0; font-weight: bold; }
            QTabBar::tab:selected { background: #FF9800; color: white; }
            QTabBar::tab:hover { background: #FFB74D; }
        """)
        main_layout.addWidget(self.tabs, 1)
        
        # Вкладка Голос
        voice_widget = QWidget()
        voice_layout = QVBoxLayout(voice_widget)
        voice_layout.setSpacing(10)
        self._build_voice_tab(voice_layout)
        self.tabs.addTab(voice_widget, "🎤 Голос")
        
        # Вкладка Встречи
        meeting_widget = QWidget()
        meeting_layout = QVBoxLayout(meeting_widget)
        meeting_layout.setSpacing(10)
        self._build_meeting_tab(meeting_layout)
        self.tabs.addTab(meeting_widget, "📹 Встречи")
        
        # Лог
        log_group = QGroupBox("📋 Лог")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)
        self.log_text.setStyleSheet("""
            QTextEdit { background: #1e1e1e; color: #00ff00; 
                       font-family: Consolas, monospace; font-size: 11px;
                       border: 1px solid #333; border-radius: 3px; }
        """)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group)
        
        # Кнопки
        btn_layout = QHBoxLayout()
        btn_hide = QPushButton("📥 В трей")
        btn_hide.clicked.connect(self.hide)
        btn_layout.addWidget(btn_hide)
        
        btn_quit = QPushButton("❌ Выход")
        btn_quit.setStyleSheet("background: #c62828;")
        btn_quit.clicked.connect(self._quit)
        btn_layout.addWidget(btn_quit)
        main_layout.addLayout(btn_layout)
    
    def _build_voice_tab(self, layout):
        """Вкладка голосового ввода"""
        
        # Горячие клавиши
        hk_group = QGroupBox("⌨️ Горячие клавиши")
        hk_layout = QVBoxLayout(hk_group)
        
        combo_layout = QHBoxLayout()
        self.mod_combo = QComboBox()
        self.mod_combo.addItems(MODIFIER_LIST)
        self.mod_combo.setFixedWidth(80)
        combo_layout.addWidget(QLabel("Мод:"))
        combo_layout.addWidget(self.mod_combo)
        combo_layout.addWidget(QLabel("+"))
        
        self.key1_combo = QComboBox()
        self.key1_combo.addItems(KEY_LIST)
        self.key1_combo.setFixedWidth(60)
        combo_layout.addWidget(self.key1_combo)
        combo_layout.addWidget(QLabel("+"))
        
        self.key2_combo = QComboBox()
        self.key2_combo.addItems(KEY_LIST)
        self.key2_combo.setFixedWidth(60)
        combo_layout.addWidget(self.key2_combo)
        
        btn_apply = QPushButton("✓")
        btn_apply.setFixedWidth(40)
        btn_apply.clicked.connect(self._apply_hotkey)
        combo_layout.addWidget(btn_apply)
        combo_layout.addStretch()
        
        hk_layout.addLayout(combo_layout)
        
        self.hk_label = QLabel()
        self.hk_label.setStyleSheet("color: #666; padding: 5px;")
        hk_layout.addWidget(self.hk_label)
        layout.addWidget(hk_group)
        
        # Модель
        model_group = QGroupBox("🧠 Модель Whisper")
        model_layout = QVBoxLayout(model_group)
        
        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(self._on_model_change)
        model_layout.addWidget(self.model_combo)
        
        self.model_status = QLabel("...")
        self.model_status.setStyleSheet("color: #888; padding: 3px;")
        model_layout.addWidget(self.model_status)
        
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        model_layout.addWidget(self.progress)
        layout.addWidget(model_group)
        
        # Микрофон
        mic_group = QGroupBox("🎙️ Микрофон")
        mic_layout = QVBoxLayout(mic_group)
        self.mic_combo = QComboBox()
        self.mic_combo.currentIndexChanged.connect(self._on_mic_change)
        mic_layout.addWidget(self.mic_combo)
        layout.addWidget(mic_group)
        
        # Автозапуск
        self.autostart_cb = QCheckBox("🔄 Автозапуск с Windows")
        self.autostart_cb.stateChanged.connect(self._on_autostart)
        layout.addWidget(self.autostart_cb)
        
        layout.addStretch()
        
        self._refresh_models()
        self._refresh_mics()
    
    def _build_meeting_tab(self, layout):
        """Вкладка записи встреч — профессиональная вёрстка"""
        
        # ====== БЛОК 1: ВЫБОР ОБЛАСТИ ======
        area_group = QGroupBox("🎯 ШАГ 1: Выберите область записи")
        area_group.setStyleSheet("""
            QGroupBox { font-weight: bold; font-size: 13px; 
                       border: 2px solid #9C27B0; border-radius: 8px;
                       margin-top: 10px; padding-top: 10px; }
            QGroupBox::title { color: #9C27B0; }
        """)
        area_layout = QVBoxLayout(area_group)
        area_layout.setSpacing(10)
        
        btn_select = QPushButton("📐 ВЫБРАТЬ ОБЛАСТЬ ЭКРАНА")
        btn_select.setStyleSheet("""
            QPushButton { background: #9C27B0; color: white; font-size: 14px; 
                         font-weight: bold; padding: 15px; border-radius: 8px; }
            QPushButton:hover { background: #7B1FA2; }
        """)
        btn_select.setMinimumHeight(50)
        btn_select.clicked.connect(self._select_screen_region)
        area_layout.addWidget(btn_select)
        
        self.region_label = QLabel("⚠️ Область НЕ выбрана — запись невозможна")
        self.region_label.setStyleSheet("""
            QLabel { color: #c62828; font-weight: bold; padding: 10px; 
                    background: #ffebee; border-radius: 5px; }
        """)
        self.region_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area_layout.addWidget(self.region_label)
        
        layout.addWidget(area_group)
        
        # ====== БЛОК 2: АУДИО ======
        audio_group = QGroupBox("🎤 ШАГ 2: Настройте звук")
        audio_group.setStyleSheet("""
            QGroupBox { font-weight: bold; font-size: 13px; 
                       border: 2px solid #2196F3; border-radius: 8px;
                       margin-top: 10px; padding-top: 10px; }
            QGroupBox::title { color: #2196F3; }
        """)
        audio_layout = QVBoxLayout(audio_group)
        audio_layout.setSpacing(8)
        
        # Микрофон
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("🎤 Микрофон (Я):"))
        self.meeting_mic_combo = QComboBox()
        self.meeting_mic_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._refresh_meeting_mics()
        mic_row.addWidget(self.meeting_mic_combo, 1)
        audio_layout.addLayout(mic_row)
        
        # Системный звук
        sys_row = QHBoxLayout()
        self.sys_audio_cb = QCheckBox("🔊 Системный звук (Собеседник)")
        self.sys_audio_cb.setChecked(True)
        self.sys_audio_cb.setStyleSheet("font-weight: bold;")
        sys_row.addWidget(self.sys_audio_cb)
        
        # Статус loopback - проверяем наличие pyaudiowpatch
        try:
            import pyaudiowpatch as pa
            p = pa.PyAudio()
            loopback_found = False
            loopback_name = ""
            
            for i in range(p.get_device_count()):
                try:
                    dev = p.get_device_info_by_index(i)
                    if dev.get('isLoopbackDevice', False):
                        loopback_found = True
                        loopback_name = dev['name'][:20]
                        break
                except:
                    continue
            
            if not loopback_found:
                # Попробуем default output
                try:
                    wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
                    if wasapi.get('defaultOutputDevice', -1) >= 0:
                        loopback_found = True
                        loopback_name = "Default Output"
                except:
                    pass
            
            p.terminate()
            
            if loopback_found:
                self.loopback_label = QLabel(f"✅ {loopback_name}")
                self.loopback_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
            else:
                self.loopback_label = QLabel("⚠️ Нужен pyaudiowpatch")
                self.loopback_label.setStyleSheet("color: #FF9800;")
        except ImportError:
            self.loopback_label = QLabel("❌ pyaudiowpatch не установлен")
            self.loopback_label.setStyleSheet("color: #c62828;")
            self.sys_audio_cb.setEnabled(False)
        except Exception as e:
            self.loopback_label = QLabel(f"⚠️ {str(e)[:20]}")
            self.loopback_label.setStyleSheet("color: #FF9800;")
        
        sys_row.addWidget(self.loopback_label)
        sys_row.addStretch()
        audio_layout.addLayout(sys_row)
        
        layout.addWidget(audio_group)
        
        # ====== БЛОК 3: УПРАВЛЕНИЕ ======
        ctrl_group = QGroupBox("⏯️ ШАГ 3: Управление записью")
        ctrl_group.setStyleSheet("""
            QGroupBox { font-weight: bold; font-size: 13px; 
                       border: 2px solid #4CAF50; border-radius: 8px;
                       margin-top: 10px; padding-top: 10px; }
            QGroupBox::title { color: #4CAF50; }
        """)
        ctrl_layout = QVBoxLayout(ctrl_group)
        
        btn_row = QHBoxLayout()
        
        self.btn_start_meeting = QPushButton("▶️ НАЧАТЬ ЗАПИСЬ")
        self.btn_start_meeting.setStyleSheet("""
            QPushButton { background: #4CAF50; color: white; font-size: 14px;
                         font-weight: bold; padding: 12px; border-radius: 8px; }
            QPushButton:hover { background: #388E3C; }
            QPushButton:disabled { background: #9E9E9E; }
        """)
        self.btn_start_meeting.setMinimumHeight(45)
        self.btn_start_meeting.clicked.connect(self._start_meeting_recording)
        btn_row.addWidget(self.btn_start_meeting)
        
        self.btn_stop_meeting = QPushButton("⏹️ ОСТАНОВИТЬ")
        self.btn_stop_meeting.setStyleSheet("""
            QPushButton { background: #f44336; color: white; font-size: 14px;
                         font-weight: bold; padding: 12px; border-radius: 8px; }
            QPushButton:hover { background: #D32F2F; }
            QPushButton:disabled { background: #9E9E9E; }
        """)
        self.btn_stop_meeting.setMinimumHeight(45)
        self.btn_stop_meeting.setEnabled(False)
        self.btn_stop_meeting.clicked.connect(self._stop_meeting_recording)
        btn_row.addWidget(self.btn_stop_meeting)
        
        ctrl_layout.addLayout(btn_row)
        
        # Статус и таймер
        status_row = QHBoxLayout()
        
        self.meeting_status = QLabel("⏸️ Ожидание")
        self.meeting_status.setStyleSheet("""
            QLabel { font-size: 12px; padding: 8px; background: #424242; 
                    color: white; border-radius: 5px; }
        """)
        status_row.addWidget(self.meeting_status, 1)
        
        self.meeting_timer_label = QLabel("00:00:00")
        self.meeting_timer_label.setStyleSheet("""
            QLabel { font-size: 20px; font-weight: bold; color: #1976D2; 
                    padding: 5px 15px; background: #E3F2FD; border-radius: 5px; }
        """)
        status_row.addWidget(self.meeting_timer_label)
        
        ctrl_layout.addLayout(status_row)
        
        self._meeting_timer = QTimer()
        self._meeting_timer.timeout.connect(self._update_meeting_timer)
        self._meeting_start_time = None
        
        layout.addWidget(ctrl_group)
        
        # ====== БЛОК 4: ЗАПИСИ ======
        rec_group = QGroupBox("📁 Записи")
        rec_layout = QVBoxLayout(rec_group)
        
        self.recordings_list = QListWidget()
        self.recordings_list.setMaximumHeight(100)
        self.recordings_list.setStyleSheet("""
            QListWidget { border: 1px solid #ccc; border-radius: 5px; }
            QListWidget::item { padding: 5px; }
            QListWidget::item:selected { background: #E3F2FD; color: black; }
        """)
        self.recordings_list.itemDoubleClicked.connect(self._open_recording)
        rec_layout.addWidget(self.recordings_list)
        
        rec_btn_row = QHBoxLayout()
        
        btn_refresh = QPushButton("🔄 Обновить")
        btn_refresh.clicked.connect(self._refresh_recordings)
        rec_btn_row.addWidget(btn_refresh)
        
        btn_transcribe = QPushButton("📝 Транскрибировать")
        btn_transcribe.setStyleSheet("background: #FF9800;")
        btn_transcribe.clicked.connect(self._transcribe_selected)
        rec_btn_row.addWidget(btn_transcribe)
        
        btn_folder = QPushButton("📂 Папка")
        btn_folder.clicked.connect(self._open_records_folder)
        rec_btn_row.addWidget(btn_folder)
        
        rec_layout.addLayout(rec_btn_row)
        layout.addWidget(rec_group)
        
        self._refresh_recordings()
    
    def _init_tray(self):
        pix = QPixmap(24, 24)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
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
    
    def _update_hk_label(self):
        self.hk_label.setText(f"💡 Удерживайте {self.hotkey.get_hotkey_string()} для записи голоса")
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
            self._log("❌ Ошибка")
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
        self.mic_combo.blockSignals(False)
    
    def _refresh_meeting_mics(self):
        self.meeting_mic_combo.clear()
        for mic in self.meeting_recorder.get_microphones():
            default = " ✓" if mic['is_default'] else ""
            self.meeting_mic_combo.addItem(f"{mic['name']}{default}", mic['id'])
    
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
            self.model_status.setText(f"✓ {name}")
            self.model_status.setStyleSheet("color: #4CAF50; padding: 3px; font-weight: bold;")
            self._log(f"✅ Модель {name} готова!")
        else:
            self.model_status.setText("✗ Ошибка")
            self.model_status.setStyleSheet("color: #f44; padding: 3px;")
    
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
        set_autostart(state == Qt.CheckState.Checked.value)
    
    # ========== ГОЛОСОВОЙ ВВОД ==========
    
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
            self._processing = False
            return
        
        dur = self.recorder.get_audio_duration(audio)
        self._log(f"⏹️ {dur:.1f} сек")
        
        if dur < 0.4:
            self._processing = False
            return
        
        self._log("🔄 Распознавание...")
        self._worker = TranscribeWorker(self.transcriber, audio)
        self._worker.finished.connect(self._on_transcribed, Qt.ConnectionType.SingleShotConnection)
        self._worker.start()
    
    def _on_transcribed(self, text):
        self._processing = False
        if text:
            self._log(f"📝 {text[:50]}...")
            self._insert(text)
        else:
            self._log("⚠️ Не распознано")
    
    def _insert(self, text):
        try:
            import pyperclip
            import ctypes
            
            time.sleep(0.8)
            pyperclip.copy(text)
            time.sleep(0.1)
            
            user32 = ctypes.windll.user32
            VK_CONTROL, VK_V = 0x11, 0x56
            
            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                           ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                           ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
            
            class INPUT(ctypes.Structure):
                _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT), ("padding", ctypes.c_ubyte * 8)]
            
            def send_key(vk, up=False):
                inp = INPUT()
                inp.type = 1
                inp.ki.wVk = vk
                inp.ki.dwFlags = 0x0002 if up else 0
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            
            send_key(VK_CONTROL)
            time.sleep(0.03)
            send_key(VK_V)
            time.sleep(0.03)
            send_key(VK_V, True)
            time.sleep(0.03)
            send_key(VK_CONTROL, True)
            
            self._log("✅ Вставлено!")
        except Exception as e:
            self._log(f"❌ {e}")
    
    # ========== ЗАПИСЬ ВСТРЕЧ ==========
    
    def _select_screen_region(self):
        """Выбор области экрана — ОБЯЗАТЕЛЬНО перед записью"""
        self._log("🎯 Выберите область мышкой...")
        
        # Скрываем главное окно
        self.hide()
        QApplication.processEvents()
        time.sleep(0.3)
        
        # Создаём селектор и СОХРАНЯЕМ ссылку чтобы не удалился!
        self._region_selector = ScreenRegionSelector(callback=self._on_region_selected)
        self._region_selector.showFullScreen()
    
    def _on_region_selected(self, region):
        """Колбэк после выбора области"""
        self._selected_region = region
        self.show()
        self.activateWindow()
        
        if region:
            self.region_label.setText(
                f"✅ Область: {region['width']} x {region['height']} px"
            )
            self.region_label.setStyleSheet("""
                QLabel { color: #2E7D32; font-weight: bold; padding: 10px;
                        background: #E8F5E9; border-radius: 5px; }
            """)
            self._log(f"✅ Область: {region['width']}x{region['height']}")
        else:
            self.region_label.setText("⚠️ Область НЕ выбрана — запись невозможна")
            self.region_label.setStyleSheet("""
                QLabel { color: #c62828; font-weight: bold; padding: 10px;
                        background: #ffebee; border-radius: 5px; }
            """)
            self._log("❌ Выбор отменён")
    
    def _start_meeting_recording(self):
        if self._meeting_recording or self._recording:
            return
        
        # ПРОВЕРКА: область ОБЯЗАТЕЛЬНА!
        if not self._selected_region:
            QMessageBox.warning(
                self, "Область не выбрана",
                "❌ Сначала выберите область экрана!\n\n"
                "Нажмите кнопку '📐 ВЫБРАТЬ ОБЛАСТЬ ЭКРАНА' и выделите "
                "область мышкой (зажмите ЛКМ и проведите)."
            )
            return
        
        mic_id = self.meeting_mic_combo.currentData()
        record_system = self.sys_audio_cb.isChecked()
        
        self._log(f"📹 Запись: {self._selected_region['width']}x{self._selected_region['height']}")
        
        success = self.meeting_recorder.start(
            region=self._selected_region,
            mic_device=mic_id,
            record_system=record_system
        )
        
        if success:
            self._meeting_recording = True
            self._meeting_start_time = time.time()
            self._meeting_timer.start(1000)
            
            self.indicator.set_meeting_mode(True)
            self.indicator.start()
            
            self.meeting_status.setText("🔴 ЗАПИСЬ")
            self.meeting_status.setStyleSheet("""
                QLabel { font-size: 14px; padding: 8px; background: #c62828;
                        color: white; border-radius: 5px; font-weight: bold; }
            """)
            
            self.btn_start_meeting.setEnabled(False)
            self.btn_stop_meeting.setEnabled(True)
            
            self._log("✅ Запись началась!")
        else:
            self._log("❌ Ошибка запуска")
    
    def _stop_meeting_recording(self):
        if not self._meeting_recording:
            return
        
        self._log("⏹️ Останавливаю...")
        
        self._meeting_recording = False
        self._meeting_timer.stop()
        self.indicator.stop()
        
        result = self.meeting_recorder.stop()
        
        self.meeting_status.setText("⏸️ Ожидание")
        self.meeting_status.setStyleSheet("""
            QLabel { font-size: 12px; padding: 8px; background: #424242;
                    color: white; border-radius: 5px; }
        """)
        
        self.btn_start_meeting.setEnabled(True)
        self.btn_stop_meeting.setEnabled(False)
        
        self._last_recording = result
        
        if result:
            if result.get("video"):
                self._log(f"✅ Видео сохранено")
            if result.get("mic_audio"):
                self._log(f"✅ Микрофон сохранён")
            if result.get("sys_audio"):
                self._log(f"✅ Сист.звук сохранён")
            elif self.sys_audio_cb.isChecked():
                self._log("⚠️ Сист.звук не записан")
            self._refresh_recordings()
    
    def _update_meeting_timer(self):
        if self._meeting_start_time:
            elapsed = int(time.time() - self._meeting_start_time)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            self.meeting_timer_label.setText(f"{h:02d}:{m:02d}:{s:02d}")
    
    def _refresh_recordings(self):
        self.recordings_list.clear()
        records_dir = Path(DEV_DIR) / "temp_records"
        
        if records_dir.exists():
            files = sorted(records_dir.glob("Meeting_*.avi"), key=os.path.getmtime, reverse=True)
            
            for f in files[:10]:
                base_name = f.stem
                mic_exists = (records_dir / f"{base_name}_mic.wav").exists()
                sys_exists = (records_dir / f"{base_name}_sys.wav").exists()
                
                icons = ""
                if mic_exists:
                    icons += "🎤"
                if sys_exists:
                    icons += "🔊"
                
                item = QListWidgetItem(f"📹 {f.name} {icons}")
                item.setData(Qt.ItemDataRole.UserRole, base_name)
                self.recordings_list.addItem(item)
    
    def _open_recording(self, item):
        base_name = item.data(Qt.ItemDataRole.UserRole)
        video_path = Path(DEV_DIR) / "temp_records" / f"{base_name}.avi"
        if video_path.exists():
            os.startfile(str(video_path))
    
    def _transcribe_selected(self):
        item = self.recordings_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Ошибка", "Выберите запись")
            return
        
        base_name = item.data(Qt.ItemDataRole.UserRole)
        records_dir = Path(DEV_DIR) / "temp_records"
        
        mic_path = records_dir / f"{base_name}_mic.wav"
        sys_path = records_dir / f"{base_name}_sys.wav"
        
        mic_str = str(mic_path) if mic_path.exists() else None
        sys_str = str(sys_path) if sys_path.exists() else None
        
        if not mic_str and not sys_str:
            QMessageBox.warning(self, "Ошибка", "Аудиофайлы не найдены")
            return
        
        self._log(f"📝 Транскрибация: {base_name}")
        self.meeting_status.setText("⏳ Транскрибация...")
        
        self._transcribe_worker = MeetingTranscribeWorker(
            self.meeting_transcriber, mic_str, sys_str, str(records_dir)
        )
        self._transcribe_worker.progress.connect(lambda s: self._log(f"   {s}"))
        self._transcribe_worker.finished.connect(self._on_meeting_transcribed)
        self._transcribe_worker.start()
    
    def _on_meeting_transcribed(self, result):
        self.meeting_status.setText("⏸️ Ожидание")
        self.meeting_status.setStyleSheet("""
            QLabel { font-size: 12px; padding: 8px; background: #424242;
                    color: white; border-radius: 5px; }
        """)
        
        if "error" in result:
            self._log(f"❌ {result['error']}")
            QMessageBox.critical(self, "Ошибка", result['error'])
            return
        
        report_path = result.get("report_path", "")
        segments = len(result.get('segments', []))
        self._log(f"✅ Готово! Сегментов: {segments}")
        
        QMessageBox.information(
            self, "Транскрибация",
            f"✅ Отчёт сохранён:\n{report_path}\n\nСегментов: {segments}"
        )
        
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
        QMainWindow { background: #fafafa; }
        QGroupBox { background: white; border: 1px solid #ddd; border-radius: 8px;
                   margin-top: 10px; padding: 15px; padding-top: 25px; }
        QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 5px;
                          font-weight: bold; color: #333; }
        QPushButton { background: #1976D2; color: white; border: none;
                     padding: 8px 16px; border-radius: 5px; font-weight: bold; }
        QPushButton:hover { background: #1565C0; }
        QPushButton:disabled { background: #BDBDBD; }
        QComboBox { padding: 6px; border: 1px solid #ccc; border-radius: 5px; 
                   background: white; }
        QComboBox:hover { border-color: #1976D2; }
        QCheckBox { spacing: 8px; }
        QLabel { color: #333; }
    """)
    
    win = MainWindow()
    win.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
