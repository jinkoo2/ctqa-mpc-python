from __future__ import annotations

import json

from PyQt5.QtCore import QSettings, QTimer, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .app_settings import (
    CASE_FOLDER_NAME_REGEX_KEY,
    DEFAULT_CASE_FOLDER_REGEX,
    DEFAULT_TEMP_CLEANUP_OLDER_THAN_DAYS,
    TEMP_CLEANUP_OLDER_THAN_DAYS_KEY,
    DOCUFORMS2_MPC_TYPE,
    ERROR_EMAIL_TO_KEY,
    EVENT_EMAIL_TO_KEY,
    INSTITUTION_KEY,
    MACHINES_KEY,
    NEW_CASE_EMAIL_TO_KEY,
    POST_PROCESSING_KEY,
    RUN_MODE_CLINIC,
    RUN_MODE_KEY,
    RUN_MODES,
    WATCHER_KEY,
    chat_webhook_urls,
    default_docuforms2_mpc_step,
    email_settings,
    find_docuforms2_step,
    format_form_ids,
    form_ids_from_machines,
    form_ids_from_step,
    get_institution,
    get_run_mode,
    load_settings,
    parse_form_ids_text,
    post_processing_steps,
    save_settings,
    settings_path,
    upsert_post_step,
    watcher_case_folder_regex,
    watcher_settings,
)
from .emailer import error_email_to_list, format_error_email_to, send_test_chat, send_test_email
from .identity import (
    DEFAULT_OIDC_CLIENT_ID,
    DEFAULT_OIDC_ISSUER,
    DEFAULT_OIDC_REDIRECT_URI,
    DEFAULT_OIDC_REGISTRATION_URL,
    DEFAULT_OIDC_SCOPES,
    IDENTITY_KEY,
    USER_ID_NONE,
    USER_ID_OIDC,
    USER_ID_OSUSER,
    USER_ID_METHODS,
    coerce_registration_url,
    format_os_user_preview,
    get_user_id_method,
    identity_block,
    oidc_settings,
    upsert_os_user_profile,
)
from .watch_service import (
    FROZEN_APP_PARAMETERS,
    SOURCE_APP_PARAMETERS,
    WatchServicePlan,
    default_app_parameters,
    default_plan,
    format_nssm_commands,
    install_watch_service,
    is_admin,
    is_windows,
)


def _scroll_page(inner: QWidget) -> QWidget:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(scroll)
    return page


def _hint_label(text: str) -> QLabel:
    hint = QLabel(text)
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #64748b;")
    hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return hint


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int_spin(value, lo: int, hi: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(lo, hi)
    try:
        box.setValue(int(value))
    except (TypeError, ValueError):
        box.setValue(lo)
    return box


def _float_spin(value, lo: float, hi: float, decimals: int = 1) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setDecimals(decimals)
    try:
        box.setValue(float(value))
    except (TypeError, ValueError):
        box.setValue(lo)
    return box


class LogTextDialog(QDialog):
    def __init__(self, parent, title: str, summary: str, body: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumSize(720, 420)
        self.resize(800, 520)
        self._body = body or ""
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.edit.setPlainText(self._body)
        self.edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        copy_btn = QPushButton("Copy to clipboard")
        copy_btn.clicked.connect(self._copy)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.addButton(copy_btn, QDialogButtonBox.ActionRole)
        layout = QVBoxLayout(self)
        layout.addWidget(summary_label)
        layout.addWidget(self.edit, 1)
        layout.addWidget(buttons)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._body)
        self.edit.selectAll()


class InstallWatchServiceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install Watcher as Service")
        self.setModal(True)
        self.resize(640, 480)
        plan = default_plan()
        self.service_name = QLineEdit(plan.service_name)
        self.display_name = QLineEdit(plan.display_name)
        self.nssm_exe = QLineEdit(plan.nssm_exe)
        self.program_exe = QLineEdit(plan.program_exe)
        self.app_parameters = QLineEdit(plan.app_parameters)
        self.app_directory = QLineEdit(plan.app_directory)
        self.settings_file = QLineEdit(plan.settings_file)
        self.users_folder = QLineEdit(plan.users_folder)
        self.use_account = QCheckBox("Log on as this account (needed for UNC share access)")
        self.use_account.setChecked(bool(plan.account))
        self.account = QLineEdit(plan.account)
        self.account.setPlaceholderText(r"DOMAIN\username")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Windows password for that account")
        self.replace_existing = QCheckBox("Replace the service if it already exists")
        self.replace_existing.setChecked(True)
        self.start_after = QCheckBox("Start the service after install")
        self.start_after.setChecked(True)
        self.use_account.toggled.connect(self._sync_account)
        self.program_exe.textChanged.connect(self._sync_app_parameters)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.addRow("service_name", self.service_name)
        form.addRow("display_name", self.display_name)
        form.addRow("nssm.exe", self._path_row(self.nssm_exe, "nssm"))
        form.addRow("program", self._path_row(self.program_exe, "exe"))
        form.addRow("arguments", self.app_parameters)
        form.addRow("app_directory", self._path_row(self.app_directory, "dir"))
        form.addRow("settings_file", self._path_row(self.settings_file, "json"))
        form.addRow("users_folder", self._path_row(self.users_folder, "dir"))
        form.addRow("", self.use_account)
        form.addRow("account", self.account)
        form.addRow("password", self.password)
        form.addRow("", self.replace_existing)
        form.addRow("", self.start_after)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Install")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(
            _hint_label(
                "Installs the DailyQA watcher with NSSM (https://nssm.cc). Packaged app: "
                "CTQAMPC.exe --mode service. From source: python.exe with "
                "-u -m ctqa_mpc watch. settings_file and users_folder are passed "
                "as environment variables (do not put --settings or --users in arguments). "
                "Use the same Windows account as the old C# service so UNC shares work. "
                "Administrator rights are required."
            )
        )
        layout.addWidget(buttons)
        self._sync_account()

    def _path_row(self, edit: QLineEdit, kind: str) -> QWidget:
        btn = QPushButton("...")
        btn.setFixedWidth(32)
        btn.clicked.connect(lambda: self._browse(edit, kind))
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(edit, 1)
        layout.addWidget(btn, 0)
        return row

    def _browse(self, edit: QLineEdit, kind: str) -> None:
        start = edit.text().strip()
        if kind == "dir":
            path = QFileDialog.getExistingDirectory(self, "Select folder", start)
        elif kind == "json":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select settings file", start, "JSON (*.json);;All files (*)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select executable", start, "Programs (*.exe);;All files (*)"
            )
        if path:
            edit.setText(path)

    def _sync_account(self, *_args) -> None:
        on = self.use_account.isChecked()
        self.account.setEnabled(on)
        self.password.setEnabled(on)

    def _sync_app_parameters(self, *_args) -> None:
        current = self.app_parameters.text().strip()
        if current and current not in (SOURCE_APP_PARAMETERS, FROZEN_APP_PARAMETERS):
            return
        self.app_parameters.setText(default_app_parameters(self.program_exe.text().strip()))

    def plan(self) -> WatchServicePlan:
        return WatchServicePlan(
            service_name=self.service_name.text().strip(),
            display_name=self.display_name.text().strip(),
            nssm_exe=self.nssm_exe.text().strip(),
            program_exe=self.program_exe.text().strip(),
            app_parameters=self.app_parameters.text().strip(),
            app_directory=self.app_directory.text().strip(),
            settings_file=self.settings_file.text().strip(),
            users_folder=self.users_folder.text().strip(),
            account=self.account.text().strip() if self.use_account.isChecked() else "",
            password=self.password.text() if self.use_account.isChecked() else "",
            start_after=self.start_after.isChecked(),
            replace_existing=self.replace_existing.isChecked(),
        )


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CTQA-MPC settings")
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.did_save = False
        self._qs = QSettings("MachineQA", "CTQAMPC")
        self._data = load_settings()
        email = email_settings(self._data)
        hooks = chat_webhook_urls(self._data)

        self.institution = QLineEdit()
        self.institution.setText(get_institution(self._data))
        self.run_mode = QComboBox()
        self.run_mode.addItems(list(RUN_MODES))
        mode_idx = self.run_mode.findText(get_run_mode(self._data))
        self.run_mode.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
        self.path_label = QLabel(str(settings_path()))
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("color: #64748b;")

        oidc = oidc_settings(self._data)
        self.user_id_method = QComboBox()
        self.user_id_method.addItems(list(USER_ID_METHODS))
        method_idx = self.user_id_method.findText(get_user_id_method(self._data))
        self.user_id_method.setCurrentIndex(method_idx if method_idx >= 0 else 1)
        self.user_id_method.currentTextChanged.connect(self._sync_identity_ui)
        self.os_user_preview = QPlainTextEdit()
        self.os_user_preview.setReadOnly(True)
        self.os_user_preview.setTabChangesFocus(True)
        self.os_user_preview.setFixedHeight(140)
        self.oidc_issuer = QLineEdit()
        self.oidc_issuer.setText(oidc["issuer"] or DEFAULT_OIDC_ISSUER)
        self.oidc_issuer.setPlaceholderText(DEFAULT_OIDC_ISSUER)
        self.oidc_client_id = QLineEdit()
        self.oidc_client_id.setText(oidc["client_id"] or DEFAULT_OIDC_CLIENT_ID)
        self.oidc_scopes = QLineEdit()
        self.oidc_scopes.setText(oidc["scopes"] or DEFAULT_OIDC_SCOPES)
        self.oidc_redirect = QLineEdit()
        self.oidc_redirect.setText(oidc.get("redirect_uri") or DEFAULT_OIDC_REDIRECT_URI)
        self.oidc_redirect.setPlaceholderText(DEFAULT_OIDC_REDIRECT_URI)
        self.oidc_registration = QLineEdit()
        self.oidc_registration.setText(oidc["registration_url"] or DEFAULT_OIDC_REGISTRATION_URL)
        self.oidc_register_btn = QPushButton("Open registration")
        self.oidc_register_btn.setAutoDefault(False)
        self.oidc_register_btn.setDefault(False)
        self.oidc_register_btn.clicked.connect(self._open_oidc_registration)

        self.error_to = QPlainTextEdit(format_error_email_to(email.get(ERROR_EMAIL_TO_KEY)))
        self.event_to = QPlainTextEdit(format_error_email_to(email.get(EVENT_EMAIL_TO_KEY)))
        self.new_case_to = QPlainTextEdit(format_error_email_to(email.get(NEW_CASE_EMAIL_TO_KEY)))
        for box in (self.error_to, self.event_to, self.new_case_to):
            box.setFixedHeight(72)
        self.email_from = QLineEdit(str(email.get("email_from") or ""))
        self.email_domain = QLineEdit(str(email.get("email_domain") or ""))
        self.email_host = QLineEdit(str(email.get("email_host_address") or ""))
        self.email_port = QLineEdit(str(email.get("email_host_port") or 25))

        self.google = QLineEdit(hooks.get("google_chat") or "")
        self.slack = QLineEdit(hooks.get("slack") or "")
        self.teams = QLineEdit(hooks.get("microsoft_teams") or "")
        self.discord = QLineEdit(hooks.get("discord") or "")

        viewer = self._data.get("Viewer") if isinstance(self._data.get("Viewer"), dict) else {}
        self.labeler_path = QLineEdit(str((viewer or {}).get("vtk_image_labeler_3d") or ""))
        self.labeler_path.setPlaceholderText(r"C:\apps\vtk_image_labeler_3d.exe")

        watcher = watcher_settings(self._data)
        self.watch_path = QLineEdit(str(watcher.get("watch_path") or ""))
        self.watch_path.setPlaceholderText(r"\\fileserver\QA\CT_Import")
        self.watch_data_root = QLineEdit(str(watcher.get("data_root") or ""))
        self.watch_case_regex = QLineEdit(watcher_case_folder_regex(watcher))
        self.watch_case_regex.setPlaceholderText(DEFAULT_CASE_FOLDER_REGEX)
        self.watch_min_files = _int_spin(watcher.get("min_num_of_files") or 457, 1, 10000)
        self.watch_poll_sec = _float_spin(watcher.get("queued_case_poll_sec") or 10.0, 1.0, 600.0)
        self.watch_max_cycles = _int_spin(watcher.get("max_wait_cycles") or 180, 1, 10000)
        self.watch_disk_scan = QCheckBox("Scan disk for missed case folders (UNC backup)")
        self.watch_disk_scan.setChecked(_as_bool(watcher.get("disk_scan_for_new_case_detection"), True))
        self.watch_disk_scan_sec = _float_spin(
            watcher.get("disk_scan_for_new_case_detection_sec") or 60.0, 5.0, 3600.0
        )
        self.watch_min_series = _int_spin(watcher.get("min_series_dicom_files") or 100, 1, 10000)
        self.watch_temp_days = _int_spin(
            watcher.get(TEMP_CLEANUP_OLDER_THAN_DAYS_KEY) or DEFAULT_TEMP_CLEANUP_OLDER_THAN_DAYS,
            1,
            365,
        )

        df = {**default_docuforms2_mpc_step(), **find_docuforms2_step(self._data)}
        self.df_enabled = QCheckBox("Upload MPC results to DocuForms2 after analysis")
        self.df_enabled.setChecked(_as_bool(df.get("enabled", True)))
        self.df_backend = QLineEdit()
        self.df_backend.setText(str(df.get("backend_url") or ""))
        self.df_backend.setPlaceholderText("https://docuforms.example.edu:9001")
        self.df_verify_ssl = QCheckBox("Verify SSL")
        self.df_verify_ssl.setChecked(_as_bool(df.get("verify_ssl", False)))
        self.df_dry_run = QCheckBox("Dry run (parse only, do not submit)")
        self.df_dry_run.setChecked(_as_bool(df.get("dry_run", False)))
        self.df_zip = QCheckBox("Attach input_dcm.zip")
        self.df_zip.setChecked(_as_bool(df.get("attach_dcm_zip", True)))
        self.df_pdf = QCheckBox("Attach report.pdf (full report.html)")
        self.df_pdf.setChecked(_as_bool(df.get("attach_pdf", True)))
        self.df_resubmit = QCheckBox("Resubmit cases that already have .docuforms2_mpc.json")
        self.df_resubmit.setChecked(_as_bool(df.get("resubmit", False)))
        self.df_timeout = QSpinBox()
        self.df_timeout.setRange(30, 3600)
        try:
            self.df_timeout.setValue(int(df.get("timeout_sec") or 300))
        except (TypeError, ValueError):
            self.df_timeout.setValue(300)
        self.df_form_ids = QPlainTextEdit()
        form_id_rows = form_ids_from_step(df) or form_ids_from_machines(self._data.get(MACHINES_KEY))
        self.df_form_ids.setPlainText(format_form_ids(form_id_rows))
        self.df_form_ids.setPlaceholderText("GECTSH = pfcc_gectsh_mpc")
        self.df_form_ids.setTabChangesFocus(True)
        self.df_form_ids.setFixedHeight(110)
        self.df_email_success_event_to = QPlainTextEdit()
        self.df_email_success_event_to.setPlainText(
            format_error_email_to(df.get("email_success_event_to"))
        )
        self.df_email_success_event_to.setPlaceholderText("one address per line")
        self.df_email_success_event_to.setTabChangesFocus(True)
        self.df_email_success_event_to.setFixedHeight(72)
        self.df_email_failure_event_to = QPlainTextEdit()
        self.df_email_failure_event_to.setPlainText(
            format_error_email_to(df.get("email_failure_event_to"))
        )
        self.df_email_failure_event_to.setPlaceholderText("one address per line")
        self.df_email_failure_event_to.setTabChangesFocus(True)
        self.df_email_failure_event_to.setFixedHeight(72)

        tabs = QTabWidget()
        tabs.addTab(self._general_page(), "General")
        tabs.addTab(self._identity_page(), "Identity")

        email_tab = QWidget()
        ef = QFormLayout(email_tab)
        ef.addRow("error_email_to", self.error_to)
        ef.addRow("event_email_to", self.event_to)
        ef.addRow("new_case_email_to", self.new_case_to)
        ef.addRow("email_from", self.email_from)
        ef.addRow("email_domain", self.email_domain)
        ef.addRow("email_host_address", self.email_host)
        ef.addRow("email_host_port", self.email_port)
        test_email = QPushButton("Send test email")
        test_email.clicked.connect(self._test_email)
        ef.addRow("", test_email)
        tabs.addTab(email_tab, "Email")

        chat_tab = QWidget()
        cf = QFormLayout(chat_tab)
        for label, edit, channel in (
            ("Google Chat", self.google, "google_chat"),
            ("Slack", self.slack, "slack"),
            ("Microsoft Teams", self.teams, "microsoft_teams"),
            ("Discord", self.discord, "discord"),
        ):
            btn = QPushButton("Send test")
            btn.clicked.connect(lambda *_a, c=channel: self._test_chat(c))
            box = QGroupBox(label)
            inner = QFormLayout(box)
            inner.addRow("webhook_url", edit)
            inner.addRow("", btn)
            cf.addRow(box)
        tabs.addTab(chat_tab, "Chat webhooks")

        viewer_tab = QWidget()
        vf = QFormLayout(viewer_tab)
        vf.addRow("vtk_image_labeler_3d", self.labeler_path)
        vf.addRow(
            "",
            _hint_label(
                "Path to vtk_image_labeler_3d.exe if it is not on PATH. "
                "Edit masks writes vtk_image_labeler_3d.project.json next to the CT."
            ),
        )
        tabs.addTab(viewer_tab, "Viewer")
        tabs.addTab(self._postprocess_page(), "Post-processing")
        tabs.addTab(self._watcher_page(), "Watcher")

        self.save_btn = QPushButton("Save")
        self.close_btn = QPushButton("Close")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        self.close_btn.clicked.connect(self.reject)
        buttons = QWidget()
        button_row = QHBoxLayout(buttons)
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        button_row.addWidget(self.save_btn)
        button_row.addWidget(self.close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)
        self.resize(960, 680)
        self._restore_window_state()

    def _general_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.addRow("Institution", self.institution)
        form.addRow("RunMode", self.run_mode)
        form.addRow("Settings file", self.path_label)
        layout.addLayout(form)
        layout.addWidget(
            _hint_label(
                "Clinic: Open Case picks a configured machine, then a case under cases_dir. "
                "Simple: Open Case picks a case folder; the parent folder is the machine name. "
                "Simple is also used when this file is missing or MACHINES is empty. "
                "Login is on the Identity tab. The Windows watch service is on the Watcher tab. "
                "Error alerts (email and chat) are on the Email "
                "and Chat webhooks tabs. DocuForms2 upload is on Post-processing. "
                "Per-user email and My machines are in User settings "
                "on the heading bar."
            )
        )
        layout.addStretch(1)
        return page

    def _identity_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.addWidget(self._identity_group())
        layout.addStretch(1)
        return _scroll_page(inner)

    def _identity_group(self) -> QGroupBox:
        form = QFormLayout()
        form.addRow("user_id_method", self.user_id_method)
        form.addRow("OS user (this PC)", self.os_user_preview)
        form.addRow("issuer", self.oidc_issuer)
        form.addRow("client_id", self.oidc_client_id)
        form.addRow("scopes", self.oidc_scopes)
        form.addRow("redirect_uri", self.oidc_redirect)
        form.addRow("registration_url", self.oidc_registration)
        form.addRow("", self.oidc_register_btn)
        box = QGroupBox("Identity")
        root = QVBoxLayout(box)
        root.addLayout(form)
        root.addWidget(
            _hint_label(
                "OSUser is the default: Windows / Linux / macOS login (no extra prompt). "
                "None: no user id. OIDC is the protocol; Keycloak is the issuer. "
                "At startup with OIDC the app opens a Sign in window, then your browser "
                "(Authorization Code + PKCE). Create a dedicated public client (ctqa-mpc). "
                "Add redirect_uri exactly to Valid redirect URIs; use 127.0.0.1, not localhost "
                "(http://127.0.0.1:17844/callback). Standard flow + PKCE S256. "
                "registration_url is the Account Console ({issuer}/account/). "
                "Profiles are JSON files in _users next to the executable, or --users DIR. "
                "Email, My machines, and new QA case emails are in User settings on the heading bar."
            )
        )
        self._sync_identity_ui()
        return box

    def _postprocess_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        form = QFormLayout()
        form.addRow("", self.df_enabled)
        form.addRow("backend_url", self.df_backend)
        form.addRow("", self.df_verify_ssl)
        form.addRow("", self.df_dry_run)
        form.addRow("", self.df_zip)
        form.addRow("", self.df_pdf)
        form.addRow("", self.df_resubmit)
        form.addRow("timeout_sec", self.df_timeout)
        form.addRow("form_ids", self.df_form_ids)
        form.addRow("email_success_event_to", self.df_email_success_event_to)
        form.addRow("email_failure_event_to", self.df_email_failure_event_to)
        box = QGroupBox("DocuForms2 MPC")
        root = QVBoxLayout(box)
        root.addLayout(form)
        root.addWidget(
            _hint_label(
                "After analysis, CTQA-MPC can push the HTML report to DocuForms2 "
                "(same pattern as CatPhan and Winston-Lutz). "
                "form_ids maps machine NAME to a DocuForms2 form (one per line: "
                "GECTSH = pfcc_gectsh_mpc). A machine with no row is skipped. "
                "Successful uploads write .docuforms2_mpc.json in the case folder so the "
                "watcher does not submit twice. Cases are not moved. "
                "email_success_event_to gets ok / dry-run; email_failure_event_to gets failed "
                "(clinic SMTP from Email). Skipped cases are not emailed. "
                "More PostProcessing types can be added later in JSON."
            )
        )
        layout.addWidget(box)
        layout.addStretch(1)
        return _scroll_page(inner)

    def _watcher_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.addWidget(self._watcher_group())
        if is_windows():
            install_btn = QPushButton("Install Watcher as Service")
            install_btn.clicked.connect(self._install_watch_service)
            layout.addWidget(install_btn)
        layout.addStretch(1)
        return _scroll_page(inner)

    def _watcher_group(self) -> QGroupBox:
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.addRow("watch_path", self.watch_path)
        form.addRow("data_root", self.watch_data_root)
        form.addRow("CASE_FOLDER_NAME_REGEX", self.watch_case_regex)
        form.addRow("min_num_of_files", self.watch_min_files)
        form.addRow("queued_case_poll_sec", self.watch_poll_sec)
        form.addRow("max_wait_cycles", self.watch_max_cycles)
        form.addRow("disk_scan_for_new_case_detection", self.watch_disk_scan)
        form.addRow("disk_scan_for_new_case_detection_sec", self.watch_disk_scan_sec)
        form.addRow("min_series_dicom_files", self.watch_min_series)
        form.addRow("temp_cleanup_older_than_days", self.watch_temp_days)
        box = QGroupBox("Watcher (Windows service)")
        root = QVBoxLayout(box)
        root.addLayout(form)
        root.addWidget(
            _hint_label(
                "Used by CTQAMPC.exe --mode service or python -m ctqa_mpc watch "
                "(Windows service via NSSM), not the GUI. "
                "watch_path is the MPC import share. "
                "CASE_FOLDER_NAME_REGEX must full-match the import folder name "
                "(default MMDDYYYY_MPC). Leave empty to accept any folder name. "
                "min_num_of_files waits until the import folder is complete. "
                "queued_case_poll_sec is how often that count is checked. "
                "disk_scan_for_new_case_detection walks the share for folders watchdog missed. "
                "Sort and analyze run in the Windows user temp folder; "
                "the finished case folder is copied to the machine cases_dir. "
                "temp_cleanup_older_than_days deletes leftover ctqa_sort_* folders "
                "older than that many days after each analysis (background thread). "
                "Analysis settings come from MACHINES."
            )
        )
        return box

    def _install_watch_service(self) -> None:
        dlg = InstallWatchServiceDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        plan = dlg.plan()
        if not plan.nssm_exe:
            QMessageBox.warning(
                self,
                "NSSM not found",
                "nssm.exe was not found. Install NSSM from https://nssm.cc, "
                "add it to PATH, or choose nssm.exe in the dialog.",
            )
            QDesktopServices.openUrl(QUrl("https://nssm.cc"))
            return
        if plan.account and not plan.password:
            if (
                QMessageBox.question(
                    self,
                    "No password",
                    "Account is set but the password is empty. Install anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
        save = QMessageBox.question(
            self,
            "Save settings",
            "Save the current settings to disk before installing the service?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if save == QMessageBox.Cancel:
            return
        if save == QMessageBox.Yes:
            self._persist()
        if not is_admin():
            commands = format_nssm_commands(plan)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Administrator required")
            box.setText(
                "Installing a Windows service requires Administrator. "
                "Run this GUI as Administrator and try again, or run the NSSM commands "
                "in an elevated prompt (password is shown as <password>)."
            )
            box.setDetailedText(commands)
            box.exec_()
            return
        ok, log = install_watch_service(plan)
        if ok:
            LogTextDialog(self, "Watcher service", "Service installed.", log).exec_()
        else:
            LogTextDialog(self, "Watcher service", "Install failed.", log).exec_()

    def _open_oidc_registration(self) -> None:
        issuer = self.oidc_issuer.text().strip() or DEFAULT_OIDC_ISSUER
        url = coerce_registration_url(self.oidc_registration.text(), issuer)
        self.oidc_registration.setText(url)
        QDesktopServices.openUrl(QUrl(url))

    def _sync_identity_ui(self, _text: str = "") -> None:
        method = self.user_id_method.currentText() or USER_ID_OSUSER
        oidc_on = method == USER_ID_OIDC
        for widget in (
            self.oidc_issuer,
            self.oidc_client_id,
            self.oidc_scopes,
            self.oidc_redirect,
            self.oidc_registration,
            self.oidc_register_btn,
        ):
            widget.setEnabled(oidc_on)
        if method == USER_ID_OSUSER:
            self.os_user_preview.setPlainText(format_os_user_preview())
        elif method == USER_ID_OIDC:
            issuer = self.oidc_issuer.text().strip() or DEFAULT_OIDC_ISSUER
            self.os_user_preview.setPlainText(
                "At startup you will get a Sign in window, then Keycloak in the browser.\n"
                f"issuer: {issuer}\n"
                f"client_id: {self.oidc_client_id.text().strip() or DEFAULT_OIDC_CLIENT_ID}\n"
                f"redirect_uri: {self.oidc_redirect.text().strip() or DEFAULT_OIDC_REDIRECT_URI}\n"
                "That redirect_uri must be allowed on the Keycloak client."
            )
        else:
            self.os_user_preview.setPlainText("No user id (clinic-wide settings only).")

    def _email_block(self) -> dict:
        return {
            ERROR_EMAIL_TO_KEY: error_email_to_list(self.error_to.toPlainText()),
            EVENT_EMAIL_TO_KEY: error_email_to_list(self.event_to.toPlainText()),
            NEW_CASE_EMAIL_TO_KEY: error_email_to_list(self.new_case_to.toPlainText()),
            "email_from": self.email_from.text().strip(),
            "email_domain": self.email_domain.text().strip(),
            "email_host_address": self.email_host.text().strip(),
            "email_host_port": int(self.email_port.text().strip() or 25),
            "enable_ssl": bool((email_settings(self._data) or {}).get("enable_ssl")),
            "email_from_enc_pw": str((email_settings(self._data) or {}).get("email_from_enc_pw") or ""),
        }

    def _preview(self) -> dict:
        data = json.loads(json.dumps(self._data))
        data[INSTITUTION_KEY] = self.institution.text().strip()
        data[RUN_MODE_KEY] = self.run_mode.currentText() or RUN_MODE_CLINIC
        ident = identity_block(self._data)
        ident["user_id_method"] = self.user_id_method.currentText() or USER_ID_OSUSER
        ident["oidc"] = {
            "issuer": self.oidc_issuer.text().strip() or DEFAULT_OIDC_ISSUER,
            "client_id": self.oidc_client_id.text().strip() or DEFAULT_OIDC_CLIENT_ID,
            "scopes": self.oidc_scopes.text().strip() or DEFAULT_OIDC_SCOPES,
            "redirect_uri": self.oidc_redirect.text().strip() or DEFAULT_OIDC_REDIRECT_URI,
            "registration_url": coerce_registration_url(
                self.oidc_registration.text().strip(),
                self.oidc_issuer.text().strip() or DEFAULT_OIDC_ISSUER,
            ),
        }
        data[IDENTITY_KEY] = ident
        notes = data.setdefault("Notifications", {})
        notes["email"] = self._email_block()
        notes["google_chat"] = {"webhook_url": self.google.text().strip()}
        notes["slack"] = {"webhook_url": self.slack.text().strip()}
        notes["microsoft_teams"] = {"webhook_url": self.teams.text().strip()}
        notes["discord"] = {"webhook_url": self.discord.text().strip()}
        viewer = data.get("Viewer") if isinstance(data.get("Viewer"), dict) else {}
        viewer = dict(viewer)
        viewer["vtk_image_labeler_3d"] = self.labeler_path.text().strip()
        data["Viewer"] = viewer
        watcher = dict(watcher_settings(data))
        watcher.update(
            {
                "watch_path": self.watch_path.text().strip(),
                "data_root": self.watch_data_root.text().strip(),
                CASE_FOLDER_NAME_REGEX_KEY: self.watch_case_regex.text().strip(),
                "min_num_of_files": self.watch_min_files.value(),
                "queued_case_poll_sec": self.watch_poll_sec.value(),
                "max_wait_cycles": self.watch_max_cycles.value(),
                "disk_scan_for_new_case_detection": self.watch_disk_scan.isChecked(),
                "disk_scan_for_new_case_detection_sec": self.watch_disk_scan_sec.value(),
                "min_series_dicom_files": self.watch_min_series.value(),
                TEMP_CLEANUP_OLDER_THAN_DAYS_KEY: self.watch_temp_days.value(),
            }
        )
        watcher.pop("temp_dir", None)
        watcher.pop("dicom_sort_base_dir", None)
        watcher.pop("temp_cleanup_weekday", None)
        watcher.pop("temp_cleanup_at", None)
        watcher.pop("directory_name_contains", None)
        watcher.pop("case_folder_name_regex", None)
        data[WATCHER_KEY] = watcher
        df_step = {
            **default_docuforms2_mpc_step(),
            "type": DOCUFORMS2_MPC_TYPE,
            "enabled": self.df_enabled.isChecked(),
            "backend_url": self.df_backend.text().strip(),
            "verify_ssl": self.df_verify_ssl.isChecked(),
            "dry_run": self.df_dry_run.isChecked(),
            "attach_dcm_zip": self.df_zip.isChecked(),
            "attach_pdf": self.df_pdf.isChecked(),
            "resubmit": self.df_resubmit.isChecked(),
            "timeout_sec": self.df_timeout.value(),
            "form_ids": parse_form_ids_text(self.df_form_ids.toPlainText()),
            "email_success_event_to": error_email_to_list(
                self.df_email_success_event_to.toPlainText()
            ),
            "email_failure_event_to": error_email_to_list(
                self.df_email_failure_event_to.toPlainText()
            ),
        }
        existing = [
            step
            for step in post_processing_steps(self._data)
            if str(step.get("type") or "").strip() not in (DOCUFORMS2_MPC_TYPE, "docuforms2_ctqa")
        ]
        data[POST_PROCESSING_KEY] = upsert_post_step(existing, df_step)
        machines = data.get(MACHINES_KEY)
        if isinstance(machines, list):
            for machine in machines:
                if isinstance(machine, dict):
                    machine.pop("docuforms2_form_id", None)
        return data

    def _test_email(self):
        try:
            send_test_email(self._preview())
            QMessageBox.information(self, "Test email", "Sent.")
        except Exception as exc:
            QMessageBox.critical(self, "Test email", str(exc))

    def _test_chat(self, channel: str):
        try:
            send_test_chat(channel, self._preview())
            QMessageBox.information(self, "Test chat", "Posted.")
        except Exception as exc:
            QMessageBox.critical(self, "Test chat", str(exc))

    def _restore_window_state(self) -> None:
        geom = self._qs.value("settings/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
            return
        QTimer.singleShot(0, self.showMaximized)

    def _save_window_state(self) -> None:
        self._qs.setValue("settings/geometry", self.saveGeometry())
        self._qs.sync()

    def accept(self) -> None:
        self._save_window_state()
        super().accept()

    def reject(self) -> None:
        self._save_window_state()
        super().reject()

    def closeEvent(self, event):
        self._save_window_state()
        super().closeEvent(event)

    def _persist(self) -> None:
        data = self._preview()
        save_settings(data, settings_path(writing=True))
        if get_user_id_method(data) == USER_ID_OSUSER:
            upsert_os_user_profile()
        self._data = data
        self.did_save = True

    def _save(self):
        self._persist()
        self.accept()
