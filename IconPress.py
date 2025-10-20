import os
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog,
    QTabWidget, QComboBox, QCheckBox, QHBoxLayout, QLineEdit, QMessageBox,
    QStyleFactory, QGridLayout, QStyle, QSizePolicy
)
from PyQt5.QtGui import QPixmap, QIcon
from PyQt5.QtCore import Qt
from PIL import Image


class ImageConverter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IconPress")
        self.setWindowIcon(QIcon.fromTheme("applications-graphics"))
        self.setStyle(QStyleFactory.create("Fusion"))
        # Allow the window to be resizable so UI texts are not clipped
        self.resize(900, 520)

        self.input_path = ""
        self.output_dir = ""

        self.apply_mac_style()
        self.init_ui()

    def apply_mac_style(self):
        self.setStyleSheet("""
            QWidget {
                font-family: 'San Francisco', 'Segoe UI', sans-serif;
                font-size: 13px;
                background-color: #f9f9f9;
            }
            QLineEdit {
                border: 1px solid #ccc;
                border-radius: 6px;
                padding: 5px;
                background-color: white;
            }
            QPushButton {
                background-color: #007aff;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #006ae6;
            }
            QTabWidget::pane {
                border: 1px solid #ccc;
                border-radius: 8px;
                margin-top: 8px;
            }
            QTabBar::tab {
                background: #eaeaea;
                border: 1px solid #ccc;
                padding: 6px 12px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 1px;
            }
            QTabBar::tab:selected {
                background: white;
                border-bottom: 1px solid white;
            }
            QLabel {
                color: #333;
            }
        """)

    def init_ui(self):
        main_layout = QHBoxLayout()  # Horizontal layout

        # ======= Preview (right) =======
        self.image_preview = QLabel("Preview")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setStyleSheet("border: 1px solid #ccc; background-color: #fafafa;")
        # Do not fix preview size; let it scale with layout
        self.image_preview.setMinimumSize(200, 200)
        self.image_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # ======= Left side =======
        left_layout = QVBoxLayout()

        # Paths
        self.path_field = QLineEdit()
        self.path_field.setReadOnly(True)
        btn_select = QPushButton("Select Image")
        btn_select.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
        btn_select.clicked.connect(self.select_file)

        self.output_field = QLineEdit()
        self.output_field.setReadOnly(True)
        btn_output = QPushButton("Select Folder")
        btn_output.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
        btn_output.clicked.connect(self.select_output_folder)

        self.replace_check = QCheckBox("Replace original")

        grid = QGridLayout()
        grid.addWidget(QLabel("File:"), 0, 0)
        grid.addWidget(self.path_field, 0, 1)
        grid.addWidget(btn_select, 0, 2)

        grid.addWidget(QLabel("Save to:"), 1, 0)
        grid.addWidget(self.output_field, 1, 1)
        grid.addWidget(btn_output, 1, 2)

        grid.addWidget(self.replace_check, 2, 1)
        # Make middle column (index 1) expand so path field is visible
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 0)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self.init_icon_tab(), "Icon")
        self.tabs.addTab(self.init_format_tab(), "Formats")

        left_layout.addLayout(grid)
        left_layout.addWidget(self.tabs)

        main_layout.addLayout(left_layout, stretch=2)
        main_layout.addWidget(self.image_preview, stretch=1)

        self.setLayout(main_layout)

    def init_icon_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        btn = QPushButton("Make icon")
        btn.setIcon(self.style().standardIcon(QStyle.SP_DriveFDIcon))
        btn.clicked.connect(self.make_icon)
        layout.addStretch()
        layout.addWidget(btn)
        layout.addStretch()
        tab.setLayout(layout)
        return tab

    def init_format_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        self.format_box = QComboBox()
        self.format_box.addItems(["JPEG", "PNG", "BMP", "ICO", "TIFF", "WEBP", "GIF"])

        btn_convert = QPushButton("Convert to format")
        btn_convert.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        btn_convert.clicked.connect(self.convert_format)

        layout.addStretch()
        layout.addWidget(self.format_box)
        layout.addWidget(btn_convert)
        layout.addStretch()
        tab.setLayout(layout)
        return tab

    def select_file(self):
        file, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.bmp *.gif *.webp *.tif *.ico)")
        if file:
            self.input_path = file
            self.path_field.setText(file)
            pixmap = QPixmap(file).scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_preview.setPixmap(pixmap)

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.output_dir = folder
            self.output_field.setText(folder)

    def get_output_path(self, new_ext):
        if self.replace_check.isChecked() or not self.output_field.text():
            base = os.path.splitext(self.input_path)[0]
            return f"{base}.{new_ext.lower()}"
        else:
            base = os.path.splitext(os.path.basename(self.input_path))[0]
            return os.path.join(self.output_field.text(), f"{base}.{new_ext.lower()}")

    def make_icon(self):
        if not self.input_path:
            self.show_message("Please select an image first.")
            return
        img = Image.open(self.input_path)
        img = img.resize((256, 256))
        output = self.get_output_path("ico")
        img.save(output, format="ICO")
        self.show_message("Done: icon created.")

    def convert_format(self):
        if not self.input_path:
            self.show_message("Please select an image first.")
            return
        fmt = self.format_box.currentText()
        output = self.get_output_path(fmt.lower())
        try:
            img = Image.open(self.input_path)
            # JPEG doesn't support alpha channel — convert to RGB
            if fmt.upper() == 'JPEG':
                if img.mode in ('RGBA', 'LA') or (hasattr(img, 'getchannel') and 'A' in img.getbands()):
                    img = img.convert('RGB')
            else:
                # For other formats, keep RGBA if present
                pass

            # Ensure output directory exists
            out_dir = os.path.dirname(output)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir, exist_ok=True)

            img.save(output, format=fmt.upper())
            self.show_message(f"Done: saved in {fmt.upper()} format.")
        except Exception as e:
            self.show_message(f"Error saving file: {e}")

    def show_message(self, text):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setText(text)
        msg.setWindowTitle("Notification")
        # Support both PyQt5 (exec_) and PyQt6 (exec)
        getattr(msg, 'exec_', msg.exec)()


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    window = ImageConverter()
    window.show()
    sys.exit(getattr(app, 'exec_', app.exec)())

