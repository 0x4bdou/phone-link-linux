# Phone Link for Linux

A local-first file transfer application that connects an Android phone to a Linux desktop over Wi-Fi. Built with Python, Flask, PySide6, and QR codes.

The goal is to build an open-source alternative to Microsoft Phone Link for Linux, starting with secure local file transfers.

## Features

- **Android to Linux:** Upload files from your phone to your computer.
- **Linux to Android:** Download files shared from your computer.
- **Desktop interface:** A native desktop application built with PySide6.
- **QR code connection:** Quickly open the transfer page on your phone.
- **HTTPS support:** Encrypt connections using TLS.
- **Temporary authentication:** A randomly generated password for each application session.
- **Local-first storage:** Files remain on your computer without cloud storage.

## Tech Stack

- Python 3
- PySide6
- Flask
- Werkzeug
- OpenSSL
- qrcode
- Pillow

## Requirements

- Linux (developed and tested on Kali Linux)
- Python 3.10 or later
- An Android phone with a modern web browser
- Both devices connected to the same trusted Wi-Fi network

## Installation

### 1. Install system dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip openssl git
```

### 2. Clone the repository

```bash
git clone https://github.com/0x4bdou/phone-link-linux.git
cd phone-link-linux
```

### 3. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install Python dependencies

```bash
python -m pip install -r requirements.txt
```

### 5. Generate a local TLS certificate

Find your computer's local IPv4 address:

```bash
hostname -I
```

Choose the IPv4 address associated with your Wi-Fi network. Replace `192.168.1.25` in the following command with that address.

```bash
mkdir -p ~/.config/phone-link
chmod 700 ~/.config/phone-link

openssl req -x509 -newkey rsa:3072 \
  -sha256 -nodes -days 30 \
  -keyout ~/.config/phone-link/key.pem \
  -out ~/.config/phone-link/cert.pem \
  -subj "/CN=Phone Link Local" \
  -addext "subjectAltName=IP:192.168.1.25"

chmod 600 ~/.config/phone-link/key.pem
chmod 644 ~/.config/phone-link/cert.pem
```

The certificate is self-signed. HTTPS encrypts traffic, but the certificate must also be trusted by the connecting device to authenticate the server properly.

### 6. Run the application

```bash
python phone_link.py
```

The desktop window displays the connection address, QR code, username, and temporary password.

## How to Use

1. Start Phone Link on your Linux computer.
2. Connect your Android phone and computer to the same trusted Wi-Fi network.
3. Scan the QR code displayed in the desktop application.
4. Open the HTTPS address in your Android browser.
5. Enter the displayed username and temporary password when prompted.
6. Upload files from Android to Linux.
7. To send files in the other direction, choose files using the desktop application's **Choose files to send to phone** button, refresh the phone webpage, and download them.

## File Storage

Files are stored locally in your home directory:

```text
~/PhoneLink/
├── Received/   # Files uploaded from Android
└── Shared/     # Files shared with Android
```

Only files placed in the `Shared` directory are available for download through the application.

## Security

This is an experimental MVP, not a fully hardened file-transfer service.

- Use it only on a trusted local network.
- Do not expose the server directly to the public internet.
- Keep the TLS private key and authentication credentials secret.
- The temporary password changes when the application restarts.
- The self-signed certificate requires proper trust configuration on the client device.
- HTTP Basic authentication may be cached by the browser.
- Review all files before sharing them with your phone.

The application does not provide full device pairing, CSRF protection, rate limiting, or a complete certificate provisioning workflow yet.

## Roadmap

- [ ] Stronger device pairing and session management
- [ ] Trusted TLS certificate provisioning
- [ ] File transfer progress and speed indicators
- [ ] Automatic device discovery
- [ ] Native Android companion application
- [ ] Clipboard synchronization
- [ ] Android notification integration
- [ ] Battery status and device information
- [ ] Improved transfer history and error handling

## Contributing

Contributions, bug reports, and feature suggestions are welcome. Feel free to open an issue or submit a pull request.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## Author

Created by [@0x4bdou](https://github.com/0x4bdou).
