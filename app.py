from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
import winreg
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request, send_from_directory
from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = BASE_DIR / "pdfs"
PDF_DIR.mkdir(exist_ok=True)
SUMATRA_CANDIDATES = [
    Path(os.environ.get("SUMATRA_PDF", "")),
    Path(r"C:\Program Files\SumatraPDF\SumatraPDF.exe"),
    Path(r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "SumatraPDF" / "SumatraPDF.exe",
]
PRICE = {"bw": 0.10, "color": 0.50}
MAX_COPIES = 100
RETENTION_SECONDS = 30 * 60
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

app = Flask(__name__)
jobs: dict[str, dict[str, Any]] = {}


def sumatra_path() -> Path:
    return next((p for p in SUMATRA_CANDIDATES if str(p) and p.is_file()), SUMATRA_CANDIDATES[0])


def default_printer_name() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Windows") as key:
            return str(winreg.QueryValueEx(key, "Device")[0]).split(",", 1)[0]
    except (FileNotFoundError, OSError):
        return ""


def remove_expired_jobs() -> None:
    now = time.time()
    for job_id, job in list(jobs.items()):
        if now - job["created_at"] >= RETENTION_SECONDS:
            (PDF_DIR / f"{job_id}.pdf").unlink(missing_ok=True)
            jobs.pop(job_id, None)


def parse_page_spec(spec: str, total: int) -> list[int]:
    """Return unique selected pages, or raise ValueError for an invalid range."""
    spec = (spec or "").strip()
    if total < 1:
        raise ValueError("The PDF has no pages.")
    if not spec:
        return list(range(1, total + 1))
    selected: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not re.fullmatch(r"\d+(?:\s*-\s*\d+)?", part):
            raise ValueError("Pages must look like 1-3,5.")
        numbers = [int(x) for x in re.findall(r"\d+", part)]
        start, end = numbers[0], numbers[-1]
        if start < 1 or end < start or end > total:
            raise ValueError(f"Page range {part} is outside 1-{total}.")
        selected.update(range(start, end + 1))
    return sorted(selected)


def validate_request(data: dict[str, Any], total: int) -> tuple[str, int, bool, list[int]]:
    pages = data.get("pages", "")
    copies = data.get("copies", 1)
    color = data.get("color", False)
    if not isinstance(pages, str):
        raise ValueError("Pages must be text.")
    if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= MAX_COPIES:
        raise ValueError(f"Copies must be an integer from 1 to {MAX_COPIES}.")
    if not isinstance(color, bool):
        raise ValueError("Color must be true or false.")
    return pages, copies, color, parse_page_spec(pages, total)


def print_pdf(job_id: str, pages: str, copies: int, color: bool) -> None:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("Invalid job ID.")
    pdf_path = (PDF_DIR / f"{job_id}.pdf").resolve()
    if pdf_path.parent != PDF_DIR.resolve() or not pdf_path.is_file():
        raise FileNotFoundError("The PDF file was not found.")
    settings = [f"{copies}x", "color" if color else "monochrome"]
    if pages.strip():
        settings.append(pages.replace(" ", ""))
    executable = sumatra_path()
    if not executable.is_file():
        raise FileNotFoundError("SumatraPDF was not found. Install it or set SUMATRA_PDF.")
    if not default_printer_name():
        raise RuntimeError("Windows has no default printer. Install/connect the printer and set it as the default printer.")
    subprocess.run(
        [str(executable), "-print-to-default", "-silent", "-print-settings", ",".join(settings), str(pdf_path)],
        check=True,
        timeout=60,
    )


@app.get("/")
def index():
    remove_expired_jobs()
    return render_template_string(PAGE)


@app.get("/send")
def send_page():
    return render_template_string(SEND_PAGE)


@app.post("/upload")
def upload_files():
    remove_expired_jobs()
    name = (request.form.get("name") or "").strip()
    files = request.files.getlist("files")
    try:
        options = request.form.get("options", "[]")
        import json
        options = json.loads(options)
        if not isinstance(options, list):
            raise ValueError
    except (ValueError, TypeError):
        return jsonify(error="Document print choices are invalid."), 400
    if not name or len(name) > 100:
        return jsonify(error="Please enter your name (maximum 100 characters)."), 400
    if not files or not any(f.filename for f in files):
        return jsonify(error="Choose at least one PDF."), 400
    added = []
    for index, upload in enumerate(files):
        original = (upload.filename or "").strip()
        if not original.lower().endswith(".pdf"):
            return jsonify(error="Only PDF files are accepted."), 400
        job_id = uuid.uuid4().hex
        target = PDF_DIR / f"{job_id}.pdf"
        upload.save(target)
        try:
            pages = len(PdfReader(str(target), strict=False).pages)
            if pages < 1:
                raise ValueError
        except Exception:
            target.unlink(missing_ok=True)
            return jsonify(error=f"{original} is not a readable PDF."), 400
        choice = options[index] if index < len(options) and isinstance(options[index], dict) else {}
        try:
            page_spec, copies, color, selected = validate_request(choice, pages)
        except ValueError as exc:
            target.unlink(missing_ok=True)
            return jsonify(error=f"{original}: {exc}"), 400
        jobs[job_id] = {"id": job_id, "sender": name, "name": original, "pages": pages,
                        "status": "waiting", "created_at": time.time(), "requested_pages": page_spec,
                        "requested_copies": copies, "requested_color": color}
        added.append(original)
    return jsonify(added=added)


@app.get("/jobs")
def get_jobs():
    remove_expired_jobs()
    return jsonify(list(jobs.values()))


@app.get("/printer-status")
def printer_status():
    return jsonify(sumatra_found=sumatra_path().is_file())


@app.get("/file/<job_id>")
def get_file(job_id: str):
    if not JOB_ID_RE.fullmatch(job_id):
        return ("Not found", 404)
    return send_from_directory(PDF_DIR, f"{job_id}.pdf", as_attachment=False)


@app.post("/print/<job_id>")
def print_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="Job not found."), 404
    try:
        pages, copies, color, selected = validate_request(request.get_json(silent=True) or {}, job["pages"])
        print_pdf(job_id, pages, copies, color)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 400
    except subprocess.CalledProcessError:
        return jsonify(error="SumatraPDF could not send the job. Check that the default printer is online and try again."), 400
    except (ValueError, FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        return jsonify(error=str(exc)), 400
    total = round(len(selected) * copies * PRICE["color" if color else "bw"], 2)
    job.update(status="printed", total=total)
    return jsonify(total=total, pages=len(selected), copies=copies)


PAGE = """<!doctype html>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Printing Dashboard</title>
<style>:root{font-family:Inter,Segoe UI,Arial,sans-serif;color:#172033;background:#f5f7fb}*{box-sizing:border-box}body{margin:0}.shell{max-width:1180px;margin:auto;padding:28px 22px}.nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:34px}.brand{font-weight:800;font-size:22px;letter-spacing:-.5px}.brand span{color:#5267e8}.nav a{color:#5267e8;text-decoration:none;font-weight:700}.hero{display:flex;justify-content:space-between;align-items:end;margin-bottom:26px}.eyebrow{text-transform:uppercase;letter-spacing:1.4px;font-size:12px;color:#68738a;font-weight:800}.hero h1{font-size:36px;margin:8px 0 0;letter-spacing:-1px}.hero p{color:#68738a}.card{background:white;border:1px solid #e5e9f2;border-radius:16px;box-shadow:0 8px 24px #27365d0b}.status{padding:12px 16px;margin-bottom:18px;color:#536078;font-size:14px}.table-wrap{overflow:auto}.jobs{width:100%;border-collapse:collapse}.jobs th{font-size:12px;text-transform:uppercase;letter-spacing:.8px;color:#8a94a8;text-align:left;padding:16px}.jobs td{padding:18px 16px;border-top:1px solid #edf0f6}.file-name{font-weight:700}.muted{color:#7e889b;font-size:13px;margin-top:5px}.jobs input,.jobs select,.jobs button,.upload button{padding:9px 10px;border:1px solid #dbe1ed;border-radius:8px;background:white}.jobs button,.upload button{background:#5267e8;color:white;border-color:#5267e8;font-weight:700;cursor:pointer}.empty{padding:54px;text-align:center;color:#7e889b}.error{color:#b00020}.preview-modal{display:none;position:fixed;inset:0;background:#172033bb;align-items:center;justify-content:center;z-index:5}.preview-box{position:relative;background:white;width:min(900px,92vw);height:min(90vh,800px);padding:12px;border-radius:16px}.preview-box iframe{width:100%;height:100%;border:0}.close{position:absolute;right:8px;top:5px;width:34px;height:34px;border:0;border-radius:50%;background:#172033;color:white;font-size:24px;line-height:20px;z-index:2}</style>
<div class='shell'><div class='nav'><div class='brand'>Print<span>Flow</span></div><a href='/send'>Send a document →</a></div><div class='hero'><div><div class='eyebrow'>Seller workspace</div><h1>Print queue</h1><p>Review incoming documents and send them to the printer.</p></div><div class='eyebrow' id='printer'></div></div><p id='message'></p>
<div class='card table-wrap'><table class='jobs'><thead><tr><th>Customer / file</th><th>Preview</th><th>Pages</th><th>Copies</th><th>Type</th><th>Action</th></tr></thead><tbody id='jobs'></tbody></table></div></div>
<div class='preview-modal' id='previewModal'><div class='preview-box'><button class='close' id='closePreview' aria-label='Close preview'>×</button><iframe id='previewFrame' title='PDF preview'></iframe></div></div>
<script>
const esc = s => String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
async function load(){
  const response = await fetch('/jobs'); if(!response.ok) throw new Error('Could not load jobs');
  const data = await response.json(); const tbody = document.getElementById('jobs'); tbody.replaceChildren();
    data.forEach(j => { const row=document.createElement('tr');
    row.innerHTML=`<td><b>${esc(j.sender||'Unknown')}</b><br><small>${esc(j.name||'PDF')} · ${j.pages} pages</small></td><td><button class="preview">Preview</button></td>`;
    row.querySelector('.preview').onclick=()=>{document.getElementById('previewFrame').src='/file/'+encodeURIComponent(j.id);document.getElementById('previewModal').style.display='flex';};
    if(j.status==='printed'){ row.insertAdjacentHTML('beforeend',`<td colspan="4">✅ Printed — RM ${Number(j.total).toFixed(2)}</td>`); }
    else { row.insertAdjacentHTML('beforeend',`<td><input class="pages" value="${esc(j.requested_pages||'')}" placeholder="all" size="8"></td><td><input class="copies" type="number" min="1" max="100" value="${j.requested_copies||1}" size="3"></td><td><select class="color"><option value="false" ${j.requested_color?'':'selected'}>B&amp;W</option><option value="true" ${j.requested_color?'selected':''}>Color</option></select></td><td><button class="print">Print</button></td>`); row.querySelector('.print').onclick=()=>printJob(j,row); }
    tbody.appendChild(row);
  }); if(!data.length) tbody.innerHTML='<tr><td colspan="6"><div class="empty">No documents waiting yet.<br><small>Share the customer upload page to receive a PDF.</small></div></td></tr>';
}
async function printJob(j,row){ const button=row.querySelector('.print'); button.disabled=true; document.getElementById('message').textContent='';
  try { const response=await fetch('/print/'+encodeURIComponent(j.id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pages:row.querySelector('.pages').value,copies:Number(row.querySelector('.copies').value),color:row.querySelector('.color').value==='true'})}); const result=await response.json(); if(!response.ok) throw new Error(result.error||'Print failed'); alert('Collect payment: RM '+Number(result.total).toFixed(2)); await load(); }
  catch(e){ document.getElementById('message').textContent=e.message; button.disabled=false; }
}
fetch('/printer-status').then(r=>r.json()).then(s=>document.getElementById('printer').textContent=s.sumatra_found?'✅ SumatraPDF detected':'⚠️ SumatraPDF not detected — install it before printing').catch(()=>{});
document.getElementById('closePreview').onclick=()=>{document.getElementById('previewModal').style.display='none';document.getElementById('previewFrame').src='about:blank';};
document.getElementById('previewModal').onclick=e=>{if(e.target.id==='previewModal')document.getElementById('closePreview').click();};
load().catch(e=>document.getElementById('message').textContent=e.message); setInterval(()=>load().catch(()=>{}),5000);
</script>"""

SEND_PAGE = """<!doctype html>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Send documents to print</title>
<style>:root{font-family:Inter,Segoe UI,Arial,sans-serif;color:#172033;background:#f5f7fb}*{box-sizing:border-box}body{margin:0}.shell{max-width:820px;margin:auto;padding:28px 22px}.nav{display:flex;justify-content:space-between;margin-bottom:54px}.brand{font-weight:800;font-size:22px}.brand span{color:#5267e8}.nav a{color:#5267e8;text-decoration:none;font-weight:700}.hero{text-align:center;margin-bottom:28px}.eyebrow{text-transform:uppercase;letter-spacing:1.4px;font-size:12px;color:#68738a;font-weight:800}.hero h1{font-size:38px;letter-spacing:-1px;margin:10px 0}.hero p,.note{color:#68738a}.upload{background:white;border:1px solid #e5e9f2;border-radius:16px;padding:28px;box-shadow:0 8px 24px #27365d0b}.upload label{display:block;margin:16px 0;font-weight:700}.upload input[type=text]{display:block;width:100%;margin-top:8px}.upload input,.upload select,.upload button{padding:11px;border:1px solid #dbe1ed;border-radius:8px;font-size:15px}.upload button{background:#5267e8;color:white;border-color:#5267e8;font-weight:700;cursor:pointer}.file-row{display:grid;grid-template-columns:minmax(150px,1fr) 100px 100px 70px 100px 36px;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid #ddd}.file-row span{overflow:hidden;text-overflow:ellipsis}.remove{border:0!important;background:#c62828!important;color:white;border-radius:50%;width:30px;height:30px;padding:0!important;font-size:18px;line-height:30px}.buyer-modal{display:none;position:fixed;inset:0;background:#000b;align-items:center;justify-content:center}.buyer-box{position:relative;background:white;width:min(850px,92vw);height:min(85vh,750px);padding:12px;border-radius:16px}.buyer-box iframe{width:100%;height:100%;border:0}.close{position:absolute;right:8px;top:5px;width:34px;height:34px;border:0;border-radius:50%;background:#172033;color:white;font-size:24px;z-index:2}</style>
<div class='shell'><div class='nav'><div class='brand'>Print<span>Flow</span></div><a href='/'>Seller dashboard →</a></div><div class='hero'><div class='eyebrow'>Campus print service</div><h1>Send your documents</h1><p>Upload one or more PDFs, choose your print settings, and send them to the counter.</p></div>
<form class='upload' id='uploadForm'><p class='note'>Files are automatically deleted after 30 minutes.</p><label>Your name<input name='name' type='text' required maxlength='100' autocomplete='name' placeholder='e.g. Ernest Yip'></label><label>PDF documents<input id='fileInput' name='files' type='file' accept='application/pdf,.pdf' multiple required></label><div id='fileList'></div><br><button>Send documents</button><p id='result'></p></form></div>
<div class='buyer-modal' id='buyerModal'><div class='buyer-box'><button class='close' id='closeBuyer' aria-label='Close preview'>×</button><iframe id='buyerFrame' title='PDF preview'></iframe></div></div>
<script>
let chosen=[]; const input=document.getElementById('fileInput'), list=document.getElementById('fileList');
function renderFiles(){list.replaceChildren();chosen.forEach((item,i)=>{const row=document.createElement('div');row.className='file-row';const name=document.createElement('span');name.textContent=item.file.name;const preview=document.createElement('button');preview.type='button';preview.textContent='Preview';preview.onclick=()=>{document.getElementById('buyerFrame').src=URL.createObjectURL(item.file);document.getElementById('buyerModal').style.display='flex'};const pages=document.createElement('input');pages.placeholder='Pages (all)';pages.value=item.pages;pages.oninput=()=>item.pages=pages.value;const copies=document.createElement('input');copies.type='number';copies.min=1;copies.max=100;copies.value=item.copies;copies.oninput=()=>item.copies=Number(copies.value);const color=document.createElement('select');color.innerHTML='<option value="false">B&W</option><option value="true">Color</option>';color.value=String(item.color);color.onchange=()=>item.color=color.value==='true';const remove=document.createElement('button');remove.type='button';remove.className='remove';remove.textContent='×';remove.title='Remove '+item.file.name;remove.onclick=()=>{chosen.splice(i,1);renderFiles()};row.append(name,preview,pages,copies,color,remove);list.append(row)});}
input.onchange=()=>{chosen=[...chosen,...[...input.files].map(file=>({file,pages:'',copies:1,color:false}))].filter((item,i,a)=>i===a.findIndex(x=>x.file.name===item.file.name&&x.file.size===item.file.size));renderFiles()};
document.getElementById('closeBuyer').onclick=()=>{document.getElementById('buyerModal').style.display='none';document.getElementById('buyerFrame').src='about:blank'};
document.getElementById('buyerModal').onclick=e=>{if(e.target.id==='buyerModal')document.getElementById('closeBuyer').click()};
document.getElementById('uploadForm').onsubmit=async e=>{e.preventDefault();const result=document.getElementById('result');if(!chosen.length){result.className='error';result.textContent='Choose at least one PDF.';return}result.className='';result.textContent='Uploading...';const form=new FormData();form.append('name',e.target.name.value);form.append('options',JSON.stringify(chosen.map(item=>({pages:item.pages,copies:Number(item.copies),color:item.color}))));chosen.forEach(item=>form.append('files',item.file));const response=await fetch('/upload',{method:'POST',body:form});const data=await response.json();if(!response.ok){result.className='error';result.textContent=data.error||'Upload failed';return}result.className='ok';result.textContent=data.added.length+' document(s) sent successfully. You may close this page.';e.target.reset();chosen=[];renderFiles()};
</script>"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8787, debug=False)
