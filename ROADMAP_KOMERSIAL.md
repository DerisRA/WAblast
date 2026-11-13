# Roadmap Komersialisasi WAblast

## Keputusan saat ini

Mulai dari aplikasi desktop Windows yang dipasang di PC masing-masing pengguna. Rencana layanan web dengan WhatsApp Business Platform Cloud API disimpan sebagai tahap berikutnya, setelah modal dan kebutuhan produk sudah siap.

## Tahap 1 — Installer desktop Windows

Tujuan: pengguna dapat memasang dan membuka WAblast tanpa memasang Python, Node.js, atau dependensi secara manual.

Pekerjaan yang diperlukan:

- Buat paket aplikasi Windows, awalnya EXE; lanjutkan menjadi installer jika proses distribusi dan pembaruan sudah ditentukan.
- Sertakan aplikasi Python, `whatsapp_bridge.js`, dependensi Node (`node_modules`), dan runtime Node yang didukung.
- Ubah pencarian Node agar memakai runtime yang dibundel ketika tersedia, lalu gunakan instalasi lokal sebagai fallback untuk mode pengembangan.
- Putuskan cara menyediakan browser yang dibutuhkan Puppeteer: gunakan browser sistem dengan instruksi yang jelas, atau bundel browser agar instalasi lebih mandiri.
- Simpan sesi WhatsApp dan pengaturan di profil Windows masing-masing pengguna; setiap pengguna menautkan akunnya sendiri melalui QR.
- Uji instalasi pada Windows bersih yang tidak memiliki Python atau Node.js, termasuk proses login QR, kirim teks/gambar, jeda, logout, dan pembaruan aplikasi.
- Periksa lisensi seluruh dependensi dan siapkan kanal untuk merilis perbaikan.

Kondisi saat ini: aplikasi masih dijalankan dari source dan mengharuskan Node.js serta Edge/Chrome tersedia. Jadi, file EXE tunggal belum cukup untuk distribusi tanpa persiapan tambahan.

## Tahap 2 — Layanan web dengan Cloud API (ditunda)

Mulai tahap ini setelah ada modal untuk hosting, pengembangan backend, dukungan pengguna, dan biaya operasional.

Garis besar yang disimpan untuk masa depan:

- Migrasikan pengiriman dari otomasi WhatsApp Web lokal ke WhatsApp Business Platform Cloud API.
- Buat dashboard web dan backend dengan antrean, status pesan, webhook, pengelolaan template, persetujuan penerima, dan opt-out.
- Pisahkan data dan kredensial setiap bisnis; lindungi token akses dan sediakan audit penggunaan.
- Onboard nomor WhatsApp Business tiap pelanggan. Jika pelanggan perlu mendaftar sendiri dari dalam produk, pelajari jalur Tech Provider dan Embedded Signup Meta.
- Buat model langganan dan perhitungan biaya yang memperhitungkan biaya platform Meta serta infrastruktur.
- Lakukan pilot terbatas sebelum membuka pendaftaran umum.

## Syarat dan Ketentuan Penggunaan WhatsApp (Draf)

WAblast mengakses WhatsApp Web melalui `whatsapp-web.js`, pustaka komunitas yang tidak resmi. Perubahan pada WhatsApp dapat memutus koneksi ini, dan penggunaannya dapat menyebabkan akun dibatasi. Jeda pengiriman tidak menjadikan integrasi ini resmi atau menjamin akun bebas dari pembatasan.

Dengan menggunakan fitur ini, Anda bertanggung jawab memastikan penerima menyetujui pesan, menghormati permintaan opt-out, dan mengikuti aturan WhatsApp serta hukum yang berlaku. Baca [Ketentuan WhatsApp](https://www.whatsapp.com/legal/terms-of-service?pix=6f_0_0), [panduan penggunaan bertanggung jawab](https://faq.whatsapp.com/361005896189245/?eea=0&locale=en_US), dan [Kebijakan Berkirim Pesan WhatsApp Business](https://business.whatsapp.com/policy/preview?lang=id_ID) sebelum mengirim pesan.

Sebelum WAblast dijual atau dibagikan secara luas, tinjau kembali ketentuan tersebut dan pastikan integrasi yang digunakan diizinkan untuk tujuan itu.

## Sumber rencana tahap Cloud API

- [Program partner WhatsApp Business](https://whatsappbusiness.com/partners/become-a-partner/)
- [Ketentuan Meta untuk WhatsApp Business](https://www.whatsapp.com/legal/meta-terms-whatsapp-business)
