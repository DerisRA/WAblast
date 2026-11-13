# Panduan Pengguna & Instalasi WAblast Desktop

Selamat datang di **WAblast**, aplikasi desktop Windows untuk mempermudah pengiriman informasi berkala kepada pelanggan Anda melalui WhatsApp secara aman dan terjadwal.

---

## 1. Persyaratan Sistem
Sebelum memasang WAblast, pastikan komputer Anda memenuhi syarat berikut:
- **Sistem Operasi**: Windows 10 atau Windows 11 (64-bit).
- **Browser**: Microsoft Edge (bawaan Windows) atau Google Chrome sudah terpasang.
- **Koneksi Internet**: Stabil untuk sinkronisasi WhatsApp Web.
- **Node.js / Python**: **TIDAK PERLU** (sudah dibundel secara otomatis di dalam aplikasi).

---

## 2. Cara Memasang Aplikasi (Menggunakan Installer)

1. Unduh berkas resmi **`WAblast_Setup_v1.0.exe`**.
2. Klik dua kali pada file `WAblast_Setup_v1.0.exe`.

### 🛡️ Mengatasi Layar Peringatan Windows SmartScreen:
Pada Windows 10/11, file aplikasi baru yang diunduh dari internet terkadang memunculkan layar biru bertuliskan:
> *"Windows protected your PC"* / *"Windows melindungi PC Anda"*

**Mengapa ini muncul?**  
Ini adalah fitur standar Windows Defender SmartScreen untuk setiap program baru yang belum memiliki reputasi unduhan ribuan kali. Aplikasi WAblast **100% bebas dari virus, malware, maupun spyware**.

**Langkah mudah membukanya:**
1. Klik tautan teks bertuliskan **"More info"** (atau *"Informasi selengkapnya"*).
2. Tombol baru akan muncul di bagian bawah: klik **"Run anyway"** (atau *"Tetap jalankan"*).
3. Wizard instalasi akan langsung terbuka.
4. Baca dan centang persetujuan **Syarat & Ketentuan Penggunaan (EULA)**, lalu klik **Next**.
5. Klik **Install**. Shortcut WAblast akan otomatis dibuat di Desktop dan Start Menu Anda.

---

## 3. Cara Menggunakan Versi Portabel (Tanpa Install)
Jika Anda menggunakan komputer kantor/kantor yang membatasi hak instalasi:
1. Unduh **`WAblast_v1.0_Portable.zip`**.
2. Klik kanan file zip, pilih **Extract All...** (Ekstrak Semua).
3. Buka folder hasil ekstrak, lalu klik dua kali pada **`WAblast.exe`**.

---

## 4. Panduan Menghubungkan WhatsApp Pertama Kali

1. Buka aplikasi **WAblast** dari Desktop.
2. Di menu bilah samping kiri (sidebar), klik menu **WhatsApp Web**.
3. Klik tombol hijau **Hubungkan**.
4. Tunggu beberapa detik hingga Kode QR (QR Code) muncul di layar.
5. Buka aplikasi WhatsApp di ponsel Anda:
   - Di Android: Ketuk menu titik tiga (kanan atas) > **Perangkat Tertaut** > **Tautkan Perangkat**.
   - Di iPhone: Buka menu **Pengaturan** (kanan bawah) > **Perangkat Tertaut** > **Tautkan Perangkat**.
6. Arahkan kamera ponsel Anda ke QR Code di layar monitor.
7. Setelah berhasil, status koneksi akan berubah menjadi **SIAP (WORKING)**. Sesi ini akan tersimpan otomatis dan Anda tidak perlu scan QR lagi pada pembukaan berikutnya.

---

## 5. Cara Mengirim Pesan Broadcast Bertahap

1. **Atur Template Pesan**:
   - Buka menu **Template Pesan**.
   - Buat hingga 5 variasi pesan yang menarik. Anda dapat menggunakan variabel otomatis `{{nama}}` dan `{{nomor}}`.
   - Tambahkan gambar brosur/promosi jika diperlukan (JPEG, PNG, atau WebP, maks. 16 MB).
2. **Masukkan Kontak**:
   - Buka menu **Kirim Pesan**.
   - Masukkan nomor telepon (satu nomor per baris, contoh: `081234567890,Budi`).
   - Atau klik tombol **Impor Kontak (CSV)** jika Anda memiliki file kontak dari Excel.
3. **Mulai Pengiriman**:
   - Centang kotak pernyataan persetujuan.
   - Klik tombol **Mulai Pengiriman**.
   - Aplikasi akan otomatis memeriksa keberadaan nomor, mengirimkan pesan bergantian dengan jeda acak (60–180 detik per pesan) untuk menjaga keamanan akun Anda.

---

## 6. Tips Keamanan & Pencegahan Blokir (Anti-Ban)
- **Hormati Opt-out**: Jika ada pelanggan yang meminta tidak ingin dikirimkan pesan lagi, masukkan nomor mereka ke menu **Daftar Opt-out**.
- **Kirim Bertahap**: Patuhi batas maksimal 50 nomor per sesi kirim (sistem otomatis memberikan jeda istirahat 5 menit setiap kelipatan 10 nomor). Jangan terburu-buru mengirim ratusan nomor dalam sekali waktu.
- **Gunakan Variasi Template**: Aktifkan fitur *Template Acak* agar isi pesan yang dikirim tidak identik secara berurutan.

---

## 7. Verifikasi Keaslian Berkas (SHA-256 Checksum)
Untuk memastikan file yang Anda unduh asli dan belum dimodifikasi:
- **`WAblast_Setup_v1.0.exe`**:  
  `DE8BE222CD8F44B7EB5BC749D1DADE49202052D0C65D638772C88541B3F1A047`
  `FB9054991F218D397B06E7430653017DA890A4AE32249CA45B70E5F72D859C15`
- **`WAblast_v1.0_Portable.zip`**:  
  `1F9F3A1E2E6F8B6AF34470E35C0EE8522C3267AF28E24FDB3447D6D09453C720`
  `21BCAA41EDAC40EE47E350C761BF35D56B788470D3194FF623344328394AE4FD`

