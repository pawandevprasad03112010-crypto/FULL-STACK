import os
import zipfile
import io
import json
import uuid
import asyncio
from typing import List
import httpx
import boto3
from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# आपके AWS S3 क्रेडेंशियल्स
AWS_ACCESS_KEY_ID = "AKIA32VVAONMQV5QKNED"
AWS_SECRET_ACCESS_KEY = "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"
REGION = "ap-south-1"
BUCKET_NAME = "property-images-estatex-1"

TEMP_DIR = "/tmp/listings_cache"
os.makedirs(TEMP_DIR, exist_ok=True)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload-json-files")
async def upload_json_files(files: List[UploadFile] = File(...)):
    # पुराना कैश साफ़ करना
    for f in os.listdir(TEMP_DIR):
        fp = os.path.join(TEMP_DIR, f)
        if os.path.isfile(fp):
            os.remove(fp)

    listings = []
    for file in files:
        filename = file.filename
        content = await file.read()
        
        safe_path = os.path.join(TEMP_DIR, filename)
        with open(safe_path, "wb") as f_out:
            f_out.write(content)
        
        try:
            raw_data = json.loads(content.decode('utf-8'))
            
            # यदि JSON रूट लिस्ट है तो उसे संभालें, वरना सिंगल डिक्शनरी मानें
            items = raw_data if isinstance(raw_data, list) else [raw_data]
            
            for item in items:
                if not isinstance(item, dict):
                    continue
                images = []
                media_section = item.get("media", {})
                if isinstance(media_section, dict):
                    img_list = media_section.get("images", [])
                    if isinstance(img_list, list):
                        for img_url in img_list:
                            if isinstance(img_url, str) and img_url.startswith("http"):
                                if img_url not in images:
                                    images.append(img_url)

                listings.append({
                    "filename": filename,
                    "images": images
                })
        except Exception as e:
            print(f"Error parsing {filename}: {e}")

    return {"listings": listings}

@app.post("/migrate-s3")
async def migrate_s3(data: dict):
    listings_data = data.get('listings', [])

    try:
        s3 = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=REGION
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"S3 connection failed: {str(e)}")

    output_zip = io.BytesIO()

    async def process_listing(client, listing):
        filename = listing['filename']
        reterr_images = listing['images']
        
        file_path = os.path.join(TEMP_DIR, filename)
        if not os.path.exists(file_path):
            return None, None

        with open(file_path, "rb") as f_in:
            raw_content = json.loads(f_in.read().decode('utf-8'))

        is_list = isinstance(raw_content, list)
        json_items = raw_content if is_list else [raw_content]

        old_to_new_map = {}

        async def download_and_upload(img_url):
            try:
                resp = await client.get(img_url, timeout=15.0, follow_redirects=True)
                if resp.status_code == 200:
                    img_bytes = resp.content
                    file_ext = img_url.split('?')[0].split('.')[-1]
                    if len(file_ext) > 4 or not file_ext.isalnum():
                        file_ext = 'jpg'
                    
                    s3_key = f"listings_images/{uuid.uuid4()}.{file_ext}"
                    
                    await asyncio.to_thread(
                        s3.put_object,
                        Bucket=BUCKET_NAME,
                        Key=s3_key,
                        Body=img_bytes,
                        ContentType=f'image/{file_ext}'
                    )
                    
                    s3_url = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{s3_key}"
                    return img_url, s3_url
            except Exception as ex:
                print(f"Failed to migrate {img_url}: {ex}")
            return img_url, None

        tasks = [download_and_upload(img_url) for img_url in reterr_images]
        results = await asyncio.gather(*tasks)

        for old_url, new_url in results:
            if new_url:
                old_to_new_map[old_url] = new_url

        # हर आइटम के अंदर media -> images को अपडेट करना
        for item in json_items:
            if isinstance(item, dict) and "media" in item and isinstance(item["media"], dict):
                updated_media_images = [
                    old_to_new_map[img] for img in reterr_images if img in old_to_new_map
                ]
                item["media"]["images"] = updated_media_images

        final_data = json_items if is_list else json_items[0]
        updated_bytes = json.dumps(final_data, indent=2, ensure_ascii=False).encode('utf-8')
        return filename, updated_bytes

    async with httpx.AsyncClient() as client:
        semaphore = asyncio.Semaphore(15)
        async def bounded_process(listing):
            async with semaphore:
                return await process_listing(client, listing)

        tasks = [bounded_process(l) for l in listings_data]
        results = await asyncio.gather(*tasks)

    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as z_out:
        for fn, bts in results:
            if fn and bts:
                z_out.writestr(fn, bts)

    output_zip.seek(0)
    return StreamingResponse(
        output_zip,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=updated_listings_s3.zip"}
    )
    
