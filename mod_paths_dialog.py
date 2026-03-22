import os
from PyQt5.QtWidgets import (
    QFileDialog, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QMessageBox
)
from settings_dialog import load_config, save_config

DEFAULT_MOD_PATHS = {
    "factorio_mods_path": os.path.join(os.environ["APPDATA"], "Factorio", "mods"),
    "minecraft_mods_path": os.path.join(os.environ["APPDATA"], ".minecraft", "mods"),
}


class ModPathsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Carpetas de Mods")
        self.config = load_config()
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Carpeta de mods (Factorio):"))
        self.factorio_path_edit = QLineEdit()
        self.factorio_path_edit.setText(
            self.config.get("factorio_mods_path", DEFAULT_MOD_PATHS["factorio_mods_path"])
        )
        self.factorio_path_edit.setReadOnly(False)
        factorio_btn = QPushButton("📁")
        factorio_btn.setFixedWidth(30)
        factorio_btn.clicked.connect(self.choose_factorio_folder)
        factorio_layout = QHBoxLayout()
        factorio_layout.addWidget(self.factorio_path_edit)
        factorio_layout.addWidget(factorio_btn)
        layout.addLayout(factorio_layout)

        layout.addWidget(QLabel("Carpeta de mods (Minecraft):"))
        self.minecraft_path_edit = QLineEdit()
        self.minecraft_path_edit.setText(
            self.config.get("minecraft_mods_path", DEFAULT_MOD_PATHS["minecraft_mods_path"])
        )
        self.minecraft_path_edit.setReadOnly(False)
        minecraft_btn = QPushButton("📁")
        minecraft_btn.setFixedWidth(30)
        minecraft_btn.clicked.connect(self.choose_minecraft_folder)
        minecraft_layout = QHBoxLayout()
        minecraft_layout.addWidget(self.minecraft_path_edit)
        minecraft_layout.addWidget(minecraft_btn)
        layout.addLayout(minecraft_layout)

        buttons_layout = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        cancel_btn = QPushButton("Cancelar")
        save_btn.clicked.connect(self.save_and_close)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(save_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def choose_factorio_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de mods (Factorio)")
        if folder:
            self.factorio_path_edit.setText(folder)

    def choose_minecraft_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de mods (Minecraft)")
        if folder:
            self.minecraft_path_edit.setText(folder)

    def save_and_close(self):
        factorio_path = self.factorio_path_edit.text().strip() or self.config.get(
            "factorio_mods_path", DEFAULT_MOD_PATHS["factorio_mods_path"]
        )
        minecraft_path = self.minecraft_path_edit.text().strip() or self.config.get(
            "minecraft_mods_path", DEFAULT_MOD_PATHS["minecraft_mods_path"]
        )

        if not self.ensure_folder_exists(factorio_path, "Carpeta de mods (Factorio)"):
            return
        if not self.ensure_folder_exists(minecraft_path, "Carpeta de mods (Minecraft)"):
            return

        self.config["factorio_mods_path"] = factorio_path
        self.config["minecraft_mods_path"] = minecraft_path
        save_config(self.config)
        self.accept()

    def ensure_folder_exists(self, folder_path, label):
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            return True
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Carpeta no encontrada")
        msg.setText(f"{label} no existe:\n\n{folder_path}")
        msg.setInformativeText("¿Deseas crearla?")
        create_btn = msg.addButton("Crear carpeta", QMessageBox.AcceptRole)
        msg.addButton("Cancelar", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() == create_btn:
            try:
                os.makedirs(folder_path, exist_ok=True)
                return True
            except Exception as e:
                QMessageBox.critical(self, "Error al crear carpeta", f"No se pudo crear la carpeta:\n{str(e)}")
                return False
        return False
