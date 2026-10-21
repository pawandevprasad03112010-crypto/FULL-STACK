import os
import zipfile
import io
import json
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
import boto3
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

app = FastAPI()

# आपके AWS और DynamoDB क्रेडेंशियल्स
AWS_ACCESS_KEY_ID = "AKIA32VVAONMQV5QKNED"
AWS_SECRET_ACCESS_KEY = "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"
REGION = "ap-south-1"
BUCKET_NAME = "property-images-estatex-1"
TABLE_NAME = "LARGE_PROPERTY_LISTING"
PRIMARY_KEY = "property_id"

executor = ThreadPoolExecutor(max_workers=10)
UPLOADED_FILES_CACHE = {}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Only Broker - S3 & DynamoDB Migrator</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen font-sans">
    <div class="container mx-auto px-4 py-8 max-w-6xl">
        <header class="bg-white shadow rounded-lg p-6 mb-8 text-center">
            <h1 class="text-3xl font-bold text-gray-800">Only Broker - ऑटोमैटिक S3 और DynamoDB माइग्रेटर</h1>
            <p class="text-gray-600 mt-2">क्रेडेंशियल्स पहले से सेट हैं। अपनी ज़िप फ़ाइल अपलोड करें और मैनेज करें।</p>
        </header>

        {% if not listings %}
        <div class="bg-white shadow rounded-lg p-8 max-w-xl mx-auto">
            <form action="/upload" method="POST" enctype="multipart/form-data" class="space-y-6">
                <div>
                    <label class="block text-lg font-medium text-gray-700 mb-2">लिस्टिंग ज़िप फ़ाइल अपलोड करें (.zip)</label>
                    <input type="file" name="zippfile" accept=".zip" required class="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100">
                </div>
                <button type="submit" class="w-full bg-blue-600 text-white font-bold py-3 px-4 rounded-lg hover:bg-blue-700 transition">अपलोड और पार्स करें</button>
            </form>
        </div>
        {% else %}
        <div class="bg-green-50 border border-green-200 shadow rounded-lg p-4 mb-6 text-green-800 font-medium text-center">
            ✓ AWS S3 और DynamoDB क्रेडेंशियल्स एक्टिव हैं। नीचे अपनी लिस्टिंग चेक करें।
        </div>

        <div class="space-y-6" id="listings-container">
            {% for item in listings %}
            <div class="bg-white shadow rounded-lg p-6 listing-card" data-filename="{{ item.filename }}">
                <h3 class="text-xl font-bold text-indigo-700 mb-4 border-b pb-2">लिस्टिंग #{{ loop.index }}: {{ item.filename }}</h3>
                <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4 image-grid">
                    {% for img in item.images %}
                    <div class="relative border rounded p-2 bg-gray-50 image-item" data-url="{{ img }}">
                        <img src="{{ img }}" alt="Media Image" class="w-full h-32 object-cover rounded mb-2" onerror="this.src='https://via.placeholder.com/150?text=Image+Error'">
                        <p class="text-xs text-gray-500 truncate mb-2" title="{{ img }}">{{ img }}</p>
                        <button onclick="removeImage(this)" class="absolute top-1 right-1 bg-red-600 text-white rounded-full w-6 h-6 flex items-center justify-center text-xs hover:bg-red-700 font-bold">&times;</button>
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        </div>

        <div class="mt-8 sticky bottom-4 bg-white shadow-xl rounded-lg p-4 flex flex-col sm:flex-row justify-between items-center border gap-4 z-50">
            <span class="font-semibold text-gray-700">कुल लिस्टिंग: {{ listings|length }}</span>
            <div class="flex gap-3">
                <button onclick="startMigration()" class="bg-green-600 text-white font-bold py-3 px-6 rounded-lg hover:bg-green-700 transition shadow-lg">S3 पर माइग्रेट करें & ज़िप डाउनलोड करें</button>
                <button onclick="saveToDynamoDB()" class="bg-purple-600 text-white font-bold py-3 px-6 rounded-lg hover:bg-purple-700 transition shadow-lg">DynamoDB में सेव करें</button>
            </div>
        </div>
        {% endif %}
    </div>

    <script>
        function removeImage(btn) {
            btn.closest('.image-item').remove();
        }

        async function getListingsData() {
            const listingsData = [];
            document.querySelectorAll('.listing-card').forEach(card => {
                const filename = card.getAttribute('data-filename');
                const images = [];
                card.querySelectorAll('.image-item').forEach(item => {
                    images.push(item.getAttribute('data-url'));
                });
                listingsData.push({ filename, images });
            });
            return listingsData;
        }

        async function startMigration() {
            const listingsData = await getListingsData();
            const btn = event.target;
            btn.textContent = 'S3 पर माइग्रेट हो रहा है...';
            btn.disabled = true;

            try {
                const response = await fetch('/migrate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ listings: listingsData })
                });

                if(response.ok) {
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'updated_listings_s3.zip';
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    alert('माइग्रेशन सफल! S3 यूआरएल अपडेट हो गए हैं और ज़िप डाउनलोड हो गई है।');
                } else {
                    const err = await response.json();
                    alert('माइग्रेशन असफल: ' + (err.detail || 'त्रुटि'));
                }
            } catch(e) {
                alert('त्रुटि: ' + e.message);
            } finally {
                btn.textContent = 'S3 पर माइग्रेट करें & ज़िप डाउनलोड करें';
                btn.disabled = false;
            }
        }

        async function saveToDynamoDB() {
            const listingsData = await getListingsData();
            const btn = event.target;
            btn.textContent = 'DynamoDB में सेव हो रहा है...';
            btn.disabled = true;

            try {
                const response = await fetch('/save-dynamodb', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ listings: listingsData })
                });

                const result = await response.json();
                if(response.ok) {
                    alert(result.message);
                } else {
                    alert('सेव करने में असफल: ' + (result.detail || 'त्रुटि'));
                }
            } catch(e) {
                alert('त्रुटि: ' + e.message);
            } finally {
                btn.textContent = 'DynamoDB में सेव करें';
                btn.disabled = false;
            }
        }
    </script>
</body>
</html>
"""

def extract_media_images(data):
    images = []
    def recursive_search(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and (v.startswith('http://') or v.startswith('https://')):
                    if any(term in v.lower() for term in ['media', 'img', 'image', '.jpg', '.jpeg', '.png', '.webp', '99acres']):
                        if v not in images:
                            images.append(v)
                else:
                    recursive_search(v)
        elif isinstance(obj, list):
            for item in obj:
                recursive_search(item)
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
async def index():
    return HTML_TEMPLATE.replace("{% if not listings %}", "{% if true %}").replace("{% else %}", "").replace("{% endif %}", "")

@app.post("/upload", response_class=HTMLResponse)
async def upload(zippfile: UploadFile = File(...)):
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

    cards_html = ""
    for idx, item in enumerate(listings):
        imgs_html = ""
        for img in item['images']:
            imgs_html += f'''
            <div class="relative border rounded p-2 bg-gray-50 image-item" data-url="{img}">
                <img src="{img}" alt="Image" class="w-full h-32 object-cover rounded mb-2" onerror="this.src='https://via.placeholder.com/150?text=Error'">
                <p class="text-xs text-gray-500 truncate mb-2" title="{img}">{img}</p>
                <button onclick="removeImage(this)" class="absolute top-1 right-1 bg-red-600 text-white rounded-full w-6 h-6 flex items-center justify-center text-xs hover:bg-red-700 font-bold">&times;</button>
            </div>
            '''
        cards_html += f'''
        <div class="bg-white shadow rounded-lg p-6 listing-card" data-filename="{item['filename']}">
            <h3 class="text-xl font-bold text-indigo-700 mb-4 border-b pb-2">लिस्टिंग #{idx+1}: {item['filename']}</h3>
            <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4 image-grid">{imgs_html}</div>
        </div>
        '''

    final_page = HTML_TEMPLATE.replace('{% if not listings %}\n        <div class="bg-white shadow rounded-lg p-8 max-w-xl mx-auto">.*?</div>\n        {% else %}', '')
    final_page = final_page.replace('{% for item in listings %}.*?{% endfor %}', cards_html)
    final_page = final_page.replace('{{ listings|length }}', str(len(listings)))
    final_page = final_page.replace('{% if not listings %}', '').replace('{% else %}', '').replace('{% endif %}', '')
    return HTMLResponse(content=final_page)

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
