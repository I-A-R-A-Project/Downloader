import os
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)
from config import DEFAULT_CONFIG, load_config, normalize_path, save_config


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.config = load_config()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        self.folder_path_edit = QLineEdit()
        self.folder_path_edit.setText(self.config.get("folder_path", DEFAULT_CONFIG["folder_path"]))
        self.folder_path_edit.setReadOnly(False)
        self.select_folder_btn = QPushButton("📁")
        self.select_folder_btn.setFixedWidth(30)
        self.select_folder_btn.clicked.connect(self.choose_folder)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(self.folder_path_edit)
        folder_layout.addWidget(self.select_folder_btn)
        layout.addLayout(folder_layout)

        self.open_folder_cb = QCheckBox("Abrir carpeta al finalizar")
        self.open_folder_cb.setChecked(self.config.get("open_on_finish", DEFAULT_CONFIG["open_on_finish"]))
        layout.addWidget(self.open_folder_cb)

        finish_layout = QHBoxLayout()
        finish_layout.addWidget(QLabel("Al finalizar todas las descargas:"))
        self.on_complete_combo = QComboBox()
        self.on_complete_combo.addItem("No hacer nada", "none")
        self.on_complete_combo.addItem("Cerrar programa", "close")
        self.on_complete_combo.addItem("Apagar computadora", "shutdown")
        saved_action = self.config.get(
            "on_all_downloads_complete",
            DEFAULT_CONFIG["on_all_downloads_complete"],
        )
        combo_index = self.on_complete_combo.findData(saved_action)
        self.on_complete_combo.setCurrentIndex(combo_index if combo_index >= 0 else 0)
        finish_layout.addWidget(self.on_complete_combo)
        layout.addLayout(finish_layout)

        self.auto_extract_cb = QCheckBox("Descomprimir al finalizar")
        self.auto_extract_cb.setChecked(
            self.config.get("auto_extract_archives", DEFAULT_CONFIG["auto_extract_archives"])
        )
        layout.addWidget(self.auto_extract_cb)

        self.delete_archive_cb = QCheckBox("Eliminar comprimido después de descomprimir")
        self.delete_archive_cb.setChecked(
            self.config.get("delete_archive_after_extract", DEFAULT_CONFIG["delete_archive_after_extract"])
        )
        layout.addWidget(self.delete_archive_cb)
        self.auto_extract_cb.toggled.connect(self.sync_extract_options)
        self.sync_extract_options(self.auto_extract_cb.isChecked())

        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Máx. descargas paralelas:"))
        self.max_downloads_spin = QSpinBox()
        self.max_downloads_spin.setMinimum(1)
        self.max_downloads_spin.setMaximum(20)
        self.max_downloads_spin.setValue(
            self.config.get("max_parallel_downloads", DEFAULT_CONFIG["max_parallel_downloads"])
        )
        hbox.addWidget(self.max_downloads_spin)
        layout.addLayout(hbox)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Modo por defecto:"))
        self.default_mode_combo = QComboBox()
        self.default_mode_combo.addItem("GUI", "gui")
        self.default_mode_combo.addItem("TUI", "tui")
        saved_mode = self.config.get("download_manager_mode", DEFAULT_CONFIG["download_manager_mode"])
        mode_index = self.default_mode_combo.findData(saved_mode)
        self.default_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        mode_layout.addWidget(self.default_mode_combo)
        layout.addLayout(mode_layout)

        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        save_btn.clicked.connect(self.save_and_close)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if folder:
            self.folder_path_edit.setText(folder)

    def sync_extract_options(self, auto_extract_enabled):
        self.delete_archive_cb.setEnabled(auto_extract_enabled)
        if not auto_extract_enabled:
            self.delete_archive_cb.setChecked(False)

    def save_and_close(self):
        folder_path = normalize_path(self.folder_path_edit.text())
        if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Critical)
            msg.setWindowTitle("Carpeta no encontrada")
            msg.setText("La carpeta especificada no existe:\n\n" + folder_path)
            msg.setInformativeText("¿Deseas crearla?")
            create_btn = msg.addButton("Crear carpeta", QMessageBox.AcceptRole)
            msg.addButton("Cancelar", QMessageBox.RejectRole)
            msg.exec_()
            if msg.clickedButton() == create_btn:
                try:
                    os.makedirs(folder_path)
                except Exception as exc:
                    QMessageBox.critical(self, "Error al crear carpeta", f"No se pudo crear la carpeta:\n{exc}")
                    return
            else:
                return

        self.config["folder_path"] = folder_path
        self.config["open_on_finish"] = self.open_folder_cb.isChecked()
        self.config["on_all_downloads_complete"] = self.on_complete_combo.currentData()
        self.config["auto_extract_archives"] = self.auto_extract_cb.isChecked()
        self.config["delete_archive_after_extract"] = (
            self.delete_archive_cb.isChecked() if self.auto_extract_cb.isChecked() else False
        )
        self.config["max_parallel_downloads"] = self.max_downloads_spin.value()
        self.config["download_manager_mode"] = self.default_mode_combo.currentData()
        save_config(self.config)
        self.accept()


class DownloadDetailsDialog(QDialog):
    def __init__(self, urls, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Detalles de descarga")
        self.resize(600, 400)

        self.config = load_config()
        self.default_path = self.config.get("folder_path", DEFAULT_CONFIG["folder_path"])
        self.entries = []

        layout = QVBoxLayout(self)
        for url in urls:
            form = QFormLayout()
            url_label = QLabel(url)
            url_label.setWordWrap(True)

            pass_input = QLineEdit()
            pass_input.setEchoMode(QLineEdit.Password)

            form.addRow(QLabel("<b>URL:</b>"), url_label)
            form.addRow("Contraseña:", pass_input)

            path_input = QLineEdit(self.default_path)
            browse_btn = QPushButton("📁")
            browse_btn.setFixedWidth(30)
            browse_btn.clicked.connect(lambda _, p=path_input: self.choose_path(p))
            path_container = QHBoxLayout()
            path_container.addWidget(path_input)
            path_container.addWidget(browse_btn)
            form.addRow("Guardar en:", path_container)

            layout.addLayout(form)
            self.entries.append({
                "url": url,
                "password_widget": pass_input,
                "path_widget": path_input,
            })

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def choose_path(self, path_input):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", path_input.text())
        if folder:
            path_input.setText(folder)

    def get_results(self):
        return [{
            "url": entry["url"],
            "password": entry["password_widget"].text().strip(),
            "path": normalize_path(entry["path_widget"].text().strip()),
        } for entry in self.entries]


class LinkInputWindow(QWidget):
    links_ready = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pegar enlaces de paginas de descarga")
        self.setMinimumSize(400, 200)

        layout = QVBoxLayout()
        self.instructions = QLabel("Pega uno o más enlaces (uno por línea):")
        self.textbox = QTextEdit()
        self.accept_button = QPushButton("Iniciar Descargas")
        self.accept_button.clicked.connect(self.proceed)
        self.settings_button = QPushButton("⚙")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.settings_button.setFixedWidth(25)

        layout.addWidget(self.instructions)
        layout.addWidget(self.textbox)
        buttons = QHBoxLayout()
        buttons.addWidget(self.accept_button)
        buttons.addWidget(self.settings_button)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self.links = []

    def open_settings_dialog(self):
        dialog = SettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            apply_settings()

    def proceed(self):
        text = self.textbox.toPlainText().strip()
        if not text:
            return
        urls = [line.strip() for line in text.splitlines() if line.strip()]
        if not urls:
            return
        dialog = DownloadDetailsDialog(urls, self)
        if dialog.exec_() == QDialog.Accepted:
            self.links = dialog.get_results()
            self.links_ready.emit(self.links)
            self.close()


def apply_settings():
    config = load_config()
    folder_path = config.get("folder_path")
    open_on_finish = config.get("open_on_finish")
    on_all_downloads_complete = config.get("on_all_downloads_complete")
    auto_extract_archives = config.get("auto_extract_archives")
    delete_archive_after_extract = config.get("delete_archive_after_extract")
    max_parallel_downloads = config.get("max_parallel_downloads")
    download_manager_mode = config.get("download_manager_mode")
    print(f"Configuración actualizada: {config}")
    return (
        folder_path,
        open_on_finish,
        on_all_downloads_complete,
        auto_extract_archives,
        delete_archive_after_extract,
        max_parallel_downloads,
        download_manager_mode,
    )
