import os
import cv2
import numpy as np
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from concurrent.futures import ThreadPoolExecutor
import zipfile
import shutil

app = FastAPI(title="Smart Watermark Remover")

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.mount("/outputs", StaticFiles(directory=OUTPUT_FOLDER), name="outputs")

def remove_watermark(image_path, output_path):
    img = cv2.imread(image_path)
    if img is None:
        return False
    
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 1. Adaptive Thresholding का उपयोग करें जो लोकल लाइटिंग और ट्रांसपेरेंसी को बेहतर समझता है
    # यह खिड़की या दीवार जैसी बड़ी समतल जगहों को छेड़े बिना केवल टेक्स्ट/लोगो के किनारों को पकड़ेगा
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                  cv2.THRESH_BINARY_INV, 11, 2)
    
    # 2. कंटूर डिटेक्शन (Contours) से केवल वाटरमार्क जैसे छोटे और विशिष्ट आकारों को अलग करें
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    mask = np.zeros_like(gray)
    for c in contours:
        area = cv2.contourArea(c)
        # वाटरमार्क के अक्षरों/डिजाइन का साइज एक तय सीमा में होता है, उसे ही टारगेट करें
        if 15 < area < 600:
            cv2.drawContours(mask, [c], -1, 255, -1)
            
    # मास्क को हल्का सा फैलाएं ताकि लोगो के किनारे पूरी तरह कवर हो जाएं
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)
    
    # 3. सटीक इनपेंटिंग (Inpainting) ताकि केवल डिटेक्टेड वाटरमार्क हटे
    dst = cv2.inpaint(img, mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
    
    cv2.imwrite(output_path, dst)
    return True

@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!DOCTYPE html>
    <html lang="hi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>⚡ Smart Watermark Remover</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: #f8fafc; text-align: center; padding: 30px; margin: 0; }
            .container { max-width: 900px; margin: auto; }
            .card { background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.3); margin-bottom: 30px; }
            h2 { color: #38bdf8; margin-top: 0; }
            p { color: #94a3b8; font-size: 14px; margin-bottom: 20px; }
            input[type="file"] { border: 2px dashed #475569; padding: 20px; width: 100%; box-sizing: border-box; border-radius: 8px; background: #0f172a; color: #cbd5e1; cursor: pointer; margin-bottom: 20px; }
            button { background: #0284c7; color: white; border: none; padding: 12px 30px; font-size: 16px; font-weight: bold; border-radius: 8px; cursor: pointer; transition: background 0.3s; width: 100%; }
            button:hover { background: #0369a1; }
            .loader { display: none; margin-top: 15px; color: #38bdf8; font-size: 14px; font-weight: bold; }
            
            #results-section { display: none; background: #1e293b; padding: 25px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.3); text-align: left; }
            .gallery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 20px; margin-top: 20px; }
            .image-card { background: #0f172a; border-radius: 8px; padding: 10px; text-align: center; border: 1px solid #334155; }
            .image-card img { width: 100%; height: 140px; object-fit: cover; border-radius: 6px; cursor: pointer; transition: transform 0.2s; }
            .image-card img:hover { transform: scale(1.03); }
            .btn-single { display: block; margin-top: 10px; background: #10b981; color: white; padding: 6px; text-decoration: none; border-radius: 5px; font-size: 13px; font-weight: bold; text-align: center; }
            .btn-single:hover { background: #059669; }
            
            .download-all-container { margin-top: 25px; text-align: center; }
            .btn-all { background: #8b5cf6; padding: 12px 25px; color: white; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block; }
            .btn-all:hover { background: #7c3aed; }

            .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.8); text-align: center; }
            .modal-content { max-width: 85%; max-height: 85vh; margin-top: 5vh; border-radius: 8px; box-shadow: 0 0 20px rgba(255,255,255,0.2); }
            .close { position: absolute; top: 20px; right: 35px; color: #f1f1f1; font-size: 40px; font-weight: bold; cursor: pointer; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h2>⚡ Smart Watermark Remover</h2>
                <p>एक साथ 10-20 इमेजेस अपलोड करें। स्मार्ट कंटूर स्कैनिंग से तुरंत साफ़ फाइल पाएं!</p>
                <form id="uploadForm" enctype="multipart/form-data">
                    <input type="file" name="images" multiple accept="image/*" required>
                    <button type="submit" id="subBtn">वाटरमार्क हटाएं और प्रोसेस करें</button>
                </form>
                <div class="loader" id="loadingText">इमेजेस स्कैन और प्रोसेस हो रही हैं, कृपया प्रतीक्षा करें...</div>
            </div>

            <div id="results-section">
                <h3>✨ प्रोसेस्ड इमेजेस (क्लिक करके बड़ा देखें)</h3>
                <div class="gallery-grid" id="galleryGrid"></div>
                <div class="download-all-container" id="downloadAllDiv"></div>
            </div>
        </div>

        <div id="imageModal" class="modal">
            <span class="close" onclick="closeModal()">&times;</span>
            <img class="modal-content" id="modalImg">
        </div>

        <script>
            document.getElementById('uploadForm').onsubmit = async function(e) {
                e.preventDefault();
                const formData = new FormData(this);
                
                document.getElementById('subBtn').style.display = 'none';
                document.getElementById('loadingText').style.display = 'block';
                document.getElementById('results-section').style.display = 'none';

                try {
                    const response = await fetch('/process', {
                        method: 'POST',
                        body: formData
                    });
                    
                    const data = await response.json();
                    
                    if(data.success) {
                        let galleryGrid = document.getElementById('galleryGrid');
                        galleryGrid.innerHTML = '';
                        
                        data.files.forEach(file => {
                            galleryGrid.innerHTML += `
                                <div class="image-card">
                                    <img src="/outputs/${file}" onclick="openModal('/outputs/${file}')" title="बड़ा देखने के लिए क्लिक करें">
                                    <a href="/outputs/${file}" download class="btn-single">डाउनलोड करें</a>
                                </div>
                            `;
                        });

                        let downloadAllDiv = document.getElementById('downloadAllDiv');
                        downloadAllDiv.innerHTML = `<a href="/outputs/${data.zip_file}" class="btn-all" download>📥 सभी इमेजेस एक साथ डाउनलोड करें (ZIP)</a>`;

                        document.getElementById('results-section').style.display = 'block';
                    } else {
                        alert('प्रोसेसिंग में त्रुटि आई।');
                    }
                } catch (err) {
                    alert('सर्वर एरर!');
                } finally {
                    document.getElementById('subBtn').style.display = 'block';
                    document.getElementById('loadingText').style.display = 'none';
                }
            };

            function openModal(src) {
                document.getElementById('imageModal').style.display = 'block';
                document.getElementById('modalImg').src = src;
            }

            function closeModal() {
                document.getElementById('imageModal').style.display = 'none';
            }

            window.onclick = function(event) {
                let modal = document.getElementById('imageModal');
                if (event.target == modal) {
                    modal.style.display = "none";
                }
            }
        </script>
    </body>
    </html>
    """

@app.post("/process")
async def process_images(images: list[UploadFile] = File(...)):
    for f in os.listdir(OUTPUT_FOLDER):
        try:
            os.remove(os.path.join(OUTPUT_FOLDER, f))
        except:
            pass

    saved_files = []
    for image in images:
        if not image.filename:
            continue
        input_path = os.path.join(UPLOAD_FOLDER, image.filename)
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
        saved_files.append(input_path)

    processed_filenames = []

    def process_single(input_path):
        filename = os.path.basename(input_path)
        output_filename = f"cleaned_{filename}"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        if remove_watermark(input_path, output_path):
            return output_filename
        return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(process_single, saved_files))
        processed_filenames = [r for r in results if r]

    if not processed_filenames:
        return JSONResponse({"success": False})

    zip_filename = 'cleaned_images.zip'
    zip_path = os.path.join(OUTPUT_FOLDER, zip_filename)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for fname in processed_filenames:
            file_full_path = os.path.join(OUTPUT_FOLDER, fname)
            zipf.write(file_full_path, fname)

    return JSONResponse({
        "success": True,
        "files": processed_filenames,
        "zip_file": zip_filename
    })
    
