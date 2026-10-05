# PrintFlow

A small local PDF print queue for campus or shop counters. Customers upload one or more PDFs, choose pages, copies, and colour, and the seller reviews each job before printing.

## Features

- Customer upload page at `/send`
- Multiple PDF uploads with buyer-side preview and remove controls
- Per-document page range, copy count, and B&W/Color choices
- Seller queue with preview, editable settings, and print action
- SumatraPDF silent printing on Windows
- Automatic PDF/job deletion after 30 minutes
- Server-side validation for PDF files, page ranges, copies, and job IDs

## Run on Windows

1. Install Python 3.10+ and SumatraPDF.
2. Install the dependency:

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Set the shop printer as the Windows default printer.
4. Double-click `Start_Printing_Dashboard.bat`.
5. Open the customer page at `http://127.0.0.1:8787/send` or the seller dashboard at `http://127.0.0.1:8787/`.

For phones on the same Wi-Fi, the app must be bound to the computer's LAN address and Windows Firewall must allow port 8787. This app is intended for a trusted local network; do not expose it directly to the public internet without authentication and HTTPS.

## Moving to a real domain

The included launcher is for local use only. To use a domain such as `print.example.com`, manually change the deployment setup rather than only changing the browser URL:

1. Deploy the app on an always-on server instead of a shop desktop.
2. Change the Flask binding from `127.0.0.1` to the server's deployment configuration and run it behind a production WSGI server such as Waitress or Gunicorn.
3. Point the domain's DNS record to the server and configure a reverse proxy such as Caddy or Nginx for HTTPS.
4. Change the customer-facing URL shown to users from `/send` on localhost to the real HTTPS domain.
5. Add login/authentication for the seller dashboard, rate limits, upload-size limits, and server-side access controls before making it public.
6. Move temporary PDFs to private managed storage and keep the 30-minute cleanup job running even when no request arrives.

Do not put printer access directly on the public server. A safer production design uses a private print agent at the shop that securely receives approved jobs from the hosted dashboard.

## Test

```powershell
py -m unittest -v
```

The print command is mocked in tests. Tests never send a real print job.

## SumatraPDF location

The app checks common locations, including the per-user installation at `%LOCALAPPDATA%\\SumatraPDF\\SumatraPDF.exe`. To use another location, set `SUMATRA_PDF` before launching.

## Project layout

- `app.py` — Flask app, upload flow, print queue, validation, and UI
- `test_app.py` — mocked print and validation tests
- `Start_Printing_Dashboard.bat` — Windows launcher
- `pdfs/` — temporary uploaded PDFs; files expire after 30 minutes
