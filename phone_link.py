
import io
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pyotp
import qrcode

from flask import (
    Flask,
    Response,
    abort,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from werkzeug.serving import make_server
from werkzeug.utils import secure_filename

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPixmap
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
# CONFIGURATION
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
TOTP_SECRET_FILE = CONFIG_DIR / "totp.secret"
SESSION_SECRET_FILE = CONFIG_DIR / "session.secret"

USERNAME = "phone"

for directory in (
    BASE_DIR,
    RECEIVED_DIR,
    SHARED_DIR,
    CONFIG_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)


# ============================================================
# PERSISTENT SECRETS
# ============================================================

def load_or_create_secret(path, generator):
    """Load a persistent secret or create it with private permissions."""
    if path.exists():
        secret = path.read_text().strip()

        if not secret:
            raise RuntimeError(f"Secret file is empty: {path}")

        return secret

    secret = generator()

    # Exclusive creation avoids accidentally overwriting an existing secret.
    descriptor = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )

    with os.fdopen(descriptor, "w") as file:
        file.write(secret + "\n")

    return secret


TOTP_SECRET = load_or_create_secret(
    TOTP_SECRET_FILE,
    pyotp.random_base32,
)

SESSION_SECRET = load_or_create_secret(
    SESSION_SECRET_FILE,
    lambda: secrets.token_hex(32),
)

totp = pyotp.TOTP(
    TOTP_SECRET,
    interval=60,
    digits=6,
)

web_app = Flask(__name__)
web_app.secret_key = SESSION_SECRET

web_app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=3600,
    MAX_FORM_MEMORY_SIZE=1024 * 1024,
)


# ============================================================
# NETWORK AND TLS
# ============================================================

def get_local_ip():
    """Find the preferred local IPv4 address."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.connect(("192.0.2.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def verify_tls_files():
    if not CERT_FILE.is_file() or not KEY_FILE.is_file():
        raise RuntimeError(
            "TLS certificate or key is missing.\n"
            f"Certificate: {CERT_FILE}\n"
            f"Private key: {KEY_FILE}\n"
            "Generate both files before starting Phone Link."
        )

    if KEY_FILE.stat().st_mode & 0o077:
        raise RuntimeError(
            f"Private key permissions must be 600.\n"
            f"Run: chmod 600 '{KEY_FILE}'"
        )


# ============================================================
# FILE HELPERS
# ============================================================

def unique_path(directory, original_name):
    """Generate a safe filename without overwriting existing files."""
    safe_name = secure_filename(original_name) or "uploaded_file"
    candidate = directory / safe_name

    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1

    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def list_shared_files():
    """List regular files that the user explicitly chose to share."""
    return sorted(
        item.name
        for item in SHARED_DIR.iterdir()
        if item.is_file() and not item.is_symlink()
    )


# ============================================================
# ANDROID WEB INTERFACE
# ============================================================

LOGIN_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phone Link Login</title>
<style>
* { box-sizing: border-box; }
body {
    background: #10131a;
    color: #edf0f7;
    font: 16px system-ui, sans-serif;
    padding: 24px;
}
main {
    max-width: 400px;
    margin: 60px auto;
    background: #1b202b;
    padding: 24px;
    border: 1px solid #303747;
    border-radius: 14px;
}
input, button {
    width: 100%;
    padding: 14px;
    margin-top: 12px;
    border-radius: 9px;
    font-size: 18px;
}
input {
    background: #10131a;
    color: white;
    border: 1px solid #394357;
    text-align: center;
    letter-spacing: 5px;
}
button {
    background: #8bb7ff;
    border: 0;
    color: #10131a;
    font-weight: bold;
}
.error { color: #ff9292; }
.muted { color: #aab3c4; font-size: 14px; }
</style>
</head>
<body>
<main>
<h2>Phone Link</h2>
<p>Enter the current six-digit code shown on your computer.</p>
{% if error %}
<p class="error">{{ error }}</p>
{% endif %}
<form method="post" action="/login" autocomplete="off">
<input
    name="code"
    type="text"
    inputmode="numeric"
    pattern="[0-9]{6}"
    maxlength="6"
    placeholder="000000"
    autocomplete="one-time-code"
    required
    autofocus>
<button type="submit">Verify code</button>
</form>
<p class="muted">Codes expire every 60 seconds.</p>
</main>
</body>
</html>
"""

TRANSFER_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phone Link</title>
<style>
* { box-sizing: border-box; }
body {
    margin: 0;
    padding: 24px;
    background: #10131a;
    color: #edf0f7;
    font: 16px system-ui, sans-serif;
}
main { max-width: 650px; margin: auto; }
h1 { font-size: 29px; margin-bottom: 8px; }
h2 { font-size: 19px; }
.muted { color: #aab3c4; font-size: 14px; }
.card {
    margin: 18px 0;
    padding: 18px;
    background: #1b202b;
    border: 1px solid #303747;
    border-radius: 14px;
}
input[type=file], button {
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
    background: #8bb7ff;
    color: #10131a;
    border: 0;
    font-weight: 600;
}
a { color: #a9caff; overflow-wrap: anywhere; }
li { margin: 14px 0; overflow-wrap: anywhere; }
</style>
</head>
<body>
<main>
<h1>Phone Link</h1>
<p class="muted">Local file transfers between your phone and computer.</p>

<section class="card">
<h2>Send files to Kali Linux</h2>
<form action="/upload" method="post" enctype="multipart/form-data">
<input type="file" name="files" multiple required>
<button type="submit">Upload files</button>
</form>
</section>

<section class="card">
<h2>Download files from Kali Linux</h2>
{% if files %}
<ul>
{% for filename in files %}
<li>
    {{ filename }}<br>
    <a href="{{ url_for('download_file', filename=filename) }}">
        Download file
    </a>
</li>
{% endfor %}
</ul>
{% else %}
<p class="muted">No shared files yet. Add them from the desktop app.</p>
{% endif %}
</section>

<form method="post" action="/logout">
<button type="submit">Log out</button>
</form>

<p class="muted">
Use only on a trusted network. Keep your pairing secret private.
</p>
</main>
</body>
</html>
"""


# ============================================================
# WEB SECURITY
# ============================================================

@web_app.before_request
def require_authentication():
    """Require a valid session for all routes except login."""
    if request.endpoint == "login":
        return None

    if not session.get("authenticated"):
        return redirect(url_for("login"))

    return None


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
# LOGIN AND LOGOUT
# ============================================================

@web_app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        code = request.form.get("code", "").strip()

        if (
            len(code) == 6
            and code.isascii()
            and code.isdigit()
            and totp.verify(code, valid_window=0)
        ):
            session.clear()
            session["authenticated"] = True
            session.permanent = True
            return redirect(url_for("index"))

        error = "Invalid or expired code. Enter the current code."

    return render_template_string(LOGIN_PAGE, error=error)


@web_app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# FILE TRANSFERS
# ============================================================

@web_app.route("/", methods=["GET"])
def index():
    return render_template_string(
        TRANSFER_PAGE,
        files=list_shared_files(),
    )


@web_app.route("/upload", methods=["POST"])
def upload():
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
# CIRCULAR COUNTDOWN WIDGET
# ============================================================

class CountdownCircle(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.remaining = 60
        self.setFixedSize(94, 94)

    def set_remaining(self, seconds):
        if seconds != self.remaining:
            self.remaining = seconds
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(8, 8, 78, 78)

        # Background ring
        painter.setPen(QPen(QColor("#303747"), 6))
        painter.drawArc(rect, 90 * 16, -360 * 16)

        # Remaining time
        painter.setPen(QPen(QColor("#8bb7ff"), 6))
        angle = int(360 * self.remaining / 60)
        painter.drawArc(rect, 90 * 16, -angle * 16)

        painter.setPen(QColor("#edf0f7"))
        painter.setFont(QFont("Sans Serif", 15, QFont.Weight.Bold))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            str(self.remaining),
        )

        painter.end()


# ============================================================
# DESKTOP APPLICATION
# ============================================================

class PhoneLinkWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        verify_tls_files()

        self.setWindowTitle(APP_NAME)
        self.resize(560, 760)

        self.local_ip = get_local_ip()
        self.url = f"https://{self.local_ip}:{PORT}/"

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
        layout.setSpacing(12)

        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size:28px;font-weight:bold;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Transfer files between Android and Kali Linux"
        )
        subtitle.setStyleSheet("color:#9ca8bb;")
        layout.addWidget(subtitle)

        layout.addWidget(
            QLabel("1. Connect both devices to the same trusted Wi-Fi.")
        )

        layout.addWidget(
            QLabel("2. Open this HTTPS address on your phone:")
        )

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

        layout.addWidget(
            QLabel("3. Enter this current six-digit login code:")
        )

        self.code_label = QLabel()
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setStyleSheet("""
            font-size:30px;
            font-weight:bold;
            letter-spacing:5px;
            background:#202735;
            padding:12px;
            border-radius:8px;
        """)
        layout.addWidget(self.code_label)

        self.countdown = CountdownCircle()
        layout.addWidget(
            self.countdown,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        self.refresh_totp_display()

        self.totp_timer = QTimer(self)
        self.totp_timer.timeout.connect(self.refresh_totp_display)
        self.totp_timer.start(250)

        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        qr_image = qrcode.make(self.url)
        buffer = io.BytesIO()
        qr_image.save(buffer, format="PNG")

        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue())

        qr_label.setPixmap(
            pixmap.scaled(
                190,
                190,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        layout.addWidget(qr_label)

        layout.addWidget(
            QLabel("4. Scan the QR code to open Phone Link on Android.")
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
                background:#11151d;
                color:#edf0f7;
                font-size:14px;
            }
            QPushButton {
                background:#273348;
                border:1px solid #3c4a61;
                border-radius:9px;
                padding:12px;
                text-align:left;
            }
            QPushButton:hover {
                background:#354761;
            }
        """)

    def refresh_totp_display(self):
        remaining = 60 - (int(time.time()) % 60)
        self.code_label.setText(totp.now())
        self.countdown.set_remaining(remaining)

    def add_files(self):
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
                self.status.setText(f"Could not share file: {error}")

        self.status.setText(
            f"Added {count} file(s). Refresh the phone webpage."
        )

    def open_received_folder(self):
        subprocess.Popen(["xdg-open", str(RECEIVED_DIR)])

    def open_shared_folder(self):
        subprocess.Popen(["xdg-open", str(SHARED_DIR)])

    def closeEvent(self, event):
        self.totp_timer.stop()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=3)
        event.accept()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        verify_tls_files()

        application = QApplication([])
        window = PhoneLinkWindow()
        window.show()

        application.exec()

    except OSError as error:
        print(f"Could not start Phone Link: {error}")
        raise
    except RuntimeError as error:
        print(f"Configuration error: {error}")
        raise
