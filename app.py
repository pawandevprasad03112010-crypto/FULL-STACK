import os
import json
from collections import OrderedDict
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from PIL import Image
from google import genai
from google.genai import types

app = Flask(__name__)
CORS(app)

# Environment Variable se API Key read karein
API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None

DEFAULT_AMENITIES = ["LIFT", "SECURITY", "POWER_BACKUP", "PARKING"]

# Exact Order Maintain Karne Ke Liye Template
def get_default_structure():
    return {
        "user_id": "ADMIN",
        "posted_by_type": "ADMIN",
        "category": {
            "purpose": "BUY",
            "property_type": "RESIDENTIAL",
            "sub_type": "FLAT_APARTMENT"
        },
        "contact": {
            "owner_name": "ADMIN",
            "phone": "na",
            "owner_type": "AGENT"
        },
        "title_and_description": {
            "title": "na",
            "description": "na"
        },
        "location": {
            "city": "Kolkata",
            "locality": "na",
            "sub_locality": "na",
            "landmark": "na",
            "pincode": "na",
            "state": "West Bengal",
            "full_address": "na"
        },
        "pricing": {
            "price_display": "na",
            "price_numeric": "na",
            "is_negotiable": True
        },
        "specifications": {
            "bhk_type": "na",
            "bhk_numeric": "na",
            "builtup_sqft": "na",
            "carpet_sqft": "na",
            "super_builtup_sqft": "na",
            "floor_no": "na",
            "total_floors": "na",
            "bathrooms": "na",
            "balconies": "na",
            "furnishing_status": "na",
            "construction_status": "na",
            "facing_direction": "NORTH WEST",
            "property_age": "na",
            "parking": "YES",
            "ownership_type": "FREEHOLD"
        },
        "amenities": DEFAULT_AMENITIES,
        "media": {
            "images": [],
            "ai_short_video_url": "na"
        },
        "created_at": "NOT_AVAILABLE_DATE"
    }

def apply_custom_logic(data):
    # 1. Area Auto-Calculation Logic
    specs = data.get("specifications", {})
    def to_float(val):
        try: return float(str(val).replace(',', '').strip())
        except (ValueError, TypeError): return None

    def to_int(val):
        try: return int(str(val).strip())
        except (ValueError, TypeError): return None

    carpet = to_float(specs.get("carpet_sqft"))
    builtup = to_float(specs.get("builtup_sqft"))
    super_builtup = to_float(specs.get("super_builtup_sqft"))

    # Agar teeno me se koi ek bhi mil jaye toh baki do calculate ho jayenge
    if super_builtup and not builtup and not carpet:
        builtup = round(super_builtup / 1.25, 2)
        carpet = round(builtup / 1.20, 2)
    elif builtup and not super_builtup and not carpet:
        super_builtup = round(builtup * 1.25, 2)
        carpet = round(builtup / 1.20, 2)
    elif carpet and not builtup and not super_builtup:
        builtup = round(carpet * 1.20, 2)
        super_builtup = round(builtup * 1.25, 2)
    elif carpet and super_builtup and not builtup:
        builtup = round(carpet * 1.20, 2)

    specs["carpet_sqft"] = carpet if carpet is not None else "na"
    specs["builtup_sqft"] = builtup if builtup is not None else "na"
    specs["super_builtup_sqft"] = super_builtup if super_builtup is not None else "na"

    # Bathroom & Balcony Logic
    bathrooms = to_int(specs.get("bathrooms"))
    balconies = to_int(specs.get("balconies"))
    if balconies is None or str(balconies).lower() == "na":
        if bathrooms is not None:
            if 1 <= bathrooms <= 3:
                specs["balconies"] = 1
            elif bathrooms >= 4:
                specs["balconies"] = 2
            else:
                specs["balconies"] = "na"
        else:
            specs["balconies"] = "na"

    # Default Fallbacks
    if not specs.get("parking") or str(specs.get("parking")).lower() == "na":
        specs["parking"] = "YES"
    if not specs.get("facing_direction") or str(specs.get("facing_direction")).lower() == "na":
        specs["facing_direction"] = "NORTH WEST"

    data["specifications"] = specs

    # 2. Amenities Fallback Logic
    amenities = data.get("amenities")
    if not amenities or len(amenities) == 0:
        data["amenities"] = DEFAULT_AMENITIES

    # 3. Full Address Combination Logic
    loc = data.get("location", {})
    address_parts = []
    for key in ["sub_locality", "locality", "landmark", "city", "state", "pincode"]:
        val = str(loc.get(key, "")).strip()
        if val and val.lower() != "na":
            address_parts.append(val)
    
    if address_parts:
        loc["full_address"] = ", ".join(address_parts)
    else:
        loc["full_address"] = "na"

    data["location"] = loc
    return data

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/process-image', methods=['POST'])
def process_image():
    if not client:
        return jsonify({"error": "GEMINI_API_KEY environment variable is not set on Render."}), 500

    uploaded_files = request.files.getlist('photos')
    if not uploaded_files or len(uploaded_files) == 0:
        return jsonify({"error": "No photos uploaded"}), 400

    images = []
    for file in uploaded_files:
        if file.filename != '':
            images.append(Image.open(file.stream))

    prompt = f"""
    You are an expert real estate data extractor. Extract property details combining ALL uploaded images/pamphlets.
    Fit the extracted details into this exact JSON structure:
    {json.dumps(get_default_structure())}

    STRICT RULES:
    1. TITLE EXTRACTION: Read the main header text in the image carefully (e.g. "Flat for Resale in MUKUNDPUR Purbalok, EM Bypass"). Include the property type, listing type, locality, and sub-locality exactly as written in that main black/dark title section into "title_and_description.title".
    2. LOCATION, LANDMARK & PINCODE: Extract locality and sub_locality from image. Use your internal knowledge base to identify landmark or pincode if the locality/sub_locality (e.g. Mukundpur, Kolkata) is recognizable.
    3. AMENITIES: Extract any amenities visible or mentioned. If none are explicitly mentioned, leave it empty [] (our backend will fill defaults).
    4. NUMERIC FIELDS: Extract numeric values for price, sqft, bathrooms, balconies, etc.
    5. Return ONLY valid JSON data, no markdown formatting like ```json.
    """

    try:
        contents = images + [prompt]
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        extracted_json = json.loads(response.text)

        # Apply all custom calculations and rules
        final_data = apply_custom_logic(extracted_json)

        # Enforce original JSON key ordering so it doesn't flip
        ordered_template = get_default_structure()
        for key in ordered_template.keys():
            if key in final_data:
                ordered_template[key] = final_data[key]

        # Prevent Flask jsonify from auto-sorting keys alphabetically
        app.config['JSON_SORT_KEYS'] = False
        return jsonify(ordered_template)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
            
