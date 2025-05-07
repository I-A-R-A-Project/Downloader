import json
import os
from PyQt5.QtWidgets import QFileDialog,QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QSpinBox, QPushButton,QLineEdit,QMessageBox

CONFIG_PATH = "config.json"
DEFAULT_CONFIG = {
    "folder_path": os.path.join(os.environ["USERPROFILE"], "Downloads"),
    "open_on_finish": False,
    "max_parallel_downloads": 2
}
def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    with open(CONFIG_PATH, "r") as f:
        return json.load(f) 
def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración ⚙")
        self.config = load_config()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Carpeta de Descarga
        self.folder_path_edit = QLineEdit()
        self.folder_path_edit.setPlaceholderText(self.config.get("folder_path",DEFAULT_CONFIG["folder_path"]))
        self.folder_path_edit.setReadOnly(False)
        self.select_folder_btn = QPushButton()
        self.select_folder_btn.setText('📁')
        self.select_folder_btn.setFixedWidth(30)
        self.select_folder_btn.clicked.connect(self.choose_folder)
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(self.folder_path_edit)
        folder_layout.addWidget(self.select_folder_btn)
        layout.addLayout(folder_layout)

        # Check: Abrir carpeta al terminar
        self.open_folder_cb = QCheckBox("Abrir carpeta al finalizar")
        self.open_folder_cb.setChecked(self.config.get("open_on_finish", DEFAULT_CONFIG["open_on_finish"]))
        layout.addWidget(self.open_folder_cb)

        # SpinBox: Máximo de descargas paralelas
        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Máx. descargas paralelas:"))
        self.max_downloads_spin = QSpinBox()
        self.max_downloads_spin.setMinimum(1)
        self.max_downloads_spin.setMaximum(20)
        self.max_downloads_spin.setValue(self.config.get("max_parallel_downloads", DEFAULT_CONFIG["max_parallel_downloads"]))
        hbox.addWidget(self.max_downloads_spin)
        layout.addLayout(hbox)

        # Botones: Guardar / Cancelar
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

    def save_and_close(self):
        folder_path = self.folder_path_edit.text()
        if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Critical)
            msg.setWindowTitle("Carpeta no encontrada")
            msg.setText("La carpeta especificada no existe:\n\n" + folder_path)
            msg.setInformativeText("¿Deseas crearla?")
            create_btn = msg.addButton("Crear carpeta", QMessageBox.AcceptRole)
            cancel_btn = msg.addButton("Cancelar", QMessageBox.RejectRole)
            msg.exec_()
            if msg.clickedButton() == create_btn:
                try:
                    os.makedirs(folder_path)
                except Exception as e:
                    QMessageBox.critical(self, "Error al crear carpeta", f"No se pudo crear la carpeta:\n{str(e)}")
                    return
            else:
                return

        self.config["folder_path"] = folder_path
        self.config["open_on_finish"] = self.open_folder_cb.isChecked()
        self.config["max_parallel_downloads"] = self.max_downloads_spin.value()
        save_config(self.config)
        self.accept()

    def get_config(self):
        return self.config
