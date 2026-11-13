# WAblast

WAblast is a Windows desktop app for sending WhatsApp messages to up to 50 recipients per batch, using five editable templates and optional images.

## Features

- Connect directly through WhatsApp Web using a QR code, or configure a WAHA server or compatible custom gateway under **Custom API**.
- Import recipients from CSV, personalize messages with `{{name}}` and `{{phone}}`, and attach JPEG, PNG, or WebP images up to 16 MB.
- Use a random delay of 60–180 seconds or set a manual delay of 10–600 seconds. After every 10 recipients, the app waits at least five minutes before continuing.
- Confirm recipient consent for every batch. The opt-out list is always applied.
- Pause or stop a batch and review per-recipient results and local history.
- Choose English or Indonesian in the sidebar. Restart the app to apply the language change.

## Run from source

Requirements: Windows 10/11, Python 3.10+ with Tkinter and Pillow, Node.js 22.12+ with npm, and Microsoft Edge or Google Chrome.

1. Install Pillow with `py -3 -m pip install Pillow`.
2. Run `install_dependencies.bat` to install the Node dependencies.
3. Run `run_app.bat`, or execute `py -3 app.py`.
4. Open **WhatsApp Web**, request a QR code, and scan it from Linked Devices on your phone.

For the gateway option, configure the server URL and session under **Custom API**. WAHA must be running separately. Custom gateways require endpoint and response mappings compatible with the provider; an API key alone does not guarantee compatibility. API keys are kept in memory for the current session.

## Privacy and limitations

The direct WhatsApp Web session and application data are stored under `%APPDATA%\WAblast`. The app restricts this folder to the current Windows account and SYSTEM using NTFS permissions. On a shared Windows account, log out after use; if remote logout cannot be confirmed, remove the linked device from your phone.

Number checks do not indicate whether a recipient is online. A sent status means the request was accepted by the connection or gateway; it does not confirm delivery or reading. The app stops the queue if the session disconnects.

whatsapp-web.js is an unofficial community library. Changes to WhatsApp Web can break the integration, and automated use may lead to account restrictions. Users are responsible for recipient consent, opt-outs, and applicable rules.

See [Installation guide](PANDUAN_INSTALASI.md) and [License and terms](LICENSE.txt).
