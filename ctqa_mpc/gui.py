"""CTQA GUI: Winston-Lutz-style heading bar, Clinic/Simple Open Case, CSV values."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import QPoint, QPointF, QSettings, QSize, QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .app_settings import (
    check_settings_paths,
    default_machine,
    format_settings_path_report,
    get_institution,
    is_simple_run_mode,
    is_under_directory,
    list_case_folders,
    load_settings,
    machine_by_name,
    machine_display_name,
    machine_name,
    missing_settings_paths,
    named_machines,
    simple_machine_name,
    watcher_settings,
)
from .identity import (
    USER_ID_NONE,
    USER_ID_OIDC,
    USER_ID_OSUSER,
    clear_current_user,
    current_user_email,
    current_user_label,
    current_user_profile,
    get_user_id_method,
    user_needs_email,
)
from .analysis import CASE_RESULT_NAME, RESULT_JSON_NAME, analysis_dir, analysis_tables_for_display, read_case_result
from .case_page import AnalyzeWorker, CasePage, ImportAnalyzeWorker
from .labeler_project import (
    csv_paths,
    launch_labeler,
    read_csv_table,
    write_baseline_project,
)

logger = logging.getLogger(__name__)

APP_TITLE = "CTQA-MPC"


def app_icon() -> QIcon:
    """Navy CatPhan-style rings for the window and taskbar."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        cx = cy = size / 2.0
        rings = (
            (0.46, QColor(31, 58, 95)),
            (0.34, QColor(248, 250, 252)),
            (0.22, QColor(52, 84, 122)),
            (0.12, QColor(248, 250, 252)),
            (0.055, QColor(31, 58, 95)),
        )
        painter.setPen(Qt.NoPen)
        for radius, color in rings:
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx, cy), size * radius, size * radius)
        painter.end()
        icon.addPixmap(pm)
    return icon


def window_title(case: str = "") -> str:
    parts = [APP_TITLE]
    institution = get_institution()
    if institution:
        parts.append(institution)
    user = current_user_label()
    if user:
        parts.append(user)
    if case:
        parts.append(case)
    return " — ".join(parts)


def _restore_layout(widget, settings: QSettings, key: str, splitter: QSplitter | None = None) -> None:
    geom = settings.value(f"{key}/geometry")
    if geom is not None:
        widget.restoreGeometry(geom)
    if splitter is not None:
        state = settings.value(f"{key}/splitter")
        if state is not None:
            splitter.restoreState(state)


def _save_layout(widget, settings: QSettings, key: str, splitter: QSplitter | None = None) -> None:
    settings.setValue(f"{key}/geometry", widget.saveGeometry())
    if splitter is not None:
        settings.setValue(f"{key}/splitter", splitter.saveState())
    settings.sync()


def case_display_name(folder: Path | None, machine: dict | None = None) -> str:
    if folder is None:
        return ""
    case = Path(folder).name
    name = machine_name(machine) or simple_machine_name(folder)
    if not name:
        return case
    return f"{name}/{case}"


def case_csv_dir(folder: Path) -> Path:
    analysis = folder / "3.analysis"
    return analysis if analysis.is_dir() else folder


def case_status(folder: Path) -> str:
    result = read_case_result(folder)
    if result in ("pass", "fail"):
        return result
    return "new"


def find_html_report(folder: Path | None) -> Path | None:
    if folder is None:
        return None
    path = Path(folder) / "3.analysis" / "report.html"
    return path if path.is_file() else None


def _toolbar_icon(name: str, size: int = 22) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(31, 58, 95)
    p.setPen(QPen(c, 1.8))
    p.setBrush(c)
    s = size
    if name == "folder":
        p.setBrush(QColor(232, 176, 54))
        p.setPen(QPen(QColor(166, 117, 20), 1.2))
        p.drawRoundedRect(2, 8, s - 4, s - 11, 2, 2)
        p.drawRoundedRect(2, 5, 9, 6, 2, 2)
    elif name == "baseline":
        p.setBrush(QColor(248, 250, 252))
        p.setPen(QPen(c, 1.4))
        p.drawRoundedRect(4, 6, s - 8, s - 10, 2, 2)
        p.drawRoundedRect(6, 3, s - 8, s - 10, 2, 2)
        p.drawLine(9, 10, s - 6, 10)
        p.drawLine(9, 14, s - 8, 14)
    elif name == "run":
        p.setBrush(QColor(22, 163, 74))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygon([QPoint(5, 4), QPoint(s - 4, s // 2), QPoint(5, s - 4)]))
    elif name == "report":
        p.setBrush(QColor(248, 250, 252))
        p.setPen(QPen(c, 1.4))
        p.drawRoundedRect(5, 3, s - 9, s - 6, 2, 2)
        p.drawLine(8, 8, s - 7, 8)
        p.drawLine(8, 12, s - 7, 12)
        p.drawLine(8, 16, s - 9, 16)
    elif name == "settings":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(71, 85, 105))
        cx = cy = s / 2.0
        for i in range(6):
            p.save()
            p.translate(cx, cy)
            p.rotate(i * 60)
            p.drawRoundedRect(-2, int(-s * 0.46), 4, int(s * 0.28), 1, 1)
            p.restore()
        p.drawEllipse(QPointF(cx, cy), s * 0.28, s * 0.28)
        p.setBrush(QColor(243, 244, 246))
        p.drawEllipse(QPointF(cx, cy), s * 0.12, s * 0.12)
    elif name == "help":
        p.setBrush(QColor(37, 99, 235))
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, s - 4, s - 4)
        p.setPen(QPen(Qt.white, 2))
        p.setBrush(Qt.NoBrush)
        p.drawArc(7, 5, 8, 8, 40 * 16, 200 * 16)
        p.drawPoint(11, s - 6)
    p.end()
    return QIcon(pm)


def _table_widget(rows: list[list[str]]) -> QTableWidget:
    table = QTableWidget()
    if not rows:
        table.setRowCount(0)
        table.setColumnCount(0)
        return table
    headers = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(body) if body else 0)
    if not body and len(rows) == 1:
        table.setRowCount(1)
        for col, cell in enumerate(headers):
            table.setItem(0, col, QTableWidgetItem(cell))
        table.horizontalHeader().hide()
    else:
        for r, row in enumerate(body):
            for c, cell in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(cell))
    table.resizeColumnsToContents()
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setAlternatingRowColors(True)
    return table


class ValuesWindow(QDialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        csv_dir: Path,
        project_writer,
        settings: dict,
        can_edit_masks: bool,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(app_icon())
        self._project_writer = project_writer
        self._settings = settings
        self._csv_dir = csv_dir
        self.resize(980, 640)
        self.setWindowFlag(Qt.Window, True)

        tabs = QTabWidget()
        tables = analysis_tables_for_display(csv_dir)
        if tables:
            for name, rows in tables:
                tabs.addTab(_table_widget(rows), name)
        else:
            files = csv_paths(csv_dir)
            if not files:
                tabs.addTab(QLabel(f"No analysis results in {csv_dir}"), "—")
            for path in files:
                try:
                    rows = read_csv_table(path)
                except OSError as exc:
                    logger.exception("read csv %s", path)
                    tabs.addTab(QLabel(str(exc)), path.name)
                    continue
                tabs.addTab(_table_widget(rows), path.stem)

        edit = QPushButton("Edit masks")
        edit.setEnabled(can_edit_masks)
        edit.clicked.connect(self._edit_masks)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(edit)
        buttons.addStretch(1)
        buttons.addWidget(close)

        root = QVBoxLayout(self)
        hint = QLabel(str(csv_dir))
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b;")
        root.addWidget(hint)
        root.addWidget(tabs, 1)
        root.addLayout(buttons)

    def _edit_masks(self):
        try:
            project = self._project_writer()
            launch_labeler(project, self._settings)
        except Exception as exc:
            logger.exception("launch Image Labeler 3D")
            QMessageBox.critical(self, "Edit masks", str(exc))


class ScanCasesWorker(QThread):
    """List case folders and peek pass/fail off the UI thread."""

    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, machine: dict | None, parent=None):
        super().__init__(parent)
        self._machine = machine

    def run(self):
        try:
            rows = [(folder, case_status(folder)) for folder in list_case_folders(self._machine)]
            self.finished_ok.emit(rows)
        except Exception as exc:
            logger.exception("case scan failed")
            self.failed.emit(str(exc))


def analysis_file_names(folder: Path) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for root in (folder / "3.analysis", folder):
        for name in ("report.html", CASE_RESULT_NAME, RESULT_JSON_NAME):
            if name in seen:
                continue
            try:
                if (root / name).is_file():
                    names.append(name)
                    seen.add(name)
            except OSError:
                continue
    return names


class OpenCaseDialog(QDialog):
    """Pick a machine, then a case under that machine's cases_dir."""

    def __init__(
        self,
        parent=None,
        last_machine: str = "",
        last_case: str = "",
        data: dict | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Open Case")
        self.setWindowIcon(app_icon())
        self.resize(720, 460)
        self.machines = named_machines(data)
        self.selected_case: Path | None = None
        self.selected_machine: dict | None = None
        self._prefer_case = last_case
        self._scan_rows: list[tuple[Path, str]] = []
        self._scan_gen = 0
        self._scan_worker: ScanCasesWorker | None = None

        self.machine_combo = QComboBox()
        for machine in self.machines:
            self.machine_combo.addItem(machine_display_name(machine) or machine_name(machine), machine)

        self.filter_combo = QComboBox()
        for status in ("all", "new", "fail", "pass"):
            self.filter_combo.addItem(status, status)

        self.case_list = QListWidget()
        self.file_list = QListWidget()
        self.case_list.currentItemChanged.connect(self._on_case_changed)
        self.case_list.itemDoubleClicked.connect(self._accept_if_case)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_if_case)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText("Open")
        self.ok_button.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(QLabel("Machine"))
        top.addWidget(self.machine_combo, 1)

        lists = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Cases"))
        self._scan_status = QLabel("")
        self._scan_status.setStyleSheet("color: #64748b;")
        filter_row.addWidget(self._scan_status, 1)
        filter_row.addWidget(QLabel("Show"))
        filter_row.addWidget(self.filter_combo)
        left_layout.addLayout(filter_row)
        left_layout.addWidget(self.case_list, 1)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Analysis files"))
        right_layout.addWidget(self.file_list, 1)
        lists.addWidget(left)
        lists.addWidget(right)
        lists.setChildrenCollapsible(False)
        lists.setStretchFactor(0, 1)
        lists.setStretchFactor(1, 1)
        self.splitter = lists

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(lists, 1)
        layout.addWidget(buttons)

        self._settings = getattr(parent, "qs", None) if parent is not None else None
        if self._settings is None:
            self._settings = QSettings("MachineQA", "CTQAMPC")
        self._splitter_restored = False
        _restore_layout(self, self._settings, "open_case")
        last_filter = str(self._settings.value("open_case/filter", "all") or "all")
        filter_idx = self.filter_combo.findData(last_filter)
        self.filter_combo.setCurrentIndex(filter_idx if filter_idx >= 0 else 0)

        idx = self._index_for_machine_name(last_machine)
        if idx >= 0:
            self.machine_combo.setCurrentIndex(idx)
        self.machine_combo.currentIndexChanged.connect(self._start_scan)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._start_scan()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._splitter_restored:
            self._splitter_restored = True
            _restore_layout(self, self._settings, "open_case", self.splitter)

    def _index_for_machine_name(self, name: str) -> int:
        want = (name or "").strip().lower()
        if not want:
            return -1
        for i in range(self.machine_combo.count()):
            machine = self.machine_combo.itemData(i) or {}
            if machine_name(machine).lower() == want:
                return i
        return self.machine_combo.findText(name)

    def done(self, result):
        self._scan_gen += 1
        self._disconnect_scan_worker()
        _save_layout(self, self._settings, "open_case", self.splitter)
        super().done(result)

    def _on_filter_changed(self, *_args):
        self._settings.setValue("open_case/filter", str(self.filter_combo.currentData() or "all"))
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        self._apply_rows(self._scan_rows)

    def _disconnect_scan_worker(self) -> None:
        worker = self._scan_worker
        self._scan_worker = None
        if worker is None:
            return
        try:
            worker.finished_ok.disconnect()
            worker.failed.disconnect()
        except TypeError:
            pass

    def _start_scan(self):
        self.case_list.clear()
        self.file_list.clear()
        self.selected_case = None
        self.selected_machine = self.machine_combo.currentData()
        self.ok_button.setEnabled(False)
        self._scan_rows = []
        self._scan_gen += 1
        gen = self._scan_gen
        self._disconnect_scan_worker()
        self._scan_status.setText("Scanning cases…")
        worker = ScanCasesWorker(self.selected_machine, self)
        worker.finished_ok.connect(lambda rows, g=gen: self._on_scan_ok(rows, g))
        worker.failed.connect(lambda msg, g=gen: self._on_scan_fail(msg, g))
        self._scan_worker = worker
        worker.start()

    def _on_scan_ok(self, rows, gen: int) -> None:
        if gen != self._scan_gen:
            return
        self._scan_worker = None
        self._scan_rows = rows if isinstance(rows, list) else []
        self._apply_rows(self._scan_rows)

    def _on_scan_fail(self, message: str, gen: int) -> None:
        if gen != self._scan_gen:
            return
        self._scan_worker = None
        self._scan_status.setText("Scan failed")
        logger.error("Open Case scan failed: %s", message)

    def _apply_rows(self, rows: list[tuple[Path, str]]) -> None:
        self.case_list.clear()
        self.file_list.clear()
        self.selected_case = None
        self.ok_button.setEnabled(False)
        wanted = self.filter_combo.currentData()
        shown = 0
        prefer = None
        for folder, status in rows:
            if wanted not in (None, "all") and status != wanted:
                continue
            item = QListWidgetItem(f"{folder.name}  ({status})")
            item.setData(Qt.UserRole, folder)
            color = {
                "new": QColor("#64748b"),
                "pass": QColor("#15803d"),
                "fail": QColor("#b91c1c"),
            }.get(status)
            if color is not None:
                item.setForeground(color)
            self.case_list.addItem(item)
            if folder.name == self._prefer_case:
                prefer = item
            shown += 1
        self._scan_status.setText(f"{shown} cases")
        if prefer is not None:
            self.case_list.setCurrentItem(prefer)
        elif self.case_list.count():
            self.case_list.setCurrentRow(0)

    def _on_case_changed(self, current, _previous):
        self.file_list.clear()
        self.selected_case = None
        self.ok_button.setEnabled(False)
        if current is None:
            return
        folder = current.data(Qt.UserRole)
        if folder is None:
            return
        self.selected_case = Path(folder)
        self.ok_button.setEnabled(True)
        names = analysis_file_names(self.selected_case)
        if not names:
            self.file_list.addItem("(no analysis files)")
            return
        for name in names:
            self.file_list.addItem(name)

    def _accept_if_case(self, *_args):
        if self.selected_case is not None:
            self.selected_machine = self.machine_combo.currentData()
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(window_title())
        self.setWindowIcon(app_icon())
        self.resize(1120, 840)
        self.settings = load_settings()
        self.machine = default_machine(self.settings)
        self.qs = QSettings("MachineQA", "CTQAMPC")
        self.folder: Path | None = None
        self._child_windows: list[QDialog] = []
        self._worker: QThread | None = None
        self._busy_page: CasePage | None = None

        self.hint = QLabel()
        self.hint.setObjectName("summaryHint")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #64748b;")
        empty = QWidget()
        empty.setStyleSheet("QWidget { background: #ffffff; }")
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(16, 16, 16, 16)
        empty_layout.addWidget(self.hint)
        empty_layout.addStretch(1)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_case_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(self.tabs)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._build_heading())
        central_layout.addWidget(self._build_toolbar())
        central_layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")
        _restore_layout(self, self.qs, "main")
        self._apply_run_mode_ui()
        self._refresh_heading()
        QTimer.singleShot(0, self._after_shown)

    def closeEvent(self, event):
        _save_layout(self, self.qs, "main")
        super().closeEvent(event)

    def _make_action(self, text, icon_name, slot, shortcut=None):
        act = QAction(_toolbar_icon(icon_name), text, self)
        if shortcut:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _tool_button(self, action: QAction) -> QToolButton:
        btn = QToolButton()
        btn.setDefaultAction(action)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setIconSize(QSize(20, 20))
        btn.setAutoRaise(True)
        return btn

    def _build_heading(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("appHeading")
        bar.setStyleSheet(
            """
            QWidget#appHeading { background: #1f3a5f; }
            QWidget#appHeading QLabel { color: #f8fafc; background: transparent; }
            QWidget#appHeading QPushButton {
                color: #f8fafc;
                background: #34547a;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QWidget#appHeading QPushButton:hover { background: #456894; }
            QWidget#appHeading QPushButton:disabled { color: #94a3b8; background: #2a4a6e; }
            """
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(28, 28))
        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.heading_institution = QLabel()
        self.heading_institution.setStyleSheet("color: #cbd5e1;")
        self.heading_user = QLabel()
        self.heading_user.setStyleSheet("font-weight: 600;")
        self.user_settings_btn = QPushButton("User settings")
        self.login_btn = QPushButton("Login")
        self.logout_btn = QPushButton("Logout")
        self.user_settings_btn.clicked.connect(lambda: self.open_user_settings())
        self.login_btn.clicked.connect(self._login_user)
        self.logout_btn.clicked.connect(self._logout_user)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(self.heading_institution)
        layout.addStretch(1)
        layout.addWidget(self.heading_user)
        layout.addWidget(self.user_settings_btn)
        layout.addWidget(self.login_btn)
        layout.addWidget(self.logout_btn)
        return bar

    def _refresh_heading(self) -> None:
        institution = get_institution(self.settings)
        self.heading_institution.setText(institution)
        self.heading_institution.setVisible(bool(institution))
        method = get_user_id_method(self.settings)
        profile = current_user_profile(self.settings)
        label = current_user_label(self.settings)
        email = current_user_email(self.settings)
        if profile and label:
            extra = f"  <{email}>" if email else ""
            self.heading_user.setText(label + extra)
        elif method == USER_ID_OIDC:
            self.heading_user.setText("Not signed in")
        elif method == USER_ID_NONE:
            self.heading_user.setText("No user")
        else:
            self.heading_user.setText("")
        signed_in = profile is not None
        self.user_settings_btn.setEnabled(signed_in)
        self.login_btn.setVisible(method == USER_ID_OIDC and not signed_in)
        self.logout_btn.setVisible(method == USER_ID_OIDC and signed_in)

    def _after_shown(self) -> None:
        self._check_settings_paths()
        if user_needs_email(self.settings):
            QMessageBox.information(
                self,
                "User settings",
                "This login has no email address. Enter one in User settings "
                "to receive QA case notifications.",
            )
            self.open_user_settings(prompt_email=True)

    def _check_settings_paths(self) -> None:
        checks = check_settings_paths(self.settings, role="gui", require=False, log=logger)
        missing = missing_settings_paths(checks)
        if not missing:
            return
        QMessageBox.warning(
            self,
            APP_TITLE,
            format_settings_path_report(checks),
        )

    def open_user_settings(self, prompt_email: bool = False):
        from .user_settings_dialog import UserSettingsDialog

        if current_user_profile(self.settings) is None:
            QMessageBox.information(
                self,
                "User settings",
                "Sign in first, or set Identity to OSUser in Settings.",
            )
            return
        dlg = UserSettingsDialog(self, prompt_email=prompt_email or user_needs_email(self.settings))
        dlg.exec_()
        self._refresh_heading()
        self._apply_run_mode_ui()

    def _login_user(self):
        from .oidc_dialog import OidcLoginDialog

        dlg = OidcLoginDialog(self, can_quit=False)
        if dlg.exec_() != QDialog.Accepted:
            return
        self._refresh_heading()
        self._apply_run_mode_ui()
        if user_needs_email(self.settings):
            self.open_user_settings(prompt_email=True)

    def _logout_user(self):
        if (
            QMessageBox.question(
                self,
                "Logout",
                "Sign out of this CTQA-MPC session?",
            )
            != QMessageBox.Yes
        ):
            return
        clear_current_user()
        self._refresh_heading()
        self._apply_run_mode_ui()

    def _build_toolbar(self) -> QWidget:
        open_act = self._make_action("Open Case", "folder", self.open_case, "Ctrl+O")
        self.import_dicom_act = self._make_action(
            "Run Analysis from Dicom Source Folder",
            "run",
            self.run_from_dicom_source,
        )
        settings_act = self._make_action("Settings", "settings", self.open_settings, "Ctrl+,")
        help_act = self._make_action("Help", "help", self._show_help)
        run_act = QAction("Run Analysis", self)
        run_act.setShortcut("Ctrl+R")
        run_act.triggered.connect(self._run_current_case)
        self.addAction(run_act)

        panel = QWidget()
        panel.setStyleSheet(
            "QWidget { background: #f3f4f6; }"
            "QToolButton { padding: 4px 8px; }"
        )
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        layout.addWidget(self._tool_button(open_act), 0)
        self.import_dicom_btn = self._tool_button(self.import_dicom_act)
        layout.addWidget(self.import_dicom_btn, 0)
        layout.addWidget(self._tool_button(settings_act), 0)
        layout.addStretch(1)
        layout.addWidget(self._tool_button(help_act), 0)
        return panel

    def _apply_run_mode_ui(self) -> None:
        page = self._current_page()
        self.setWindowTitle(
            window_title(case_display_name(page.folder, page.machine) if page else "")
        )
        clinic = not is_simple_run_mode(self.settings)
        self.import_dicom_act.setVisible(clinic)
        self.import_dicom_btn.setVisible(clinic)
        if is_simple_run_mode(self.settings):
            self.hint.setText(
                "Simple mode: Open Case picks a case folder and opens it as a tab. "
                "Use View Image, Run Analysis, View Report, and Show Baseline on the case tab."
            )
        else:
            self.hint.setText(
                "Clinic mode: Open Case picks a machine, then a case under that machine's cases_dir. "
                "Run Analysis from Dicom Source Folder sorts an MPC folder under watch_path "
                "like the watcher service, emails the report, then opens the published case. "
                "The case opens as a tab with Point, Distance, and Axis sections. "
                "Show Baseline on the case tab opens that machine's baseline."
            )

    def open_settings(self):
        from .settings_dialog import SettingsDialog

        dlg = SettingsDialog(self)
        dlg.exec_()
        if not dlg.did_save:
            return
        self.settings = load_settings()
        self.machine = default_machine(self.settings)
        self._apply_run_mode_ui()
        self._refresh_heading()
        self._check_settings_paths()

    def _clinic_machines(self) -> list[dict]:
        machines = named_machines(self.settings)
        if machines:
            return machines
        QMessageBox.warning(
            self,
            APP_TITLE,
            "No machines are defined. Open Settings and add at least one machine in "
            "settings.json, or set RunMode to Simple.",
        )
        return []

    def _pick_clinic_case(self) -> tuple[Path, dict] | None:
        machines = self._clinic_machines()
        if not machines:
            return None
        last_machine = str(self.qs.value("last_machine", "") or "")
        last_case = Path(str(self.qs.value("last_directory", "") or "")).name
        dlg = OpenCaseDialog(
            self,
            last_machine=last_machine,
            last_case=last_case,
            data=self.settings,
        )
        if dlg.exec_() != QDialog.Accepted or dlg.selected_case is None:
            return None
        machine = dlg.selected_machine or machines[0]
        self.qs.setValue("last_machine", machine_name(machine))
        self.qs.setValue("last_directory", str(dlg.selected_case))
        return dlg.selected_case, machine

    def _pick_simple_folder(
        self,
        title: str,
        *,
        settings_key: str = "last_directory",
        start_at_parent: bool = True,
    ) -> Path | None:
        last = str(self.qs.value(settings_key, "") or self.qs.value("last_directory", "") or "")
        start_path = Path(last).expanduser() if last else Path()
        start = ""
        if start_path.is_dir():
            parent = start_path.parent
            if start_at_parent and parent.is_dir():
                start = str(parent)
            else:
                start = str(start_path)
        elif start_path.parent.is_dir():
            start = str(start_path.parent)
        chosen = QFileDialog.getExistingDirectory(self, title, start)
        if not chosen:
            return None
        folder = Path(chosen)
        self.qs.setValue(settings_key, str(folder))
        self.qs.setValue("last_directory", str(folder))
        machine_name = simple_machine_name(folder)
        if machine_name:
            self.qs.setValue("last_machine", machine_name)
        return folder

    def _machine_for_folder(self, folder: Path) -> dict | None:
        name = simple_machine_name(folder)
        return machine_by_name(name, self.settings) or default_machine(self.settings)

    def show_baseline(self, machine: dict | None = None):
        machine = machine or {}
        folder = Path(str(machine.get("baseline_dir") or "")).expanduser()
        if not folder.is_dir():
            QMessageBox.warning(self, APP_TITLE, f"baseline_dir not found:\n{folder}")
            return
        csv_dir = analysis_dir(folder)
        self._open_values(
            title=f"Baseline — {machine_display_name(machine) or folder.name}",
            csv_dir=csv_dir if csv_dir.is_dir() else folder,
            project_writer=lambda: write_baseline_project(folder, machine),
            can_edit_masks=False,
        )

    def open_case(self):
        if is_simple_run_mode(self.settings):
            folder = self._pick_simple_folder("Case folder")
            if folder is None:
                return
            machine = self._machine_for_folder(folder) or {}
            self.machine = machine or self.machine
            self._open_case_tab(folder, machine)
            return
        picked = self._pick_clinic_case()
        if picked is None:
            return
        folder, machine = picked
        self.machine = machine
        self._open_case_tab(folder, machine)

    def run_from_dicom_source(self) -> None:
        if is_simple_run_mode(self.settings):
            return
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, APP_TITLE, "Analysis is already running.")
            return
        raw_watch = str(watcher_settings(self.settings).get("watch_path") or "").strip()
        watch_path = Path(raw_watch).expanduser() if raw_watch else Path()
        if not raw_watch or not watch_path.is_dir():
            QMessageBox.critical(
                self,
                APP_TITLE,
                f"watch_path not found:\n{raw_watch or '(empty)'}\n\nSet Watcher.watch_path in Settings.",
            )
            return
        last = str(self.qs.value("last_dicom_import", "") or "")
        start = last if last and Path(last).is_dir() else str(watch_path)
        chosen = QFileDialog.getExistingDirectory(self, "DICOM source folder", start)
        if not chosen:
            return
        folder = Path(chosen)
        if not is_under_directory(folder, watch_path):
            QMessageBox.critical(
                self,
                APP_TITLE,
                f"Choose a DICOM folder under watch_path:\n{watch_path}\n\nSelected:\n{folder}",
            )
            return
        self.qs.setValue("last_dicom_import", str(folder))
        self.statusBar().showMessage(f"Analyzing DICOM source {folder.name}…")
        self.import_dicom_act.setEnabled(False)
        self._worker = ImportAnalyzeWorker(folder, self.settings)
        self._worker.finished_ok.connect(self._on_import_analyze_ok)
        self._worker.failed.connect(self._on_import_analyze_fail)
        self._worker.start()

    def _on_import_analyze_ok(self, rows) -> None:
        self._worker = None
        self.import_dicom_act.setEnabled(True)
        published = list(rows or [])
        if not published:
            self.statusBar().showMessage("DICOM source analysis finished with no case.")
            QMessageBox.critical(self, APP_TITLE, "No case was published from that DICOM folder.")
            return
        last_folder = None
        last_machine = None
        for dest, machine in published:
            folder = Path(dest)
            last_folder, last_machine = folder, machine or {}
            self.machine = last_machine
            self._open_case_tab(folder, last_machine)
        self.statusBar().showMessage(f"Analysis finished: {last_folder.name if last_folder else ''}")

    def _on_import_analyze_fail(self, message: str) -> None:
        self._worker = None
        self.import_dicom_act.setEnabled(True)
        self.statusBar().showMessage("DICOM source analysis failed")
        QMessageBox.critical(self, APP_TITLE, message)

    def _current_page(self) -> CasePage | None:
        page = self.tabs.currentWidget()
        return page if isinstance(page, CasePage) else None

    def _show_case_stack(self) -> None:
        self.stack.setCurrentIndex(1 if self.tabs.count() else 0)

    def _open_case_tab(self, folder: Path, machine: dict | None):
        folder = Path(folder)
        machine = machine or {}
        self.machine = machine
        self.folder = folder
        try:
            key = folder.resolve()
        except OSError:
            key = folder
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if not isinstance(page, CasePage):
                continue
            try:
                same = page.folder.resolve() == key
            except OSError:
                same = page.folder == folder
            if same:
                page.machine = machine
                page.settings = self.settings
                if not page._busy:
                    page.reload()
                self.tabs.setCurrentIndex(i)
                self._show_case_stack()
                self.setWindowTitle(window_title(case_display_name(folder, machine)))
                return
        page = CasePage(folder, machine, self.settings, self)
        page.run_requested.connect(lambda p=page: self._run_case_analysis(p))
        page.baseline_requested.connect(lambda p=page: self.show_baseline(p.machine))
        idx = self.tabs.addTab(page, case_display_name(folder, machine))
        self.tabs.setCurrentIndex(idx)
        self._show_case_stack()
        self.setWindowTitle(window_title(case_display_name(folder, machine)))

    def _close_case_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self._busy_page:
            QMessageBox.information(self, APP_TITLE, "Wait for analysis to finish before closing this tab.")
            return
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()
        self._show_case_stack()
        self._on_tab_changed(self.tabs.currentIndex())

    def _on_tab_changed(self, index: int) -> None:
        page = self.tabs.widget(index) if index >= 0 else None
        if isinstance(page, CasePage):
            self.folder = page.folder
            self.machine = page.machine
            self.setWindowTitle(window_title(case_display_name(page.folder, page.machine)))
            return
        self.folder = None
        self.setWindowTitle(window_title())

    def _run_current_case(self) -> None:
        page = self._current_page()
        if page is None:
            return
        self._run_case_analysis(page)

    def _run_case_analysis(self, page: CasePage) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, APP_TITLE, "Analysis is already running.")
            return
        if not page.machine:
            QMessageBox.warning(self, APP_TITLE, "No machine in settings.json")
            return
        page.set_busy(True)
        self._busy_page = page
        self.import_dicom_act.setEnabled(False)
        self.statusBar().showMessage(f"Analyzing {page.folder.name}…")
        self._worker = AnalyzeWorker(
            page.folder,
            machine_name(page.machine),
            self.settings,
        )
        self._worker.finished_ok.connect(self._on_analysis_ok)
        self._worker.failed.connect(self._on_analysis_fail)
        self._worker.start()

    def _on_analysis_ok(self, _case_dir: str) -> None:
        page = self._busy_page
        self._busy_page = None
        self._worker = None
        self.import_dicom_act.setEnabled(True)
        if page is not None:
            page.set_busy(False)
            page.reload()
            idx = self.tabs.indexOf(page)
            if idx >= 0:
                self.tabs.setTabText(idx, case_display_name(page.folder, page.machine))
            self.statusBar().showMessage(f"Analysis finished: {page.folder.name}")
        else:
            self.statusBar().showMessage("Analysis finished.")
        QMessageBox.information(self, APP_TITLE, "Analysis finished.")

    def _on_analysis_fail(self, message: str) -> None:
        page = self._busy_page
        self._busy_page = None
        self._worker = None
        self.import_dicom_act.setEnabled(True)
        if page is not None:
            page.set_busy(False)
        self.statusBar().showMessage("Analysis failed")
        QMessageBox.critical(self, APP_TITLE, message)

    def _open_values(self, *, title: str, csv_dir: Path, project_writer, can_edit_masks: bool):
        win = ValuesWindow(
            self,
            title=title,
            csv_dir=csv_dir,
            project_writer=project_writer,
            settings=self.settings,
            can_edit_masks=can_edit_masks,
        )
        self._child_windows.append(win)
        win.show()

    def _show_help(self):
        QMessageBox.information(
            self,
            APP_TITLE,
            "Open Case: pick a case; it opens as a tab with Date/Time, Operator, "
            "and the same analysis sections as the HTML report "
            "(Point Comparison, Distance Comparison, Axis Orientation).\n\n"
            "Clinic: Run Analysis from Dicom Source Folder picks an MPC folder "
            "under Watcher.watch_path, then sorts DICOM, finds the 16 BBs, emails "
            "the HTML report, and opens the published case (same as --mode service).\n\n"
            "On the case tab: View Image opens vtk_image_labeler_3d on today's CT. "
            "Run Analysis starts the pipeline (Ctrl+R). "
            "View Report opens 3.analysis/report.html (or the C# out/report.html). "
            "Show Baseline opens that machine's baseline_dir (no machine picker).\n\n"
            "Settings: Institution, RunMode, Identity (OSUser default, or None / OIDC), "
            "email, chat webhooks, Image Labeler 3D path, and Watcher "
            "(MPC folder watch; Install Watcher as Service uses NSSM).\n\n"
            "User settings (top bar): email, My machines, and new QA case emails.",
        )


def run_app() -> int:
    from .identity import upsert_os_user_profile
    from .oidc_dialog import OidcLoginDialog

    app = QApplication.instance() or QApplication([])
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MachineQA.CTQAMPC")
    except Exception:
        pass
    font = app.font()
    font.setPointSize(max(font.pointSize() + 3, 13))
    app.setFont(font)
    icon = app_icon()
    app.setWindowIcon(icon)
    method = get_user_id_method()
    if method == USER_ID_OSUSER:
        upsert_os_user_profile()
    if method == USER_ID_OIDC:
        login = OidcLoginDialog()
        login.setWindowIcon(icon)
        if login.exec_() != QDialog.Accepted:
            return 0
    win = MainWindow()
    win.setWindowIcon(icon)
    win.show()
    return app.exec_()
