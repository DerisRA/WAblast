# PRD — Aplikasi Desktop WAblast

**Status:** Draft implementasi  
**Tanggal:** 28 Oktober 2026  
**Versi:** 0.3 — koneksi WhatsApp Web langsung

## 1. Ringkasan

Aplikasi desktop Windows untuk mengirim pesan dari lima template yang dapat diedit. Pengguna menautkan akun dengan QR, memasukkan penerima, memilih template beserta gambar opsional, lalu mengirim batch maksimal 10 nomor dengan jeda acak 1–3 menit.

Aplikasi menjalankan koneksi WhatsApp Web secara lokal melalui whatsapp-web.js dan Puppeteer. Tidak diperlukan WAHA atau Docker.

## 2. Tujuan dan pengguna

Sasaran pengguna adalah pemilik usaha atau staf yang mengirim informasi kepada penerima yang telah setuju dihubungi. Aplikasi menyediakan template, impor nomor, status pengiriman, daftar opt-out, serta riwayat run.

## 3. Alur utama

1. Pengguna memasang dependensi Node/Puppeteer satu kali.
2. Pengguna membuka menu samping WhatsApp Web dan meminta QR.
3. Pengguna memindai QR lewat menu Perangkat Tertaut di ponsel. Sesi disimpan lokal.
4. Pengguna mengedit hingga lima template teks dan gambar JPEG, PNG, atau WebP maksimal 16 MB melalui menu samping Template.
5. Pengguna memasukkan nomor satu per baris atau mengimpor CSV; nama opsional dapat dipakai sebagai variabel.
6. Aplikasi menormalkan nomor, melewati duplikat dan opt-out, lalu memeriksa pendaftaran nomor di WhatsApp.
7. Pengguna meninjau template dan penerima, mengonfirmasi persetujuan, lalu memulai run.
8. Aplikasi mengirim hingga 10 pesan dengan jeda acak 60–180 detik dan menampilkan hasil per nomor.

## 4. Fitur MVP

### Koneksi dan sesi

- Jalankan klien WhatsApp Web lokal tanpa server WAHA.
- Tampilkan QR dan status: mulai, menunggu scan, siap, terputus, atau gagal.
- Simpan sesi login lokal; sediakan logout yang menghapus sesi tertaut.
- Terapkan ACL NTFS pada folder data agar hanya SID akun Windows aktif dan SYSTEM yang memiliki akses; blokir login WhatsApp jika proteksi ACL gagal.
- Sediakan tombol Logout WhatsApp di halaman koneksi; coba logout jarak jauh, tutup browser, hapus profil LocalAuth lokal, dan beri tahu jika logout jarak jauh perlu diselesaikan dari ponsel.
- Jelaskan bahwa ACL tidak memisahkan beberapa orang yang memakai akun Windows yang sama; sediakan pembersihan sesi lokal untuk komputer bersama.
- Tutup proses browser dengan bersih saat aplikasi ditutup.

### Template dan penerima

- Lima template bernama yang berisi teks dan gambar opsional.
- Pilihan Template Acak mengundi satu template berisi untuk setiap penerima; template tanpa teks tidak diikutkan.
- Gambar maksimal 16 MB; validasi format dilakukan pada isi file dan hanya menerima JPEG, PNG, atau WebP.
- Variabel dasar: {{nama}} dan {{nomor}}.
- Input manual atau impor CSV; nomor lokal boleh memakai awalan `0` atau tanpa nol awal, lalu dinormalisasi memakai kode negara default. Nomor internasional dengan awalan `+` atau `00` dipertahankan sebagai nomor internasional.
- Tandai duplikat dan nomor opt-out agar tidak dikirim.
- Persetujuan wajib dikonfirmasi ulang setiap run; daftar opt-out selalu diterapkan tanpa opsi untuk menonaktifkannya.
- Jika ada baris opt-out yang tidak valid, blokir run sampai baris tersebut diperbaiki.
- Cek pendaftaran WhatsApp sebelum mencoba pengiriman; nomor yang tidak terdaftar dilewati.
- Gambar dikirim bersama caption dari isi template.

### Pengiriman dan riwayat

- Maksimal 10 penerima per run.
- Jeda antar pesan dipilih acak dari 60 hingga 180 detik.
- Tombol jeda, lanjutkan, dan hentikan antrean.
- Status per nomor: menunggu, mengirim, dikirim, gagal, dilewati, atau dibatalkan.
- Jika sesi terputus, hentikan pesan berikutnya.
- Simpan waktu, template, nomor, dan status hasil secara lokal.

## 5. Kriteria penerimaan

- QR tampil dan sesi dapat mencapai status siap.
- Folder data mendapat ACL NTFS yang ketat; kegagalan ACL mencegah sesi WhatsApp dijalankan.
- Tombol Logout WhatsApp mencoba logout dari WhatsApp, lalu menutup browser dan menghapus profil lokal, termasuk jika socket sedang tidak merespons.
- Template teks dan gambar bisa dibuat, disimpan, dan dipratinjau.
- Pilihan Template Acak hanya memilih template yang memiliki teks dan mencatat template yang dipakai per nomor.
- Run di atas 10 nomor ditolak.
- Nomor opt-out dan yang tidak terdaftar dilewati.
- Run tidak dapat dimulai tanpa konfirmasi persetujuan dan tidak dapat melewati filter opt-out.
- Jeda antar pesan bernilai acak dari 60 hingga 180 detik.
- Pengiriman gambar dan teks menggunakan template terpilih, termasuk pilihan acak per nomor.
- Status per nomor dan riwayat run tersimpan.
- Pemutusan sesi menghentikan antrean berikutnya.

## 6. Ketergantungan dan batasan

- Source mode membutuhkan Python 3.10+, Tkinter, Pillow, Node.js 22.12+, npm, Edge atau Chrome, serta dependensi whatsapp-web.js, qrcode, dan Puppeteer.
- Login menggunakan QR WhatsApp Web; sesi berada di profil Windows pengguna.
- Library ini tidak resmi dan tidak berafiliasi dengan WhatsApp. Perubahan WhatsApp Web dapat memutus integrasi dan penggunaan otomatisasi dapat membuat akun dibatasi.
- Pemeriksaan nomor hanya memastikan nomor terdaftar, bukan sedang online.
- Build EXE mandiri dan panggilan suara AI belum termasuk MVP.

## 7. Di luar cakupan

Panggilan AI, chatbot, CRM, banyak sesi/akun, pengiriman grup/kanal, penjadwalan, serta analitik kampanye.
