
import io
import secrets
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import qrcode
from flask import (
    Flask,
    Response,
    abort,
    render_template_string,
    request,
    send_from_directory,
)
from werkzeug.serving import make_server
from werkzeug.utils import secure_filename

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# ============================================================
# Configuration
# ============================================================

APP_NAME = "Phone Link"
PORT = 8765
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

BASE_DIR = Path.home() / "PhoneLink"
RECEIVED_DIR = BASE_DIR / "Received"
SHARED_DIR = BASE_DIR / "Shared"

CONFIG_DIR = Path.home() / ".config" / "phone-link"
CERT_FILE = CONFIG_DIR / "cert.pem"
KEY_FILE = CONFIG_DIR / "key.pem"

for directory in (BASE_DIR, RECEIVED_DIR, SHARED_DIR, CONFIG_DIR):
    directory.mkdir(parents=True, exist_ok=True)

# Restrict local file access on Linux.
for directory in (BASE_DIR, RECEIVED_DIR, SHARED_DIR, CONFIG_DIR):
    directory.chmod(0o700)

# Generate a new temporary password every time the app starts.
PASSWORD = secrets.token_urlsafe(32)
USERNAME = "phone"

web_app = Flask(__name__)
web_app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
web_app.config["MAX_FORM_MEMORY_SIZE"] = 1024 * 1024


# ============================================================
# Network and file helpers
# ============================================================

def get_local_ip():
    """Determine the preferred local IPv4 address."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.connect(("192.0.2.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def unique_path(directory: Path, original_name: str) -> Path:
    """Create a safe filename without overwriting existing files."""
    safe_name = secure_filename(original_name) or "uploaded_file"
    destination = directory / safe_name

    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    counter = 1

    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def list_shared_files():
    """Return regular files in the explicitly shared directory."""
    return sorted(
        item.name
        for item in SHARED_DIR.iterdir()
        if item.is_file() and not item.is_symlink()
    )


def verify_tls_files():
    """Check that the TLS certificate and private key exist."""
    if not CERT_FILE.is_file() or not KEY_FILE.is_file():
        raise RuntimeError(
            "TLS files are missing.\n"
            f"Expected certificate: {CERT_FILE}\n"
            f"Expected private key: {KEY_FILE}\n"
            "Generate them using the OpenSSL instructions."
        )

    # Never accept a broadly readable private key.
    if KEY_FILE.stat().st_mode & 0o077:
        raise RuntimeError(
            f"Private key permissions are too broad: {KEY_FILE}\n"
            f"Run: chmod 600 '{KEY_FILE}'"
        )


# ============================================================
# Android web interface
# ============================================================

HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <title>Phone Link</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            padding: 24px;
            background: #10131a;
            color: #edf0f7;
            font-family: system-ui, sans-serif;
        }

        main {
            max-width: 650px;
            margin: auto;
        }

        h1 {
            font-size: 29px;
            margin-bottom: 8px;
        }

        h2 {
            font-size: 19px;
        }

        .muted {
            color: #aab3c4;
            font-size: 14px;
        }

        .card {
            margin: 18px 0;
            padding: 18px;
            border: 1px solid #303747;
            border-radius: 14px;
            background: #1b202b;
        }

        input[type=file],
        button {
            width: 100%;
            padding: 13px;
            margin-top: 12px;
            border-radius: 9px;
            font-size: 16px;
        }

        input[type=file] {
            background: #272e3b;
            color: white;
        }

        button {
            border: 0;
            background: #8bb7ff;
            color: #10131a;
            font-weight: 600;
            cursor: pointer;
        }

        a {
            color: #a9caff;
            overflow-wrap: anywhere;
        }

        li {
            margin: 14px 0;
            overflow-wrap: anywhere;
        }
    </style>
</head>

<body>
<main>
    <h1>Phone Link</h1>
    <p class="muted">
        Private file transfers between your phone and computer.
    </p>

    <section class="card">
        <h2>Send files to Kali Linux</h2>

        <p>Select one or more files on your phone.</p>

        <form action="/upload"
              method="post"
              enctype="multipart/form-data">

            <input type="file"
                   name="files"
                   multiple
                   required>

            <button type="submit">Upload files</button>
        </form>
    </section>

    <section class="card">
        <h2>Download files from Kali Linux</h2>

        {% if files %}
            <ul>
            {% for filename in files %}
                <li>
                    {{ filename }}
                    <br>
                    <a href="{{ url_for('download_file',
                                        filename=filename) }}">
                        Download file
                    </a>
                </li>
            {% endfor %}
            </ul>
        {% else %}
            <p class="muted">
                No shared files yet.
                Add files using the desktop application.
            </p>
        {% endif %}
    </section>

    <p class="muted">
        Keep this connection on a trusted network.
        Do not share your login credentials.
    </p>
</main>
</body>
</html>
"""


# ============================================================
# Authentication and HTTP security headers
# ============================================================

@web_app.before_request
def require_authentication():
    """Require HTTP Basic authentication for every endpoint."""
    credentials = request.authorization

    if (
        credentials is None
        or credentials.type.lower() != "basic"
        or credentials.username != USERNAME
        or not secrets.compare_digest(
            credentials.password or "",
            PASSWORD,
        )
    ):
        return Response(
            "Authentication required",
            status=401,
            headers={
                "WWW-Authenticate": 'Basic realm="Phone Link"',
                "Cache-Control": "no-store",
            },
        )


@web_app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'unsafe-inline'; "
        "img-src 'self' data:; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'"
    )
    return response


# ============================================================
# Transfer routes
# ============================================================

@web_app.route("/", methods=["GET"])
def index():
    return render_template_string(
        HTML_PAGE,
        files=list_shared_files(),
    )


@web_app.route("/upload", methods=["POST"])
def upload():
    """Receive files from the authenticated Android browser."""
    uploaded_files = request.files.getlist("files")
    saved_count = 0

    for uploaded in uploaded_files:
        if not uploaded or not uploaded.filename:
            continue

        destination = unique_path(
            RECEIVED_DIR,
            uploaded.filename,
        )

        uploaded.save(destination)
        saved_count += 1

    if saved_count == 0:
        return "No files selected.", 400

    return Response(
        f"""
        <!doctype html>
        <html lang="en">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Upload complete</title>
        <body style="font-family:system-ui;padding:24px">
            <h2>Upload complete</h2>
            <p>Received {saved_count} file(s).</p>
            <a href="/">Back to Phone Link</a>
        </body>
        </html>
        """,
        mimetype="text/html",
    )


@web_app.route("/download/<path:filename>", methods=["GET"])
def download_file(filename):
    """Download a file only from the explicitly shared directory."""
    return send_from_directory(
        SHARED_DIR,
        filename,
        as_attachment=True,
        conditional=True,
    )


@web_app.errorhandler(413)
def file_too_large(_error):
    return "Request too large. Maximum request size is 2 GB.", 413


@web_app.errorhandler(404)
def not_found(_error):
    return "Not found.", 404


# ============================================================
# Desktop application
# ============================================================

class PhoneLinkWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        verify_tls_files()

        self.setWindowTitle(APP_NAME)
        self.resize(560, 700)

        self.local_ip = get_local_ip()
        self.url = f"https://{self.local_ip}:{PORT}/"

        # Bind to all interfaces so another device on the LAN can connect.
        self.server = make_server(
            "0.0.0.0",
            PORT,
            web_app,
            threaded=True,
            ssl_context=(str(CERT_FILE), str(KEY_FILE)),
        )

        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.server_thread.start()

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel(APP_NAME)
        title.setStyleSheet(
            "font-size: 28px; font-weight: bold;"
        )
        layout.addWidget(title)

        subtitle = QLabel(
            "Transfer files between Android and Kali Linux"
        )
        subtitle.setStyleSheet("color: #9ca8bb;")
        layout.addWidget(subtitle)

        layout.addWidget(
            QLabel("1. Connect both devices to the same trusted Wi-Fi.")
        )

        layout.addWidget(QLabel("2. Open this HTTPS address on your phone:"))

        address = QLabel(self.url)
        address.setWordWrap(True)
        address.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        address.setStyleSheet(
            "background:#202735;padding:12px;border-radius:8px;"
        )
        layout.addWidget(address)

        copy_button = QPushButton("Copy phone link")
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.url)
        )
        layout.addWidget(copy_button)

        password_label = QLabel(
            f"Username: {USERNAME}\n"
            f"Temporary password: {PASSWORD}"
        )
        password_label.setWordWrap(True)
        password_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        password_label.setStyleSheet(
            "background:#202735;padding:12px;border-radius:8px;"
        )
        layout.addWidget(password_label)

        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        qr_image = qrcode.make(self.url)
        buffer = io.BytesIO()
        qr_image.save(buffer, format="PNG")

        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue())

        qr_label.setPixmap(
            pixmap.scaled(
                220,
                220,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        layout.addWidget(qr_label)

        layout.addWidget(
            QLabel("3. Scan the QR code, open the link, and authenticate.")
        )

        share_button = QPushButton("Choose files to send to phone")
        share_button.clicked.connect(self.add_files)
        layout.addWidget(share_button)

        received_button = QPushButton("Open received files folder")
        received_button.clicked.connect(self.open_received_folder)
        layout.addWidget(received_button)

        shared_button = QPushButton("Open shared files folder")
        shared_button.clicked.connect(self.open_shared_folder)
        layout.addWidget(shared_button)

        self.status = QLabel(f"HTTPS server running on port {PORT}")
        self.status.setStyleSheet("color:#6fca9b;")
        layout.addWidget(self.status)

        self.setCentralWidget(root)

        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #11151d;
                color: #edf0f7;
                font-size: 14px;
            }

            QPushButton {
                background: #273348;
                border: 1px solid #3c4a61;
                border-radius: 9px;
                padding: 12px;
                text-align: left;
            }

            QPushButton:hover {
                background: #354761;
            }
        """)

    def add_files(self):
        """Copy selected files into the explicitly shared directory."""
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose files to share",
        )

        if not paths:
            return

        count = 0

        for source in paths:
            source_path = Path(source)

            if not source_path.is_file():
                continue

            destination = unique_path(
                SHARED_DIR,
                source_path.name,
            )

            try:
                shutil.copy2(source_path, destination)
                count += 1
            except OSError as error:
                self.status.setText(
                    f"Could not share a file: {error}"
                )

        self.status.setText(
            f"Shared {count} file(s). Refresh the phone webpage."
        )

    def open_received_folder(self):
        subprocess.Popen(["xdg-open", str(RECEIVED_DIR)])

    def open_shared_folder(self):
        subprocess.Popen(["xdg-open", str(SHARED_DIR)])

    def closeEvent(self, event):
        """Stop the HTTP server cleanly when the window closes."""
        self.status.setText("Stopping server...")

        self.server.shutdown()
        self.server.server_close()

        self.server_thread.join(timeout=3)

        event.accept()


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    qt_app = QApplication([])
    window = PhoneLinkWindow()
    window.show()
    qt_app.exec()

