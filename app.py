import os
import zipfile
import io
import json
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
import boto3
import requests
from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()

# Jinja2 टेम्पलेट फोल्डर सेट करना
templates = Jinja2Templates(directory="templates")

# आपके AWS और DynamoDB क्रेडेंशियल्स
AWS_ACCESS_KEY_ID = "AKIA32VVAONMQV5QKNED"
AWS_SECRET_ACCESS_KEY = "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"
REGION = "ap-south-1"
BUCKET_NAME = "property-images-estatex-1"
TABLE_NAME = "LARGE_PROPERTY_LISTING"
PRIMARY_KEY = "property_id"

executor = ThreadPoolExecutor(max_workers=10)
UPLOADED_FILES_CACHE = {}

def extract_media_images(data):
    images = []
    def recursive_search(obj, parent_key=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                # प्रोफाइल, यूजर, कॉंटेक्ट या अवतार वाले URLs को पूरी तरह से इग्नोर करना
                if any(bad_word in k.lower() for bad_word in ['profile', 'user', 'contact', 'avatar', 'agent', 'builder_logo']):
                    continue
                
                if isinstance(v, str) and (v.startswith('http://') or v.startswith('https://')):
                    v_lower = v.lower()
                    if any(bad in v_lower for bad in ['profile', 'user', 'contact', 'avatar', 'default']):
                        continue
                    
                    # केवल असली प्रॉपर्टी और मीडिया इमेजेस वाले URLs को चुनना
                    if any(term in v_lower for term in ['media', 'img', 'image', 'property', '.jpg', '.jpeg', '.png', '.webp', '99acres']):
                        if v not in images:
                            images.append(v)
                else:
                    recursive_search(v, parent_key=k)
        elif isinstance(obj, list):
            for item in obj:
                recursive_search(item, parent_key=parent_key)
                
    recursive_search(data)
    return images

def update_images_in_json(obj, old_to_new_map):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v in old_to_new_map:
                obj[k] = old_to_new_map[v]
            elif isinstance(v, (dict, list)):
                update_images_in_json(v, old_to_new_map)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str) and item in old_to_new_map:
                obj[i] = old_to_new_map[item]
            elif isinstance(item, (dict, list)):
                update_images_in_json(item, old_to_new_map)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "listings": None})

@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, zippfile: UploadFile = File(...)):
    global UPLOADED_FILES_CACHE
    UPLOADED_FILES_CACHE.clear()

    contents = await zippfile.read()
    listings = []

    with zipfile.ZipFile(io.BytesIO(contents), 'r') as z:
        for filename in z.namelist():
            if filename.endswith('.json') and not filename.startswith('__MACOSX'):
                file_bytes = z.read(filename)
                UPLOADED_FILES_CACHE[filename] = file_bytes
                try:
                    data = json.loads(file_bytes.decode('utf-8'))
                    images = extract_media_images(data)
                    listings.append({
                        'filename': filename,
                        'images': images
                    })
                except Exception as e:
                    print(f"Error parsing {filename}: {e}")

    return templates.TemplateResponse("index.html", {"request": request, "listings": listings})

@app.post("/migrate")
async def migrate(data: dict):
    listings_data = data.get('listings')

    try:
        s3 = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=REGION
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"S3 कनेक्शन विफल: {str(e)}")

    output_zip = io.BytesIO()

    def process_single_listing(listing):
        filename = listing['filename']
        retained_images = listing['images']
        original_bytes = UPLOADED_FILES_CACHE.get(filename)
        if not original_bytes:
            return None, None

        json_data = json.loads(original_bytes.decode('utf-8'))
        old_to_new_map = {}

        for img_url in retained_images:
            try:
                resp = requests.get(img_url, timeout=10)
                if resp.status_code == 200:
                    img_bytes = resp.content
                    file_ext = img_url.split('?')[0].split('.')[-1]
                    if len(file_ext) > 4 or not file_ext.isalnum():
                        file_ext = 'jpg'
                    
                    s3_key = f"listings_images/{uuid.uuid4()}.{file_ext}"
                    s3.put_object(Bucket=BUCKET_NAME, Key=s3_key, Body=img_bytes, ContentType=f'image/{file_ext}')
                    
                    s3_url = f"https://{BUCKET_NAME}.s3.{REGION}.amazonaws.com/{s3_key}"
                    old_to_new_map[img_url] = s3_url
            except Exception as ex:
                print(f"Failed to migrate {img_url}: {ex}")

        update_images_in_json(json_data, old_to_new_map)
        updated_json_bytes = json.dumps(json_data, indent=2, ensure_ascii=False).encode('utf-8')
        return filename, updated_json_bytes

    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as z_out:
        loop = asyncio.get_running_loop()
        tasks = [loop.run_in_executor(executor, process_single_listing, listing) for listing in listings_data]
        results = await asyncio.gather(*tasks)

        for filename, updated_bytes in results:
            if filename and updated_bytes:
                z_out.writestr(filename, updated_bytes)

    output_zip.seek(0)
    return StreamingResponse(
        output_zip,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=updated_listings_s3.zip"}
    )

@app.post("/save-dynamodb")
async def save_to_dynamodb(data: dict):
    listings_data = data.get('listings')

    try:
        dynamodb = boto3.resource(
            'dynamodb',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=REGION
        )
        table = dynamodb.Table(TABLE_NAME)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"DynamoDB कनेक्शन विफल: {str(e)}")

    saved_count = 0
    for listing in listings_data:
        filename = listing['filename']
        original_bytes = UPLOADED_FILES_CACHE.get(filename)
        if not original_bytes:
            continue

        try:
            json_data = json.loads(original_bytes.decode('utf-8'))
            if PRIMARY_KEY not in json_data:
                prop_id = filename.split('_')[1].split('.')[0] if '_' in filename else str(uuid.uuid4())
                json_data[PRIMARY_KEY] = prop_id

            table.put_item(Item=json_data)
            saved_count += 1
        except Exception as ex:
            print(f"Failed to save {filename} to DynamoDB: {ex}")

    return {"message": f"सफलतापूर्वक {saved_count} लिस्टिंग DynamoDB टेबल '{TABLE_NAME}' में सेव कर दी गई हैं!"}
    
