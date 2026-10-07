"""Case tab: HTML-report sections plus View Image / Run Analysis from Case Folder / View Report."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .labeler_project import launch_labeler, write_case_project
from .app_settings import machine_display_name, machine_name
from .report import analysis_is_done, build_case_report, _fmt

logger = logging.getLogger(__name__)

_PASS = QColor("#15803d")
_FAIL = QColor("#b91c1c")
_MUTED = QColor("#64748b")


class AnalyzeWorker(QThread):
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, case_dir: Path, machine_name: str, settings: dict):
        super().__init__()
        self.case_dir = Path(case_dir)
        self.machine_name = machine_name
        self.settings = settings

    def run(self):
        try:
            from .pipeline import run_case

            run_case(
                self.case_dir,
                machine_name=self.machine_name,
                send_email=False,
                data=self.settings,
            )
            self.finished_ok.emit(str(self.case_dir))
        except Exception as exc:
            logger.exception("analysis failed")
            self.failed.emit(str(exc))


class ImportAnalyzeWorker(QThread):
    """Run the watcher import pipeline (sort, analyze, email, publish) off the UI thread."""

    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, import_dir: Path, settings: dict):
        super().__init__()
        self.import_dir = Path(import_dir)
        self.settings = settings

    def run(self):
        try:
            from .watcher import process_import_dir

            published = process_import_dir(
                self.import_dir,
                self.settings,
                send_email=True,
                strict=True,
            )
            rows = [(str(dest), machine) for dest, machine in published]
            self.finished_ok.emit(rows)
        except Exception as exc:
            logger.exception("DICOM source analysis failed")
            self.failed.emit(str(exc))


class CasePage(QWidget):
    run_requested = pyqtSignal()
    baseline_requested = pyqtSignal()

    def __init__(self, folder: Path, machine: dict, settings: dict, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.machine = machine or {}
        self.settings = settings
        self._busy = False

        self.header_when = QLabel()
        self.header_user = QLabel()
        self.header_machine = QLabel()
        self.header_result = QLabel()
        self.header_result.setStyleSheet("font-weight: 600;")
        for label in (self.header_when, self.header_user, self.header_machine, self.header_result):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        meta = QHBoxLayout()
        meta.setSpacing(24)
        meta.addWidget(self.header_when)
        meta.addWidget(self.header_user)
        meta.addWidget(self.header_machine)
        meta.addWidget(self.header_result)
        meta.addStretch(1)

        self.view_image_btn = QPushButton("View Image")
        self.run_btn = QPushButton("Run Analysis from Case Folder")
        self.report_btn = QPushButton("View Report")
        self.baseline_btn = QPushButton("Show Baseline")
        self.view_image_btn.clicked.connect(self.view_image)
        self.run_btn.clicked.connect(self.run_requested.emit)
        self.report_btn.clicked.connect(self.view_report)
        self.baseline_btn.clicked.connect(self.baseline_requested.emit)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.view_image_btn)
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.report_btn)
        buttons.addWidget(self.baseline_btn)
        buttons.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        self.path_hint = QLabel(str(self.folder))
        self.path_hint.setWordWrap(True)
        self.path_hint.setStyleSheet("color: #64748b;")
        self.path_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.sections_host = QWidget()
        self.sections_layout = QVBoxLayout(self.sections_host)
        self.sections_layout.setContentsMargins(0, 0, 8, 0)
        self.sections_layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.sections_host)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        root.addLayout(buttons)
        root.addWidget(self.progress)
        root.addLayout(meta)
        root.addWidget(self.path_hint)
        root.addWidget(scroll, 1)
        self.reload()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.run_btn.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setFormat("Analyzing…")

    def reload(self) -> None:
        report = build_case_report(self.folder, self.machine)
        when = report.get("datetime") or "—"
        self.header_when.setText(f"Date/Time   {when}")
        self.header_user.setText(f"Operator   {report.get('operator') or 'NA'}")
        machine_label = machine_display_name(self.machine) or machine_name(self.machine)
        self.header_machine.setText(f"Machine   {machine_label}" if machine_label else "")
        self.header_machine.setVisible(bool(machine_label))
        result = str(report.get("result") or "new").lower()
        self.header_result.setText(result.upper())
        color = {"pass": _PASS, "fail": _FAIL}.get(result, _MUTED)
        self.header_result.setStyleSheet(f"font-weight: 700; color: {color.name()};")
        self.report_btn.setEnabled(self._report_path() is not None)
        self.baseline_btn.setEnabled(self._baseline_dir() is not None)
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        sections = report.get("sections") or []
        if not sections:
            empty = QLabel("No baseline measurements found for this machine.")
            empty.setStyleSheet("color: #64748b;")
            self.sections_layout.addWidget(empty)
        for section in sections:
            self.sections_layout.addWidget(self._section_widget(section))
        self.sections_layout.addStretch(1)

    def view_image(self) -> None:
        try:
            project = write_case_project(self.folder, self.machine)
            launch_labeler(project, self.settings)
        except Exception as exc:
            logger.exception("launch Image Labeler 3D")
            QMessageBox.critical(self, "View Image", str(exc))

    def view_report(self) -> None:
        path = self._report_path()
        if path is None:
            QMessageBox.information(
                self,
                "No report",
                "No report.html yet. Run Analysis from Case Folder first.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _report_path(self) -> Path | None:
        for path in (self.folder / "results" / "report.html", self.folder / "out" / "report.html"):
            if path.is_file():
                return path
        return None

    def _baseline_dir(self) -> Path | None:
        folder = Path(str(self.machine.get("baseline_dir") or "")).expanduser()
        return folder if folder.is_dir() else None

    def _section_widget(self, section: dict) -> QWidget:
        box = QWidget()
        box.setObjectName("caseSection")
        box.setStyleSheet(
            "QWidget#caseSection { background: #ffffff; border: 1px solid #d1d5db; border-radius: 6px; }"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 8, 10, 8)
        title = QLabel(section["title"])
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(_section_table(section))
        foot = QLabel(section["tolerance"])
        foot.setStyleSheet("color: #64748b;")
        layout.addWidget(foot)
        return box


def _section_table(section: dict) -> QTableWidget:
    rows = section.get("rows") or []
    headers = [
        section.get("label_header") or "",
        section.get("value_header") or "Value",
        "Ref",
        "Diff",
        "Pass/Fail",
    ]
    table = QTableWidget(len(rows), 5)
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setSelectionMode(QTableWidget.NoSelection)
    table.setFocusPolicy(Qt.NoFocus)
    table.setStyleSheet(
        """
        QTableWidget {
            background: #ffffff;
            alternate-background-color: #f8fafc;
            gridline-color: #e5e7eb;
            border: 1px solid #e5e7eb;
        }
        QHeaderView::section {
            background: #1f3a5f;
            color: #ffffff;
            font-weight: 600;
            padding: 6px;
            border: none;
            border-right: 1px solid #34547a;
        }
        """
    )
    fmt = section.get("num_format") or "0.0"
    for r, row in enumerate(rows):
        values = [
            str(row.get("label") or ""),
            _fmt(row["value"], fmt) if row.get("value") is not None else "",
            _fmt(row["baseline"], fmt) if row.get("baseline") is not None else "",
            _fmt(row["diff"], fmt) if row.get("diff") is not None else "",
            str(row.get("result") or "").title(),
        ]
        for c, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            if c == 4:
                result = str(row.get("result") or "").lower()
                if result == "pass":
                    item.setForeground(_PASS)
                elif result == "fail":
                    item.setForeground(_FAIL)
            table.setItem(r, c, item)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Stretch)
    table.resizeRowsToContents()
    header_h = table.horizontalHeader().height()
    rows_h = sum(table.rowHeight(i) for i in range(table.rowCount()))
    table.setFixedHeight(header_h + rows_h + 6)
    table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return table
