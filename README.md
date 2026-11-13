# WAblast

Rencana distribusi installer desktop dan tahap Cloud API di masa depan: [ROADMAP_KOMERSIAL.md](ROADMAP_KOMERSIAL.md).

Aplikasi desktop Windows untuk mengirim pesan WhatsApp dari lima template yang dapat diedit, dengan gambar opsional, batch maksimal 50 nomor, jeda acak 1–3 menit, dan istirahat 5 menit setiap kelipatan 10 nomor.

## Cara kerja

Aplikasi terhubung langsung ke WhatsApp Web melalui proses lokal whatsapp-web.js; tidak memakai WAHA, server lokal terpisah, atau Docker. Sesi login menggunakan QR dan disimpan di folder `%APPDATA%\WAblast`\\whatsapp_auth, sehingga biasanya tidak perlu scan ulang setiap kali aplikasi dibuka.

## Persyaratan source

- Windows 10/11
- Python 3.10+ dengan Tkinter dan Pillow
- Node.js 22.12+ dan npm
- Microsoft Edge atau Google Chrome terpasang

## Persiapan satu kali

Di folder aplikasi, jalankan install_dependencies.bat. Skrip menginstal modul Node yang diperlukan. Puppeteer memakai Edge atau Chrome yang sudah terpasang dan tidak mengunduh browser Chromium tambahan.

## Menjalankan

Jalankan run_app.bat, atau dari PowerShell:

py -3 app.py

Pada menu WhatsApp Web, klik Hubungkan / Minta QR, lalu pindai QR melalui WhatsApp di ponsel: Perangkat Tertaut → Tautkan Perangkat. Sesi tertaut disimpan secara lokal. Tombol Logout WhatsApp di halaman tersebut mencoba logout jarak jauh, menutup browser lokal, lalu menghapus sesi login dari PC.

## Perangkat bersama

WAblast membatasi folder data `%APPDATA%\WAblast` dengan ACL NTFS untuk akun Windows aktif dan SYSTEM. Jika ACL gagal diterapkan, koneksi WhatsApp diblokir. ACL ini tidak memisahkan orang yang memakai akun Windows yang sama. Setelah selesai di komputer bersama, buka halaman WhatsApp Web dan gunakan tombol Logout WhatsApp. Jika logout jarak jauh tidak dapat dipastikan, hapus perangkat WAblast dari menu Perangkat Tertaut di ponsel.

## Mengirim pesan

1. Buat hingga lima template melalui menu samping Template; setiap template dapat memiliki gambar JPEG, PNG, atau WebP maksimal 16 MB.
2. Masukkan satu nomor per baris atau impor CSV. Nomor lokal boleh memakai `08...` atau tanpa nol seperti `8...`; aplikasi menambahkan kode negara default (62). Nomor internasional seperti `+62...` dan `0062...` juga didukung. Nama opsional dapat ditulis setelah koma.
3. Masukkan nomor opt-out di menu terkait; filter selalu aktif, dan baris nomor yang tidak valid akan menghentikan run sampai diperbaiki.
4. Konfirmasi bahwa semua penerima menyetujui pesan pada setiap run, lalu pilih template tertentu atau `Template Acak (per nomor)`. Persetujuan wajib dan tidak tersimpan sebagai persetujuan untuk run berikutnya.

Sebelum mengirim, aplikasi memeriksa apakah nomor terdaftar di WhatsApp. Nomor yang tidak terdaftar dilewati. Pemeriksaan ini tidak menunjukkan apakah seseorang sedang online. Status Dikirim berarti WhatsApp Web menerima permintaan, bukan konfirmasi pesan sudah dibaca.

## Batasan dan catatan

- Maksimal 50 nomor per run; jeda antar pesan dipilih acak dari 60 hingga 180 detik, dan istirahat batch otomatis 5 menit setiap 10 nomor.
- Gambar diperiksa berdasarkan format file sebenarnya dan dibatasi maksimal 16 MB.
- Sesi yang terputus menghentikan antrean.
- whatsapp-web.js adalah library komunitas tidak resmi yang mengendalikan WhatsApp Web melalui Puppeteer. WhatsApp dapat mengubah layanan atau membatasi akun yang memakai klien otomatis tidak resmi.
- Panggilan suara AI tidak termasuk.
- Source mode membutuhkan Python, Node.js, dan Edge/Chrome. Build EXE mandiri belum disiapkan.

## Referensi

- https://github.com/wwebjs/whatsapp-web.js
- https://wwebjs.dev/guide/creating-your-bot/authentication
