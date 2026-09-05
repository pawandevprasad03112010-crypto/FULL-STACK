import os
import json
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from PIL import Image
from google import genai
from google.genai import types

app = Flask(__name__)
CORS(app)

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

JSON_STRUCTURE = {
    "user_id": "ADMIN",
    "posted_by_type": "ADMIN",
    "category": { "purpose": "BUY", "property_type": "RESIDENTIAL", "sub_type": "FLAT_APARTMENT" },
    "contact": { "owner_name": "ADMIN", "phone": "na", "owner_type": "AGENT" },
    "title_and_description": { "title": "na", "description": "na" },
    "location": { "city": "Kolkata", "locality": "na", "sub_locality": "na", "landmark": "na", "pincode": "na", "state": "West Bengal", "full_address": "na" },
    "pricing": { "price_display": "na", "price_numeric": "na", "is_negotiable": True },
    "specifications": {
      "bhk_type": "na", "bhk_numeric": "na", "builtup_sqft": "na", "carpet_sqft": "na",
      "super_builtup_sqft": "na", "floor_no": "na", "total_floors": "na", "bathrooms": "na",
      "balconies": "na", "furnishing_status": "na", "construction_status": "na",
      "facing_direction": "na", "property_age": "na", "parking": "na", "ownership_type": "FREEHOLD"
    },
    "amenities": ["LIFT", "SECURITY", "POWER_BACKUP", "PARKING"],
    "media": { "images": [], "ai_short_video_url": "na" },
    "created_at": "NOT_AVAILABLE_DATE"
}

def apply_custom_specifications_logic(specs):
    def to_float(val):
        try: return float(val)
        except (ValueError, TypeError): return None

    def to_int(val):
        try: return int(val)
        except (ValueError, TypeError): return None

    # 1. Area Auto-Calculation Logic
    carpet = to_float(specs.get("carpet_sqft"))
    builtup = to_float(specs.get("builtup_sqft"))
    super_builtup = to_float(specs.get("super_builtup_sqft"))

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

    # 2. Balcony Logic based on Bathrooms
    bathrooms = to_int(specs.get("bathrooms"))
    balconies = to_int(specs.get("balconies"))

    if balconies is None:
        if bathrooms is not None:
            if 1 <= bathrooms <= 3:
                specs["balconies"] = 1
            elif bathrooms >= 4:
                specs["balconies"] = 2
            else:
                specs["balconies"] = "na"
        else:
            specs["balconies"] = "na"

    # 3. Parking Default Logic
    parking = str(specs.get("parking", "")).strip()
    if not parking or parking.lower() == "na":
        specs["parking"] = "YES"

    # 4. Facing Direction Default Logic
    facing = str(specs.get("facing_direction", "")).strip()
    if not facing or facing.lower() == "na":
        specs["facing_direction"] = "NORTH WEST"

    return specs

# Route to load Frontend HTML
@app.route('/')
def home():
    return render_template('index.html')

# Route to process Image
@app.route('/process-image', methods=['POST'])
def process_image():
    if 'photo' not in request.files:
        return jsonify({"error": "No photo uploaded"}), 400

    file = request.files['photo']
    image = Image.open(file.stream)

    prompt = f"""
    Extract property details from this image/pamphlet and fit them into this exact JSON schema:
    {json.dumps(JSON_STRUCTURE)}

    Rules:
    1. If a field's value is missing or not visible in the image, strictly set its value to "na".
    2. Extract numeric values for numeric fields like carpet_sqft, builtup_sqft, super_builtup_sqft, bathrooms, balconies, etc.
    3. Keep existing default values like city="Kolkata", state="West Bengal", ownership_type="FREEHOLD" unless image explicitly mentions otherwise.
    4. Return ONLY valid JSON data, no extra markdown, text or explanation.
    """

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        extracted_json = json.loads(response.text)

        if "specifications" in extracted_json:
            extracted_json["specifications"] = apply_custom_specifications_logic(extracted_json["specifications"])

        return jsonify(extracted_json)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
  
