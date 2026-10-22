import os
import zipfile
import io
import json
import uuid
import asyncio
from decimal import Decimal
import httpx
import boto3
from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# AWS और DynamoDB क्रेडेंशियल्स
AWS_ACCESS_KEY_ID = "AKIA32VVAONMQV5QKNED"
AWS_SECRET_ACCESS_KEY = "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"
REGION = "ap-south-1"
BUCKET_NAME = "property-images-estatex-1"
TABLE_NAME = "LARGE_PROPERTY_LISTING"
PRIMARY_KEY = "property_id"

TEMP_DIR = "/tmp/listings_cache"
os.makedirs(TEMP_DIR, exist_ok=True)

def extract_media_images(data):
    """ आपके JSON स्ट्रक्चर के media -> images से केवल प्रॉपर्टी की तस्वीरें निकालेगा """
    images = []
    try:
        media_section = data.get("media", {})
        if isinstance(media_section, dict):
            img_list = media_section.get("images", [])
            if isinstance(img_list, list):
                for img_url in img_list:
                    if isinstance(img_url, str) and img_url.startswith("http"):
                        if img_url not in images:
                            images.append(img_url)
    except Exception as e:
        print(f"Error extracting images: {e}")
    return images

def clean_and_convert_data(obj):
    """ None वैल्यूज़ हटाना और Float को DynamoDB के लिए Decimal में बदलना """
    if isinstance(obj, list):
        return [clean_and_convert_data(v) for v in obj if v is not None]
    elif isinstance(obj, dict):
        return {str(k): clean_and_convert_data(v) for k, v in obj.items() if v is not None and v != ""}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, int):
        return int(obj)
    else:
        return str(obj) if isinstance(obj, str) else obj

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "listings": None})

@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, zippfile: UploadFile = File(...)):
    for f in os.listdir(TEMP_DIR):
        file_path = os.path.join(TEMP_DIR, f)
        if os.path.isfile(file_path):
            os.remove(file_path)

    contents = await zippfile.read()
    listings = []

    with zipfile.ZipFile(io.BytesIO(contents), 'r') as z:
        for filename in z.namelist():
            if filename.endswith('.json') and not filename.startswith('__MACOSX'):
                file_bytes = z.read(filename)
                safe_path = os.path.join(TEMP_DIR, filename)
                with open(safe_path, "wb") as f_out:
                    f_out.write(file_bytes)
                
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
        raise HTTPException(status_code=400, detail=f"S3 connection failed: {str(e)}")

    output_zip = io.BytesIO()

    async def process_listing_async(client, listing):
        filename = listing['filename']
        retained_images = listing['images']
        
        file_path = os.path.join(TEMP_DIR, filename)
        if not os.path.exists(file_path):
            return None, None

        with open(file_path, "rb") as f_in:
            original_bytes = f_in.read()

        json_data = json.loads(original_bytes.decode('utf-8'))
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

        tasks = [download_and_upload(img_url) for img_url in retained_images]
        results = await asyncio.gather(*tasks)

        for old_url, new_url in results:
            if new_url:
                old_to_new_map[old_url] = new_url

        # केवल media -> images के अंदर के यूआरएल बदलना
        if "media" in json_data and isinstance(json_data["media"], dict):
            if "images" in json_data["media"] and isinstance(json_data["media"]["images"], list):
                updated_media_images = [
                    old_to_new_map.get(img, img) 
                    for img in json_data["media"]["images"] 
                    if img in old_to_new_map
                ]
                json_data["media"]["images"] = updated_media_images

        updated_json_bytes = json.dumps(json_data, indent=2, ensure_ascii=False).encode('utf-8')
        
        with open(file_path, "wb") as f_out:
            f_out.write(updated_json_bytes)

        return filename, updated_json_bytes

    async with httpx.AsyncClient() as client:
        semaphore = asyncio.Semaphore(15)
        
        async def bounded_process(listing):
            async with semaphore:
                return await process_listing_async(client, listing)

        all_tasks = [bounded_process(listing) for listing in listings_data]
        completed_results = await asyncio.gather(*all_tasks)

    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as z_out:
        for filename, updated_bytes in completed_results:
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
        raise HTTPException(status_code=400, detail=f"DynamoDB connection failed: {str(e)}")

    saved_count = 0
    for listing in listings_data:
        filename = listing['filename']
        file_path = os.path.join(TEMP_DIR, filename)
        if not os.path.exists(file_path):
            continue

        try:
            with open(file_path, "rb") as f_in:
                original_bytes = f_in.read()

            json_data = json.loads(original_bytes.decode('utf-8'))
            
            # Primary Key सुनिश्चित करना
            prop_id = json_data.get(PRIMARY_KEY)
            if not prop_id:
                prop_id = filename.split('_')[1].split('.')[0] if '_' in filename else str(uuid.uuid4())
                json_data[PRIMARY_KEY] = str(prop_id)
            else:
                json_data[PRIMARY_KEY] = str(prop_id)

            # डेटा क्लीनिंग (Decimal और None removal)
            cleaned_item = clean_and_convert_data(json_data)

            table.put_item(Item=cleaned_item)
            saved_count += 1
        except Exception as ex:
            print(f"Failed to save {filename} to DynamoDB: {str(ex)}")

    return {"message": f"सफलतापूर्वक {saved_count} लिस्टिंग DynamoDB टेबल '{TABLE_NAME}' में सेव हो गई हैं!"}
                
