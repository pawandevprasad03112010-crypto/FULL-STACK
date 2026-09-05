import os
import json
from flask import Flask, request, Response, render_template
from flask_cors import CORS
from PIL import Image
from google import genai
from google.genai import types

app = Flask(__name__)
CORS(app)

# JSON key auto-sorting ko disable karein
app.json.sort_keys = False

API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None

DEFAULT_AMENITIES = ["LIFT", "SECURITY", "POWER_BACKUP", "PARKING"]

# AAPKA EXACT JSON FORMAT (Sequence Maintain karne ke liye)
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
    # 1. Real Estate Standard Area Calculation Logic
    specs = data.get("specifications", {})

    def to_float(val):
        if val is None or str(val).strip().lower() in ["na", "", "none", "null"]:
            return None
        try:
            # Clean string like "1430 sqft", "1,430", etc.
            clean_str = "".join([c for c in str(val) if c.isdigit() or c == '.'])
            return float(clean_str) if clean_str else None
        except (ValueError, TypeError):
            return None

    def to_int(val):
        try: return int(str(val).strip())
        except (ValueError, TypeError): return None

    carpet = to_float(specs.get("carpet_sqft"))
    builtup = to_float(specs.get("builtup_sqft"))
    super_builtup = to_float(specs.get("super_builtup_sqft"))

    # Agart teeno me se koi bhi 1 mil jaye, toh baki 2 calculate ho jayenge
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
    elif builtup and super_builtup and not carpet:
        carpet = round(builtup / 1.20, 2)

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
        return Response(json.dumps({"error": "GEMINI_API_KEY environment variable is not set on Render."}), status=500, mimetype='application/json')

    uploaded_files = request.files.getlist('photos')
    if not uploaded_files or len(uploaded_files) == 0:
        return Response(json.dumps({"error": "No photos uploaded"}), status=400, mimetype='application/json')

    images = []
    for file in uploaded_files:
        if file.filename != '':
            images.append(Image.open(file.stream))

    prompt = f"""
    You are an expert real estate data extractor. Extract property details combining ALL uploaded images.
    Fit the extracted details into this exact JSON structure:
    {json.dumps(get_default_structure())}

    STRICT RULES FOR EXTRACTION:
    1. TITLE: "title_and_description.title" MUST contain ONLY the main dark bold property name (e.g. "Moonlit Heights" or "MUKUNDPUR" or "Axis Plaza Apartment").
    2. DESCRIPTION: "title_and_description.description" MUST contain the entire header/property text line.
    3. LOCATION & LOCALITY:
       - Do NOT put property titles or building names into "locality" or "sub_locality".
       - "locality" and "sub_locality" MUST be the actual area/neighborhood names (e.g. "Baguiati", "EM Bypass", "Mukundpur").
       - Search & fill accurate Landmark and Pincode for this locality using web knowledge.
    4. AREA SPECIFICATIONS (SQFT):
       - Look for ANY area mention in image (Super Built-up Area, Built-up Area, or Carpet Area).
       - Extract numeric sqft value for whichever is visible (e.g. 1430 for "1430 sqft Super Built-up Area").
       - Put "na" for areas not explicitly mentioned in image (Backend will auto-calculate them).
    5. AMENITIES: Extract visible amenities. If none found, return [].
    6. Return ONLY raw JSON string without markdown wrappers.
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

        # Apply custom logic & calculation formulas
        final_data = apply_custom_logic(extracted_json)

        # Enforce exact top-to-bottom key sequence matching your provided template
        template = get_default_structure()
        ordered_output = {}
        for key in template.keys():
            if key in final_data:
                ordered_output[key] = final_data[key]
            else:
                ordered_output[key] = template[key]

        # Convert to formatted JSON string maintaining exact key order
        json_output_string = json.dumps(ordered_output, indent=2, sort_keys=False)

        return Response(json_output_string, status=200, mimetype='application/json')

    except Exception as e:
        return Response(json.dumps({"error": str(e)}), status=500, mimetype='application/json')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
