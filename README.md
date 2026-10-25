
# Phone Link for Linux

A local-first Android-to-Linux file transfer application built with
Python, Flask, PySide6, and QR codes.

## Features

- Upload files from Android to Linux
- Download shared files from Linux to Android
- Desktop interface with QR code pairing
- Temporary password authentication
- HTTPS support using a local TLS certificate
- Local file storage without cloud services

## Requirements

- Linux (developed for Kali Linux)
- Python 3
- Android phone with a modern web browser
- Both devices on a trusted Wi-Fi network

## Installation

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip openssl

git clone YOUR_REPOSITORY_URL
cd phone-link

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Generate a local TLS certificate for your current Wi-Fi IP address.
See the security setup instructions in the project documentation.

## Run

```bash
source .venv/bin/activate
python phone_link.py
```

Open the HTTPS address shown in the desktop window on your Android
phone and authenticate with the temporary credentials.

## Storage

Files received from Android are stored in `~/PhoneLink/Received/`.
Files shared with Android are stored in `~/PhoneLink/Shared/`.

## Security

This is an experimental local-network MVP. Use it only on a trusted
network. A self-signed certificate requires explicit trust on the
client; HTTPS alone does not make an untrusted certificate safe.

Do not expose the server directly to the public internet.

## Roadmap

- Trusted certificate provisioning
- Stronger device pairing and session management
- Transfer progress indicators
- Automatic device discovery
- Native Android companion application
- Clipboard and notification synchronization

## License

MIT License. See `LICENSE`.
